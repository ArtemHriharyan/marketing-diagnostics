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

Неизменность слоя raw (принцип 2): если окно уже было выгружено СО СТАРОЙ
схемой (в манифесте schema_version отличается от текущей SCHEMA_VERSION),
старые visits_* файлы не трогаются — недостающие поля довыгружаются отдельным
проходом в ПОДКАТАЛОГ metrika_logs/backfill/ и помечаются
manifest.patch_backfill: true. Подкаталог намеренно вне обычного
`*.csv.gz`-глоба верхнего уровня: сверка (scripts/verify_metrika) и transform
читают только базовые visits_* и не спотыкаются о backfill-файлы (в них нет
ym:s:dateTime). Склейка base+backfill по ym:s:visitID — забота слоя transform.

── Патч 2A-patch (сверка с реальным API Logs, goal-массивы D01/D09) ─────────
Состав полей сверен построчно с https://yandex.ru/dev/metrika/ru/logs/fields/visits:
  - убраны ym:s:isRobot (такого поля нет; см. isRobotPro ниже),
    ym:s:screenResolution (избыточно поверх screenWidth/screenHeight),
    ym:s:lastSignGCLID / ym:s:lastSignhasGCLID (100% пустые — нет Google Ads
    трафика у проверенных клиентов, добавлять по факту появления канала);
  - добавлены ym:s:goalsDateTime + ym:s:goalsSerialNumber (параллельные
    массивы к ym:s:goalsID — разделяют переотработку цели (D01) от повторного
    обращения (D09)), ym:s:from (внутренний тег источника Метрики, T01/T03),
    ym:s:bounce (явный флаг отказа, C06/C07), ym:s:endURL (страница выхода,
    C06/C07/C12);
  - ym:s:isRobotPro — опционально, доступно только в Метрике Про. Идёт через
    ту же негоциацию logrequests/evaluate, что и остальные поля патча: если
    тариф не позволяет, поле изолируется в dropped_fields, а
    manifest.is_robot_pro_available=false; иначе true.
SCHEMA_VERSION поднята до "visits-v3" — это заставляет _should_backfill
запустить ЕЩЁ ОДНУ довыгрузку для окон, уже пропатченных под visits-v2
(старый patch_date/schema_version не равен текущему).

── Патч 2A-patch, уточнение после боевого прогона (2026-07-22) ─────────────
Боевой прогон подтвердил: доступный тариф этого счётчика НЕ поддерживает
даже ym:s:isRobotPro (API отклоняет поле как для isRobot). Детекция бота
через Logs API для этого доступа невозможна — это ПОСТОЯННОЕ ограничение,
а не временная деградация тарифа: isRobotPro убран из кандидатов насовсем,
никакой негоциации/ретраев вокруг него больше нет.
manifest.bot_detection_available всегда False (см. BOT_DETECTION_AVAILABLE
ниже); D11 в config/methodology.yaml зафиксирован как
type_downgraded="permanent_LOW" с полем downgrade_reason.
ym:s:regionCity заменяется попыткой ym:s:regionArea: имя поля не гадается —
факт поддержки проверяется отдельным logrequests/evaluate на каждый прогон
(см. _resolve_region_field). Принято -> используем regionArea,
manifest.region_field_verified=true. API вернул «Unknown field» -> откат на
regionCity (уже известное рабочее поле патча 0.3.x), verified=false, а
реальный текст ошибки API сохраняется в manifest.region_field_error. Другое
имя в рамках этой задачи не пробуется (см. протокол микрозадач CLAUDE.md).
ym:s:ipAddress по-прежнему не запрашивается (приватность важнее, см.
data-export-spec-v1.md, раздел A).
SCHEMA_VERSION поднята до "visits-v4" — довыгрузка сработает и для окон,
уже частично выгруженных под visits-v3 в ходе боевого прогона.

