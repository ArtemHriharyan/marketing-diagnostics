"""Экстрактор: сайт-кролер — очередь URL + сбор свойств страниц.

Задача 3.5A — построение очереди URL без HTTP-запросов.
Задача 3.5B — детерминированный HTTP-обход и запись pages.parquet.

Контракт:
    Читает   — config.crawl_seed_urls, config.crawl.max_urls, config.crawl.base_url,
               config.sources.crux.key_urls; опционально — канонические таблицы
               GSC/Webmaster (страницы по кликам) и Direct (страницы по расходу).
    Пишет    — data/raw/site_crawl/url_queue.json
               data/raw/site_crawl/pages.parquet   (если base_url доступен)
               manifest.json
    Деградация — опционален; при отсутствии base_url HTTP-обход пропускается.
    LLM      — не используется (принцип 3).

pages.parquet schema (PAGES_SCHEMA):
    url, http_status, redirect_chain, final_url, canonical_url,
    robots_directive, in_sitemap, title, description, h1, crawled_at

redirect_chain — JSON-список промежуточных URL (history.url) до финального.
crawled_at     — ISO-8601 UTC.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SCRIPT_VERSION = "0.2.0"
SOURCE = "site_crawl"
CANONICAL_TABLES: list[str] = ["pages"]

DEFAULT_MAX_URLS = 30
CRAWL_TIMEOUT_SEC = 15

# Колонки выходной таблицы — строгий контракт (3.5B).
PAGES_SCHEMA: list[str] = [
    "url",
    "http_status",
    "redirect_chain",
    "final_url",
    "canonical_url",
    "robots_directive",
    "in_sitemap",
    "title",
    "description",
    "h1",
    "crawled_at",
]

# Колонка страницы в канонических таблицах GSC/Webmaster.
_PAGE_COL_GSC = "page"
_PAGE_COL_WM = "page"
# Колонка страницы в Директ-расходах.
_PAGE_COL_COST = "entry_page"
# Колонка расхода/кликов для сортировки.
_COST_COL = "cost"
_CLICKS_COL = "clicks"


# ── HTML-парсер (stdlib) ──────────────────────────────────────────────────────

class _MetaParser(HTMLParser):
    """Извлекает title, description, h1, canonical, robots из HTML без зависимостей."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.description: str | None = None
        self.h1: str | None = None
        self.canonical_url: str | None = None
        self.robots_directive: str | None = None
        self._in_title = False
        self._in_h1 = False
        self._title_buf: list[str] = []
        self._h1_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = {k.lower(): v for k, v in attrs}
        tag = tag.lower()
        if tag == "title" and self.title is None:
            self._in_title = True
            self._title_buf = []
        elif tag == "h1" and self.h1 is None:
            self._in_h1 = True
            self._h1_buf = []
        elif tag == "meta":
            name = (d.get("name") or "").lower()
            if name == "description" and self.description is None:
                raw = (d.get("content") or "").strip()
                self.description = raw or None
            elif name == "robots" and self.robots_directive is None:
                raw = (d.get("content") or "").strip()
                self.robots_directive = raw or None
        elif tag == "link":
            rel = (d.get("rel") or "").lower()
            if rel == "canonical" and self.canonical_url is None:
                raw = (d.get("href") or "").strip()
                self.canonical_url = raw or None

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._in_title:
            self._in_title = False
            text = "".join(self._title_buf).strip()
            self.title = text or None
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            text = "".join(self._h1_buf).strip()
            self.h1 = text or None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        elif self._in_h1:
            self._h1_buf.append(data)


def _parse_page_meta(html_text: str) -> dict[str, Any]:
    """Разобрать HTML и вернуть dict с title/description/h1/canonical_url/robots_directive."""
    parser = _MetaParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return {
        "title": parser.title,
        "description": parser.description,
        "h1": parser.h1,
        "canonical_url": parser.canonical_url,
        "robots_directive": parser.robots_directive,
    }


# ── Sitemap ───────────────────────────────────────────────────────────────────

