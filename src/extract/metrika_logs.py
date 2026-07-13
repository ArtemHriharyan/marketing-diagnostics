"""Экстрактор: Яндекс.Метрика Logs API (визиты).

Контракт:
    Читает   — config.sources.metrika (counter_id), токен METRIKA_TOKEN из .env,
               окно дат (config.data_window / defaults.data_window_months).
    Пишет    — data/raw/metrika_logs/ (сырые визиты, csv.gz как отдал API,
               по одному файлу на часть Logs API) + обновляет manifest.json
               (canonical_tables: [visits]).
    Деградация — источник опционален; при недоступности визиты отсутствуют,
                 проверки с requires=[visits] уходят в degradation_report.
    LLM      — не используется.

Механика Logs API (асинхронный):
    1. GET  logrequests/evaluate — согласовать состав полей (валидность + объём),
       БЕЗ создания джобы (read-only). Так мы определяем реально поддерживаемые
       поля до запуска выгрузки;
    2. POST logrequests   — создать запрос на выгрузку окна принятыми полями;
    3. GET  logrequests/{id} — поллинг статуса до "processed";
    4. GET  .../part/{n}/download — скачать части;
    5. склейка — здесь не делаем: части кладём как есть (raw), склейка/парсинг
       живут в слое transform.
Большие окна делятся на календарные месяцы (по одному logrequest на месяц):
Logs API ограничивает объём одного запроса.

── Патч 0.3.x (расширение полей под каталог угроз v2) ──────────────────────
Добавлены поля визита для T02 (наивная vs last-significant атрибуция), C21
(браузер/ОС/размер экрана) и A12/S26 (гео). Состав полей согласуется с API
рантайм-негоциацией через logrequests/evaluate: неподдерживаемое имя даёт
HTTP 400 «Unknown field ... for the source visits». Негоциация бинарным
делением ИЗОЛИРУЕТ конкретные неподдерживаемые поля (а не выбрасывает весь
пакет) и фиксирует их в manifest.dropped_fields с причиной от API. Каждый
отклонённый набор логируется безопасно (поля + status + code/message/errors),
без токена и заголовка Authorization.

Неизменность слоя raw (принцип 2): если окно уже было выгружено ДО патча
(в манифесте нет patch_date), старые visits_* файлы не трогаются — недостающие
поля довыгружаются отдельным проходом в ПОДКАТАЛОГ metrika_logs/backfill/ и
помечаются manifest.patch_backfill: true. Подкаталог намеренно вне обычного
`*.csv.gz`-глоба верхнего уровня: сверка (scripts/verify_metrika) и transform
читают только базовые visits_* и не спотыкаются о backfill-файлы (в них нет
ym:s:dateTime). Склейка base+backfill по ym:s:visitID — забота слоя transform.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.3.1"
SOURCE = "metrika_logs"
CANONICAL_TABLES = ["visits"]

# Версия схемы полей визита. v2 = базовый набор + поля патча (T02/C21/гео).
# Пишется в manifest.schema_version — по нему видно, какой контракт полей
# у выгрузки, независимо от версии кода.
SCHEMA_VERSION = "visits-v2"

# Дата патча расширения полей. Пишется в manifest.patch_date — граница, по
# которой видно, какие выгрузки уже содержат новые поля.
PATCH_DATE = "2026-07-13"

# База management/logrequests API Метрики.
# ВНИМАНИЕ (квирк Logs API): создание запроса и evaluate — множественное число
# /logrequests, а статус/скачивание/очистка — ЕДИНСТВЕННОЕ /logrequest/{id}.
API_BASE = "https://api-metrika.yandex.net/management/v1/counter"

# Базовый набор полей визита (до патча). Порядок фиксирован — на него опирается
# transform. Все поля базы поддерживаются источником visits.
VISIT_FIELDS_BASE = [
    "ym:s:visitID",
    "ym:s:clientID",
    "ym:s:dateTime",
    "ym:s:lastsignTrafficSource",
    "ym:s:lastsignUTMSource",
    "ym:s:lastsignUTMMedium",
    "ym:s:lastsignUTMCampaign",
    "ym:s:lastSignDirectClickOrder",
    "ym:s:deviceCategory",
    "ym:s:startURL",
    "ym:s:goalsID",
    "ym:s:referer",
    "ym:s:isNewUser",
    "ym:s:pageViews",
    "ym:s:visitDuration",
]

# Поля, добавленные патчем. Валидность каждого проверяется рантайм-негоциацией
# (logrequests/evaluate); принятые попадают в manifest.patch_fields, отклонённые —
# в manifest.dropped_fields с причиной. Имена ниже сверены с боевым API Метрики:
#   ym:s:screenWidth/Height — реальные поля (ym:s:screenResolution НЕ существует);
#   ym:s:isRobot — ПРОБНОЕ: источник visits не отдаёт флаг робота (ни isRobot, ни
#     robotness), поэтому обычно уходит в dropped — D11 по Logs этим полем закрыть
#     нельзя, это фиксируется в манифесте честно, а не молча.
PATCH_ADDED_FIELDS = [
    "ym:s:lastTrafficSource",    # T02: НАИВНАЯ модель атрибуции — ОТДЕЛЬНО от
                                 # ym:s:lastsignTrafficSource (last-significant).
                                 # Нужны ОБЕ: это разные модели атрибуции.
    "ym:s:browser",             # C21: проблема в конкретном браузере
    "ym:s:operatingSystem",     # C21: ... или ОС
    "ym:s:screenWidth",         # C21: ширина экрана (замена screenResolution)
    "ym:s:screenHeight",        # C21: высота экрана
    "ym:s:regionCountry",       # A12 (нецелевая гео) / S26 (гео-спрос)
    "ym:s:regionCity",          # A12 / S26
    "ym:s:isRobotPro",          # D11: расширенный флаг робота (замена isRobot —
                                # отклонён API для источника visits)
]

# GCLID в Logs API: правильный регистр — GCLID (не Gclid).
# Yclid (Яндекс) не нужен отдельно: связка с Директом идёт через
# ym:s:lastSignDirectClickOrder, уже входящий в VISIT_FIELDS_BASE.
PATCH_CLICKID_PROBE = ["ym:s:lastSignGCLID", "ym:s:lastSignhasGCLID"]

# Все новые поля, которые ПЫТАЕМСЯ добавить (негоциация оставит поддерживаемые).
PATCH_CANDIDATE_FIELDS = PATCH_ADDED_FIELDS + PATCH_CLICKID_PROBE

# Полный желаемый состав (порядок: base -> patch -> clickid). Имя VISIT_FIELDS
# сохранено: на него опираются transform (build_canonical) и смоук-тесты. Это
# ЖЕЛАЕМЫЙ набор; фактически принятый API набор — в manifest.available_fields.
VISIT_FIELDS = VISIT_FIELDS_BASE + PATCH_CANDIDATE_FIELDS

# Ключ склейки backfill со старым слоем — обязателен в любом backfill-наборе.
BACKFILL_JOIN_KEY = "ym:s:visitID"
BACKFILL_SUBDIR = "backfill"

# Поллинг готовности logrequest.
POLL_MAX_ATTEMPTS = 60
POLL_INTERVAL_SEC = 10.0
_STATUS_READY = {"processed"}
_STATUS_PENDING = {"created", "processing", "awaiting_retry"}
_STATUS_FAILED = {"processing_failed", "canceled", "cleaned_by_user",
                  "cleaned_automatically_as_too_old"}


def _auth_headers(token: str) -> dict[str, str]:
    """Заголовок авторизации Метрики. Токен нигде не логируется."""
    return {"Authorization": f"OAuth {token}"}


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости METRIKA_TOKEN (запрос счётчика).

    True — токен валиден и счётчик доступен; False — источник недоступен
    (в т.ч. мёртвый токен). Исключения наружу не пробрасываем: intake лишь
    печатает таблицу доступности.
    """
    import requests

    metrika = (config.get("sources") or {}).get("metrika") or {}
    counter_id = metrika.get("counter_id")
    if not counter_id:
        return False
    try:
        token = C.get_token(env, "METRIKA_TOKEN", SOURCE)
    except C.AuthError:
        return False

    session = requests.Session()
    try:
        resp = C.http_request(
            session, "GET", f"{API_BASE}/{counter_id}",
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
    sleeper: Callable[[float], None] = time.sleep,
    backfill: bool | None = None,
) -> dict[str, Any]:
    """Выгрузить визиты окна в data/raw/metrika_logs/ (csv.gz по частям).

    Возвращает метаданные для manifest.json. При мёртвом токене поднимает
    AuthError с внятным сообщением и кодом «источник недоступен».

    ``backfill``:
        None (по умолчанию) — авто: довыгрузка новых полей патча, если окно уже
            было выгружено ДО патча (см. _should_backfill); иначе полная выгрузка.
        True  — принудительная довыгрузка только новых полей в backfill/.
        False — принудительная полная выгрузка (перезапись слоя целиком).
    """
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    metrika = (config.get("sources") or {}).get("metrika") or {}
    counter_id = metrika.get("counter_id")
    if not counter_id:
        raise C.SourceUnavailable(SOURCE, "не задан sources.metrika.counter_id в config.yaml")

    token = C.get_token(env, "METRIKA_TOKEN", SOURCE)
    headers = _auth_headers(token)

    date_from, date_to = C.resolve_window(config, defaults, today=today)
    raw_root = Path(paths.raw)
    src_dir = C.source_dir(paths, SOURCE)

    existing = (manifest_load(raw_root).get("sources") or {}).get(SOURCE)
    do_backfill = _should_backfill(backfill, existing, date_from, date_to, src_dir)

    if do_backfill:
        return _run_backfill(
            session, counter_id, headers, paths, src_dir,
            date_from, date_to, sleeper=sleeper, log=log,
        )
    return _run_full(
        session, counter_id, headers, paths, src_dir,
        date_from, date_to, sleeper=sleeper, log=log,
    )