── Патч 4X-metrika-lookback (carry-forward контекст, 2026-07-22) ───────────
_run_full дополнительно запрашивает config.transform.traffic_resolve_lookback_days
(config/defaults.yaml, по умолчанию 30) дней ДО data_window.date_from —
только для восстановления цепочки clientID в carry-forward
(resolve_traffic_source, src/transform/build_canonical.py, T02/T03), не для
метрик. Пишется в отдельный подкаталог LOOKBACK_SUBDIR ("lookback/", тот же
приём, что и backfill/): build_visits/_read_metrika_logs_rows глобит только
visits_*.csv.gz верхнего уровня src_dir, поэтому лог-визиты lookback ей не
видны и ни в одну метрику не попадают, пока отдельная задача не научит
transform читать этот каталог и проставлять canonical-флаг is_lookback_only
явно (raw-слой хранит только то, что вернул API — синтетическую колонку в
CSV extract не добавляет, см. принцип неизменности сырья). Фактическая
глубина (manifest.lookback_days_covered/lookback_effective_date_from)
считается ПО ДАННЫМ (по чанкам с rows>0), а не предполагается равной
запрошенной: если у счётчика нет истории так далеко назад, самые старые
чанки просто вернут 0 строк — это не ошибка. Поля для lookback-запроса
переиспользуются из уже согласованного набора основного окна (валидность
поля от диапазона дат не зависит) — повторной evaluate нет. Применяется
только в _run_full (полная выгрузка); _run_backfill (довыгрузка новых полей
патча схемы для уже выгруженного окна) lookback не трогает — не в скоупе
этой задачи.
"""

from __future__ import annotations

import gzip
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.4.0"
SOURCE = "metrika_logs"
CANONICAL_TABLES = ["visits"]

# Версия схемы полей визита. v2 = базовый набор + поля патча 0.3.x (T02/C21/гео).
# v3 = v2 + поля 2A-patch (goal-массивы D01/D09, from/bounce/endURL, isRobotPro).
# v4 = v3 без isRobotPro (недоступен на тарифе, подтверждено боевым прогоном)
# + ym:s:regionArea вместо ym:s:regionCity (с рантайм-откатом).
# Пишется в manifest.schema_version — по нему видно, какой контракт полей
# у выгрузки, независимо от версии кода.
SCHEMA_VERSION = "visits-v4"

# Дата патча расширения полей. Пишется в manifest.patch_date — граница, по
# которой видно, какие выгрузки уже содержат новые поля.
PATCH_DATE = "2026-07-22"

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

# Поля, добавленные патчем 0.3.x. Валидность каждого проверяется рантайм-негоциацией
# (logrequests/evaluate); принятые попадают в manifest.patch_fields, отклонённые —
# в manifest.dropped_fields с причиной. Имена ниже сверены с боевым API Метрики:
#   ym:s:screenWidth/Height — реальные поля (ym:s:screenResolution НЕ существует).
PATCH_ADDED_FIELDS = [
    "ym:s:lastTrafficSource",    # T02: НАИВНАЯ модель атрибуции — ОТДЕЛЬНО от
                                 # ym:s:lastsignTrafficSource (last-significant).
                                 # Нужны ОБЕ: это разные модели атрибуции.
    "ym:s:browser",             # C21: проблема в конкретном браузере
    "ym:s:operatingSystem",     # C21: ... или ОС
    "ym:s:screenWidth",         # C21: ширина экрана (замена screenResolution)
    "ym:s:screenHeight",        # C21: высота экрана
    "ym:s:regionCountry",       # A12 (нецелевая гео) / S26 (гео-спрос)
    # ym:s:regionCity/regionArea — НЕ здесь: решается рантайм-проверкой
    # _resolve_region_field (2A-patch), а не статическим кандидатом.
]

# Поля, добавленные 2A-patch: сверены построчно с реальным Logs API
# (https://yandex.ru/dev/metrika/ru/logs/fields/visits). GCLID/hasGCLID убраны
# насовсем (100% пустые — нет Google Ads трафика у проверенных клиентов), а не
# перенесены сюда как пробные: добавлять по факту появления канала, отдельным
# конфиг-флагом, не молчаливой пробой.
PATCH2_ADDED_FIELDS = [
    "ym:s:goalsDateTime",        # D01/D09: время каждого срабатывания цели —
                                 # параллельный массив к ym:s:goalsID, отличает
                                 # переотработку (несколько за 1 сек) от
                                 # повторного обращения (2-я заявка через 10 мин).
    "ym:s:goalsSerialNumber",    # D01: порядковый номер срабатывания цели —
                                 # тоже параллельный массив к ym:s:goalsID.
    "ym:s:from",                 # T01/T03: внутренний тег источника Метрики
                                 # (закладка/прямой ввод/переход из приложения) —
                                 # UTM и referer эту разницу не видят.
    "ym:s:bounce",               # C06/C07: явный флаг отказа Метрики (отказ —
                                 # НЕ то же самое, что pageViews=1).
    "ym:s:endURL",               # C06/C07/C12: страница выхода — воронка и
                                 # анализ точек потери.
]

# D11 (флаг робота): ни ym:s:isRobot (не существует), ни ym:s:isRobotPro
# (боевой прогон подтвердил — тариф этого доступа его тоже отклоняет) не
# запрашиваются. Это ПОСТОЯННОЕ ограничение, не тарифная деградация, поэтому
# никакой негоциации/ретраев вокруг него нет: manifest.bot_detection_available
# всегда False, жёстко (см. _run_full / _run_backfill). D11 зафиксирован в
# config/methodology.yaml как type_downgraded="permanent_LOW".
BOT_DETECTION_AVAILABLE = False

# Регион визита (A12/S26): ym:s:regionCity заменяется попыткой
# ym:s:regionArea, но имя не предполагается — проверяется отдельным
# logrequests/evaluate на каждый прогон (_resolve_region_field). Принято ->
# regionArea (verified=true); API отклонил («Unknown field») -> откат на
# уже известный regionCity (verified=false, текст ошибки — в manifest).
# Другие имена в рамках этой задачи не перебираются.
REGION_FIELD_PRIMARY = "ym:s:regionArea"
REGION_FIELD_FALLBACK = "ym:s:regionCity"

# Все новые поля, которые ПЫТАЕМСЯ добавить через общую негоциацию (регион —
# отдельная рантайм-проверка, сюда не входит; бот-детекция не пробуется вовсе).
PATCH_CANDIDATE_FIELDS = PATCH_ADDED_FIELDS + PATCH2_ADDED_FIELDS

# Полный желаемый состав (порядок: base -> patch 0.3.x -> patch 2A -> регион).
# Имя VISIT_FIELDS сохранено: на него опираются transform (build_canonical) и
# смоук-тесты. Это ЖЕЛАЕМЫЙ набор (regionArea как предпочитаемое имя); фактически
# принятый API набор, включая реальный откат региона, — в manifest.available_fields.
VISIT_FIELDS = VISIT_FIELDS_BASE + PATCH_CANDIDATE_FIELDS + [REGION_FIELD_PRIMARY]

# Ключ склейки backfill со старым слоем — обязателен в любом backfill-наборе.
BACKFILL_JOIN_KEY = "ym:s:visitID"
BACKFILL_SUBDIR = "backfill"

# T02/T03 (задача 4X-metrika-lookback): визиты за N дней ДО data_window.date_from
# нужны ТОЛЬКО для восстановления цепочки clientID в carry-forward
# (resolve_traffic_source, src/transform/build_canonical.py) — не для метрик.
# Подкаталог, отдельный от backfill/: build_visits/_read_metrika_logs_rows
# глобит только visits_*.csv.gz верхнего уровня src_dir, поэтому файлы здесь
# невидимы существующему transform/compute, пока отдельная задача не научит
# его читать этот каталог и проставлять canonical-флаг is_lookback_only явно.
LOOKBACK_SUBDIR = "lookback"

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
    force_lookback_backfill: bool = False,
) -> dict[str, Any]:
    """Выгрузить визиты окна в data/raw/metrika_logs/ (csv.gz по частям).

    Возвращает метаданные для manifest.json. При мёртвом токене поднимает
    AuthError с внятным сообщением и кодом «источник недоступен».

    ``backfill``:
        None (по умолчанию) — авто: довыгрузка новых полей патча, если окно уже
            было выгружено ДО патча (см. _should_backfill); иначе полная выгрузка.
        True  — принудительная довыгрузка только новых полей в backfill/.
        False — принудительная полная выгрузка (перезапись слоя целиком).

    ``force_lookback_backfill`` (задача 4X-lookback-wiring-check): True —
    принудительно дозаполнить LOOKBACK_SUBDIR для окна, которое УЖЕ извлечено
    (не дожидаясь естественного триггера _should_backfill/_already_extracted,
    и не трогая ни visits_*.csv.gz верхнего уровня, ни backfill/). Нужен,
    когда окно было извлечено ДО появления lookback (4X-metrika-lookback) или
    когда действующий SCHEMA_VERSION совпадает с manifest.schema_version, из-за
    чего обычный запуск экстракт просто пропустит окно целиком. Имеет
    приоритет над ``backfill`` — если окно ещё не извлекалось вовсе (нет
    записи в manifest), откатывается на обычную полную выгрузку (нечего
    форсировать поверх пустоты).

    Если manifest.json содержит primary_window + compare_window (записал intake),
    выгрузка выполняется дважды: primary -> .../primary/, compare -> .../compare/.
    Логика backfill, чанкинга и ретраев применяется к каждому окну независимо.
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

    (date_from, date_to), compare_window = C.resolve_windows(
        paths.raw, config, defaults, today=today
    )
    raw_root = Path(paths.raw)
    base_src_dir = C.source_dir(paths, SOURCE)
    has_compare = compare_window is not None

    windows: list[tuple] = [(date_from, date_to, "primary")]
    if has_compare:
        windows.append((compare_window[0], compare_window[1], "compare"))

    last_result: dict[str, Any] = {}
    for win_from, win_to, slot in windows:
        src_dir = base_src_dir / slot if has_compare else base_src_dir
        source_key = SOURCE if slot == "primary" else f"{SOURCE}/compare"
        existing = (manifest_load(raw_root).get("sources") or {}).get(source_key)
        do_backfill = _should_backfill(backfill, existing, win_from, win_to, src_dir)

        if force_lookback_backfill:
            result = _run_lookback_backfill_only(
                session, counter_id, headers, paths, src_dir, win_from, win_to, existing,
                sleeper=sleeper, log=log, source_key=source_key, defaults=defaults,
            )
        elif do_backfill:
            result = _run_backfill(
                session, counter_id, headers, paths, src_dir,
                win_from, win_to, sleeper=sleeper, log=log, source_key=source_key,
            )
        elif backfill is None and _already_extracted(existing, win_from, win_to, src_dir):
            log(f"{SOURCE}: данные за {C.fmt(win_from)}..{C.fmt(win_to)} уже выгружены "
                f"(patch_date={existing['patch_date']}), пропускаем")
            result = {k: v for k, v in existing.items() if k != "fetched_at"}
        else:
            result = _run_full(
                session, counter_id, headers, paths, src_dir,
                win_from, win_to, sleeper=sleeper, log=log, source_key=source_key,
                defaults=defaults,
            )
        last_result = result

    return last_result