def _parse_sitemap_xml(xml_text: str) -> set[str]:
    """Извлечь URL из sitemap XML (обычный и index-формат). Нормализует trailing slash."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return set()
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: set[str] = set()
    for loc in root.findall(".//sm:loc", ns):
        if loc.text:
            u = loc.text.strip()
            urls.add(u.rstrip("/") or "/")
    return urls


def fetch_sitemap(
    base_url: str,
    session: Any,
    timeout: int = CRAWL_TIMEOUT_SEC,
) -> set[str]:
    """Скачать /sitemap.xml и вернуть множество нормализованных URL. При ошибке — пустое."""
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    try:
        resp = session.get(sitemap_url, timeout=timeout, allow_redirects=True)
        if getattr(resp, "status_code", None) != 200:
            return set()
        return _parse_sitemap_xml(resp.text)
    except Exception:
        return set()


# ── HTTP-обход ────────────────────────────────────────────────────────────────

def _to_absolute(url: str, base_url: str) -> str:
    """Превратить относительный путь в абсолютный URL."""
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    base = base_url.rstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return base + url


def crawl_pages(
    urls: list[str],
    base_url: str,
    sitemap_urls: set[str],
    *,
    session: Any,
    log: Any = None,
    timeout: int = CRAWL_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """Обойти список URL и собрать свойства страниц.

    Возвращает список dict по схеме PAGES_SCHEMA (без поля url_sources).
    Ошибки соединения не роняют пайплайн: http_status=0, мета-поля=None.
    """
    log = log or (lambda _: None)
    pages: list[dict[str, Any]] = []

    for url in urls:
        abs_url = _to_absolute(url, base_url)
        crawled_at = datetime.now(timezone.utc).isoformat()

        record: dict[str, Any] = {
            "url": url,
            "http_status": None,
            "redirect_chain": "[]",
            "final_url": abs_url,
            "canonical_url": None,
            "robots_directive": None,
            "in_sitemap": False,
            "title": None,
            "description": None,
            "h1": None,
            "crawled_at": crawled_at,
        }

        try:
            resp = session.get(abs_url, timeout=timeout, allow_redirects=True)
            status = getattr(resp, "status_code", None)
            record["http_status"] = status

            # Цепочка редиректов: промежуточные URL из history (без финального).
            history = getattr(resp, "history", []) or []
            chain = [getattr(r, "url", "") for r in history]
            record["redirect_chain"] = json.dumps(chain, ensure_ascii=False)

            final_url = getattr(resp, "url", abs_url) or abs_url
            record["final_url"] = final_url

            # Проверка наличия в sitemap по нормализованному final_url.
            norm = (final_url or "").rstrip("/") or "/"
            record["in_sitemap"] = norm in sitemap_urls

            # Парсинг HTML: для 2xx ответов.
            if status is not None and 200 <= status < 300:
                text = getattr(resp, "text", "") or ""
                if text:
                    meta = _parse_page_meta(text)
                    record.update(meta)

        except Exception as exc:
            log(f"{SOURCE}: ошибка при обходе {url}: {type(exc).__name__}")
            record["http_status"] = 0

        log(f"{SOURCE}: {url} → {record['http_status']}")
        pages.append(record)

    return pages


def write_pages_parquet(pages: list[dict[str, Any]], out_dir: Path) -> Path:
    """Записать список страниц в pages.parquet по схеме PAGES_SCHEMA."""
    import pandas as pd

    out = Path(out_dir) / "pages.parquet"
    df = pd.DataFrame(pages, columns=PAGES_SCHEMA)
    df["http_status"] = pd.to_numeric(df["http_status"], errors="coerce").astype("Int64")
    df["in_sitemap"] = df["in_sitemap"].astype(bool)
    df.to_parquet(out, index=False)
    return out


# ── resolve_base_url ──────────────────────────────────────────────────────────

def _resolve_base_url(config: dict[str, Any]) -> str | None:
    """Определить базовый URL сайта для HTTP-обхода.

    Приоритет: config.crawl.base_url → config.sources.webmaster.host_id.
    host_id формата «https:example.com:443» → «https://example.com».
    """
    crawl_cfg = config.get("crawl") or {}
    base = crawl_cfg.get("base_url")
    if base:
        return str(base).rstrip("/")

    wm = ((config.get("sources") or {}).get("webmaster") or {})
    host_id = wm.get("host_id")
    if host_id and isinstance(host_id, str):
        parts = host_id.split(":")
        if len(parts) >= 2:
            scheme = parts[0].lower()
            domain = parts[1].lstrip("/")
            if scheme in ("http", "https") and domain:
                return f"{scheme}://{domain}"

    return None


# ── Публичный API ─────────────────────────────────────────────────────────────

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
            "caveat":          str | None, # заполнен при усечении
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
    """Записать очередь URL и выполнить HTTP-обход страниц.

    Если config.crawl.base_url (или webmaster.host_id) не задан — HTTP-обход
    пропускается, pages.parquet не создаётся (штатная деградация).
    """
    from . import _common as C

    log = log or (lambda _msg: None)

    canonical_dir: Path | None = None
    try:
        canonical_dir = Path(paths.canonical) if paths.canonical else None
    except AttributeError:
        pass

    result = build_url_priority_list(config, canonical_dir)
    log(
        f"{SOURCE}: кандидатов {result['total_candidates']}, "
        f"итого в очереди {len(result['urls'])}"
        + (f" (усечено, кавет: {result['caveat']!r})" if result["truncated"] else "")
    )

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    _dump(out_dir / "url_queue.json", result)

    # HTTP-обход (3.5B) — пропускается, если base_url неизвестен.
    pages: list[dict[str, Any]] = []
    base_url = _resolve_base_url(config)
    if base_url and result["urls"]:
        try:
            import requests as _requests

            session = _requests.Session()
            session.headers["User-Agent"] = "marketing-diagnostics-crawler/1.0"
            sitemap_urls = fetch_sitemap(base_url, session)
            log(f"{SOURCE}: sitemap содержит {len(sitemap_urls)} URL")
            pages = crawl_pages(
                result["urls"],
                base_url,
                sitemap_urls,
                session=session,
                log=log,
            )
            if pages:
                write_pages_parquet(pages, out_dir)
                log(f"{SOURCE}: pages.parquet записан, {len(pages)} страниц")
        except Exception as exc:
            log(f"{SOURCE}: HTTP-обход пропущен: {exc}")
    else:
        log(f"{SOURCE}: base_url не задан — HTTP-обход пропущен")

    manifest = _record_manifest(paths, result, crawled=len(pages))
    log(f"{SOURCE}: url_queue.json записан, {len(result['urls'])} URL")

    return {
        "source": SOURCE,
        "rows": len(result["urls"]),
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "pages_crawled": len(pages),
        **result,
    }


# ── Вспомогательные функции ───────────────────────────────────────────────────

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


def _record_manifest(
    paths: Any,
    result: dict[str, Any],
    crawled: int = 0,
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    extra: dict[str, Any] = {
        "source_mode": "deterministic",
        "total_candidates": result["total_candidates"],
        "urls_queued": len(result["urls"]),
        "truncated": result["truncated"],
        "pages_crawled": crawled,
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
