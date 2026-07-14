"""Сборка канонических таблиц из сырых выгрузок.

Контракт:
    Читает   — data/raw/<source>/ (все выгруженные источники) + manifest.json,
               config.yaml клиента (маппинг целей, brand_terms, costs_manual),
               config/defaults.yaml (окно, пороги).
    Пишет    — data/canonical/*.parquet (pyarrow), по одной таблице на сущность:
                 visits.parquet          — визиты с source/campaign/goal/segment
                 costs.parquet           — расходы (Директ + costs_manual)
                 direct_queries.parquet  — поисковые запросы Директа
                 campaign_strategies.parquet — стратегии кампаний Директа
                 seo_queries.parquet     — запросы Вебмастер + GSC
                 crm.parquet             — сделки/лиды
               и data/canonical/manifest.json — какие таблицы построены и
               оговорки к ним (сейчас единственная — utm_uncertain).
    Инварианты — не читает и не перезаписывает слои metrics/findings/report;
                 перезапись своего слоя целиком допустима (идемпотентность).
                 Суммы — float рубли, проценты — доли. UTF-8, БЕЗ LLM.

Строится ТОЛЬКО то, для чего есть сырьё: доступность источника определяется
по data/raw/manifest.json (src.pipeline.manifest), а не по client config.yaml
(в конфиге источник может быть заявлен, но фактически не выгружен).
Единственное исключение — costs.parquet: он может быть построен целиком из
config.costs_manual (ручные фиксы, разворачиваемые в дневные строки), даже
если Директ не подключён — так CPA считается единообразно для клиентов
без Директа (SEO-only).

client_answers и webvisor_findings НЕ трансформируются — это источники,
которые compute/analyze читают напрямую из inputs/ клиента; их доступность
учитывает src.pipeline.degradation, а не этот модуль.

wordstat.parquet вне контракта этой задачи (схема не задана) — сырьё
data/raw/wordstat/ пока не трансформируется.

Каноническая схема — единственный контракт между transform и compute.

── Таблица соответствия ym:s:lastsignTrafficSource -> source_group ────────
Источник значений — дименшн Logs API Яндекс.Метрики. Маппинг ниже —
детерминированное решение (см. _TRAFFIC_SOURCE_MAP), не подлежащее
интерпретации на лету:
    ad                     -> ad        (платная реклама)
    cpa_network            -> ad        (CPA-сети — тоже платный трафик)
    search_engine          -> organic   (органическая выдача поисковиков)
    direct                 -> direct    (прямые заходы)
    link                   -> referral  (переходы по внешним ссылкам)
    recommendation_system  -> referral  (Дзен и т.п. — не organic и не ad)
    internal               -> internal  (внутренние переходы по сайту)
    social_network         -> social    (социальные сети)
    messenger              -> messenger (мессенджеры)
    email                  -> other     (почтовые рассылки)
    <всё остальное/пусто>  -> other

── Правило UTM-порога (source_final) ───────────────────────────────────────
Среди визитов с source_group=="ad" считаем долю с пустым/"не определено"
utm_source. Если доля < defaults.utm_undefined_threshold — эти визиты всё
равно относим к source_final="ad" (шум приемлем). Если доля >= порога —
эти визиты получают source_final="undefined", а в data/canonical/manifest.json
выставляется флаг utm_uncertain=true (compute обязан переносить его в выводы
как оговорку "источник части трафика не определён наверняка"). Визиты
source_group=="ad" с непустым utm_source всегда source_final="ad". Все
остальные группы: source_final == source_group.
"""

from __future__ import annotations

import calendar
import csv
import gzip
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..extract import _common as extract_common
from ..extract.metrika_logs import VISIT_FIELDS
from ..pipeline import manifest as manifest_mod

CANONICAL_MANIFEST_NAME = "manifest.json"


# ── Маппинг источника трафика (см. докстринг модуля) ───────────────────────
_TRAFFIC_SOURCE_MAP: dict[str, str] = {
    "ad": "ad",
    "cpa_network": "ad",
    "search_engine": "organic",
    "direct": "direct",
    "link": "referral",
    "recommendation_system": "referral",
    "internal": "internal",
    "social_network": "social",
    "messenger": "messenger",
    "email": "other",
}

_VALID_SOURCE_GROUPS = {
    "ad", "organic", "direct", "referral", "internal", "social",
    "messenger", "other",
}

# Значения utm_source, которые считаются "не заданными".
_UTM_UNDEFINED_TOKENS = {"", "не определено", "(not set)", "not set", "undefined", "none"}

# ym:s:deviceCategory Метрики: 1=desktop, 2=mobile, 3=tablet, 4=tv.
# Нераспознанное/пустое значение -> desktop (решение: большинство визитов
# без явной категории приходят с обычных браузеров, схема не допускает "нет").
_DEVICE_MAP: dict[str, str] = {"1": "desktop", "2": "mobile", "3": "tablet", "4": "tv"}
_DEFAULT_DEVICE = "desktop"

_TRUE_TOKENS = {"1", "true", "yes", "y", "да"}

