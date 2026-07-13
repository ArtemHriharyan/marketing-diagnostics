"""Экстрактор: Chrome UX Report API (полевые Core Web Vitals).

Контракт:
    Читает   — config.sources.crux (api_key_env, origin, key_urls) и ключ CrUX из
               .env (по имени api_key_env, по умолчанию CRUX_API_KEY). Окно дат не
               используется: CrUX отдаёт свой скользящий 28-дневный период.
    Пишет    — data/raw/crux/crux.json (запись по origin + по каждому проверенному
               URL) + manifest.json. Канонической таблицы НЕ даёт (compute читает
               crux.json напрямую) -> canonical_tables: [].
    Деградация — опционален; CWV-находки (C01/C02/S20) при отсутствии данных
                 опираются только на лабораторный замер (см. ниже).
    LLM      — не используется.

ШТАТНЫЙ (не аварийный) случай — данных нет:
    CrUX возвращает 404 для origin/URL, у которых недостаточно трафика Chrome для
    попадания в датасет. Это СВОЙСТВО САЙТА, а не ошибка запроса — НЕ ретраим и НЕ
    роняем стадию. В manifest пишем cwv_field_data_available: false (per origin и
    per URL). Если данных нет во всём наборе — C01/C02/S20 в compute обязаны
    опираться ТОЛЬКО на лабораторный замер (ручной, inputs/manual_cwv.yaml — вне
    этого пайплайна) с потолком confidence = MED (defaults.crux_min_field_data).

Механика запросов (экономно — у CrUX жёсткие лимиты):
    1. Запрос на уровне origin.
    2. Если у origin данных нет — точечные запросы по URL НЕ делаем (почти
       наверняка тоже пусто). Если есть — проверяем до MAX_KEY_URLS ключевых
       посадочных URL из config.sources.crux.key_urls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.1.0"
SOURCE = "crux"
CANONICAL_TABLES: list[str] = []

API_URL = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
DEFAULT_API_KEY_ENV = "CRUX_API_KEY"

# Точечно, а не веерно: не больше пяти ключевых посадочных URL.
MAX_KEY_URLS = 5

# p75 этих метрик забираем в компактную сводку (полная запись тоже сохраняется).
CWV_METRICS = [
    "largest_contentful_paint",
    "cumulative_layout_shift",
    "interaction_to_next_paint",
    "first_contentful_paint",
]

# HTTP 404 у CrUX = «нет полевых данных» (штатно), а не сбой.
NO_DATA_STATUS = 404


def _api_key_env(crux_cfg: dict[str, Any]) -> str:
    return str(crux_cfg.get("api_key_env") or DEFAULT_API_KEY_ENV)


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка: задан ли origin и есть ли ключ CrUX в .env."""
    crux = (config.get("sources") or {}).get("crux") or {}
    if not _resolve_origin(config):
        return False
    return bool((env or {}).get(_api_key_env(crux)))


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    session: Any = None,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Выгрузить полевые CWV из CrUX в data/raw/crux/ (штатно переживает «нет данных»)."""
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    crux = (config.get("sources") or {}).get("crux") or {}
    origin = _resolve_origin(config)
    if not origin:
        raise C.SourceUnavailable(
            SOURCE, "не задан sources.crux.origin (и нет sources.gsc.site_url) в config.yaml"
        )

    key_env = _api_key_env(crux)
    api_key = (env or {}).get(key_env)
    if not api_key:
        raise C.SourceUnavailable(
            SOURCE, f"нет {key_env} в .env — CrUX недоступен"
        )

    key_urls = _key_urls(crux)
    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    query_url = f"{API_URL}?key={api_key}"
    log(f"{SOURCE}: origin {origin}, ключевых URL к проверке {len(key_urls)}")

    results: list[dict[str, Any]] = []

    # 1. Уровень origin.
    origin_available, origin_record = _query_record(session, query_url, {"origin": origin})
    results.append(_result_entry("origin", origin, origin_available, origin_record))
    log(f"{SOURCE}: origin — данные {'есть' if origin_available else 'нет (404, штатно)'}")

    # 2. URL-уровень только если у origin данные есть (иначе почти наверняка пусто).
    if origin_available and key_urls:
        for url in key_urls:
            available, record = _query_record(session, query_url, {"url": url})
            results.append(_result_entry("url", url, available, record))
            log(f"{SOURCE}: url {url} — данные {'есть' if available else 'нет'}")
    elif key_urls:
        log(f"{SOURCE}: у origin данных нет — точечные запросы по {len(key_urls)} URL пропущены")

    any_available = any(r["field_data_available"] for r in results)
    _dump(out_dir / "crux.json", {
        "origin": origin,
        "cwv_field_data_available": any_available,
        "queried_at": None,
        "records": results,
    })

    manifest = _record_manifest(paths, origin, results, any_available)
    if not any_available:
        log(f"{SOURCE}: полевых данных нет — C01/C02/S20 опираются только на "
            "лабораторный замер (inputs/manual_cwv.yaml), confidence capped MED")
    log(f"{SOURCE}: готово — cwv_field_data_available={any_available}, "
        f"записей {len(results)}")

    return {
        "source": SOURCE,
        "rows": sum(1 for r in results if r["field_data_available"]),
        "cwv_field_data_available": any_available,
        "records": results,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Запрос к CrUX ───────────────────────────────────────────────────────────
def _query_record(session, url, body: dict[str, str]) -> tuple[bool, dict[str, Any] | None]:
    """Один запрос records:queryRecord. 404 -> (False, None) БЕЗ ретрая (штатно).

    Прочие 4xx/5xx проходят обычную обработку (_common): 429/5xx ретраятся,
    401/403 -> AuthError (кривой ключ), иначе SourceUnavailable через ensure_ok.
    """
    resp = C.http_request(
        session, "POST", url,
        source=SOURCE,
        headers={"Content-Type": "application/json"},
        json=body, timeout=60,
    )
    status = getattr(resp, "status_code", None)
    if status == NO_DATA_STATUS:
        return False, None
    C.ensure_ok(resp, SOURCE, "records:queryRecord")
    record = (resp.json() or {}).get("record")
    return (record is not None), record


def _result_entry(target_type: str, target: str, available: bool,
                  record: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "target_type": target_type,           # origin | url
        "target": target,
        "field_data_available": available,
        "p75": _p75(record) if available else {},
        "record": record,
    }


def _p75(record: dict[str, Any] | None) -> dict[str, Any]:
    """Компактная сводка p75 по ключевым CWV-метрикам (для удобства compute)."""
    metrics = (record or {}).get("metrics") or {}
    out: dict[str, Any] = {}
    for name in CWV_METRICS:
        block = metrics.get(name) or {}
        p75 = (block.get("percentiles") or {}).get("p75")
        if p75 is not None:
            out[name] = p75
    return out


# ── Конфиг: origin и ключевые URL ───────────────────────────────────────────
def _resolve_origin(config: dict[str, Any]) -> str | None:
    """origin из sources.crux.origin; запасной вариант — sources.gsc.site_url."""
    sources = config.get("sources") or {}
    crux = sources.get("crux") or {}
    origin = (crux.get("origin") or "").strip() if crux.get("origin") else ""
    if origin:
        return origin.rstrip("/")
    site_url = ((sources.get("gsc") or {}).get("site_url") or "").strip()
    return site_url.rstrip("/") or None


def _key_urls(crux_cfg: dict[str, Any]) -> list[str]:
    """До MAX_KEY_URLS ключевых посадочных URL из config (мусор/пусто отбрасываем)."""
    urls = [str(u).strip() for u in (crux_cfg.get("key_urls") or []) if str(u).strip()]
    return urls[:MAX_KEY_URLS]


# ── Манифест и дамп ─────────────────────────────────────────────────────────
def _record_manifest(paths, origin, results, any_available) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    per_target = {r["target"]: r["field_data_available"] for r in results}
    notes: list[str] = []
    if not any_available:
        notes.append(
            "CrUX не отдал полевых данных ни для origin, ни для URL — сайт ниже "
            "порога охвата Chrome (штатный случай, не сбой). CWV-находки "
            "(C01/C02/S20) опираются только на лабораторный замер и capped MED "
            "(defaults.crux_min_field_data)."
        )

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from="", date_to="",
        rows=sum(1 for r in results if r["field_data_available"]),
        script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "source_mode": "api",
            "origin": origin,
            "cwv_field_data_available": any_available,
            "field_data_available_by_target": per_target,
            "notes": notes,
        },
    )


def _dump(path: Path, obj: Any) -> None:
    import json

    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
