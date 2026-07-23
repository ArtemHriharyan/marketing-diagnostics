"""Экстрактор: Яндекс Wordstat — основные запросы (topRequests) и их недельная
динамика спроса (dynamics). Транспорт — Yandex Cloud Search API v2 (WS-2).

Контракт:
    Читает   — config.wordstat_seeds (маски запросов), config.sources.wordstat
               (regions, devices, folder_id), config.top_n_gap / config.top_n_seasonality,
               inputs/wordstat_stopwords.yaml (см. wordstat_config.py),
               WORDSTAT_API_KEY, окно дат (primary_window из manifest intake).
    Пишет    — data/raw/wordstat/topRequests_raw/<маска>.json (сырой ответ
               GetTop как есть, по одному файлу на маску) +
               data/raw/wordstat/wordstat_weekly.parquet (недельные точки
               спроса по каждой отобранной фразе) +
               data/raw/wordstat/wordstat_core_queries.parquet (сами отобранные
               фразы с объяснением, откуда каждая взята) + manifest.json
               (canonical_tables: [wordstat] — будущая canonical-таблица,
               transform для неё пока не реализован, см. build_canonical.py).
    Деградация — опционален; без него S05/S06/S07/S26 уходят в degradation.
    LLM      — не используется.

Модель данных (target_queries, purpose, scope) и конфигурация (wordstat_seeds,
top_n_gap/top_n_seasonality, wordstat_stopwords.yaml) сохранены из версии 0.3.0
(WS-1) без изменений — эта задача (WS-2, task_id
wordstat-transport-cloud-v2-migration) меняет только транспорт.

ПРИЧИНА МИГРАЦИИ: api.wordstat.yandex.net (REST v1, Bearer-токен) отключён
Яндексом безвозвратно — подтверждено поддержкой Яндекса, это не временная
проблема сертификата. Старый транспорт полностью удалён (мёртвый код никто не
держит, в отличие от легаси Direct v4, который ещё формально жив).

Механика (Yandex Cloud Search API v2 — WordstatService, см.
https://searchapi.api.cloud.yandex.net):
    Авторизация — Authorization: Api-Key <WORDSTAT_API_KEY> (сервисный ключ
    Yandex Cloud; НЕ путать со старым Bearer-токеном Wordstat v1 — это другой
    секрет). ``folderId`` обязателен в теле КАЖДОГО запроса (config.sources.
    wordstat.folder_id) — без него API отвечает INVALID_ARGUMENT.

    1. Для каждой seed-маски — POST /v2/wordstat/topRequests
       {"phrase":.., "numPhrases":.., "regions":[<строки>], "devices":[<enum>],
       "folderId":..} -> {"totalCount":.., "results":[{"phrase":..,"count":..}],
       "associations":[...]}. Используется только "results" (аналог topRequests
       из WS-1); "associations" (семантически похожие запросы) в модель не
       включаются — это расширило бы candidate-пул за пределы контракта WS-1.
       int64-поля (count, totalCount) сериализуются протобуфом как JSON-строки
       — конвертируются в int в _add_candidate (уже устойчив к строкам).
       Сырой ответ сохраняется как есть.

    2-3. Отбор gap_candidates / seasonality_candidates / target_queries —
       БЕЗ ИЗМЕНЕНИЙ, см. wordstat_config.classify() и _merge_*_candidates ниже
       (WS-1 п.2-3).

    4. Для каждой уникальной фразы target_queries — ОДИН вызов POST
       /v2/wordstat/dynamics {"phrase":.., "period":"PERIOD_WEEKLY",
       "fromDate":.., "toDate":.., "regions":[...], "devices":[...],
       "folderId":..} -> {"results":[{"date":.., "count":.., "share":..}]}.
       fromDate/toDate — google.protobuf.Timestamp, сериализуется как RFC3339
       ("2026-01-01T00:00:00Z"); count — тоже JSON-строка. Ответ приводится к
       прежнему контракту (date -> "YYYY-MM-DD", count -> int) в _call_dynamics.

       ``toDate`` ОБЯЗАН приходиться на воскресенье — иначе API отвечает 400
       InvalidArgument ("the to field value should be Sunday", подтверждено
       2026-07-22). date_to окна (primary_window/config.data_window) — конец
       календарного месяца и на воскресенье, как правило, не попадает, поэтому
       перед вызовом dynamics конец периода округляется ВВЕРХ до ближайшего
       воскресенья (см. _align_to_sunday) — вперёд, а не назад, чтобы не
       обрезать данные за последнюю неполную неделю запрошенного окна.
       date_from/date_to самого окна (используются topRequests-циклом,
       manifest и остальным пайплайном) не меняются — округление касается
       только значения, отправляемого в тело запроса dynamics.

    ОПЕРАТОРЫ МАСОК: v2 API операторы Wordstat (!слово, +слово, [слово],
    сравнение нескольких фраз через |) НЕ поддерживает — подтверждено
    документацией и независимым источником при миграции (2026-07-22). Сейчас
    wordstat_seeds по факту не использует эти операторы (обычные фразы), но
    если в будущем кто-то добавит маску с оператором — API примет её как
    буквальный текст фразы, без интерпретации оператора. Явно фиксируем здесь,
    чтобы не потерялось молча.

RATE LIMITS: Транспорт (сеть, 5xx, 429) идёт через общий C.http_request с
    экспоненциальным бэкоффом — тот же механизм, что у Директа (429 уважает
    Retry-After). Отдельного 503-цикла квоты, как в WS-1 (v1 API), больше нет:
    он был специфичен для документированного поведения старого API ("503 =
    дневная квота"); для v2 такое поведение нигде не подтверждено, ограничения
    — стандартный RPS-рейтлимит Yandex Cloud, уже покрытый C.http_request.
    Число успешных вызовов API по-прежнему фиксируется в manifest
    (wordstat_calls_made) для прозрачности. Тарификация Wordstat в составе
    Search API на момент миграции (2026-07-22) — бесплатная (см. описание
    задачи); отдельного учёта стоимости в manifest не ведём, пока это не
    изменится.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from . import _common as C
from . import wordstat_config as WC

SCRIPT_VERSION = "0.4.0"
SOURCE = "wordstat"
# Будущая canonical-таблица (transform для wordstat пока не реализован, см.
# build_canonical.py) — НЕ путать с именами сырых parquet-файлов ниже, у них
# своя раскладка (wordstat_weekly / wordstat_core_queries).
CANONICAL_TABLES = ["wordstat"]

API_VERSION_USED = "cloud_search_v2"
MIGRATION_REASON = (
    "api.wordstat.yandex.net (REST v1) отключён Яндексом безвозвратно — "
    "подтверждено поддержкой Яндекса, не временная проблема сертификата "
    "(task_id wordstat-transport-cloud-v2-migration, 2026-07-22)."
)

API_BASE_URL = "https://searchapi.api.cloud.yandex.net"
TOP_REQUESTS_PATH = "/v2/wordstat/topRequests"
DYNAMICS_PATH = "/v2/wordstat/dynamics"
REGIONS_TREE_PATH = "/v2/wordstat/getRegionsTree"  # бесплатен — годится для ping

DEFAULT_TOP_N_GAP = 15
DEFAULT_TOP_N_SEASONALITY = 10
DEFAULT_DEVICES = ["all"]

# GetTopRequest.num_phrases: 1-2000 (проверено по proto WordstatService). Берём
# максимум, чтобы сохранить прежний охват topRequests (WS-1 не ограничивал
# кандидатов на входе — фильтрация top_n происходит уже после ответа API).
TOP_REQUESTS_NUM_PHRASES = 2000

PERIOD_WEEKLY = "PERIOD_WEEKLY"

WEEKLY_FIELDS = ["phrase", "normalized_phrase", "date", "count", "share", "purpose"]
CORE_FIELDS = [
    "phrase", "normalized_phrase", "seed_mask", "purpose", "scope", "top_requests_count",
]


def _auth_headers(api_key: str) -> dict[str, str]:
    """Заголовок авторизации Yandex Cloud Search API. Ключ нигде не логируется."""
    return {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости WORDSTAT_API_KEY через getRegionsTree (бесплатно)."""
    import requests

    try:
        api_key = C.get_token(env, "WORDSTAT_API_KEY", SOURCE)
    except C.AuthError:
        return False
    ws_cfg = (config.get("sources") or {}).get("wordstat") or {}
    try:
        folder_id = _folder_id(ws_cfg)
    except C.SourceUnavailable:
        return False
    session = requests.Session()
    try:
        resp = C.http_request(
            session, "POST", API_BASE_URL + REGIONS_TREE_PATH,
            source=SOURCE, headers=_auth_headers(api_key),
            json={"folderId": folder_id}, timeout=30,
        )
        C.ensure_ok(resp, SOURCE, REGIONS_TREE_PATH)
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
    """Выгрузить topRequests + недельную dynamics по seed-маскам в data/raw/wordstat/."""
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    seeds = [s for s in (config.get("wordstat_seeds") or []) if str(s).strip()]
    if not seeds:
        raise C.SourceUnavailable(
            SOURCE, "не задан wordstat_seeds в config.yaml (список масок запросов)"
        )

    ws_cfg = (config.get("sources") or {}).get("wordstat") or {}
    regions = _region_ids(ws_cfg)
    devices = _device_list(ws_cfg)
    folder_id = _folder_id(ws_cfg)
    top_n_gap = int(config.get("top_n_gap") or DEFAULT_TOP_N_GAP)
    top_n_seasonality = int(config.get("top_n_seasonality") or DEFAULT_TOP_N_SEASONALITY)

    api_key = C.get_token(env, "WORDSTAT_API_KEY", SOURCE)
    headers = _auth_headers(api_key)
    fmt = C.resolve_raw_format(ws_cfg)

    stopwords_path = Path(getattr(paths, "root", None) or ".") / "inputs" / "wordstat_stopwords.yaml"
    stopword_entries = WC.load_stopwords(stopwords_path)
    stopwords_empty = not stopword_entries

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    raw_top_dir = out_dir / "topRequests_raw"
    raw_top_dir.mkdir(parents=True, exist_ok=True)

    (date_from, date_to), _compare = C.resolve_windows(paths.raw, config, defaults, today=today)
    dynamics_date_to = _align_to_sunday(date_to)

    calls_made = {"count": 0}
    log(f"{SOURCE}: масок {len(seeds)}, регионы {regions or 'вся Россия'}, устройства {devices}")

    # ── Шаги 1-3: topRequests -> target_queries (дедуп по normalize(phrase)) ──
    target_queries: dict[str, dict[str, Any]] = {}
    used_slugs: set[str] = set()
    for mask in seeds:
        raw = _call_top_requests(
            session, headers, mask, regions, devices, folder_id, sleeper, calls_made,
        )
        _dump_json(_raw_dump_path(raw_top_dir, mask, used_slugs), raw)
        items = list(raw.get("results") or [])
        _merge_gap_candidates(target_queries, mask, items, top_n_gap, stopword_entries)
        _merge_seasonality_candidates(target_queries, mask, items, top_n_seasonality, stopword_entries)

    if not target_queries:
        raise C.SourceUnavailable(SOURCE, "topRequests не вернул ни одной фразы ни по одной маске")

    # ── Шаг 4: dynamics — один вызов на фразу, полный диапазон, weekly ──────
    weekly_rows: list[dict[str, Any]] = []
    core_rows: list[dict[str, Any]] = []
    for norm_phrase, entry in target_queries.items():
        purpose = sorted(entry["purpose"])
        dyn = _call_dynamics(
            session, headers, entry["phrase"], regions, devices, folder_id,
            date_from, dynamics_date_to, sleeper, calls_made,
        )
        for point in list(dyn.get("results") or []):
            weekly_rows.append({
                "phrase": entry["phrase"],
                "normalized_phrase": norm_phrase,
                "date": _timestamp_to_date(point.get("date")),
                "count": _to_int(point.get("count")),
                "share": point.get("share"),
                "purpose": purpose,
            })
        core_rows.append({
            "phrase": entry["phrase"],
            "normalized_phrase": norm_phrase,
            "seed_mask": entry["seed_mask"],
            "purpose": purpose,
            "scope": entry["scope"],
            "top_requests_count": entry.get("count"),
        })

    C.write_table(out_dir / "wordstat_weekly", weekly_rows, WEEKLY_FIELDS, fmt)
    C.write_table(out_dir / "wordstat_core_queries", core_rows, CORE_FIELDS, fmt)

    manifest = _record_manifest(
        paths, regions, devices, folder_id, date_from, date_to,
        rows=len(weekly_rows), core_rows=len(core_rows),
        calls_made=calls_made["count"], stopwords_empty=stopwords_empty,
        dynamics_date_to=C.fmt(dynamics_date_to),
    )
    log(
        f"{SOURCE}: готово — {len(target_queries)} фраз, {len(weekly_rows)} недельных точек, "
        f"вызовов API: {calls_made['count']}"
    )

    return {
        "source": SOURCE,
        "rows": len(weekly_rows),
        "target_queries": len(target_queries),
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Конфиг ──────────────────────────────────────────────────────────────────
def _region_ids(ws_cfg: dict[str, Any]) -> list[int]:
    """GeoID регионов из config.sources.wordstat.regions; мусор отбрасываем."""
    ids: list[int] = []
    for value in ws_cfg.get("regions") or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _device_list(ws_cfg: dict[str, Any]) -> list[str]:
    """Устройства из config.sources.wordstat.devices; пусто -> ["all"]."""
    devices = [str(d).strip() for d in (ws_cfg.get("devices") or []) if str(d).strip()]
    return devices or list(DEFAULT_DEVICES)


def _folder_id(ws_cfg: dict[str, Any]) -> str:
    """folder_id из config.sources.wordstat.folder_id — обязателен в каждом
    запросе Yandex Cloud Search API v2 (без него API отвечает INVALID_ARGUMENT).
    """
    folder_id = str(ws_cfg.get("folder_id") or "").strip()
    if not folder_id:
        raise C.SourceUnavailable(
            SOURCE,
            "не задан sources.wordstat.folder_id в config.yaml — обязателен "
            "для Yandex Cloud Search API v2 (folderId в теле каждого запроса)",
        )
    return folder_id


def _device_enum(device: str) -> str:
    """Значение конфига (all|desktop|phone|tablet) -> имя enum Device в proto
    (DEVICE_ALL|DEVICE_DESKTOP|DEVICE_PHONE|DEVICE_TABLET)."""
    return f"DEVICE_{device.strip().upper()}"


# ── Отбор target_queries (WS-1 п.2-3, без изменений) ─────────────────────────
def _count_key(item: dict[str, Any]) -> int:
    """Ключ сортировки по убыванию частоты; битые/отсутствующие count — в конец."""
    try:
        return int(item.get("count"))
    except (TypeError, ValueError):
        return -1


def _add_candidate(
    target: dict[str, dict[str, Any]],
    seed_mask: str,
    item: dict[str, Any],
    *,
    purpose: str,
    stopword_entries: list[dict[str, Any]],
) -> None:
    phrase = str(item.get("phrase") or "").strip()
    norm = WC.normalize(phrase)
    if not norm:
        return
    count = _to_int(item.get("count"))

    cls = WC.classify(phrase, stopword_entries)
    scope = cls if cls in ("junk", "general") else "gap-specific"

    entry = target.get(norm)
    if entry is None:
        target[norm] = {
            "phrase": phrase,
            "seed_mask": seed_mask,
            "purpose": {purpose},
            "scope": scope,
            "count": count,
        }
        return
    entry["purpose"].add(purpose)
    if count is not None and (entry.get("count") is None or count > entry["count"]):
        entry["count"] = count


def _merge_gap_candidates(
    target: dict[str, dict[str, Any]],
    seed_mask: str,
    items: list[dict[str, Any]],
    top_n: int,
    stopword_entries: list[dict[str, Any]],
) -> None:
    """gap_candidates: топ top_n после исключения junk И general (S07)."""
    ranked = sorted(items, key=_count_key, reverse=True)
    kept = 0
    for it in ranked:
        if kept >= top_n:
            break
        phrase = str(it.get("phrase") or "").strip()
        if not phrase or WC.classify(phrase, stopword_entries) in ("junk", "general"):
            continue
        _add_candidate(target, seed_mask, it, purpose="gap", stopword_entries=stopword_entries)
        kept += 1


def _merge_seasonality_candidates(
    target: dict[str, dict[str, Any]],
    seed_mask: str,
    items: list[dict[str, Any]],
    top_n: int,
    stopword_entries: list[dict[str, Any]],
) -> None:
    """seasonality_candidates: seed-маска безусловно + топ top_n с фильтром только junk."""
    _add_candidate(
        target, seed_mask, {"phrase": seed_mask, "count": None},
        purpose="seasonality", stopword_entries=stopword_entries,
    )
    ranked = sorted(items, key=_count_key, reverse=True)
    kept = 0
    for it in ranked:
        if kept >= top_n:
            break
        phrase = str(it.get("phrase") or "").strip()
        if not phrase or WC.classify(phrase, stopword_entries) == "junk":
            continue
        _add_candidate(target, seed_mask, it, purpose="seasonality", stopword_entries=stopword_entries)
        kept += 1


# ── Yandex Cloud Search API v2 — WordstatService (GetTop / GetDynamics) ──────
def _call_top_requests(session, headers, phrase, regions, devices, folder_id, sleeper, calls_made):
    body: dict[str, Any] = {
        "phrase": phrase,
        "numPhrases": TOP_REQUESTS_NUM_PHRASES,
        "devices": [_device_enum(d) for d in devices],
        "folderId": folder_id,
    }
    if regions:
        body["regions"] = [str(r) for r in regions]
    return _post(session, headers, TOP_REQUESTS_PATH, body, sleeper, calls_made)


def _call_dynamics(session, headers, phrase, regions, devices, folder_id, date_from, date_to,
                    sleeper, calls_made):
    body: dict[str, Any] = {
        "phrase": phrase,
        "period": PERIOD_WEEKLY,
        "fromDate": _rfc3339(date_from),
        "toDate": _rfc3339(date_to),
        "devices": [_device_enum(d) for d in devices],
        "folderId": folder_id,
    }
    if regions:
        body["regions"] = [str(r) for r in regions]
    return _post(session, headers, DYNAMICS_PATH, body, sleeper, calls_made)


def _post(session, headers, path, body, sleeper, calls_made) -> dict[str, Any]:
    """POST с ретраями через общий C.http_request (5xx/429/сеть — как у Директа).

    Отдельного цикла для квоты, в отличие от WS-1 (v1 API, 503), больше нет —
    см. докстринг модуля."""
    url = API_BASE_URL + path
    resp = C.http_request(
        session, "POST", url,
        source=SOURCE, headers=headers, json=body, timeout=60, sleeper=sleeper,
    )
    C.ensure_ok(resp, SOURCE, path)
    calls_made["count"] += 1
    return resp.json() or {}


# ── Выравнивание периода dynamics под требования API ─────────────────────────
def _align_to_sunday(d: date) -> date:
    """Ближайшее воскресенье НЕ РАНЬШЕ ``d`` (округление вперёд).

    GetDynamicsRequest (PERIOD_WEEKLY) требует ``toDate`` строго на
    воскресенье (400 InvalidArgument иначе). Округляем вперёд, а не назад,
    чтобы не потерять данные за последнюю неполную неделю окна — ``date_from``
    самого окна не трогаем, расширяется только правая граница dynamics-запроса.
    """
    # date.weekday(): понедельник=0 … воскресенье=6.
    days_until_sunday = (6 - d.weekday()) % 7
    return d + timedelta(days=days_until_sunday)


# ── Преобразование protobuf JSON (Timestamp/int64-строки) в контракт WS-1 ───
def _rfc3339(d) -> str:
    """date -> RFC3339 UTC-строка для google.protobuf.Timestamp ("...T00:00:00Z")."""
    return f"{C.fmt(d)}T00:00:00Z"


def _timestamp_to_date(value: Any) -> str | None:
    """RFC3339-строка ("2026-01-05T00:00:00Z") -> "YYYY-MM-DD" (контракт WS-1)."""
    if not value:
        return None
    return str(value).split("T", 1)[0]


def _to_int(value: Any) -> int | None:
    """int64 protobuf-поле (JSON-строка или число) -> int | None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Файлы ────────────────────────────────────────────────────────────────────
def _slug(phrase: str) -> str:
    norm = WC.normalize(phrase)
    slug = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "_", norm).strip("_")
    return slug or "mask"


def _raw_dump_path(base_dir: Path, mask: str, used: set[str]) -> Path:
    """Файл сырого topRequests-ответа для маски; коллизии слагов — суффиксом."""
    slug = _slug(mask)
    candidate = slug
    i = 2
    while candidate in used:
        candidate = f"{slug}_{i}"
        i += 1
    used.add(candidate)
    return base_dir / f"{candidate}.json"


def _dump_json(path: Path, obj: Any) -> None:
    import json

    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _record_manifest(
    paths, regions, devices, folder_id, date_from, date_to, *,
    rows: int, core_rows: int, calls_made: int, stopwords_empty: bool,
    dynamics_date_to: str,
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "regions": regions,
            "devices": devices,
            "folder_id": folder_id,
            "core_query_rows": core_rows,
            "wordstat_calls_made": calls_made,
            "wordstat_stopwords_empty": stopwords_empty,
            "api_version_used": API_VERSION_USED,
            "migration_reason": MIGRATION_REASON,
            # date_to окна не всегда воскресенье (см. _align_to_sunday) —
            # фиксируем фактическую дату, отправленную в dynamics.toDate.
            "wordstat_dynamics_date_to": dynamics_date_to,
        },
    )