def _lookback_days_from_config(defaults: dict[str, Any] | None) -> int:
    """config/defaults.yaml: transform.traffic_resolve_lookback_days (default 30)."""
    return int(((defaults or {}).get("transform") or {}).get("traffic_resolve_lookback_days", 30))


def _fetch_lookback(
    session, counter_id, headers, src_dir, date_from, fields, lookback_days, *, sleeper, log,
) -> dict[str, Any]:
    """Довыгрузить lookback_days дней ДО date_from в LOOKBACK_SUBDIR/ (см. константу).

    Поля переиспользуются из уже согласованного для основного окна набора
    (``fields``) — валидность полей от диапазона дат не зависит (см.
    докстринг _negotiate_fields), повторная evaluate не нужна.

    Фактическая глубина фиксируется ПО ДАННЫМ (по чанкам с rows>0), а не
    предполагается равной запрошенной: если у счётчика попросту нет истории
    так далеко назад (начало истории счётчика вообще), самые старые чанки
    вернут 0 строк — это не ошибка, а честная граница lookback_days_covered.
    """
    if lookback_days <= 0:
        return {
            "lookback_requested_days": lookback_days,
            "lookback_date_from_requested": None,
            "lookback_date_to": None,
            "lookback_rows": 0,
            "lookback_parts": 0,
            "lookback_effective_date_from": None,
            "lookback_days_covered": 0,
        }

    lookback_from = date_from - timedelta(days=lookback_days)
    lookback_to = date_from - timedelta(days=1)

    out_dir = Path(src_dir) / LOOKBACK_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}: lookback {C.fmt(lookback_from)}..{C.fmt(lookback_to)} — только контекст "
        f"для восстановления цепочки clientID (T02/T03), не для метрик")

    chunks = C.month_chunks(lookback_from, lookback_to)
    total_rows = 0
    parts_written = 0
    chunk_rows: list[tuple[Any, int]] = []

    for chunk_from, chunk_to in chunks:
        request_id = _create_log_request(session, counter_id, headers, chunk_from, chunk_to, fields)
        log(f"{SOURCE}: lookback чанк {C.fmt(chunk_from)}..{C.fmt(chunk_to)} -> logrequest {request_id}")
        info = _poll_until_ready(session, counter_id, headers, request_id, sleeper=sleeper)

        parts = info.get("parts") or [{"part_number": 0}]
        chunk_total = 0
        for part in parts:
            part_num = part.get("part_number", 0)
            text = _download_part(session, counter_id, headers, request_id, part_num)
            rows = C.count_data_rows(text, has_header=True)
            chunk_total += rows
            total_rows += rows
            fname = f"visits_lookback_{C.fmt(chunk_from)}_{C.fmt(chunk_to)}_part{part_num:03d}.csv.gz"
            _write_gz(out_dir / fname, text)
            parts_written += 1
        chunk_rows.append((chunk_from, chunk_total))

    covered_starts = [c_from for c_from, rows in chunk_rows if rows > 0]
    effective_from = min(covered_starts) if covered_starts else None
    days_covered = (date_from - effective_from).days if effective_from else 0

    log(f"{SOURCE}: lookback готов — {parts_written} частей, {total_rows} визитов; "
        f"фактическая глубина {days_covered} из запрошенных {lookback_days} дн.")

    return {
        "lookback_requested_days": lookback_days,
        "lookback_date_from_requested": C.fmt(lookback_from),
        "lookback_date_to": C.fmt(lookback_to),
        "lookback_rows": total_rows,
        "lookback_parts": parts_written,
        "lookback_effective_date_from": C.fmt(effective_from) if effective_from else None,
        "lookback_days_covered": days_covered,
    }