# BiddingStrategyType Директа v5 -> optimize_for. Список — по документации
# API v5 (справочник типов стратегий); нераспознанное -> "unknown".
_STRATEGY_CLICKS = {
    "HIGHEST_POSITION", "AVERAGE_CPC", "AVERAGE_CPC_PER_FILTER",
    "AVERAGE_CPC_PER_CAMPAIGN", "WB_MAXIMUM_CLICKS", "MANUAL_CPC", "MANUAL_CPM",
}
_STRATEGY_CONVERSIONS = {
    "AVERAGE_CPA", "AVERAGE_CPA_PER_FILTER", "AVERAGE_CPA_PER_CAMPAIGN",
    "AVERAGE_CPI", "AVERAGE_ROI", "AVERAGE_CPO",
    "WB_MAXIMUM_CONVERSION_RATE", "PAY_FOR_CONVERSION",
}
# Ключи типов кампаний Директа, под которыми может лежать BiddingStrategy.
_CAMPAIGN_TYPE_KEYS = (
    "TextCampaign", "DynamicTextCampaign", "SmartCampaign", "MobileAppCampaign",
    "CpmBannerCampaign", "McBannerCampaign", "ContentPromotionCampaign",
)

_VALID_COST_SOURCE_TAGS = {"direct", "agency_fee", "seo_fee", "yandex_business", "other"}

_VALID_CRM_STATUSES = {"new", "in_progress", "won", "lost"}


# ═════════════════════════════ Правила: visits ═════════════════════════════
def classify_traffic_source(raw: str | None) -> str:
    """ym:s:lastsignTrafficSource -> source_group (см. таблицу в докстринге)."""
    key = (raw or "").strip().lower()
    return _TRAFFIC_SOURCE_MAP.get(key, "other")


def map_device(raw: str | None) -> str:
    """ym:s:deviceCategory ("1".."4") -> device enum. Нераспознанное -> desktop."""
    return _DEVICE_MAP.get((raw or "").strip(), _DEFAULT_DEVICE)


def parse_bool_flag(raw: str | None) -> bool:
    """ym:s:isNewUser и подобные "1/0"-флаги -> bool. Нераспознанное -> False."""
    return (raw or "").strip().lower() in _TRUE_TOKENS


def normalize_entry_page(start_url: str | None) -> str:
    """ym:s:startURL -> нормализованный path: без домена/query/фрагмента,

    в нижнем регистре, без хвостового slash (кроме корня "/").
    """
    from urllib.parse import urlsplit

    path = urlsplit(start_url or "").path or "/"
    path = path.lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def parse_goal_ids(raw: str | None) -> list[str]:
    """ym:s:goalsID ("123,456" и т.п.) -> список id как строк.

    Разделители: запятая, точка с запятой, вертикальная черта. Дубликаты
    (если один и тот же id встретился несколько раз — визит достигал цель
    повторно) сохраняются: они нужны для form_submit_count.
    """
    if not raw or not raw.strip():
        return []
    parts = re.split(r"[,;|]", raw.strip())
    return [p.strip() for p in parts if p.strip()]


def goal_flags(goal_ids: list[str], goals_cfg: dict[str, Any]) -> dict[str, Any]:
    """goal_ids визита + config.goals -> флаги достижений визит-уровня.

    form_open/form_submit/call_click/messenger_click — хотя бы одно
    достижение соответствующей группы целей за визит. form_submit_count —
    сколько раз за визит встретился любой из form_submit_goal_ids (для
    расчёта переотработки, проверка 0.1).
    """
    form_open_ids = {str(g) for g in goals_cfg.get("form_open_goal_ids") or []}
    form_submit_ids = {str(g) for g in goals_cfg.get("form_submit_goal_ids") or []}
    call_click_ids = {str(g) for g in goals_cfg.get("call_click_goal_ids") or []}
    messenger_ids = {str(g) for g in goals_cfg.get("messenger_goal_ids") or []}
    return {
        "form_open": any(g in form_open_ids for g in goal_ids),
        "form_submit": any(g in form_submit_ids for g in goal_ids),
        "call_click": any(g in call_click_ids for g in goal_ids),
        "messenger_click": any(g in messenger_ids for g in goal_ids),
        "form_submit_count": sum(1 for g in goal_ids if g in form_submit_ids),
    }


def dedupe_visits(df: pd.DataFrame) -> pd.DataFrame:
    """1 строка на visit_id: при дублях берём последнюю по dt (dateTime)."""
    if df.empty:
        return df
    return (
        df.sort_values("dt", kind="stable")
        .drop_duplicates(subset="visit_id", keep="last")
        .reset_index(drop=True)
    )


def apply_utm_threshold(
    source_group: pd.Series, utm_source_raw: pd.Series, threshold: float,
) -> tuple[pd.Series, bool, float]:
    """Правило UTM-порога (см. докстринг модуля). Возвращает

    (source_final, utm_uncertain, frac_undefined).
    """
    utm_missing = (
        utm_source_raw.fillna("").astype(str).str.strip().str.lower().isin(_UTM_UNDEFINED_TOKENS)
    )
    is_ad = source_group == "ad"
    ad_total = int(is_ad.sum())
    undefined_ad = int((is_ad & utm_missing).sum())
    frac_undefined = (undefined_ad / ad_total) if ad_total else 0.0
    utm_uncertain = frac_undefined >= threshold

    source_final = source_group.astype(str).copy()
    ambiguous_mask = is_ad & utm_missing
    source_final.loc[ambiguous_mask] = "undefined" if utm_uncertain else "ad"
    return source_final, utm_uncertain, frac_undefined