# ── Полная выгрузка (перезапись слоя целиком) ──────────────────────────────
def _run_full(
    session, counter_id, headers, paths, src_dir,
    date_from, date_to, *, sleeper, log,
) -> dict[str, Any]:
    out_dir = C.reset_dir(src_dir)
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)}, счётчик {counter_id} (полная выгрузка)")

    chunks = C.month_chunks(date_from, date_to)
    accepted, dropped_reasons = _negotiate_fields(
        session, counter_id, headers, chunks, VISIT_FIELDS_BASE, PATCH_CANDIDATE_FIELDS, log,
    )
    fields = VISIT_FIELDS_BASE + accepted

    total_rows = 0
    parts_written = 0
    for chunk_from, chunk_to in chunks:
        request_id = _create_log_request(session, counter_id, headers, chunk_from, chunk_to, fields)
        log(f"{SOURCE}: чанк {C.fmt(chunk_from)}..{C.fmt(chunk_to)} -> logrequest {request_id}")
        info = _poll_until_ready(session, counter_id, headers, request_id, sleeper=sleeper)

        parts = info.get("parts") or [{"part_number": 0}]
        for part in parts:
            part_num = part.get("part_number", 0)
            text = _download_part(session, counter_id, headers, request_id, part_num)
            total_rows += C.count_data_rows(text, has_header=True)
            fname = f"visits_{C.fmt(chunk_from)}_{C.fmt(chunk_to)}_part{part_num:03d}.csv.gz"
            _write_gz(out_dir / fname, text)
            parts_written += 1

    manifest = _record_manifest(paths, date_from, date_to, total_rows, extra={
        "schema_version": SCHEMA_VERSION,
        "fields": fields,
        "available_fields": fields,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "patch_fields": accepted,
        "patch_date": PATCH_DATE,
        "patch_backfill": False,
    })
    log(f"{SOURCE}: готово — {parts_written} частей, {total_rows} визитов; "
        f"полей принято {len(fields)}, отклонено {len(dropped_reasons)}")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "patch_backfill": False,
        "available_fields": fields,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "manifest": manifest,
    }