# ── Принудительная дозаливка lookback/ для уже извлечённого окна ───────────
_ENTRY_MANAGED_KEYS = (
    "date_from", "date_to", "rows", "fetched_at", "extracted_at",
    "script_version", "canonical_tables",
)


def _run_lookback_backfill_only(
    session, counter_id, headers, paths, src_dir, date_from, date_to, existing, *,
    sleeper, log, source_key=SOURCE, defaults=None,
) -> dict[str, Any]:
    """force_lookback_backfill=True: дозаполнить LOOKBACK_SUBDIR, не трогая
    visits_*.csv.gz верхнего уровня и не дожидаясь _should_backfill.

    Поля для lookback-запроса переиспользуются из уже зафиксированного в
    manifest ``available_fields``/``fields`` — повторной evaluate/негоциации
    нет (state окна не меняется этим вызовом). Все прочие поля существующей
    записи манифеста (schema_version, region_field, patch_fields и т.п.)
    сохраняются как есть — update_source перезаписывает запись целиком,
    поэтому их нужно явно перенести, а не полагаться на merge.

    Если записи ``existing`` ещё нет вовсе (окно ни разу не извлекалось) —
    форсировать нечего поверх пустоты: откатываемся на обычную _run_full
    (она уже включает lookback как часть обычной полной выгрузки).
    """
    if not existing:
        log(f"{SOURCE}: force_lookback_backfill запрошен, но окно "
            f"{C.fmt(date_from)}..{C.fmt(date_to)} ещё не извлекалось — "
            f"выполняем обычную полную выгрузку (уже включает lookback)")
        return _run_full(
            session, counter_id, headers, paths, src_dir, date_from, date_to,
            sleeper=sleeper, log=log, source_key=source_key, defaults=defaults,
        )

    fields = existing.get("available_fields") or existing.get("fields")
    if not fields:
        raise C.SourceUnavailable(
            SOURCE,
            "force_lookback_backfill: в существующей записи манифеста нет "
            "available_fields/fields — нечем переиспользовать состав полей "
            "для lookback-запроса",
        )

    log(f"{SOURCE}: force_lookback_backfill для {C.fmt(date_from)}..{C.fmt(date_to)} — "
        f"дозаполняем {LOOKBACK_SUBDIR}/, основной слой не трогаем")

    lookback_days = _lookback_days_from_config(defaults)
    lookback_stats = _fetch_lookback(
        session, counter_id, headers, src_dir, date_from, fields, lookback_days,
        sleeper=sleeper, log=log,
    )

    carried_over = {k: v for k, v in existing.items() if k not in _ENTRY_MANAGED_KEYS}
    manifest = _record_manifest(
        paths, source_key, date_from, date_to, existing.get("rows", 0),
        extra={**carried_over, **lookback_stats},
    )
    log(f"{SOURCE}: force_lookback_backfill готов — {lookback_stats['lookback_rows']} визитов "
        f"lookback, глубина {lookback_stats['lookback_days_covered']} из "
        f"{lookback_stats['lookback_requested_days']} запрошенных дней")

    result = {k: v for k, v in existing.items() if k != "fetched_at"}
    result.update(lookback_stats)
    result["manifest"] = manifest
    return result