# ═════════════════════════════ Правила: costs ═══════════════════════════════
def _iter_days(date_from: date, date_to: date) -> Iterable[date]:
    cur = date_from
    while cur <= date_to:
        yield cur
        cur += timedelta(days=1)


def expand_manual_costs(
    costs_manual: dict[str, Any], date_from: date, date_to: date,
) -> list[dict[str, Any]]:
    """config.costs_manual -> дневные строки costs (fee / дней в месяце).

    agency_fee_rub_month -> source_tag=agency_fee, seo_fee_rub_month ->
    source_tag=seo_fee, элементы costs_manual.other -> их собственный
    source_tag (валидный enum, иначе "other"). Нулевые/отсутствующие фиксы
    строк не дают. Пустой результат ([]) — если фиксов нет вовсе.
    """
    fees: list[tuple[str, str | None, float]] = []

    agency = float(costs_manual.get("agency_fee_rub_month") or 0)
    if agency > 0:
        fees.append(("agency_fee", None, agency))

    seo = float(costs_manual.get("seo_fee_rub_month") or 0)
    if seo > 0:
        fees.append(("seo_fee", None, seo))

    for item in costs_manual.get("other") or []:
        rub = float(item.get("rub_month") or 0)
        if rub <= 0:
            continue
        tag = item.get("source_tag") or "other"
        if tag not in _VALID_COST_SOURCE_TAGS:
            tag = "other"
        fees.append((tag, item.get("name"), rub))

    if not fees or date_from > date_to:
        return []

    rows: list[dict[str, Any]] = []
    for day in _iter_days(date_from, date_to):
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        for tag, name, rub_month in fees:
            rows.append({
                "date": day,
                "source_tag": tag,
                "campaign_id": None,
                "campaign_name": name,
                "cost_rub": round(rub_month / days_in_month, 6),
                "clicks": None,
                "impressions": None,
            })
    return rows


# ═════════════════════════════ Правила: direct_queries / campaign_strategies
def classify_strategy_optimize_for(strategy_type: str | None) -> str:
    """BiddingStrategyType Директа -> optimize_for (clicks|conversions|unknown)."""
    key = (strategy_type or "").strip().upper()
    if key in _STRATEGY_CLICKS:
        return "clicks"
    if key in _STRATEGY_CONVERSIONS:
        return "conversions"
    return "unknown"


def _extract_bidding_strategy_type(campaign: dict[str, Any]) -> str | None:
    """Достать BiddingStrategyType из ответа campaigns.get (структура вложена

    под одним из типов кампаний: TextCampaign.BiddingStrategy.Search/Network
    и т.п.; часть стратегий не делится на Search/Network — берём плоское
    значение).
    """
    for key in _CAMPAIGN_TYPE_KEYS:
        block = campaign.get(key)
        if not isinstance(block, dict):
            continue
        strategy = block.get("BiddingStrategy")
        if not isinstance(strategy, dict):
            continue
        for scope in ("Search", "Network"):
            scoped = strategy.get(scope)
            if isinstance(scoped, dict) and scoped.get("BiddingStrategyType"):
                return str(scoped["BiddingStrategyType"])
        if strategy.get("BiddingStrategyType"):
            return str(strategy["BiddingStrategyType"])
    return None


# ═════════════════════════════ Правила: seo_queries ═════════════════════════
def is_brand_query(query: str | None, brand_terms: Iterable[Any]) -> bool:
    """Регистронезависимое вхождение любого из config.brand_terms в query."""
    q = (query or "").lower()
    terms = [str(t).strip().lower() for t in (brand_terms or []) if str(t).strip()]
    return any(term in q for term in terms)


# ═════════════════════════════ Правила: crm ═════════════════════════════════
def normalize_crm_status(raw_status: str | None) -> str:
    """CRM status (уже пропущенный через config.crm_csv.status_map) -> enum.

    Нераспознанное/пустое/не входящее в {new,in_progress,won,lost} -> unknown.
    """
    key = (raw_status or "").strip().lower()
    return key if key in _VALID_CRM_STATUSES else "unknown"


def normalize_crm_source(raw_source: str | None) -> str:
    """CRM source -> нормализованная строка (обрезка пробелов, нижний регистр)."""
    value = (raw_source or "").strip().lower()
    return value or "unknown"