# ── Довыгрузка новых полей (неизменность старого слоя) ──────────────────────
def _run_backfill(
    session, counter_id, headers, paths, src_dir,
    date_from, date_to, *, sleeper, log,
) -> dict[str, Any]:
    """Выгрузить ТОЛЬКО новые поля патча в metrika_logs/backfill/, не трогая старьё."""
    out_dir = Path(src_dir) / BACKFILL_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)} — довыгрузка новых полей в "
        f"{BACKFILL_SUBDIR}/ (старые visits_* не трогаем, принцип неизменности слоя)")

    chunks = C.month_chunks(date_from, date_to)
    # Обязательные поля backfill: ТОЛЬКО ключ склейки; кандидаты — новые поля.
    # Никаких метрик/несовместимых полей: только visitID + поля патча.
    mandatory = [BACKFILL_JOIN_KEY]
    accepted, dropped_reasons = _negotiate_fields(
        session, counter_id, headers, chunks, mandatory, PATCH_CANDIDATE_FIELDS, log,
    )
    backfill_fields = mandatory + accepted
    _assert_backfill_composition(backfill_fields)

    total_rows = 0
    parts_written = 0
    for chunk_from, chunk_to in chunks:
        request_id = _create_log_request(
            session, counter_id, headers, chunk_from, chunk_to, backfill_fields,
        )
        log(f"{SOURCE}: backfill чанк {C.fmt(chunk_from)}..{C.fmt(chunk_to)} -> logrequest {request_id}")
        info = _poll_until_ready(session, counter_id, headers, request_id, sleeper=sleeper)

        parts = info.get("parts") or [{"part_number": 0}]
        for part in parts:
            part_num = part.get("part_number", 0)
            text = _download_part(session, counter_id, headers, request_id, part_num)
            total_rows += C.count_data_rows(text, has_header=True)
            fname = f"visits_backfill_{C.fmt(chunk_from)}_{C.fmt(chunk_to)}_part{part_num:03d}.csv.gz"
            _write_gz(out_dir / fname, text)
            parts_written += 1

    available = VISIT_FIELDS_BASE + accepted   # полный набор после склейки base+backfill
    manifest = _record_manifest(paths, date_from, date_to, total_rows, extra={
        "schema_version": SCHEMA_VERSION,
        "fields": available,
        "available_fields": available,
        "backfill_fields": backfill_fields,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "patch_fields": accepted,
        "patch_date": PATCH_DATE,
        "patch_backfill": True,
        "backfill_dir": f"{SOURCE}/{BACKFILL_SUBDIR}",
        "backfill_rows": total_rows,
        "note": ("новые поля патча довыгружены в подкаталог backfill/ "
                 "(visits_backfill_*), старые visits_* не изменялись (неизменность "
                 "слоя raw). Склейка по ym:s:visitID — в transform."),
    })
    log(f"{SOURCE}: довыгрузка готова — {parts_written} backfill-частей, {total_rows} строк; "
        f"принято полей {len(accepted)}, отклонено {len(dropped_reasons)} {sorted(dropped_reasons)}")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "backfill_rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "patch_backfill": True,
        "patch_fields": accepted,
        "backfill_fields": backfill_fields,
        "available_fields": available,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "manifest": manifest,
    }


