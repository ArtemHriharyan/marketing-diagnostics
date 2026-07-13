"""Экстрактор: Яндекс Wordstat (частотность спроса по маскам запросов).

Контракт:
    Читает   — config.wordstat_seeds (маски запросов), config.wordstat_geo
               (GeoID регионов; пусто = вся Россия), WORDSTAT_TOKEN.
    Пишет    — data/raw/wordstat/ (частотности по маскам и связанным запросам,
               raw JSON) + manifest.json (canonical_tables: [wordstat]).
    Деградация — опционален; без него проверка 5.5 (спрос) уходит в degradation.
    LLM      — не используется.

Механика (Wordstat Reports API, асинхронный):
    Wordstat работает через очередь отчётов Яндекс.Директа v4:
        1. CreateNewWordstatReport(Phrases, GeoID) -> ReportID;
        2. GetWordstatReportList — поллинг статуса до "Done";
        3. GetWordstatReport(ReportID) — забрать результат;
        4. DeleteWordstatReport(ReportID) — освободить слот очереди.
    Маски бьём на батчи по PHRASES_PER_REPORT фраз (лимит одного отчёта).

RATE LIMITS (жёсткие):
    Лимит одновременных отчётов и частоты вызовов у Wordstat строгий, поэтому
    обрабатываем очередь строго последовательно (один отчёт в полёте) и делаем
    паузу PAUSE_SEC между КАЖДЫМ вызовом API. Токен нигде не логируется.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.2.0"
SOURCE = "wordstat"
CANONICAL_TABLES = ["wordstat"]

# Очередь отчётов Wordstat живёт в legacy v4 API Директа.
API_URL = "https://api.direct.yandex.ru/v4/json/"

PHRASES_PER_REPORT = 10        # лимит фраз на один отчёт Wordstat
PAUSE_SEC = 3.0               # пауза между любыми вызовами (жёсткие rate limits)
POLL_MAX_ATTEMPTS = 60        # опросов статуса на один отчёт
POLL_INTERVAL_SEC = 10.0      # пауза между опросами статуса

# Код legacy v4, означающий мёртвый/невалидный токен -> AuthError.
AUTH_ERROR_CODES = {53}
# Код «превышен лимит отчётов Wordstat в очереди» -> подождать и повторить.
QUEUE_LIMIT_ERROR_CODE = 31
# Код 58 «No access» — приложению не выдан доступ к API Директа (нужна заявка в
# интерфейсе Директа). Не мёртвый токен, а отсутствие права -> источник недоступен.


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости WORDSTAT_TOKEN (пустой GetWordstatReportList)."""
    import requests

    try:
        token = C.get_token(env, "WORDSTAT_TOKEN", SOURCE)
    except C.AuthError:
        return False
    session = requests.Session()
    try:
        _call(session, token, "GetWordstatReportList", sleeper=lambda _s: None)
        return True
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
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Выгрузить частотности по маскам wordstat_seeds в data/raw/wordstat/."""
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    seeds = [s for s in (config.get("wordstat_seeds") or []) if str(s).strip()]
    if not seeds:
        raise C.SourceUnavailable(
            SOURCE, "не задан wordstat_seeds в config.yaml (список масок запросов)"
        )
    geo = _geo_ids(config)

    token = C.get_token(env, "WORDSTAT_TOKEN", SOURCE)
    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    log(f"{SOURCE}: масок {len(seeds)}, гео {geo or 'вся Россия'}")

    results: list[dict[str, Any]] = []
    batches = [seeds[i:i + PHRASES_PER_REPORT] for i in range(0, len(seeds), PHRASES_PER_REPORT)]
    for idx, phrases in enumerate(batches, start=1):
        log(f"{SOURCE}: отчёт {idx}/{len(batches)} — фраз {len(phrases)}")
        report = _run_report(session, token, phrases, geo, sleeper, log)
        results.extend(report)

    _dump(out_dir / "wordstat.json", results)
    rows = len(results)
    manifest = _record_manifest(paths, geo, rows)
    log(f"{SOURCE}: готово — {rows} фраз с частотностью")

    return {
        "source": SOURCE,
        "rows": rows,
        "geo": geo,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


def _geo_ids(config: dict[str, Any]) -> list[int]:
    """GeoID регионов из config.wordstat_geo (числа; мусор отбрасываем)."""
    geo: list[int] = []
    for value in config.get("wordstat_geo") or []:
        try:
            geo.append(int(value))
        except (TypeError, ValueError):
            continue
    return geo


# ── Очередь отчётов Wordstat (строго последовательно, с паузами) ────────────
def _run_report(session, token, phrases, geo, sleeper, log) -> list[dict[str, Any]]:
    """Полный цикл одного отчёта: create -> poll -> get -> delete."""
    report_id = _create_report(session, token, phrases, geo, sleeper, log)
    _poll_until_done(session, token, report_id, sleeper, log)
    data = _get_report(session, token, report_id, sleeper)
    _delete_report(session, token, report_id, sleeper)
    return data or []


def _create_report(session, token, phrases, geo, sleeper, log) -> Any:
    """CreateNewWordstatReport -> ReportID. При переполнении очереди ждём и ретраим."""
    param: dict[str, Any] = {"Phrases": list(phrases)}
    if geo:
        param["GeoID"] = list(geo)
    for _attempt in range(POLL_MAX_ATTEMPTS):
        try:
            return _call(session, token, "CreateNewWordstatReport",
                         param=param, sleeper=sleeper)
        except _QueueFull:
            log(f"{SOURCE}: очередь Wordstat заполнена — пауза и повтор")
            sleeper(POLL_INTERVAL_SEC)
    raise C.SourceUnavailable(
        SOURCE, f"очередь Wordstat не освободилась за {POLL_MAX_ATTEMPTS} попыток"
    )


def _poll_until_done(session, token, report_id, sleeper, log) -> None:
    """Опрашивать GetWordstatReportList, пока нужный отчёт не станет 'Done'."""
    for _attempt in range(POLL_MAX_ATTEMPTS):
        reports = _call(session, token, "GetWordstatReportList", sleeper=sleeper) or []
        status = next(
            (r.get("StatusReport") for r in reports if r.get("ReportID") == report_id),
            None,
        )
        if status == "Done":
            return
        if status == "Failed":
            raise C.SourceUnavailable(
                SOURCE, f"Wordstat отклонил отчёт {report_id} (StatusReport=Failed)"
            )
        sleeper(POLL_INTERVAL_SEC)
    raise C.SourceUnavailable(
        SOURCE, f"Wordstat не подготовил отчёт {report_id} за {POLL_MAX_ATTEMPTS} опросов"
    )


def _get_report(session, token, report_id, sleeper) -> list[dict[str, Any]]:
    """GetWordstatReport(ReportID) -> список результатов по фразам."""
    return _call(session, token, "GetWordstatReport", param=report_id, sleeper=sleeper)


def _delete_report(session, token, report_id, sleeper) -> None:
    """DeleteWordstatReport(ReportID) — освобождаем слот очереди (ошибку глотаем)."""
    try:
        _call(session, token, "DeleteWordstatReport", param=report_id, sleeper=sleeper)
    except C.SourceUnavailable:
        pass  # не смогли удалить — не критично для выгрузки


class _QueueFull(RuntimeError):
    """Внутренний сигнал: очередь отчётов Wordstat переполнена (error_code 31)."""


def _call(session, token, method, *, param: Any = None,
          sleeper: Callable[[float], None] = time.sleep) -> Any:
    """Один вызов legacy v4 API + пауза (rate limits). Возвращает поле data.

    Тело содержит token (в код/логи не попадает). Ошибки уровня API приходят в
    теле как error_code/error_str: 53/58 -> AuthError, 31 -> очередь переполнена.
    """
    import json

    body: dict[str, Any] = {"method": method, "token": token}
    if param is not None:
        body["param"] = param
    # КВИРК legacy v4: тело обязано быть в UTF-8 БЕЗ ascii-эскейпинга. requests
    # с json= сериализует через ensure_ascii=True (кириллица уходит как \uXXXX) и
    # сервер отвечает 501 «Request encoding is not UTF8». Поэтому кодируем сами.
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    resp = C.http_request(
        session, "POST", API_URL,
        source=SOURCE,
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=payload, timeout=120,
    )
    C.ensure_ok(resp, SOURCE, method)
    payload = resp.json() or {}

    error_code = payload.get("error_code")
    if error_code is not None:
        code = int(error_code)
        if code in AUTH_ERROR_CODES:
            raise C.AuthError(SOURCE, C.auth_dead_message(SOURCE))
        if code == QUEUE_LIMIT_ERROR_CODE:
            raise _QueueFull(str(payload.get("error_str") or "queue full"))
        # Прочие ошибки API (включая 58 «No access») — детально, чтобы аналитик
        # понял причину: напр. приложению не выдан доступ к API Директа.
        detail = " ".join(filter(None, [
            str(payload.get("error_str") or ""),
            str(payload.get("error_detail") or ""),
        ])).strip()
        raise C.SourceUnavailable(
            SOURCE, f"{method}: Wordstat error {code}" + (f" — {detail}" if detail else "")
        )

    sleeper(PAUSE_SEC)  # уважение жёстких rate limits между вызовами
    return payload.get("data")


def _dump(path: Path, obj: Any) -> None:
    import json

    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _record_manifest(paths, geo, rows) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    # Частотность Wordstat — снимок «сейчас», а не окно дат: date_from/to пустые.
    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from="", date_to="",
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={"geo": geo},
    )