# ── Полная выгрузка (перезапись слоя целиком) ──────────────────────────────
def _run_full(
    session, counter_id, headers, paths, src_dir,
    date_from, date_to, *, sleeper, log, source_key=SOURCE, defaults=None,
) -> dict[str, Any]:
    out_dir = C.reset_dir(src_dir)
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)}, счётчик {counter_id} (полная выгрузка)")

    chunks = C.month_chunks(date_from, date_to)
    region_field, region_verified, region_error = _resolve_region_field(
        session, counter_id, headers, chunks, log,
    )
    accepted, dropped_reasons = _negotiate_fields(
        session, counter_id, headers, chunks, VISIT_FIELDS_BASE, PATCH_CANDIDATE_FIELDS, log,
    )
    fields = VISIT_FIELDS_BASE + accepted + [region_field]

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

    lookback_days = _lookback_days_from_config(defaults)
    lookback_stats = _fetch_lookback(
        session, counter_id, headers, src_dir, date_from, fields, lookback_days,
        sleeper=sleeper, log=log,
    )

    manifest = _record_manifest(paths, source_key, date_from, date_to, total_rows, extra={
        "schema_version": SCHEMA_VERSION,
        "fields": fields,
        "available_fields": fields,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "patch_fields": accepted + [region_field],
        "patch_date": PATCH_DATE,
        "patch_backfill": False,
        "bot_detection_available": BOT_DETECTION_AVAILABLE,
        "region_field": region_field,
        "region_field_verified": region_verified,
        "region_field_error": region_error,
        **lookback_stats,
    })
    log(f"{SOURCE}: готово — {parts_written} частей, {total_rows} визитов; "
        f"полей принято {len(fields)}, отклонено {len(dropped_reasons)}; "
        f"region_field={region_field} verified={region_verified}")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "bot_detection_available": BOT_DETECTION_AVAILABLE,
        "region_field": region_field,
        "region_field_verified": region_verified,
        "region_field_error": region_error,
        "patch_backfill": False,
        "available_fields": fields,
        "dropped_fields": sorted(dropped_reasons),
        **lookback_stats,
        "dropped_reasons": dropped_reasons,
        "manifest": manifest,
    }