def _assert_backfill_composition(backfill_fields: list[str]) -> None:
    """Инвариант backfill-набора: обязателен ключ склейки, есть новые поля,
    нет базовых не-ключевых полей и нет метрик (только ym:s:* атрибуты визита).
    """
    if BACKFILL_JOIN_KEY not in backfill_fields:
        raise C.SourceUnavailable(SOURCE, f"backfill-набор без ключа склейки {BACKFILL_JOIN_KEY}")
    new_fields = [f for f in backfill_fields if f != BACKFILL_JOIN_KEY]
    if not new_fields:
        raise C.SourceUnavailable(
            SOURCE, "backfill: ни одно новое поле не поддержано API — довыгружать нечего")
    # Никаких базовых полей (кроме ключа) и никаких метрик ym:s:*<...>: в backfill
    # идут только новые поля-кандидаты патча.
    stray = [f for f in new_fields if f not in PATCH_CANDIDATE_FIELDS]
    if stray:
        raise C.SourceUnavailable(SOURCE, f"backfill: посторонние поля в наборе: {stray}")


def _should_backfill(
    flag: bool | None, existing: dict[str, Any] | None,
    date_from: Any, date_to: Any, src_dir: Path,
) -> bool:
    """Нужна ли довыгрузка новых полей вместо полной перезагрузки.

    Явный ``flag`` (True/False) уважается. В авто-режиме (None) довыгрузка —
    когда то же окно уже выгружено ДО патча (в манифесте нет patch_date), а на
    диске лежат старые visits_* (верхнего уровня, не backfill).
    """
    if flag is not None:
        return flag
    if not existing:
        return False
    if existing.get("date_from") != C.fmt(date_from) or existing.get("date_to") != C.fmt(date_to):
        return False
    if existing.get("patch_date"):        # выгрузка уже патченная — полнота есть
        return False
    old_files = [p for p in Path(src_dir).glob("visits_*.csv.gz")
                 if not p.name.startswith("visits_backfill_")]
    return bool(old_files)


