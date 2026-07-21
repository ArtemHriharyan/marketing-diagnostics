"""Экстрактор: Яндекс Wordstat — основные запросы (topRequests) и их недельная
динамика спроса (dynamics). Полная замена прежнего месячного агрегата (WS-1).

Контракт:
    Читает   — config.wordstat_seeds (маски запросов), config.sources.wordstat
               (regions, devices), config.top_n_gap / config.top_n_seasonality,
               inputs/wordstat_stopwords.yaml (см. wordstat_config.py),
               WORDSTAT_TOKEN, окно дат (primary_window из manifest intake).
    Пишет    — data/raw/wordstat/topRequests_raw/<маска>.json (сырой ответ
               topRequests как есть, по одному файлу на маску) +
               data/raw/wordstat/wordstat_weekly.parquet (недельные точки
               спроса по каждой отобранной фразе) +
               data/raw/wordstat/wordstat_core_queries.parquet (сами отобранные
               фразы с объяснением, откуда каждая взята) + manifest.json
               (canonical_tables: [wordstat] — будущая canonical-таблица,
               transform для неё пока не реализован, см. build_canonical.py).
    Деградация — опционален; без него S05/S06/S07/S26 уходят в degradation.
    LLM      — не используется.

Механика (Wordstat API, https://api.wordstat.yandex.net, см. WS-1):
    Авторизация — Authorization: Bearer <WORDSTAT_TOKEN> (не легаси v4 токен
    Директа — это отдельный OAuth-токен именно для Wordstat API).

    1. Для каждой seed-маски — POST /v1/topRequests
       {"phrase": <маска>, "regions": [...], "devices": [...]} ->
       {"topRequests": [{"phrase": ..., "count": ...}, ...]}.
       Сырой ответ сохраняется как есть (топ-запросы за последние 30 дней —
       так работает сам метод API, окно не настраивается).

    2. Из ответа строятся два подсписка (см. _merge_gap_candidates /
       _merge_seasonality_candidates):
         a) gap_candidates — топ top_n_gap после исключения junk И general
            (wordstat_config.classify) — назначение S07 (спрос без посадочной).
         b) seasonality_candidates — сама seed-маска (безусловно, даже если её
            нет в ответе topRequests) + топ top_n_seasonality по чистой частоте
            с исключением только junk (general остаётся) — назначение
            S05/S06 (сезонность) и S26 (гео-спрос).

    3. target_queries — дедуп gap_candidates + seasonality_candidates по
       normalize(phrase) (wordstat_config.normalize). Каждая запись несёт
       purpose (["gap"] | ["seasonality"] | ["gap","seasonality"]) и scope
       ("junk" | "general" | "gap-specific" — junk на практике сюда не
       попадает, т.к. вырезается из обоих подсписков раньше; "gap-specific" —
       фраза, для которой classify() не сработал ни на junk, ни на general).

    4. Для каждой уникальной фразы target_queries — ОДИН вызов POST
       /v1/dynamics {"phrase":.., "regions":[...], "devices":[...],
       "period":"weekly", "fromDate":.., "toDate":..} -> {"dynamics":
       [{"date":.., "count":.., "share":..}, ...]}. Полный диапазон окна одним
       вызовом — недельная бинуемость приходит от API, цикла по неделям нет.

RATE LIMITS / КВОТА (жёсткие, но размер квоты не документирован заранее):
    Транспорт (сеть, 5xx кроме 503, 429) идёт через общий C.http_request с
    экспоненциальным бэкоффом — тот же механизм, что у Директа. HTTP 503 —
    отдельный код: превышена дневная квота Wordstat API ("Service unavailable,
    try again later" по документации метода). Это НЕ временный сетевой сбой,
    поэтому 503 обрабатывается отдельным внешним циклом (_post): ждём и
    повторяем с тем же экспоненциальным бэкоффом, а факт хотя бы одного 503 за
    прогон и итоговое число успешных вызовов API фиксируются в manifest как
    wordstat_quota_hit / wordstat_calls_made. Размер квоты нигде не
    хардкодится — так фактический лимит проявляется только по итогам первого
    боевого прогона на реальном клиенте, а не предполагается заранее.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C
from . import wordstat_config as WC

SCRIPT_VERSION = "0.3.0"
SOURCE = "wordstat"
# Будущая canonical-таблица (transform для wordstat пока не реализован, см.
# build_canonical.py) — НЕ путать с именами сырых parquet-файлов ниже, у них
# своя раскладка (wordstat_weekly / wordstat_core_queries).
CANONICAL_TABLES = ["wordstat"]

API_BASE_URL = "https://api.wordstat.yandex.net"
TOP_REQUESTS_PATH = "/v1/topRequests"
DYNAMICS_PATH = "/v1/dynamics"
REGIONS_TREE_PATH = "/v1/getRegionsTree"  # не тратит дневную квоту — годится для ping

DEFAULT_TOP_N_GAP = 15
DEFAULT_TOP_N_SEASONALITY = 10
DEFAULT_DEVICES = ["all"]

# Внешний ретрай именно для 503 (квота) — отдельно от общего C.http_request,
# т.к. 503 квоты не «временный сбой сети», а осмысленное ожидание окна квоты.
QUOTA_RETRY_MAX_ATTEMPTS = 5

WEEKLY_FIELDS = ["phrase", "normalized_phrase", "date", "count", "share", "purpose"]
CORE_FIELDS = [
    "phrase", "normalized_phrase", "seed_mask", "purpose", "scope", "top_requests_count",
]


def _auth_headers(token: str) -> dict[str, str]:
    """Заголовок авторизации Wordstat API. Токен нигде не логируется."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости WORDSTAT_TOKEN через getRegionsTree (без квоты)."""
    import requests

    try:
        token = C.get_token(env, "WORDSTAT_TOKEN", SOURCE)
    except C.AuthError:
        return False
    session = requests.Session()
    try:
        resp = C.http_request(
            session, "POST", API_BASE_URL + REGIONS_TREE_PATH,
            source=SOURCE, headers=_auth_headers(token), json={}, timeout=30,
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
    top_n_gap = int(config.get("top_n_gap") or DEFAULT_TOP_N_GAP)
    top_n_seasonality = int(config.get("top_n_seasonality") or DEFAULT_TOP_N_SEASONALITY)

    token = C.get_token(env, "WORDSTAT_TOKEN", SOURCE)
    headers = _auth_headers(token)
    fmt = C.resolve_raw_format(ws_cfg)

    stopwords_path = Path(getattr(paths, "root", None) or ".") / "inputs" / "wordstat_stopwords.yaml"
    stopword_entries = WC.load_stopwords(stopwords_path)
    stopwords_empty = not stopword_entries

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    raw_top_dir = out_dir / "topRequests_raw"
    raw_top_dir.mkdir(parents=True, exist_ok=True)

    (date_from, date_to), _compare = C.resolve_windows(paths.raw, config, defaults, today=today)

    quota_state = {"hit": False, "calls_made": 0}
    log(f"{SOURCE}: масок {len(seeds)}, регионы {regions or 'вся Россия'}, устройства {devices}")

    # ── Шаги 1-3: topRequests -> target_queries (дедуп по normalize(phrase)) ──
    target_queries: dict[str, dict[str, Any]] = {}
    used_slugs: set[str] = set()
    for mask in seeds:
        raw = _call_top_requests(session, headers, mask, regions, devices, sleeper, log, quota_state)
        _dump_json(_raw_dump_path(raw_top_dir, mask, used_slugs), raw)
        items = list(raw.get("topRequests") or [])
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
            session, headers, entry["phrase"], regions, devices,
            date_from, date_to, sleeper, log, quota_state,
        )
        for point in list(dyn.get("dynamics") or []):
            weekly_rows.append({
                "phrase": entry["phrase"],
                "normalized_phrase": norm_phrase,
                "date": point.get("date"),
                "count": point.get("count"),
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
        paths, regions, devices, date_from, date_to,
        rows=len(weekly_rows), core_rows=len(core_rows),
        quota_state=quota_state, stopwords_empty=stopwords_empty,
    )
    log(
        f"{SOURCE}: готово — {len(target_queries)} фраз, {len(weekly_rows)} недельных точек, "
        f"вызовов API: {quota_state['calls_made']}, квота исчерпывалась: {quota_state['hit']}"
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


# ── Отбор target_queries (WS-1 п.2-3) ────────────────────────────────────────
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
    count = item.get("count")
    try:
        count = int(count) if count is not None else None
    except (TypeError, ValueError):
        count = None

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


# ── Wordstat API (topRequests / dynamics) ────────────────────────────────────
def _call_top_requests(session, headers, phrase, regions, devices, sleeper, log, quota_state):
    body: dict[str, Any] = {"phrase": phrase, "devices": list(devices)}
    if regions:
        body["regions"] = list(regions)
    return _post(session, headers, TOP_REQUESTS_PATH, body, sleeper, log, quota_state)


def _call_dynamics(session, headers, phrase, regions, devices, date_from, date_to,
                    sleeper, log, quota_state):
    body: dict[str, Any] = {
        "phrase": phrase,
        "devices": list(devices),
        "period": "weekly",
        "fromDate": C.fmt(date_from),
        "toDate": C.fmt(date_to),
    }
    if regions:
        body["regions"] = list(regions)
    return _post(session, headers, DYNAMICS_PATH, body, sleeper, log, quota_state)


def _post(session, headers, path, body, sleeper, log, quota_state) -> dict[str, Any]:
    """POST с ретраями. 5xx (кроме 503)/сеть/429 — через C.http_request (как у
    Директа). 503 — квота: отдельный внешний цикл с бэкоффом, учёт в quota_state
    (manifest: wordstat_quota_hit / wordstat_calls_made)."""
    url = API_BASE_URL + path
    for attempt in range(1, QUOTA_RETRY_MAX_ATTEMPTS + 1):
        resp = C.http_request(
            session, "POST", url,
            source=SOURCE, headers=headers, json=body, timeout=60,
            retry_statuses=(500, 502, 504),  # 503 обрабатываем сами ниже
            sleeper=sleeper,
        )
        if getattr(resp, "status_code", None) == 503:
            quota_state["hit"] = True
            if attempt >= QUOTA_RETRY_MAX_ATTEMPTS:
                raise C.SourceUnavailable(
                    SOURCE,
                    f"{path}: квота Wordstat (503) не отпустила за "
                    f"{QUOTA_RETRY_MAX_ATTEMPTS} попыток",
                )
            log(f"{SOURCE}: квота исчерпана (503) на {path} — пауза и повтор "
                f"({attempt}/{QUOTA_RETRY_MAX_ATTEMPTS})")
            sleeper(C.backoff_delay(attempt))
            continue
        C.ensure_ok(resp, SOURCE, path)
        quota_state["calls_made"] += 1
        return resp.json() or {}
    raise C.SourceUnavailable(SOURCE, f"{path}: исчерпаны попытки")  # pragma: no cover


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
    paths, regions, devices, date_from, date_to, *,
    rows: int, core_rows: int, quota_state: dict[str, Any], stopwords_empty: bool,
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
            "core_query_rows": core_rows,
            "wordstat_quota_hit": quota_state["hit"],
            "wordstat_calls_made": quota_state["calls_made"],
            "wordstat_stopwords_empty": stopwords_empty,
        },
    )
