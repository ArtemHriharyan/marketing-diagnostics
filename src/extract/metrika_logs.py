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
    1. POST logrequests   — создать запрос на выгрузку окна;
    2. GET  logrequests/{id} — поллинг статуса до "processed";
    3. GET  .../part/{n}/download — скачать части;
    4. склейка — здесь не делаем: части кладём как есть (raw), склейка/парсинг
       живут в слое transform.
Большие окна делятся на календарные месяцы (по одному logrequest на месяц):
Logs API ограничивает объём одного запроса.

── Патч 0.3.0 (расширение полей под каталог угроз v2) ──────────────────────
Добавлены поля визита для T02 (наивная vs last-significant атрибуция), D11
(боты), C21 (браузер/ОС/разрешение) и A12/S26 (гео). Состав полей теперь
согласовывается с API «лесенкой» (_FIELD_LADDER): наличие ОТДЕЛЬНОГО поля
yclid/gclid в Logs API не гарантировано, поэтому такие поля ПРОБНЫЕ — при 400
на состав полей запрос откатывается без них, а фактически принятый набор
фиксируется в manifest.available_fields (принцип «проверь, не предполагай»).

Неизменность слоя raw (принцип 2): если окно уже было выгружено ДО патча
(в манифесте нет patch_date), старые visits_* файлы не трогаются — недостающие
поля довыгружаются отдельным проходом в visits_backfill_* и помечаются
manifest.patch_backfill: true. Так по манифесту видно, за какой период данные
полны сразу, а за какой полнота собирается склейкой основного и backfill-слоёв.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.3.0"
SOURCE = "metrika_logs"
CANONICAL_TABLES = ["visits"]

# Дата патча 0.3.0 (расширение полей). Пишется в manifest.patch_date — граница,
# по которой видно, какие выгрузки уже содержат новые поля.
PATCH_DATE = "2026-07-13"

# База management/logrequests API Метрики.
# ВНИМАНИЕ (квирк Logs API): создание запроса — множественное число
# /logrequests, а статус/скачивание/очистка — ЕДИНСТВЕННОЕ /logrequest/{id}.
API_BASE = "https://api-metrika.yandex.net/management/v1/counter"

# Базовый набор полей визита (до патча 0.3.0). Порядок фиксирован — на него
# опирается transform. ym:s:lastSignDirectClickOrder доступен не на всех
# счётчиках; при 400 на состав полей он отбрасывается последним звеном лесенки.
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

# Поля, добавленные патчем 0.3.0. Фиксируются в manifest.patch_fields, чтобы по
# манифесту было видно, за какой период данные полны, а за какой (выгруженный
# ДО патча) — их ещё нужно довыгрузить.
PATCH_ADDED_FIELDS = [
    "ym:s:isRobot",              # D11: доля ботов (на extract НЕ фильтруем)
    "ym:s:lastTrafficSource",    # T02: НАИВНАЯ модель атрибуции — ОТДЕЛЬНО от
                                 # ym:s:lastsignTrafficSource (last-significant).
                                 # Нужны ОБЕ: это разные модели атрибуции.
    "ym:s:browser",             # C21: проблема в конкретном браузере
    "ym:s:operatingSystem",     # C21: ... или ОС
    "ym:s:screenResolution",    # C21: ... или разрешении экрана
    "ym:s:regionCountry",       # A12 (нецелевая гео) / S26 (гео-спрос)
    "ym:s:regionCity",          # A12 / S26
]

# Click ID «в чистом виде» (yclid/gclid) как ОТДЕЛЬНОЕ поле визита в Logs API
# НЕ гарантирован: доступность зависит от версии API и настроек счётчика. Поэтому
# поля ПРОБНЫЕ — заказываем, а при 400 «неизвестное поле» откатываемся без них;
# фактически принятый состав фиксируется в manifest.available_fields. Связка
# визита с кампанией Директа через ym:s:lastSignDirectClickOrder уже есть в базе.
VISIT_FIELDS_CLICKID_PROBE = [
    "ym:s:lastSignYclid",   # ПРОБНОЕ: yandex click id, если счётчик его отдаёт
    "ym:s:lastSignGclid",   # ПРОБНОЕ: google click id (кросс-плейсмент), если есть
]

# Полный желаемый состав (порядок: base -> patch -> clickid). Имя VISIT_FIELDS
# сохранено: на него опирается transform (build_canonical) и смоук-тесты.
VISIT_FIELDS = VISIT_FIELDS_BASE + PATCH_ADDED_FIELDS + VISIT_FIELDS_CLICKID_PROBE

# Лесенка отката состава полей: пробуем самый полный, при 400 на состав
# отбрасываем сначала пробные clickid, затем и lastSignDirectClickOrder.
_FIELD_LADDER = [
    VISIT_FIELDS,
    VISIT_FIELDS_BASE + PATCH_ADDED_FIELDS,
    [f for f in (VISIT_FIELDS_BASE + PATCH_ADDED_FIELDS)
     if f != "ym:s:lastSignDirectClickOrder"],
]