# ── Согласование состава полей (logrequests/evaluate, read-only) ────────────
def _negotiate_fields(
    session, counter_id, headers, chunks, mandatory, candidates, log,
) -> tuple[list[str], dict[str, str]]:
    """Определить поддерживаемый API поднабор ``candidates`` (поверх ``mandatory``).

    Через logrequests/evaluate (джоба НЕ создаётся). Если полный состав принят —
    возвращаем все кандидаты. Иначе бинарным делением ИЗОЛИРУЕМ неподдерживаемые
    поля и возвращаем (принятые, {поле: причина_отклонения}). Каждый отклонённый
    набор логируется безопасно (без токена/заголовков).
    """
    candidates = list(candidates)
    if not chunks:
        return candidates, {}
    # Евалюируем на маленьком окне (первый месяц) — валидность полей от окна не
    # зависит, а объём заведомо мал (не спутаем 400-«поле» с отказом по размеру).
    d1, d2 = C.fmt(chunks[0][0]), C.fmt(chunks[0][1])

    ok, err = _evaluate_fields(session, counter_id, headers, d1, d2, list(mandatory) + candidates)
    if ok:
        return candidates, {}

    _log_rejected(log, candidates, err)
    dropped = _find_bad_fields(session, counter_id, headers, d1, d2, list(mandatory), candidates, log)
    accepted = [f for f in candidates if f not in dropped]
    return accepted, dropped


def _find_bad_fields(
    session, counter_id, headers, d1, d2, mandatory, group, log,
) -> dict[str, str]:
    """Бинарное деление: вернуть {неподдерживаемое поле: причина} внутри ``group``."""
    ok, err = _evaluate_fields(session, counter_id, headers, d1, d2, mandatory + list(group))
    if ok:
        return {}
    _log_rejected(log, group, err)
    if len(group) == 1:
        return {group[0]: _reason_text(err)}
    mid = len(group) // 2
    bad: dict[str, str] = {}
    bad.update(_find_bad_fields(session, counter_id, headers, d1, d2, mandatory, group[:mid], log))
    bad.update(_find_bad_fields(session, counter_id, headers, d1, d2, mandatory, group[mid:], log))
    return bad


def _evaluate_fields(session, counter_id, headers, d1, d2, fields) -> tuple[bool, dict[str, Any] | None]:
    """logrequests/evaluate: (ok, err). ok=True — состав полей валиден (джоба НЕ
    создаётся). 400 -> ok=False + безопасно извлечённая ошибка API.
    """
    resp = C.http_request(
        session, "GET", f"{API_BASE}/{counter_id}/logrequests/evaluate",
        source=SOURCE, headers=headers,
        params={"date1": d1, "date2": d2, "source": "visits", "fields": ",".join(fields)},
        timeout=30,
    )
    status = getattr(resp, "status_code", None)
    if status == 200:
        return True, None
    return False, _safe_error(resp, status)


def _safe_error(resp: Any, status: Any) -> dict[str, Any]:
    """Безопасно извлечь тело ошибки API: status/code/message/errors[].

    Ни токен, ни заголовок Authorization сюда не попадают (берём только тело
    ответа, которое их не содержит).
    """
    info: dict[str, Any] = {"status": status}
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        info["code"] = payload.get("code")
        if payload.get("message"):
            info["message"] = payload.get("message")
        errs = payload.get("errors")
        if isinstance(errs, list):
            info["errors"] = [
                {"error_type": e.get("error_type"), "message": e.get("message")}
                for e in errs if isinstance(e, dict)
            ]
    return info


