"""Экстрактор: Google Search Console API (searchanalytics.query).

Активен, когда config.sources.gsc.mode == "api" (при появлении реального доступа:
сервисный ключ в credentials_path / GSC_CREDENTIALS_PATH). Сейчас основной путь —
ручной (gsc_manual.py, mode: manual). Переключение режима mode: manual <-> api
не должно требовать правок ничего дальше по пайплайну: выходной контракт
data/raw/gsc/gsc_YYYY-MM.{csv,parquet} у обоих экстракторов ОДИНАКОВ.

Контракт:
    Читает   — config.sources.gsc (site_url, опц. raw_format), путь к сервисному
               ключу GSC_CREDENTIALS_PATH из .env, окно дат.
    Пишет    — data/raw/gsc/ (по одному файлу на месяц: срез
               (query, page, device) x (clicks, impressions, ctr, position),
               parquet или csv) + manifest.json (canonical_tables: [seo_queries],
               source_mode: api, completeness: verified).
    Деградация — опционален; дополняет seo_queries стороной Google (проверка 5.4).
    LLM      — не используется.

Авторизация (принцип 6):
    Сервисный аккаунт Google. Путь к JSON-ключу лежит в .env
    (GSC_CREDENTIALS_PATH), сам ключ в код/логи не попадает. Access-token
    минтуется через google-auth (ленивый импорт) и уходит только в заголовок
    Authorization. В юнит-тестах токен и session инъектируются напрямую.

Механика Search Analytics API:
    POST .../sites/{siteUrl}/searchAnalytics/query с телом
    {startDate, endDate, dimensions, rowLimit, startRow}. Пагинация: тянем
    страницами по ROW_LIMIT строк, увеличивая startRow, пока страница полная.
    Большое окно режем на календарные месяцы (помесячная разбивка сырья).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.2.0"
SOURCE = "gsc"
CANONICAL_TABLES = ["seo_queries"]

# Read-only доступ к Search Console.
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API_BASE = "https://searchconsole.googleapis.com/webmasters/v3/sites"

# Максимум строк на страницу (лимит API). Пагинация — по startRow.
ROW_LIMIT = 25000
DIMENSIONS = ["query", "page", "device"]

# Порядок колонок сырья фиксирован — на него опирается transform (тот же, что у
# gsc_manual.py).
RAW_FIELDS = ["month", "query", "page", "device",
              "clicks", "impressions", "ctr", "position"]


def _auth_headers(access_token: str) -> dict[str, str]:
    """Заголовок авторизации Google. Токен нигде не логируется."""
    return {"Authorization": f"Bearer {access_token}"}


def _site_url_path(site_url: str) -> str:
    """siteUrl как сегмент пути (полностью URL-кодированный, включая '/')."""
    from urllib.parse import quote

    return quote(site_url, safe="")


def _mint_access_token(env: dict[str, str]) -> str:
    """Получить access-token сервисного аккаунта по GSC_CREDENTIALS_PATH.

    Ключ читается из файла, путь к которому лежит в .env; сам ключ не логируется.
    Отсутствие пути/файла или отсутствие google-auth -> источник недоступен
    (штатная деградация, принцип 4), а не крэш пайплайна.
    """
    creds_path = (env or {}).get("GSC_CREDENTIALS_PATH")
    if not creds_path:
        raise C.AuthError(
            SOURCE,
            "нет GSC_CREDENTIALS_PATH в .env — "
            + C.auth_dead_message(SOURCE),
        )
    if not Path(creds_path).exists():
        raise C.AuthError(
            SOURCE, f"файл сервисного ключа не найден: {creds_path}"
        )
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise C.SourceUnavailable(
            SOURCE,
            "не установлен google-auth (pip install google-auth) — "
            "GSC недоступен",
        ) from exc

    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=[GSC_SCOPE]
        )
        creds.refresh(Request())
    except Exception as exc:  # битый ключ/сеть — детали ключа не светим
        raise C.AuthError(
            SOURCE, f"не удалось авторизоваться в GSC: {type(exc).__name__}"
        ) from exc
    return creds.token


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка: есть site_url и минтится ли токен сервисного аккаунта."""
    gsc = (config.get("sources") or {}).get("gsc") or {}
    if not gsc.get("site_url"):
        return False
    try:
        _mint_access_token(env)
        return True
    except C.SourceUnavailable:
        return False


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    session: Any = None,
    access_token: str | None = None,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Выгрузить поисковую аналитику Google помесячно в data/raw/gsc/.

    ``access_token`` — если передан, минт токена пропускается (используется в
    тестах). Иначе токен берётся у сервисного аккаунта по GSC_CREDENTIALS_PATH.
    """
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    gsc = (config.get("sources") or {}).get("gsc") or {}
    site_url = gsc.get("site_url")
    if not site_url:
        raise C.SourceUnavailable(SOURCE, "не задан sources.gsc.site_url в config.yaml")

    token = access_token or _mint_access_token(env)
    headers = _auth_headers(token)
    fmt = C.resolve_raw_format(gsc)

    date_from, date_to = C.resolve_window(config, defaults, today=today)
    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    query_url = f"{API_BASE}/{_site_url_path(site_url)}/searchAnalytics/query"
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)}, сайт {site_url}, формат {fmt}")

    total_rows = 0
    files_written = 0
    for chunk_from, chunk_to in C.month_chunks(date_from, date_to):
        rows = _query_window(session, query_url, headers, chunk_from, chunk_to, sleeper)
        records = _to_records(rows, C.fmt(chunk_from))
        out = C.write_table(out_dir / f"gsc_{C.fmt(chunk_from)}", records, RAW_FIELDS, fmt)
        total_rows += len(records)
        files_written += 1
        log(f"{SOURCE}: месяц {C.fmt(chunk_from)} — {len(records)} строк -> {out.name}")

    manifest = _record_manifest(paths, date_from, date_to, total_rows, fmt)
    log(f"{SOURCE}: готово — {files_written} файлов, {total_rows} строк")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "files": files_written,
        "raw_format": fmt,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "source_mode": "api",
        "completeness": "verified",
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Search Analytics API ────────────────────────────────────────────────────
def _query_window(session, url, headers, date_from, date_to, sleeper) -> list[dict[str, Any]]:
    """Все строки (query, page, device) за месяц с пагинацией по startRow."""
    all_rows: list[dict[str, Any]] = []
    start_row = 0
    while True:
        body = {
            "startDate": C.fmt(date_from),
            "endDate": C.fmt(date_to),
            "dimensions": DIMENSIONS,
            "rowLimit": ROW_LIMIT,
            "startRow": start_row,
        }
        resp = C.http_request(
            session, "POST", url,
            source=SOURCE, headers=headers, json=body, timeout=120,
        )
        C.ensure_ok(resp, SOURCE, "searchanalytics.query")
        batch = resp.json().get("rows") or []
        all_rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
        sleeper(0)  # точка вежливой паузы между страницами
    return all_rows


def _to_records(rows: list[dict[str, Any]], month: str) -> list[dict[str, Any]]:
    """Разложить keys[query, page, device] в плоские записи с метриками."""
    records: list[dict[str, Any]] = []
    for row in rows:
        keys = row.get("keys") or []
        query, page, device = (list(keys) + ["", "", ""])[:3]
        records.append({
            "month": month,
            "query": query,
            "page": page,
            "device": device,
            "clicks": row.get("clicks"),
            "impressions": row.get("impressions"),
            "ctr": row.get("ctr"),
            "position": row.get("position"),
        })
    return records


def _record_manifest(paths, date_from, date_to, rows, fmt) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={"engine": "google", "raw_format": fmt,
               "source_mode": "api", "completeness": "verified"},
    )