def _to_optional_bool(value: Any) -> bool | None:
    """Значение из CSV/parquet ("True"/"False"/bool/NaN/None) -> bool | None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in ("", "none", "nan"):
        return None
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if text in ("", "none", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


# ═══════════════════════════ Чтение сырья: visits ═══════════════════════════
# Слой Logs API: базовые визиты — visits_*.csv.gz верхнего уровня; довыгруженные
# патчем поля — в подкаталоге backfill/ (visits_backfill_*.csv.gz), склеиваются
# по ym:s:visitID (см. build_visits). Разделение намеренное: базовый слой
# неизменяем, backfill добавляет только новые колонки, не размножая строки.
BACKFILL_SUBDIR = "backfill"

# Соответствие полей backfill (ym:s:*) -> канонические колонки. is_robot сюда НЕ
# входит: источник visits его не отдаёт (см. manifest.dropped_fields Метрики),
# поэтому в canonical он присутствует как nullable-колонка без значения.
_BACKFILL_COLUMNS = [
    "last_traffic_source_naive",
    "browser",
    "os",
    "screen_width",
    "screen_height",
    "screen_resolution",
    "region_country",
    "region_city",
]


def _read_metrika_logs_rows(raw_dir: Path) -> list[dict[str, str]]:
    """Базовые визиты — только visits_*.csv.gz верхнего уровня (без backfill)."""
    rows: list[dict[str, str]] = []
    for path in sorted(raw_dir.glob("visits_*.csv.gz")):
        if path.name.startswith("visits_backfill_"):
            continue  # backfill читается отдельно (_read_metrika_backfill)
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            rows.extend(reader)
    return rows


def _parse_backfill_int(raw: str | None) -> int | None:
    """Числовое поле backfill (ширина/высота экрана) -> int | None (nullable)."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_backfill_row(row: dict[str, str]) -> dict[str, Any] | None:
    """Строка backfill (ym:s:* новые поля) -> канонические поля по visit_id."""
    visit_id = (row.get("ym:s:visitID") or "").strip()
    if not visit_id:
        return None

    def _s(key: str) -> str | None:
        value = (row.get(key) or "").strip()
        return value or None

    width = _parse_backfill_int(row.get("ym:s:screenWidth"))
    height = _parse_backfill_int(row.get("ym:s:screenHeight"))
    resolution = f"{width}x{height}" if (width is not None and height is not None) else None

    return {
        "visit_id": visit_id,
        # last_traffic_source_naive — ТОЛЬКО из наивной модели ym:s:lastTrafficSource;
        # source_group/source_final (last-significant) этим НЕ трогаются.
        "last_traffic_source_naive": _s("ym:s:lastTrafficSource"),
        "browser": _s("ym:s:browser"),
        "os": _s("ym:s:operatingSystem"),
        "screen_width": width,
        "screen_height": height,
        "screen_resolution": resolution,
        "region_country": _s("ym:s:regionCountry"),
        "region_city": _s("ym:s:regionCity"),
    }


