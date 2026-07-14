"""Экстрактор: каркас сайт-кролера (построение очереди URL, без сетевого обхода).

Контракт:
    Читает   — config.crawl_seed_urls, config.crawl.max_urls,
               config.sources.crux.key_urls; опционально — канонические таблицы
               GSC/Webmaster (страницы по кликам) и Direct (страницы по расходу).
               Сетевых запросов НЕ делает — только детерминированный приоритет.
    Пишет    — data/raw/site_crawl/url_queue.json + manifest.json.
    Деградация — опционален; при отсутствии очереди CWV-точечные URL берутся
                 из config.sources.crux.key_urls.
    LLM      — не используется (принцип 3).

Правила формирования очереди (в порядке приоритета):
    1. Явные URL из config.crawl_seed_urls (всегда включаются первыми).
    2. Ключевые посадочные URL из config.sources.crux.key_urls (если не dup).
    3. Топ-N страниц по расходу Директа (canonical/costs.parquet, по убыванию cost).
    4. Топ-N страниц по органике GSC/Webmaster (canonical/seo_queries_gsc.parquet
       или seo_queries_webmaster.parquet, по убыванию clicks).
    5. Страницы, URL которых содержит хотя бы одно слово из config.wordstat_seeds
       (из органики, не уже добавленные выше).

Если кандидатов больше max_urls — хвост отбрасывается; в manifest записывается
кавет с числом отброшенных.

Параметр max_urls:
    config.crawl.max_urls  (клиент)  > DEFAULT_MAX_URLS (30)  > аргумент функции.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "0.1.0"
SOURCE = "site_crawl"
CANONICAL_TABLES: list[str] = []

DEFAULT_MAX_URLS = 30

# Колонка страницы в канонических таблицах GSC/Webmaster.
_PAGE_COL_GSC = "page"
_PAGE_COL_WM = "page"
# Колонка страницы в Директ-расходах.
_PAGE_COL_COST = "entry_page"
# Колонка расхода/кликов для сортировки.
_COST_COL = "cost"
_CLICKS_COL = "clicks"


# ── Публичный API ────────────────────────────────────────────────────────────

def resolve_max_urls(config: dict[str, Any], default: int = DEFAULT_MAX_URLS) -> int:
    """max_urls из config.crawl.max_urls, иначе ``default``."""
    crawl_cfg = (config.get("crawl") or {})
    val = crawl_cfg.get("max_urls")
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def build_url_priority_list(
    config: dict[str, Any],
    canonical_dir: Path | None = None,
    *,
    max_urls: int | None = None,
    top_n_each_source: int = 20,
) -> dict[str, Any]:
    """Построить приоритетный список URL без HTTP-запросов.

    Возвращает словарь::

        {
            "urls":            list[str],  # итоговый список, ≤ max_urls
            "total_candidates": int,       # до усечения
            "truncated":       bool,
            "caveat":          str | None, # заполнен при usечении
            "url_sources":     dict[str, str],  # url -> откуда взят
        }
    """
    effective_max = max_urls if max_urls is not None else resolve_max_urls(config)

    seen: dict[str, str] = {}  # url -> источник (первый выигрывает)

    def _add(url: str, source_label: str) -> None:
        url = url.strip()
        if not url:
            return
        # Normalise trailing slash but preserve bare "/" root.
        normalised = url.rstrip("/") or "/"
        if normalised not in seen:
            seen[normalised] = source_label

    # 1. Явные seed URL из config.
    for u in (config.get("crawl_seed_urls") or []):
        _add(str(u), "explicit_seed")

    # 2. Ключевые посадочные URL из CrUX-конфига.
    crux_cfg = ((config.get("sources") or {}).get("crux") or {})
    for u in (crux_cfg.get("key_urls") or []):
        _add(str(u), "crux_key_url")

    # 3–5. Страницы из канонических таблиц (если доступны).
    if canonical_dir is not None:
        canonical_dir = Path(canonical_dir)

        # 3. По расходу Директа.
        for url in _pages_from_canonical(
            canonical_dir, "costs.parquet", _PAGE_COL_COST, _COST_COL, top_n_each_source
        ):
            _add(url, "top_spend")

        # 4а. Органика GSC.
        for url in _pages_from_canonical(
            canonical_dir, "seo_queries_gsc.parquet", _PAGE_COL_GSC, _CLICKS_COL, top_n_each_source
        ):
            _add(url, "top_organic_gsc")

        # 4б. Органика Webmaster (запасной вариант, если GSC нет).
        for url in _pages_from_canonical(
            canonical_dir, "seo_queries_webmaster.parquet", _PAGE_COL_WM, _CLICKS_COL, top_n_each_source
        ):
            _add(url, "top_organic_webmaster")

        # 5. Страницы с ключевыми словами из wordstat_seeds.
        seeds = [str(s).lower() for s in (config.get("wordstat_seeds") or []) if str(s).strip()]
        if seeds:
            for url in _pages_matching_keywords(
                canonical_dir, seeds, top_n_each_source
            ):
                _add(url, "keyword_match")

    all_candidates = list(seen.keys())
    total = len(all_candidates)
    truncated = total > effective_max
    final_urls = all_candidates[:effective_max]

    caveat: str | None = None
    if truncated:
        dropped = total - effective_max
        caveat = (
            f"Очередь усечена до {effective_max} URL (crawl.max_urls). "
            f"Отброшено {dropped} кандидатов. "
            "Увеличьте crawl.max_urls в config.yaml, если нужно покрыть больше страниц."
        )

    return {
        "urls": final_urls,
        "total_candidates": total,
        "truncated": truncated,
        "caveat": caveat,
        "url_sources": {u: seen[u] for u in final_urls},
    }


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Записать очередь URL в data/raw/site_crawl/url_queue.json.

    Сетевых запросов не делает — только детерминированный приоритет.
    """
    from . import _common as C

    log = log or (lambda _msg: None)

    canonical_dir: Path | None = None
    try:
        canonical_dir = Path(paths.canonical) if paths.canonical else None
    except AttributeError:
        pass

    result = build_url_priority_list(config, canonical_dir)
    log(f"{SOURCE}: кандидатов {result['total_candidates']}, "
        f"итого в очереди {len(result['urls'])}"
        + (f" (усечено, кавет: {result['caveat']!r})" if result["truncated"] else ""))

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    _dump(out_dir / "url_queue.json", result)

    manifest = _record_manifest(paths, result)
    log(f"{SOURCE}: url_queue.json записан, {len(result['urls'])} URL")

    return {
        "source": SOURCE,
        "rows": len(result["urls"]),
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        **result,
    }