def _reason_text(err: dict[str, Any] | None) -> str:
    """Читаемая причина отклонения одного поля из тела ошибки API."""
    err = err or {}
    errs = err.get("errors") or []
    if errs and errs[0].get("message"):
        return str(errs[0]["message"])
    if err.get("message"):
        return str(err["message"])
    return f"HTTP {err.get('status')}"


def _log_rejected(log, fields, err: dict[str, Any] | None) -> None:
    """Безопасно залогировать отклонённый набор полей (без токена/заголовков)."""
    err = err or {}
    errs = err.get("errors") or []
    detail = "; ".join(
        f"{e.get('error_type')}: {e.get('message')}" for e in errs
    ) or err.get("message") or ""
    log(f"{SOURCE}: evaluate отклонил состав {list(fields)} — "
        f"HTTP {err.get('status')} code={err.get('code')} {detail}".rstrip())


# ── Шаги Logs API ──────────────────────────────────────────────────────────
def _create_log_request(session, counter_id, headers, date_from, date_to, fields) -> Any:
    """Создать logrequest на выгрузку визитов принятым составом полей.

    Состав уже согласован через evaluate; здесь 400 — нештатная ситуация,
    падаем внятно (безопасно, без токена).
    """
    params = {
        "date1": C.fmt(date_from),
        "date2": C.fmt(date_to),
        "source": "visits",
        "fields": ",".join(fields),
    }
    resp = C.http_request(
        session, "POST", f"{API_BASE}/{counter_id}/logrequests",
        source=SOURCE, headers=headers, params=params, timeout=60,
    )
    if getattr(resp, "status_code", None) == 400:
        err = _safe_error(resp, 400)
        raise C.SourceUnavailable(SOURCE, f"Logs API отклонил создание запроса: {_reason_text(err)}")
    C.ensure_ok(resp, SOURCE, "create logrequest")
    log_request = resp.json().get("log_request") or {}
    request_id = log_request.get("request_id")
    if request_id is None:
        raise C.SourceUnavailable(SOURCE, "Logs API не вернул request_id")
    return request_id


def _poll_until_ready(session, counter_id, headers, request_id, *, sleeper) -> dict[str, Any]:
    """Поллить статус logrequest до готовности. Вернуть блок log_request."""
    url = f"{API_BASE}/{counter_id}/logrequest/{request_id}"  # singular!
    for _attempt in range(POLL_MAX_ATTEMPTS):
        resp = C.http_request(session, "GET", url, source=SOURCE,
                              headers=headers, timeout=30)
        C.ensure_ok(resp, SOURCE, "poll logrequest")
        info = resp.json().get("log_request") or {}
        status = info.get("status")
        if status in _STATUS_READY:
            return info
        if status in _STATUS_FAILED:
            raise C.SourceUnavailable(
                SOURCE, f"Logs API отклонил выгрузку (status={status})"
            )
        sleeper(POLL_INTERVAL_SEC)
    raise C.SourceUnavailable(
        SOURCE, f"Logs API не подготовил выгрузку за {POLL_MAX_ATTEMPTS} опросов"
    )


def _download_part(session, counter_id, headers, request_id, part_num) -> str:
    """Скачать часть выгрузки как текст (TSV, как отдаёт Logs API)."""
    url = f"{API_BASE}/{counter_id}/logrequest/{request_id}/part/{part_num}/download"  # singular!
    resp = C.http_request(session, "GET", url, source=SOURCE,
                          headers=headers, timeout=300)
    C.ensure_ok(resp, SOURCE, f"download part {part_num}")
    return resp.text


def _write_gz(path: Path, text: str) -> None:
    """Сохранить часть как csv.gz (сжимаем сами, если API отдал открытым текстом)."""
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(text)


def manifest_load(raw_root: Path) -> dict[str, Any]:
    """Прочитать текущий manifest.json (для решения о backfill)."""
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.load_manifest(Path(raw_root))


def _record_manifest(paths, date_from, date_to, rows, *, extra=None) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra=extra,
    )