# ── Довыгрузка новых полей (неизменность старого слоя) ──────────────────────
def _run_backfill(
    session, counter_id, headers, paths, src_dir,
    date_from, date_to, *, sleeper, log, source_key=SOURCE,
) -> dict[str, Any]:
    """Выгрузить ТОЛЬКО новые поля патча в <src_dir>/backfill/, не трогая старьё."""
    out_dir = Path(src_dir) / BACKFILL_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)} — довыгрузка новых полей в "
        f"{BACKFILL_SUBDIR}/ (старые visits_* не трогаем, принцип неизменности слоя)")

    chunks = C.month_chunks(date_from, date_to)
    region_field, region_verified, region_error = _resolve_region_field(
        session, counter_id, headers, chunks, log,
    )
    # Обязательные поля backfill: ТОЛЬКО ключ склейки; кандидаты — новые поля.
    # Никаких метрик/несовместимых полей: только visitID + поля патча.
    mandatory = [BACKFILL_JOIN_KEY]
    accepted, dropped_reasons = _negotiate_fields(
        session, counter_id, headers, chunks, mandatory, PATCH_CANDIDATE_FIELDS, log,
    )
    backfill_fields = mandatory + accepted + [region_field]
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

    available = VISIT_FIELDS_BASE + accepted + [region_field]   # полный набор после склейки base+backfill
    # backfill_dir — путь относительно data/raw/ (для transform/verify_metrika).
    backfill_dir = (Path(src_dir).relative_to(Path(paths.raw)) / BACKFILL_SUBDIR).as_posix()
    manifest = _record_manifest(paths, source_key, date_from, date_to, total_rows, extra={
        "schema_version": SCHEMA_VERSION,
        "fields": available,
        "available_fields": available,
        "backfill_fields": backfill_fields,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "patch_fields": accepted + [region_field],
        "patch_date": PATCH_DATE,
        "patch_backfill": True,
        "backfill_dir": backfill_dir,
        "backfill_rows": total_rows,
        "bot_detection_available": BOT_DETECTION_AVAILABLE,
        "region_field": region_field,
        "region_field_verified": region_verified,
        "region_field_error": region_error,
        "note": ("новые поля патча довыгружены в подкаталог backfill/ "
                 "(visits_backfill_*), старые visits_* не изменялись (неизменность "
                 "слоя raw). Склейка по ym:s:visitID — в transform."),
    })
    log(f"{SOURCE}: довыгрузка готова — {parts_written} backfill-частей, {total_rows} строк; "
        f"принято полей {len(accepted)}, отклонено {len(dropped_reasons)} {sorted(dropped_reasons)}; "
        f"region_field={region_field} verified={region_verified}")

    return {
        "source": SOURCE,
        "rows": total_rows,
        "backfill_rows": total_rows,
        "parts": parts_written,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "patch_backfill": True,
        "patch_fields": accepted + [region_field],
        "backfill_fields": backfill_fields,
        "available_fields": available,
        "dropped_fields": sorted(dropped_reasons),
        "dropped_reasons": dropped_reasons,
        "bot_detection_available": BOT_DETECTION_AVAILABLE,
        "region_field": region_field,
        "region_field_verified": region_verified,
        "region_field_error": region_error,
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
    # идут только новые поля-кандидаты патча + результат _resolve_region_field
    # (regionArea либо откат regionCity — не статический кандидат негоциации).
    allowed = set(PATCH_CANDIDATE_FIELDS) | {REGION_FIELD_PRIMARY, REGION_FIELD_FALLBACK}
    stray = [f for f in new_fields if f not in allowed]
    if stray:
        raise C.SourceUnavailable(SOURCE, f"backfill: посторонние поля в наборе: {stray}")


def _already_extracted(
    existing: dict[str, Any] | None, date_from: Any, date_to: Any, src_dir: Path,
) -> bool:
    """True — данные за это окно уже выгружены с ТЕКУЩЕЙ схемой полей и лежат на диске.

    Сверяем ``schema_version``, а не просто наличие ``patch_date``: манифест
    прошлого патча (напр. visits-v2) тоже имеет непустой patch_date, но не
    содержит полей текущего патча (visits-v3) — такое окно не «уже выгружено».
    """
    if not existing:
        return False
    if existing.get("date_from") != C.fmt(date_from) or existing.get("date_to") != C.fmt(date_to):
        return False
    if existing.get("schema_version") != SCHEMA_VERSION:
        return False
    # Файлы должны реально существовать на диске.
    files = [p for p in Path(src_dir).glob("visits_*.csv.gz")
             if not p.name.startswith("visits_backfill_")]
    return bool(files)


def _should_backfill(
    flag: bool | None, existing: dict[str, Any] | None,
    date_from: Any, date_to: Any, src_dir: Path,
) -> bool:
    """Нужна ли довыгрузка новых полей вместо полной перезагрузки.

    Явный ``flag`` (True/False) уважается. В авто-режиме (None) довыгрузка —
    когда то же окно уже выгружено СО СТАРОЙ схемой (в манифесте
    ``schema_version`` отличается от текущей ``SCHEMA_VERSION`` — это ловит и
    допатчевые данные без patch_date вовсе, и данные под предыдущим патчем,
    напр. visits-v2 при текущей visits-v3), а на диске лежат старые visits_*
    (верхнего уровня, не backfill).
    """
    if flag is not None:
        return flag
    if not existing:
        return False
    if existing.get("date_from") != C.fmt(date_from) or existing.get("date_to") != C.fmt(date_to):
        return False
    if existing.get("schema_version") == SCHEMA_VERSION:   # выгрузка уже полная
        return False
    old_files = [p for p in Path(src_dir).glob("visits_*.csv.gz")
                 if not p.name.startswith("visits_backfill_")]
    return bool(old_files)


# ── Регион визита: regionArea с откатом на regionCity (2A-patch) ───────────
def _resolve_region_field(
    session, counter_id, headers, chunks, log,
) -> tuple[str, bool, str | None]:
    """Определить фактическое имя поля региона визита одним запросом evaluate.

    Возвращает ``(field, verified, error)``:
        field    — что реально запрашивать (regionArea либо откат на regionCity);
        verified — True, если API в ЭТОМ прогоне принял regionArea;
        error    — текст ошибки API, если пришлось откатиться (иначе None).

    Никакого дальнейшего перебора имён — только этот один запрос и, при
    отказе, единственный известный откат.
    """
    if not chunks:
        return REGION_FIELD_FALLBACK, False, None
    d1, d2 = C.fmt(chunks[0][0]), C.fmt(chunks[0][1])
    ok, err = _evaluate_fields(
        session, counter_id, headers, d1, d2, VISIT_FIELDS_BASE + [REGION_FIELD_PRIMARY],
    )
    if ok:
        return REGION_FIELD_PRIMARY, True, None
    reason = _reason_text(err)
    log(f"{SOURCE}: {REGION_FIELD_PRIMARY} отклонено API ({reason}) — "
        f"откат на {REGION_FIELD_FALLBACK}")
    return REGION_FIELD_FALLBACK, False, reason


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


def _record_manifest(paths, source_key, date_from, date_to, rows, *, extra=None) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), source_key,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra=extra,
    )