# ── Вспомогательные функции ──────────────────────────────────────────────────

def _pages_from_canonical(
    canonical_dir: Path,
    table_file: str,
    page_col: str,
    sort_col: str,
    top_n: int,
) -> list[str]:
    """Топ-N уникальных URL из parquet-таблицы, отсортированных по убыванию sort_col."""
    path = canonical_dir / table_file
    if not path.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path, columns=[c for c in [page_col, sort_col] if c])
        if page_col not in df.columns:
            return []
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False)
        pages = df[page_col].dropna().astype(str).unique().tolist()
        result_pages = []
        for p in pages[:top_n]:
            p = p.strip()
            if p:
                result_pages.append(p.rstrip("/") or "/")
        return result_pages
    except Exception:
        return []


def _pages_matching_keywords(
    canonical_dir: Path,
    seeds: list[str],
    top_n: int,
) -> list[str]:
    """Страницы GSC/Webmaster, URL которых содержит слово из seeds."""
    matched: list[str] = []
    for table_file, page_col in [
        ("seo_queries_gsc.parquet", _PAGE_COL_GSC),
        ("seo_queries_webmaster.parquet", _PAGE_COL_WM),
    ]:
        path = canonical_dir / table_file
        if not path.exists():
            continue
        try:
            import pandas as pd
            df = pd.read_parquet(path, columns=[page_col])
            pages = df[page_col].dropna().astype(str).unique()
            for page in pages:
                page_lower = page.lower()
                if any(seed in page_lower for seed in seeds):
                    url = page.strip().rstrip("/")
                    if url not in matched:
                        matched.append(url)
        except Exception:
            continue
        if len(matched) >= top_n:
            break
    return matched[:top_n]


def _record_manifest(paths: Any, result: dict[str, Any]) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    extra: dict[str, Any] = {
        "source_mode": "deterministic",
        "total_candidates": result["total_candidates"],
        "urls_queued": len(result["urls"]),
        "truncated": result["truncated"],
    }
    if result["caveat"]:
        extra["caveat"] = result["caveat"]

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from="", date_to="",
        rows=len(result["urls"]),
        script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra=extra,
    )


def _dump(path: Path, obj: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