# Состав довыгрузки (backfill): ключ склейки + только новые поля патча.
# visitID нужен, чтобы transform склеил довыгруженные поля со старыми файлами.
_BACKFILL_FIELDS = ["ym:s:visitID"] + PATCH_ADDED_FIELDS + VISIT_FIELDS_CLICKID_PROBE
_BACKFILL_LADDER = [
    _BACKFILL_FIELDS,
    ["ym:s:visitID"] + PATCH_ADDED_FIELDS,
]

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
        True  — принудительная довыгрузка только новых полей в visits_backfill_*.
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

    total_rows = 0
    parts_written = 0
    used_fields: list[str] = list(VISIT_FIELDS)
    for chunk_from, chunk_to in C.month_chunks(date_from, date_to):
        request_id, used_fields = _create_log_request(
            session, counter_id, headers, chunk_from, chunk_to, ladder=_FIELD_LADDER,
        )
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

    dropped = [f for f in VISIT_FIELDS if f not in used_fields]
    if dropped:
        log(f"{SOURCE}: API не принял поля {dropped} — выгружены без них")
    manifest = _record_manifest(paths, date_from, date_to, total_rows, extra={
        "fields": used_fields,
        "available_fields": used_fields,
        "dropped_fields": dropped,
        "patch_fields": [f for f in (PATCH_ADDED_FIELDS + VISIT_FIELDS_CLICKID_PROBE)
                         if f in used_fields],
        "patch_date": PATCH_DATE,
        "patch_backfill": False,
    })
    log(f"{SOURCE}: готово — {parts_written} частей, {total_rows} визитов")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "patch_backfill": False,
        "dropped_fields": dropped,
        "manifest": manifest,
    }


# ── Довыгрузка новых полей (неизменность старого слоя) ──────────────────────
def _run_backfill(
    session, counter_id, headers, paths, src_dir,
    date_from, date_to, *, sleeper, log,
) -> dict[str, Any]:
    """Выгрузить ТОЛЬКО новые поля патча в visits_backfill_*, не трогая старьё."""
    out_dir = Path(src_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)} — довыгрузка новых полей "
        f"(старые visits_* не трогаем, принцип неизменности слоя)")

    total_rows = 0
    parts_written = 0
    used_fields: list[str] = list(_BACKFILL_FIELDS)
    for chunk_from, chunk_to in C.month_chunks(date_from, date_to):
        request_id, used_fields = _create_log_request(
            session, counter_id, headers, chunk_from, chunk_to, ladder=_BACKFILL_LADDER,
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

    patch_fields = [f for f in used_fields if f != "ym:s:visitID"]
    manifest = _record_manifest(paths, date_from, date_to, total_rows, extra={
        # Полный логический набор доступен после склейки base + backfill.
        "fields": VISIT_FIELDS_BASE + PATCH_ADDED_FIELDS,
        "patch_fields": patch_fields,
        "patch_date": PATCH_DATE,
        "patch_backfill": True,
        "backfill_files_prefix": "visits_backfill_",
        "note": ("новые поля патча довыгружены отдельными visits_backfill_* файлами; "
                 "старые visits_* не изменялись (неизменность слоя raw). Склейка по "
                 "ym:s:visitID — в transform."),
    })
    log(f"{SOURCE}: довыгрузка готова — {parts_written} backfill-частей, {total_rows} строк")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "patch_backfill": True,
        "patch_fields": patch_fields,
        "manifest": manifest,
    }


def _should_backfill(
    flag: bool | None, existing: dict[str, Any] | None,
    date_from: Any, date_to: Any, src_dir: Path,
) -> bool:
    """Нужна ли довыгрузка новых полей вместо полной перезагрузки.

    Явный ``flag`` (True/False) уважается. В авто-режиме (None) довыгрузка —
    когда то же окно уже выгружено ДО патча (в манифесте нет patch_date), а на
    диске лежат старые visits_* без backfill.
    """
    if flag is not None:
        return flag
    if not existing:
        return False
    if existing.get("date_from") != C.fmt(date_from) or existing.get("date_to") != C.fmt(date_to):
        return False
    if existing.get("patch_date"):        # выгрузка уже патченная — полнота есть
        return False
    src_dir = Path(src_dir)
    old_files = [p for p in src_dir.glob("visits_*.csv.gz")
                 if not p.name.startswith("visits_backfill_")]
    return bool(old_files)


# ── Шаги Logs API ──────────────────────────────────────────────────────────
def _create_log_request(
    session, counter_id, headers, date_from, date_to, *, ladder,
) -> tuple[Any, list[str]]:
    """Создать logrequest, согласуя состав полей «лесенкой».

    Пробуем составы из ``ladder`` по очереди; при 400 (Logs API не принял состав
    полей) переходим к более короткому. Возвращает (request_id, принятые поля).
    """
    last_resp = None
    for fields in ladder:
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
            # Состав полей не принят (напр. пробные yclid/gclid) — пробуем короче.
            last_resp = resp
            continue
        C.ensure_ok(resp, SOURCE, "create logrequest")
        log_request = resp.json().get("log_request") or {}
        request_id = log_request.get("request_id")
        if request_id is None:
            raise C.SourceUnavailable(SOURCE, "Logs API не вернул request_id")
        return request_id, list(fields)

    # Все составы отклонены (400 на каждый) — падаем внятно.
    if last_resp is not None:
        C.ensure_ok(last_resp, SOURCE, "create logrequest (все составы полей отклонены)")
    raise C.SourceUnavailable(SOURCE, "Logs API отклонил все составы полей")


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