def _read_metrika_backfill(metrika_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Прочитать backfill/visits_backfill_*.csv.gz в {visit_id: поля} + статистику.

    Дедуп ключа детерминированный: файлы и строки обходятся в устойчивом порядке
    (sorted по имени файла), при повторе visit_id побеждает ПОСЛЕДНЯЯ строка;
    число отброшенных дублей возвращается в статистике.
    """
    backfill_dir = Path(metrika_dir) / BACKFILL_SUBDIR
    by_visit: dict[str, dict[str, Any]] = {}
    total = 0
    dedup_dropped = 0
    if backfill_dir.exists():
        for path in sorted(backfill_dir.glob("visits_backfill_*.csv.gz")):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    parsed = _parse_backfill_row(row)
                    if parsed is None:
                        continue
                    total += 1
                    vid = parsed["visit_id"]
                    if vid in by_visit:
                        dedup_dropped += 1
                    by_visit[vid] = parsed  # последняя строка ключа побеждает
    return by_visit, {"backfill_rows": total, "backfill_dedup_dropped": dedup_dropped}


def _join_backfill(df: pd.DataFrame, metrika_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Left join базовых визитов с backfill по visit_id (число строк не растёт).

    Визит без backfill сохраняется (новые поля = null). Backfill-ключи, которых
    нет среди базовых визитов (unmatched), в canonical не попадают, но их число
    фиксируется в статистике (flags.metrika_backfill).

    Если patch-поля уже присутствуют в df (schema_version=visits-v2,
    patch_backfill=false — поля вшиты в базовый CSV), merge пропускается:
    backfill-директория пуста или отсутствует, данные брать неоткуда.
    """
    # patch-поля уже в df -> skip merge, только добавляем is_robot и считаем stats
    patch_already_present = all(col in df.columns for col in _BACKFILL_COLUMNS)
    if patch_already_present:
        df = df.copy()
        df["is_robot"] = None
        stats = {
            "backfill_rows": 0,
            "backfill_dedup_dropped": 0,
            "base_visits": int(len(df)),
            "backfill_matched": 0,
            "backfill_unmatched": 0,
            "is_robot_available": False,
        }
        return df, stats

    by_visit, stats = _read_metrika_backfill(metrika_dir)
    base_ids = set(df["visit_id"])
    matched = sum(1 for vid in by_visit if vid in base_ids)
    stats.update({
        "base_visits": int(len(df)),
        "backfill_matched": matched,
        "backfill_unmatched": len(by_visit) - matched,
        "is_robot_available": False,  # источник visits не отдаёт флаг робота (D11)
    })

    columns = ["visit_id"] + _BACKFILL_COLUMNS
    if by_visit:
        bf_df = pd.DataFrame(list(by_visit.values()), columns=columns)
    else:
        bf_df = pd.DataFrame(columns=columns)

    n_before = len(df)
    merged = df.merge(bf_df, on="visit_id", how="left")
    # Ключи backfill уникальны (дедуплицированы) -> left join не размножает строки.
    if len(merged) != n_before:
        raise AssertionError(
            f"backfill join изменил число строк: {n_before} -> {len(merged)}"
        )
    # is_robot присутствует в схеме как nullable, но НЕ заполняется (API не отдаёт).
    merged["is_robot"] = None
    return merged, stats


def _parse_visit_row(row: dict[str, str], goals_cfg: dict[str, Any]) -> dict[str, Any] | None:
    visit_id = (row.get("ym:s:visitID") or "").strip()
    if not visit_id:
        return None
    dt = pd.to_datetime(row.get("ym:s:dateTime"), errors="coerce")
    if pd.isna(dt):
        return None
    dt = dt.to_pydatetime()

    goal_ids = parse_goal_ids(row.get("ym:s:goalsID"))
    flags = goal_flags(goal_ids, goals_cfg)
    source_group = classify_traffic_source(row.get("ym:s:lastsignTrafficSource"))
    if source_group not in _VALID_SOURCE_GROUPS:
        source_group = "other"

    def _s(key: str) -> str | None:
        v = (row.get(key) or "").strip()
        return v or None

    width = _parse_backfill_int(row.get("ym:s:screenWidth"))
    height = _parse_backfill_int(row.get("ym:s:screenHeight"))
    resolution = f"{width}x{height}" if (width is not None and height is not None) else None

    return {
        "visit_id": visit_id,
        "client_id": (row.get("ym:s:clientID") or "").strip(),
        "dt": dt,
        "date": dt.date(),
        "device": map_device(row.get("ym:s:deviceCategory")),
        "source_group": source_group,
        "utm_source_raw": (row.get("ym:s:lastsignUTMSource") or "").strip(),
        "entry_page": normalize_entry_page(row.get("ym:s:startURL")),
        "form_open": flags["form_open"],
        "form_submit": flags["form_submit"],
        "call_click": flags["call_click"],
        "messenger_click": flags["messenger_click"],
        "form_submit_count": flags["form_submit_count"],
        "is_new_user": parse_bool_flag(row.get("ym:s:isNewUser")),
        # patch fields: present when schema_version=visits-v2 (patch_backfill=false)
        "last_traffic_source_naive": _s("ym:s:lastTrafficSource"),
        "browser": _s("ym:s:browser"),
        "os": _s("ym:s:operatingSystem"),
        "screen_width": width,
        "screen_height": height,
        "screen_resolution": resolution,
        "region_country": _s("ym:s:regionCountry"),
        "region_city": _s("ym:s:regionCity"),
    }


def _empty_backfill_stats() -> dict[str, Any]:
    return {
        "backfill_rows": 0, "backfill_dedup_dropped": 0, "base_visits": 0,
        "backfill_matched": 0, "backfill_unmatched": 0, "is_robot_available": False,
    }


def build_visits(
    raw_dir: Path, config: dict[str, Any], defaults: dict[str, Any],
) -> tuple[pd.DataFrame, bool, dict[str, Any]]:
    """data/raw/metrika_logs/ -> (визиты, utm_uncertain, статистика backfill).

    Базовые визиты — из visits_*.csv.gz; новые поля патча (наивная атрибуция,
    браузер/ОС/экран, гео) join-ятся из backfill/ по visit_id, не размножая строк.
    """
    raw_rows = _read_metrika_logs_rows(raw_dir)
    goals_cfg = config.get("goals") or {}
    parsed = [r for r in (_parse_visit_row(row, goals_cfg) for row in raw_rows) if r is not None]
    if not parsed:
        return pd.DataFrame(), False, _empty_backfill_stats()

    df = pd.DataFrame(parsed)
    df = dedupe_visits(df)

    threshold = float(defaults.get("utm_undefined_threshold", 0.25))
    source_final, utm_uncertain, _frac = apply_utm_threshold(
        df["source_group"], df["utm_source_raw"], threshold
    )
    df["source_final"] = source_final
    df["is_ad"] = df["source_final"] == "ad"

    df, backfill_stats = _join_backfill(df, raw_dir)
    return df, utm_uncertain, backfill_stats


# ═══════════════════════════ Чтение сырья: costs / direct ═══════════════════
def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def build_costs(
    direct_dir: Path | None,
    manifest_direct_entry: dict[str, Any] | None,
    config: dict[str, Any],
    defaults: dict[str, Any],
) -> pd.DataFrame:
    """Директ (campaign_performance.tsv) + config.costs_manual -> costs.

    Строится, даже если Директа нет (SEO-only клиент): фиксы из
    costs_manual разворачиваются в дневные строки для всего окна анализа.
    """
    rows: list[dict[str, Any]] = []

    if direct_dir is not None:
        micros_per_rub = float((manifest_direct_entry or {}).get("cost_micros_per_rub") or 1_000_000)
        for row in _read_tsv(direct_dir / "campaign_performance.tsv"):
            date_str = (row.get("Date") or "").strip()
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            cost_raw = _to_optional_float(row.get("Cost")) or 0.0
            rows.append({
                "date": day,
                "source_tag": "direct",
                "campaign_id": _to_optional_str(row.get("CampaignId")),
                "campaign_name": _to_optional_str(row.get("CampaignName")),
                "cost_rub": round(cost_raw / micros_per_rub, 6),
                "clicks": int(float(row["Clicks"])) if row.get("Clicks") not in (None, "") else None,
                "impressions": int(float(row["Impressions"])) if row.get("Impressions") not in (None, "") else None,
            })

    costs_manual = config.get("costs_manual") or {}
    date_from, date_to = extract_common.resolve_window(config, defaults)
    rows.extend(expand_manual_costs(costs_manual, date_from, date_to))

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def build_direct_queries(
    direct_dir: Path, manifest_direct_entry: dict[str, Any] | None,
) -> pd.DataFrame:
    """search_query_performance.tsv -> direct_queries.

    Отчёт агрегирован за всё окно выгрузки (без разбивки по дням) — Директ
    не запрашивает Date для SEARCH_QUERY_PERFORMANCE_REPORT. date_month
    фиксируется как месяц окончания окна выгрузки (манифест источника
    direct.date_to), это метка окна, а не помесячная разбивка.
    """
    query_rows = _read_tsv(direct_dir / "search_query_performance.tsv")
    if not query_rows:
        return pd.DataFrame()

    campaign_rows = _read_tsv(direct_dir / "campaign_performance.tsv")
    name_by_id = {
        (r.get("CampaignId") or "").strip(): (r.get("CampaignName") or "").strip()
        for r in campaign_rows if r.get("CampaignId")
    }

    micros_per_rub = float((manifest_direct_entry or {}).get("cost_micros_per_rub") or 1_000_000)
    date_to = (manifest_direct_entry or {}).get("date_to") or ""
    date_month = date_to[:7] if len(date_to) >= 7 else ""

    rows: list[dict[str, Any]] = []
    for row in query_rows:
        campaign_id = (row.get("CampaignId") or "").strip()
        cost_raw = _to_optional_float(row.get("Cost")) or 0.0
        rows.append({
            "query": row.get("Query") or "",
            "campaign_id": campaign_id or None,
            "campaign_name": name_by_id.get(campaign_id) or None,
            "cost_rub": round(cost_raw / micros_per_rub, 6),
            "clicks": int(float(row["Clicks"])) if row.get("Clicks") not in (None, "") else 0,
            "date_month": date_month,
        })
    return pd.DataFrame(rows)


def build_campaign_strategies(direct_dir: Path) -> pd.DataFrame:
    """campaign_strategies.json (campaigns.get) -> campaign_strategies."""
    path = direct_dir / "campaign_strategies.json"
    if not path.exists():
        return pd.DataFrame()
    with path.open("r", encoding="utf-8") as fh:
        campaigns = json.load(fh) or []

    rows: list[dict[str, Any]] = []
    for campaign in campaigns:
        strategy_type = _extract_bidding_strategy_type(campaign)
        rows.append({
            "campaign_id": str(campaign.get("Id")),
            "campaign_name": campaign.get("Name") or "",
            "strategy_type": strategy_type or "",
            "optimize_for": classify_strategy_optimize_for(strategy_type),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ═══════════════════════════ Чтение сырья: seo_queries ═══════════════════════
def _read_gsc_frames(gsc_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(gsc_dir.glob("gsc_*.csv")):
        frames.append(pd.read_csv(path, dtype=str))
    for path in sorted(gsc_dir.glob("gsc_*.parquet")):
        frames.append(pd.read_parquet(path).astype(str))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_seo_queries_gsc(gsc_dir: Path, config: dict[str, Any]) -> pd.DataFrame:
    """data/raw/gsc/gsc_*.{csv,parquet} -> строки seo_queries (engine=google).

    GSC даёт срез (query, page, device); device в канонической схеме нет,
    поэтому агрегируем по (query, page, month), суммируя clicks/impressions
    и беря средневзвешенную по impressions позицию.
    """
    raw = _read_gsc_frames(gsc_dir)
    if raw.empty:
        return pd.DataFrame()

    raw["clicks"] = pd.to_numeric(raw["clicks"], errors="coerce").fillna(0)
    raw["impressions"] = pd.to_numeric(raw["impressions"], errors="coerce").fillna(0)
    raw["position"] = pd.to_numeric(raw["position"], errors="coerce")
    raw["month"] = raw["month"].astype(str).str.slice(0, 7)

    raw["_pos_weighted"] = raw["position"] * raw["impressions"]
    grouped = raw.groupby(["query", "page", "month"], as_index=False).agg(
        clicks=("clicks", "sum"),
        impressions=("impressions", "sum"),
        _pos_weighted=("_pos_weighted", "sum"),
        _position_mean=("position", "mean"),
    )
    grouped["position_avg"] = grouped.apply(
        lambda r: (r["_pos_weighted"] / r["impressions"]) if r["impressions"] > 0 else r["_position_mean"],
        axis=1,
    )

    brand_terms = config.get("brand_terms") or []
    return pd.DataFrame({
        "engine": "google",
        "query": grouped["query"],
        "page": grouped["page"],
        "month": grouped["month"],
        "impressions": grouped["impressions"].astype(int),
        "clicks": grouped["clicks"].astype(int),
        "position_avg": grouped["position_avg"],
        "is_brand": grouped["query"].apply(lambda q: is_brand_query(q, brand_terms)),
    })


def build_seo_queries_webmaster(
    webmaster_dir: Path,
    manifest_webmaster_entry: dict[str, Any] | None,
    config: dict[str, Any],
) -> pd.DataFrame:
    """data/raw/webmaster/search_queries_popular.json -> строки seo_queries.

    Популярные запросы Вебмастера агрегированы за всё окно (без разбивки
    по дням/месяцам и без page) — month фиксируется как месяц окончания
    окна выгрузки (манифест источника webmaster.date_to), page отсутствует.
    """
    path = webmaster_dir / "search_queries_popular.json"
    if not path.exists():
        return pd.DataFrame()
    with path.open("r", encoding="utf-8") as fh:
        queries = json.load(fh) or []
    if not queries:
        return pd.DataFrame()

    date_to = (manifest_webmaster_entry or {}).get("date_to") or ""
    month = date_to[:7] if len(date_to) >= 7 else ""
    brand_terms = config.get("brand_terms") or []

    rows: list[dict[str, Any]] = []
    for item in queries:
        indicators = item.get("indicators") or {}
        query = item.get("query_text") or ""
        position_avg = indicators.get("AVG_CLICK_POSITION")
        if position_avg is None:
            position_avg = indicators.get("AVG_SHOW_POSITION")
        rows.append({
            "engine": "yandex",
            "query": query,
            "page": None,
            "month": month,
            "impressions": int(indicators.get("TOTAL_SHOWS") or 0),
            "clicks": int(indicators.get("TOTAL_CLICKS") or 0),
            "position_avg": float(position_avg) if position_avg is not None else None,
            "is_brand": is_brand_query(query, brand_terms),
        })
    return pd.DataFrame(rows)


# ═══════════════════════════ Чтение сырья: crm ═══════════════════════════════
def _read_crm_leads(crm_dir: Path) -> pd.DataFrame:
    parquet_path = crm_dir / "leads.parquet"
    csv_path = crm_dir / "leads.csv"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str)
    return pd.DataFrame()


def build_crm(crm_dir: Path) -> pd.DataFrame:
    """data/raw/crm/leads.{csv,parquet} -> crm (нормализация статуса/источника)."""
    raw = _read_crm_leads(crm_dir)
    if raw.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        lead_date_raw = _to_optional_str(row.get("lead_date"))
        try:
            lead_date = datetime.strptime(lead_date_raw, "%Y-%m-%d").date() if lead_date_raw else None
        except ValueError:
            lead_date = None
        if lead_date is None:
            continue
        rows.append({
            "lead_date": lead_date,
            "source_norm": normalize_crm_source(_to_optional_str(row.get("source"))),
            "status_norm": normalize_crm_status(_to_optional_str(row.get("status"))),
            "amount_rub": _to_optional_float(row.get("amount_rub")),
            "is_new_client": _to_optional_bool(row.get("is_new_client")),
            "phone_hash": _to_optional_str(row.get("phone_hash")),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ═════════════════════════════ Запись parquet ═══════════════════════════════
_ARROW_TYPES: dict[str, pa.DataType] = {
    "string": pa.string(),
    "bool": pa.bool_(),
    "int": pa.int64(),
    "float": pa.float64(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
}

SCHEMAS: dict[str, dict[str, str]] = {
    "visits": {
        # ── Базовые 16 колонок (контракт до патча — НЕ менять) ──────────────
        "visit_id": "string", "client_id": "string", "dt": "timestamp", "date": "date",
        "device": "string", "source_group": "string", "utm_source_raw": "string",
        "source_final": "string", "is_ad": "bool", "entry_page": "string",
        "form_open": "bool", "form_submit": "bool", "call_click": "bool",
        "messenger_click": "bool", "form_submit_count": "int", "is_new_user": "bool",
        # ── Поля патча (из backfill; отсутствие -> null) ────────────────────
        "last_traffic_source_naive": "string",  # T02: наивная модель атрибуции
        "browser": "string",                    # C21
        "os": "string",                         # C21
        "screen_width": "int",                  # C21 (nullable)
        "screen_height": "int",                 # C21 (nullable)
        "screen_resolution": "string",          # "<width>x<height>" при наличии обоих
        "region_country": "string",             # A12 / S26
        "region_city": "string",                # A12 / S26
        # is_robot: источник visits его не отдаёт (D11) -> nullable, всегда null,
        # НИКОГДА не false; недоступность фиксируется в flags.metrika_backfill.
        "is_robot": "bool",
    },
    "costs": {
        "date": "date", "source_tag": "string", "campaign_id": "string",
        "campaign_name": "string", "cost_rub": "float", "clicks": "int",
        "impressions": "int",
    },
    "direct_queries": {
        "query": "string", "campaign_id": "string", "campaign_name": "string",
        "cost_rub": "float", "clicks": "int", "date_month": "string",
    },
    "campaign_strategies": {
        "campaign_id": "string", "campaign_name": "string", "strategy_type": "string",
        "optimize_for": "string",
    },
    "seo_queries": {
        "engine": "string", "query": "string", "page": "string", "month": "string",
        "impressions": "int", "clicks": "int", "position_avg": "float", "is_brand": "bool",
    },
    "crm": {
        "lead_date": "date", "source_norm": "string", "status_norm": "string",
        "amount_rub": "float", "is_new_client": "bool", "phone_hash": "string",
    },
}


def _column_values(df: pd.DataFrame, name: str, arrow_type: str) -> list[Any]:
    if name not in df.columns:
        return [None] * len(df)
    values = df[name].tolist()
    if arrow_type == "date":
        out = []
        for v in values:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                out.append(None)
            elif isinstance(v, date) and not isinstance(v, datetime):
                out.append(v)
            else:
                out.append(pd.Timestamp(v).date())
        return out
    if arrow_type == "timestamp":
        return [None if v is None or pd.isna(v) else pd.Timestamp(v).to_pydatetime() for v in values]
    if arrow_type == "bool":
        return [_to_optional_bool(v) if not isinstance(v, bool) else v for v in values]
    if arrow_type == "int":
        return [None if v is None or (isinstance(v, float) and pd.isna(v)) else int(v) for v in values]
    if arrow_type == "float":
        return [None if v is None or (isinstance(v, float) and pd.isna(v)) else float(v) for v in values]
    return [None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v) for v in values]


def write_canonical_table(df: pd.DataFrame, table_name: str, out_path: Path) -> None:
    """Записать DataFrame как parquet со строго заданной схемой (SCHEMAS)."""
    schema = SCHEMAS[table_name]
    fields = [pa.field(col, _ARROW_TYPES[t]) for col, t in schema.items()]
    arrow_schema = pa.schema(fields)
    arrays = [
        pa.array(_column_values(df, col, t), type=_ARROW_TYPES[t])
        for col, t in schema.items()
    ]
    table = pa.Table.from_arrays(arrays, schema=arrow_schema)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)


def _write_canonical_manifest(canonical_dir: Path, tables: list[str], flags: dict[str, Any]) -> None:
    from datetime import timezone

    payload = {
        "tables": sorted(tables),
        "flags": flags,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with (canonical_dir / CANONICAL_MANIFEST_NAME).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ═════════════════════════════ Оркестрация ══════════════════════════════════
def build(paths: Any, config: dict[str, Any], defaults: dict[str, Any]) -> list[str]:
    """Построить все возможные канонические таблицы; вернуть список их имён.

    Доступность источника — по data/raw/manifest.json (не по config.yaml):
    таблица строится, только если соответствующий raw-источник в манифесте
    присутствует (кроме costs — она дополнительно может быть построена
    целиком из config.costs_manual, см. докстринг модуля).
    """
    raw_dir = Path(paths.raw)
    canonical_dir = Path(paths.canonical)
    canonical_dir.mkdir(parents=True, exist_ok=True)

    raw_manifest = manifest_mod.load_manifest(raw_dir)
    sources = raw_manifest.get("sources") or {}

    built: list[str] = []
    flags: dict[str, Any] = {}

    if "metrika_logs" in sources:
        visits_df, utm_uncertain, backfill_stats = build_visits(
            raw_dir / "metrika_logs", config, defaults
        )
        if not visits_df.empty:
            write_canonical_table(visits_df, "visits", canonical_dir / "visits.parquet")
            built.append("visits")
            flags["utm_uncertain"] = utm_uncertain
            # Статистика склейки base+backfill (в т.ч. unmatched и недоступность
            # is_robot) — фиксируется для аналитика, а не «молча».
            flags["metrika_backfill"] = backfill_stats

    direct_dir = raw_dir / "direct" if "direct" in sources else None
    direct_entry = sources.get("direct")
    costs_df = build_costs(direct_dir, direct_entry, config, defaults)
    if not costs_df.empty:
        write_canonical_table(costs_df, "costs", canonical_dir / "costs.parquet")
        built.append("costs")

    if direct_dir is not None:
        dq_df = build_direct_queries(direct_dir, direct_entry)
        if not dq_df.empty:
            write_canonical_table(dq_df, "direct_queries", canonical_dir / "direct_queries.parquet")
            built.append("direct_queries")

        cs_df = build_campaign_strategies(direct_dir)
        if not cs_df.empty:
            write_canonical_table(
                cs_df, "campaign_strategies", canonical_dir / "campaign_strategies.parquet"
            )
            built.append("campaign_strategies")

    seo_frames: list[pd.DataFrame] = []
    if "gsc" in sources:
        seo_frames.append(build_seo_queries_gsc(raw_dir / "gsc", config))
    if "webmaster" in sources:
        seo_frames.append(
            build_seo_queries_webmaster(raw_dir / "webmaster", sources.get("webmaster"), config)
        )
    seo_frames = [f for f in seo_frames if not f.empty]
    if seo_frames:
        seo_df = pd.concat(seo_frames, ignore_index=True)
        write_canonical_table(seo_df, "seo_queries", canonical_dir / "seo_queries.parquet")
        built.append("seo_queries")

    if "crm" in sources:
        crm_df = build_crm(raw_dir / "crm")
        if not crm_df.empty:
            write_canonical_table(crm_df, "crm", canonical_dir / "crm.parquet")
            built.append("crm")

    _write_canonical_manifest(canonical_dir, built, flags)
    return built
