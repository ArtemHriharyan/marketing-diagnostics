"""Экстрактор: Яндекс.Вебмастер API v4 (поисковые запросы, позиции, CTR).

Контракт:
    Читает   — config.sources.webmaster (host_id), WEBMASTER_TOKEN, окно дат.
    Пишет    — data/raw/webmaster/ (популярные запросы + история показов/кликов,
               raw JSON) + manifest.json (canonical_tables: [seo_queries]).
    Деградация — опционален; вместе с GSC формирует seo_queries. Без обоих
                 проверки блока 5 с requires=[seo_queries] уходят в degradation.
    LLM      — не используется.

Что выгружаем:
    1. search_queries_popular.json — популярные запросы за окно: показы, клики,
       средняя позиция показа/клика (индикаторы TOTAL_SHOWS / TOTAL_CLICKS /
       AVG_SHOW_POSITION / AVG_CLICK_POSITION), постранично.
    2. search_queries_history.json — временной ряд суммарных показов и кликов
       (all/history). Тянем максимально длинное окно, какое отдаёт API.

ЧЕСТНОСТЬ ПРО ГЛУБИНУ ИСТОРИИ:
    API Вебмастера отдаёт историю мельче, чем веб-интерфейс. Если API вернул
    ряд короче запрошенного окна (усечён слева), это НЕ ошибка — фиксируем
    ограничение честно в manifest.notes, чтобы аналитик не принял усечённые
    данные за реальный старт трафика.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.2.0"
SOURCE = "webmaster"
CANONICAL_TABLES = ["seo_queries"]

API_BASE = "https://api.webmaster.yandex.net/v4"

# Индикаторы популярных запросов (показы, клики, средние позиции).
QUERY_INDICATORS = [
    "TOTAL_SHOWS",
    "TOTAL_CLICKS",
    "AVG_SHOW_POSITION",
    "AVG_CLICK_POSITION",
]
# Индикаторы временного ряда (all/history).
HISTORY_INDICATORS = ["TOTAL_SHOWS", "TOTAL_CLICKS"]

# Постраничная выгрузка популярных запросов.
POPULAR_PAGE_LIMIT = 100
POPULAR_MAX_PAGES = 500  # страховка от бесконечного цикла


def _auth_headers(token: str) -> dict[str, str]:
    """Заголовок авторизации Вебмастера. Токен нигде не логируется."""
    return {"Authorization": f"OAuth {token}"}


def _host_segment(host_id: str) -> str:
    """host_id как сегмент пути (Вебмастер отдаёт id вида 'https:site.ru:443')."""
    from urllib.parse import quote

    return quote(str(host_id), safe="")


def _wm_ok(resp: Any, context: str) -> Any:
    """Проверка ответа Вебмастера с вытаскиванием человекочитаемой ошибки API.

    401/403 -> AuthError. Иначе на 4xx/5xx достаём error_code/error_message из
    тела (напр. HOST_NOT_VERIFIED — хост не подтверждён в кабинете) и отдаём их
    аналитику как есть, а не голый «HTTP 404».
    """
    status = getattr(resp, "status_code", None)
    if status in C.AUTH_STATUSES:
        raise C.AuthError(SOURCE, C.auth_dead_message(SOURCE))
    if status is None or status >= 400:
        detail = ""
        try:
            body = resp.json() or {}
            detail = body.get("error_message") or body.get("error_code") or ""
        except Exception:
            detail = ""
        msg = f"{context}: HTTP {status}"
        if detail:
            msg += f" — {detail}"
        raise C.SourceUnavailable(SOURCE, msg)
    return resp


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости WEBMASTER_TOKEN (запрос /user)."""
    import requests

    webmaster = (config.get("sources") or {}).get("webmaster") or {}
    if not webmaster.get("host_id"):
        return False
    try:
        token = C.get_token(env, "WEBMASTER_TOKEN", SOURCE)
    except C.AuthError:
        return False

    session = requests.Session()
    try:
        resp = C.http_request(
            session, "GET", f"{API_BASE}/user",
            source=SOURCE, headers=_auth_headers(token), timeout=30,
        )
        return getattr(resp, "status_code", 500) < 400
    except C.SourceUnavailable:
        return False


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
    """Выгрузить популярные запросы и историю в data/raw/webmaster/."""
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    webmaster = (config.get("sources") or {}).get("webmaster") or {}
    host_id = webmaster.get("host_id")
    if not host_id:
        raise C.SourceUnavailable(SOURCE, "не задан sources.webmaster.host_id в config.yaml")

    token = C.get_token(env, "WEBMASTER_TOKEN", SOURCE)
    headers = _auth_headers(token)

    date_from, date_to = C.resolve_window(config, defaults, today=today)
    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))

    user_id = _fetch_user_id(session, headers)
    host_base = f"{API_BASE}/user/{user_id}/hosts/{_host_segment(host_id)}"
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)}, host {host_id}")

    # 1. Популярные запросы за окно (постранично).
    popular = _fetch_popular(session, host_base, headers, date_from, date_to)
    _dump(out_dir / "search_queries_popular.json", popular)
    log(f"{SOURCE}: популярных запросов — {len(popular)}")

    # 2. Временной ряд суммарных показов/кликов (максимум истории).
    history = _fetch_history(session, host_base, headers, date_from, date_to)
    _dump(out_dir / "search_queries_history.json", history)

    # Честная отметка об ограничении глубины истории.
    notes = _history_notes(history, date_from)
    manifest = _record_manifest(paths, date_from, date_to, len(popular), notes)
    if notes:
        log(f"{SOURCE}: ограничение истории — {notes[0]}")
    log(f"{SOURCE}: готово — {len(popular)} запросов, история из {len(history.get('indicators', {}))} рядов")

    return {
        "source": SOURCE,
        "rows": len(popular),
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "notes": notes,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Шаги Webmaster API ──────────────────────────────────────────────────────
def _fetch_user_id(session, headers) -> Any:
    """user_id владельца токена (нужен во всех остальных путях API)."""
    resp = C.http_request(
        session, "GET", f"{API_BASE}/user",
        source=SOURCE, headers=headers, timeout=30,
    )
    _wm_ok(resp, "user")
    user_id = resp.json().get("user_id")
    if user_id is None:
        raise C.SourceUnavailable(SOURCE, "Webmaster API не вернул user_id")
    return user_id


def _fetch_popular(session, host_base, headers, date_from, date_to) -> list[dict[str, Any]]:
    """Популярные запросы за окно, постранично (offset/limit)."""
    queries: list[dict[str, Any]] = []
    offset = 0
    for _page in range(POPULAR_MAX_PAGES):
        params = [
            ("order_by", "TOTAL_SHOWS"),
            ("date_from", C.fmt(date_from)),
            ("date_to", C.fmt(date_to)),
            ("offset", offset),
            ("limit", POPULAR_PAGE_LIMIT),
        ]
        params += [("query_indicator", ind) for ind in QUERY_INDICATORS]
        resp = C.http_request(
            session, "GET", f"{host_base}/search-queries/popular",
            source=SOURCE, headers=headers, params=params, timeout=60,
        )
        _wm_ok(resp, "search-queries/popular")
        batch = resp.json().get("queries") or []
        queries.extend(batch)
        if len(batch) < POPULAR_PAGE_LIMIT:
            break
        offset += POPULAR_PAGE_LIMIT
    return queries


def _fetch_history(session, host_base, headers, date_from, date_to) -> dict[str, Any]:
    """Временной ряд суммарных показов/кликов по всем запросам (all/history)."""
    params = [
        ("date_from", C.fmt(date_from)),
        ("date_to", C.fmt(date_to)),
    ]
    params += [("query_indicator", ind) for ind in HISTORY_INDICATORS]
    resp = C.http_request(
        session, "GET", f"{host_base}/search-queries/all/history",
        source=SOURCE, headers=headers, params=params, timeout=60,
    )
    _wm_ok(resp, "search-queries/all/history")
    return resp.json() or {}


def _history_notes(history: dict[str, Any], requested_from) -> list[str]:
    """Сформировать честные заметки об ограничении глубины истории.

    Вебмастер API отдаёт историю мельче веб-интерфейса. Если самый ранний
    отсчёт ряда позже запрошенного date_from — ряд усечён слева; фиксируем это.
    """
    notes = [
        "Webmaster API отдаёт историю мельче веб-интерфейса; проверьте глубину "
        "в интерфейсе, если нужен более ранний период."
    ]
    earliest = _earliest_history_date(history)
    if earliest and earliest > C.fmt(requested_from):
        notes.append(
            f"история усечена слева: запрошено с {C.fmt(requested_from)}, "
            f"API отдал с {earliest} — ограничение API, не старт трафика."
        )
    return notes


def _earliest_history_date(history: dict[str, Any]) -> str | None:
    """Самая ранняя дата среди всех рядов indicators (или None)."""
    indicators = history.get("indicators") or {}
    dates: list[str] = []
    for series in indicators.values():
        for point in series or []:
            date_val = point.get("date")
            if date_val:
                dates.append(str(date_val)[:10])
    return min(dates) if dates else None


def _dump(path: Path, obj: Any) -> None:
    import json

    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _record_manifest(paths, date_from, date_to, rows, notes) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={"notes": notes},
    )
