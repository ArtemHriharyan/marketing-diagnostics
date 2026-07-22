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
                 site_pages.parquet      — страницы из кролера (URL-норм., дедуп)
                 site_link_graph.parquet — граф внутренних ссылок
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
from ..extract.metrika_logs import LOOKBACK_SUBDIR, VISIT_FIELDS
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

# ym:s:lastsignTrafficSource, при которых источник считается техническим
# артефактом разрыва сессии, а не самостоятельным каналом (T02/T03 carry-forward).
_TRAFFIC_RESOLVE_AMBIGUOUS = {"internal", "undefined"}

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


def normalize_url(url: str | None) -> str | None:
    """Нормализация URL: строчные scheme/netloc, без trailing-slash (кроме корня "/").

    Применяется к site_pages.url и site_link_graph.from_url/to_url для дедупликации.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not url:
        return url
    raw = str(url).strip()
    if not raw:
        return raw
    try:
        parts = urlsplit(raw)
        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return urlunsplit((
            parts.scheme.lower() if parts.scheme else parts.scheme,
            parts.netloc.lower() if parts.netloc else parts.netloc,
            path,
            parts.query,
            parts.fragment,
        ))
    except Exception:
        return raw


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


def dedupe_site_pages(df: pd.DataFrame) -> pd.DataFrame:
    """Нормализация url и дедуп по url: при дублях первая строка побеждает."""
    if df.empty:
        return df
    df = df.copy()
    df["url"] = df["url"].apply(normalize_url)
    return df.drop_duplicates(subset="url", keep="first").reset_index(drop=True)


def dedupe_site_link_graph(df: pd.DataFrame) -> pd.DataFrame:
    """Нормализация from_url/to_url и дедуп по паре (from_url, to_url)."""
    if df.empty:
        return df
    df = df.copy()
    df["from_url"] = df["from_url"].apply(normalize_url)
    df["to_url"] = df["to_url"].apply(normalize_url)
    return df.drop_duplicates(subset=["from_url", "to_url"], keep="first").reset_index(drop=True)


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


# ── T02/T03: carry-forward источника трафика для internal/undefined ────────
def resolve_traffic_source(
    df: pd.DataFrame, lookback_cutoff: date | None = None,
) -> pd.DataFrame:
    """Восстановить lastsign-источник для визитов с internal/undefined (T02/T03).

    Читает ``client_id``, ``dt``, ``date``, ``last_sign_traffic_source_raw``.
    Визиты сортируются по (client_id, dt) по возрастанию; визит с сырым
    источником из ``_TRAFFIC_RESOLVE_AMBIGUOUS`` получает значение ближайшего
    ПРЕДЫДУЩЕГО визита того же clientID с реальным источником (только вперёд
    по времени — назад не смотрим). ``lookback_cutoff`` — нижняя граница даты
    визита, который допустимо использовать как реальный источник (граница
    lookback-окна, config/defaults.yaml: transform.traffic_resolve_lookback_days);
    визиты старше границы остаются в df, но цепочку не продолжают.

    Добавляет колонки:
        source_group_resolved   — source_group после восстановления (для визита
                                   с реальным источником не меняется);
        traffic_source_resolved — bool, False, если для clientID не нашлось ни
                                   одного реального источника в пределах истории
                                   (это ожидаемое поведение, не ошибка).

    Исходные lastsign/naive поля не трогает — новые колонки отдельно от них.
    """
    if df.empty:
        out = df.copy()
        out["source_group_resolved"] = pd.Series(dtype="object")
        out["traffic_source_resolved"] = pd.Series(dtype="bool")
        return out

    ordered = df.sort_values(["client_id", "dt"], kind="stable")
    resolved_raw: list[str | None] = []
    resolved_flag: list[bool] = []
    last_real: dict[Any, str | None] = {}

    for _, row in ordered.iterrows():
        client_id = row["client_id"]
        raw = row.get("last_sign_traffic_source_raw")
        norm = (raw or "").strip().lower()
        visit_date = row["date"]

        if norm not in _TRAFFIC_RESOLVE_AMBIGUOUS:
            if lookback_cutoff is None or visit_date >= lookback_cutoff:
                last_real[client_id] = raw
            resolved_raw.append(raw)
            resolved_flag.append(True)
            continue

        prior = last_real.get(client_id)
        if prior is not None:
            resolved_raw.append(prior)
            resolved_flag.append(True)
        else:
            resolved_raw.append(raw)
            resolved_flag.append(False)

    ordered = ordered.assign(_resolved_raw=resolved_raw, traffic_source_resolved=resolved_flag)
    ordered["source_group_resolved"] = ordered["_resolved_raw"].apply(classify_traffic_source)
    ordered = ordered.drop(columns=["_resolved_raw"])
    return ordered.loc[df.index].reset_index(drop=True)


def compute_traffic_resolve_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Доля визитов с traffic_source_resolved=False среди internal/undefined.

    Обязательный caveat манифеста (T02/T03) — считается только над визитами,
    у которых сырой lastsign-источник был internal/undefined; визиты с уже
    реальным источником в знаменатель не входят.
    """
    if df.empty or "last_sign_traffic_source_raw" not in df.columns:
        return {"internal_or_undefined_total": 0, "unresolved_count": 0, "unresolved_frac": 0.0}
    norm = df["last_sign_traffic_source_raw"].fillna("").astype(str).str.strip().str.lower()
    ambiguous_mask = norm.isin(_TRAFFIC_RESOLVE_AMBIGUOUS)
    total = int(ambiguous_mask.sum())
    unresolved = int((ambiguous_mask & ~df["traffic_source_resolved"]).sum())
    frac = (unresolved / total) if total else 0.0
    return {
        "internal_or_undefined_total": total,
        "unresolved_count": unresolved,
        "unresolved_frac": frac,
    }


# ═════════════════════════════ Правила: costs ═══════════════════════════════
_VAT_RATE = 1.2  # НДС 20%


def _vat_lookup(vat_basis_by_source: list[dict[str, Any]]) -> dict[str, bool | None]:
    """finance.vat_basis_by_source -> {source_tag: vat_included | None}.

    vat_included=True  — расходы в кабинете указаны с НДС (gross)
    vat_included=False — без НДС (net)
    vat_included=None  — не указано -> база НДС неизвестна
    Источники, не упомянутые в списке, дают vat_basis_unknown.
    """
    out: dict[str, bool | None] = {}
    for entry in (vat_basis_by_source or []):
        src = (entry.get("source") or "").strip()
        vat = entry.get("vat_included")
        if src:
            out[src] = None if vat is None else bool(vat)
    return out


def _apply_vat_to_rows(rows: list[dict[str, Any]], vat_map: dict[str, bool | None]) -> None:
    """In-place: добавляет cost_normalized и cost_status к каждой строке расходов.

    gross (vat_included=True)  -> normalized = raw / 1.2, status = "gross"
    net   (vat_included=False) -> normalized = raw,       status = "net"
    иначе                      -> normalized = null,      status = "vat_basis_unknown"
    """
    for row in rows:
        basis = vat_map.get(row["source_tag"])
        raw = row["cost_raw"]
        if basis is True:
            row["cost_normalized"] = round(raw / _VAT_RATE, 6)
            row["cost_status"] = "gross"
        elif basis is False:
            row["cost_normalized"] = raw
            row["cost_status"] = "net"
        else:
            row["cost_normalized"] = None
            row["cost_status"] = "vat_basis_unknown"


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
                "cost_raw": round(rub_month / days_in_month, 6),
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


# Историческое имя поля региона визита — используется, если manifest ещё не
# содержит region_field (выгрузка до 2A-patch, старый extract писал только
# ym:s:regionCity без записи имени поля в manifest).
_REGION_FIELD_LEGACY_DEFAULT = "ym:s:regionCity"


def _resolve_region_field(manifest_metrika_entry: dict[str, Any] | None) -> str:
    """Фактическое имя поля региона визита в сыром CSV (2A-patch).

    extract/metrika_logs.py решает во время выгрузки, какое имя РЕАЛЬНО ушло
    в API (ym:s:regionArea, если API его принял; откат на ym:s:regionCity,
    если API отклонил regionArea — см. manifest.region_field_verified) и
    записывает выбранное имя в manifest.region_field. Transform не гадает и
    не хардкодит имя — читает то же имя, что реально запрашивал extract;
    иначе колонка молча уходит в None, даже если данные в CSV есть под другим
    заголовком. Manifest без этого поля (выгрузка до 2A-patch) -> откат на
    исторически известное ym:s:regionCity, не пустая колонка.
    """
    entry = manifest_metrika_entry or {}
    return entry.get("region_field") or _REGION_FIELD_LEGACY_DEFAULT


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


def _read_metrika_lookback_rows(raw_dir: Path) -> list[dict[str, str]]:
    """Визиты lookback-окна (см. LOOKBACK_SUBDIR, src.extract.metrika_logs).

    Нужны ТОЛЬКО для восстановления цепочки clientID в carry-forward
    (resolve_traffic_source, T02/T03, задача 4X-lookback-canonical-flag) — сами
    по себе в основные агрегаты/визиты отчёта не попадают (см. build_visits).
    Поля переиспользуют состав основного окна (_fetch_lookback), поэтому
    парсятся тем же _parse_visit_row, что и базовые визиты.
    """
    rows: list[dict[str, str]] = []
    lookback_dir = Path(raw_dir) / LOOKBACK_SUBDIR
    if not lookback_dir.exists():
        return rows
    for path in sorted(lookback_dir.glob("visits_lookback_*.csv.gz")):
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


def _parse_backfill_row(row: dict[str, str], region_field: str) -> dict[str, Any] | None:
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
        "region_city": _s(region_field),
    }


def _read_metrika_backfill(
    metrika_dir: Path, region_field: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
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
                    parsed = _parse_backfill_row(row, region_field)
                    if parsed is None:
                        continue
                    total += 1
                    vid = parsed["visit_id"]
                    if vid in by_visit:
                        dedup_dropped += 1
                    by_visit[vid] = parsed  # последняя строка ключа побеждает
    return by_visit, {"backfill_rows": total, "backfill_dedup_dropped": dedup_dropped}


def _join_backfill(
    df: pd.DataFrame, metrika_dir: Path, region_field: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Left join базовых визитов с backfill по visit_id (число строк не растёт).

    Визит без backfill сохраняется (новые поля = null). Backfill-ключи, которых
    нет среди базовых визитов (unmatched), в canonical не попадают, но их число
    фиксируется в статистике (flags.metrika_backfill).

    Если backfill-директория отсутствует или пуста (schema_version=visits-v2,
    patch_backfill=false — поля вшиты в базовый CSV), merge пропускается:
    данные брать неоткуда.
    """
    # Backfill нужен только когда в backfill/ есть файлы. Проверять col in df.columns
    # нельзя: _parse_visit_row всегда включает patch-колонки (с None) в dict, поэтому
    # df всегда их содержит — независимо от того, вшиты поля в базовый CSV или нет.
    backfill_dir = Path(metrika_dir) / BACKFILL_SUBDIR
    no_backfill_files = not backfill_dir.exists() or not any(
        backfill_dir.glob("visits_backfill_*.csv.gz")
    )
    if no_backfill_files:
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

    by_visit, stats = _read_metrika_backfill(metrika_dir, region_field)
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

    # Дропаем patch-колонки из базового df перед merge: _parse_visit_row всегда
    # включает их (с None), иначе merge создаст суффиксы _x/_y.
    df_base = df.drop(columns=[c for c in _BACKFILL_COLUMNS if c in df.columns])
    n_before = len(df_base)
    merged = df_base.merge(bf_df, on="visit_id", how="left")
    # Ключи backfill уникальны (дедуплицированы) -> left join не размножает строки.
    if len(merged) != n_before:
        raise AssertionError(
            f"backfill join изменил число строк: {n_before} -> {len(merged)}"
        )
    # is_robot присутствует в схеме как nullable, но НЕ заполняется (API не отдаёт).
    merged["is_robot"] = None
    return merged, stats


def _parse_visit_row(
    row: dict[str, str], goals_cfg: dict[str, Any], region_field: str,
) -> dict[str, Any] | None:
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
        # Сырой ym:s:lastsignTrafficSource — отдельно от source_group (после
        # маппинга "undefined" неотличимо от "other"); нужен carry-forward
        # (resolve_traffic_source, T02/T03) и QA-сверке.
        "last_sign_traffic_source_raw": _to_optional_str(row.get("ym:s:lastsignTrafficSource")),
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
        "region_city": _s(region_field),
    }


def _empty_backfill_stats() -> dict[str, Any]:
    return {
        "backfill_rows": 0, "backfill_dedup_dropped": 0, "base_visits": 0,
        "backfill_matched": 0, "backfill_unmatched": 0, "is_robot_available": False,
        "traffic_source_resolve": {
            "internal_or_undefined_total": 0, "unresolved_count": 0, "unresolved_frac": 0.0,
        },
    }


def build_visits(
    raw_dir: Path, config: dict[str, Any], defaults: dict[str, Any],
    manifest_metrika_entry: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, bool, dict[str, Any]]:
    """data/raw/metrika_logs/ -> (визиты, utm_uncertain, статистика backfill).

    Базовые визиты — из visits_*.csv.gz; новые поля патча (наивная атрибуция,
    браузер/ОС/экран, гео) join-ятся из backfill/ по visit_id, не размножая строк.

    ``manifest_metrika_entry`` — запись data/raw/manifest.json.sources.metrika_logs
    (2A-patch): region_field фиксирует фактическое имя поля региона визита,
    которое реально запрашивал extract (ym:s:regionArea, если API его принял,
    либо откат ym:s:regionCity, если отклонил — см. _resolve_region_field).

    Помимо основного окна читает raw_dir/<LOOKBACK_SUBDIR>/ (задача
    4X-lookback-canonical-flag): визиты за N дней ДО основного окна, нужные
    ТОЛЬКО для восстановления цепочки clientID в carry-forward
    (resolve_traffic_source, T02/T03). Каждая строка результата помечена
    явным булевым полем is_lookback_only (True — визит из lookback-окна).
    UTM-порог и склейка с backfill считаются ТОЛЬКО по основному окну — до
    того, как lookback-строки подмешиваются в df, — поэтому их наличие не
    меняет ни source_final/is_ad визитов основного окна, ни статистику
    backfill. resolve_traffic_source видит обе группы вместе (лукбэк-визит
    может стать восстановленным «реальным источником» для визита основного
    окна), а traffic_source_resolve-статистика считается только по строкам
    основного окна (is_lookback_only=False) — лукбэк не входит в знаменатель.
    is_lookback_only остаётся в возвращаемом DataFrame для явности, но не
    входит в SCHEMAS["visits"]: фактическую фильтрацию лукбэк-строк перед
    записью visits.parquet выполняет build() — они никогда не попадают в
    parquet, отдаваемый в compute.
    """
    region_field = _resolve_region_field(manifest_metrika_entry)
    raw_rows = _read_metrika_logs_rows(raw_dir)
    goals_cfg = config.get("goals") or {}
    parsed = [r for r in (_parse_visit_row(row, goals_cfg, region_field) for row in raw_rows)
              if r is not None]
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

    df, backfill_stats = _join_backfill(df, raw_dir, region_field)
    df["is_lookback_only"] = False

    lookback_raw_rows = _read_metrika_lookback_rows(raw_dir)
    lookback_parsed = [
        r for r in (_parse_visit_row(row, goals_cfg, region_field) for row in lookback_raw_rows)
        if r is not None
    ]
    if lookback_parsed:
        lookback_df = dedupe_visits(pd.DataFrame(lookback_parsed))
        lookback_df["is_lookback_only"] = True
        # source_final/is_ad не используются для лукбэк-строк (фильтруются перед
        # parquet), но заполняются здесь же (а не оставляются NaN), чтобы concat
        # не апкастил is_ad всего столбца до object — dtype визитов основного
        # окна не должен зависеть от присутствия лукбэк-данных.
        lookback_df["source_final"] = lookback_df["source_group"]
        lookback_df["is_ad"] = lookback_df["source_final"] == "ad"
        combined = pd.concat([df, lookback_df], ignore_index=True)
    else:
        combined = df

    lookback_days = int(
        ((defaults or {}).get("transform") or {}).get("traffic_resolve_lookback_days", 30)
    )
    date_from, _date_to = extract_common.resolve_window(config, defaults)
    lookback_cutoff = date_from - timedelta(days=lookback_days)
    combined = resolve_traffic_source(combined, lookback_cutoff=lookback_cutoff)

    main_mask = combined["is_lookback_only"] == False  # noqa: E712
    stats = {
        **backfill_stats,
        "traffic_source_resolve": compute_traffic_resolve_stats(combined[main_mask]),
    }

    return combined, utm_uncertain, stats


# ═══════════════════════════ Чтение сырья: costs / direct ═══════════════════
def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _read_tsv_dir(path: Path) -> list[dict[str, str]]:
    """Прочитать все *.tsv непосредственно в path (не рекурсивно), объединить строки."""
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    for tsv_path in sorted(path.glob("*.tsv")):
        rows.extend(_read_tsv(tsv_path))
    return rows


def build_costs(
    direct_dir: Path | None,
    manifest_direct_entry: dict[str, Any] | None,
    config: dict[str, Any],
    defaults: dict[str, Any],
    vat_basis_by_source: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Директ (campaign_performance.tsv) + config.costs_manual -> costs.

    Строится, даже если Директа нет (SEO-only клиент): фиксы из
    costs_manual разворачиваются в дневные строки для всего окна анализа.
    cost_normalized рассчитывается по vat_basis_by_source; при отсутствии
    базы НДС — null, cost_status="vat_basis_unknown" (не "как есть" молча).
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
            raw_cost = _to_optional_float(row.get("Cost")) or 0.0
            rows.append({
                "date": day,
                "source_tag": "direct",
                "campaign_id": _to_optional_str(row.get("CampaignId")),
                "campaign_name": _to_optional_str(row.get("CampaignName")),
                "cost_raw": round(raw_cost / micros_per_rub, 6),
                "clicks": int(float(row["Clicks"])) if row.get("Clicks") not in (None, "") else None,
                "impressions": int(float(row["Impressions"])) if row.get("Impressions") not in (None, "") else None,
            })

    costs_manual = config.get("costs_manual") or {}
    date_from, date_to = extract_common.resolve_window(config, defaults)
    rows.extend(expand_manual_costs(costs_manual, date_from, date_to))

    if not rows:
        return pd.DataFrame()

    vat_map = _vat_lookup(vat_basis_by_source or [])
    _apply_vat_to_rows(rows, vat_map)
    return pd.DataFrame(rows)


_MICROS_PER_RUB = 1_000_000


def _parse_cost(raw: Any) -> tuple[int, float]:
    """Cost-поле из TSV Директа -> (cost_raw_micros: int, cost_rub: float).

    cost_rub — ТОЛЬКО валютная конверсия (микрорубли -> рубли), НЕ НДС-база.
    Единственное место деления микрорублей — все билдеры используют только его.

    Не путать с cost_normalized (отдельное поле в direct_queries/campaigns/geo,
    заполняется в compute-слое после ответа на Q01, см. SCHEMAS и
    docs/implementation_status.md, задача 4X-direct-normalize-2) — до этой
    задачи оба понятия ошибочно делили одно имя "cost_normalized".
    """
    micros = int(float(raw)) if raw not in (None, "", "nan") else 0
    return micros, round(micros / _MICROS_PER_RUB, 6)


def _parse_int_field(raw: Any, default: int = 0) -> int:
    try:
        return int(float(raw)) if raw not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default


def _parse_date_field(raw: Any) -> date | None:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def build_direct_queries(
    direct_dir: Path, manifest_direct_entry: dict[str, Any] | None,
    macro_goals: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """SEARCH_QUERY_PERFORMANCE_REPORT -> direct_queries.

    Читает из direct/queries/ (новый помесячный формат), если папка есть;
    иначе откатывается на legacy search_query_performance.tsv.
    cost_raw хранится как int64 микрорублей; cost_rub = float64 рублей
    (валютная конверсия, считается всегда). cost_normalized = null и
    vat_basis_applied = False на этом слое — НДС-нормализацию применяет
    compute после ответа на Q01 (см. SCHEMAS["direct_queries"]).
    """
    queries_dir = direct_dir / "queries"
    if queries_dir.exists() and list(queries_dir.glob("*.tsv")):
        raw_rows = _read_tsv_dir(queries_dir)
    else:
        raw_rows = _read_tsv(direct_dir / "search_query_performance.tsv")

    if not raw_rows:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        cost_raw, cost_rub = _parse_cost(row.get("Cost"))
        rows.append({
            "date": _parse_date_field(row.get("Date")),
            "campaign_id": _to_optional_str(row.get("CampaignId")),
            "campaign_name": _to_optional_str(row.get("CampaignName")),
            "ad_group_id": _to_optional_str(row.get("AdGroupId")),
            "query": row.get("Query") or "",
            "match_type": _to_optional_str(row.get("MatchType")),
            "device": _to_optional_str(row.get("Device")),
            "cost_raw": cost_raw,
            "cost_rub": cost_rub,
            "cost_normalized": None,
            "vat_basis_applied": False,
            "clicks": _parse_int_field(row.get("Clicks")),
            "impressions": _parse_int_field(row.get("Impressions")),
            "conversions_all": _parse_int_field(row.get("Conversions")),
        })

    df = pd.DataFrame(rows)

    if macro_goals:
        goal_ids = [str(g["id"]) for g in macro_goals]
        key_cols = ["date", "campaign_id", "campaign_name", "ad_group_id",
                    "query", "match_type", "device"]
        df = _join_goal_convs(df, queries_dir / "goals", goal_ids, key_cols)

    return df


def build_direct_campaigns(
    direct_dir: Path, manifest_direct_entry: dict[str, Any] | None,
    macro_goals: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """CAMPAIGN_PERFORMANCE_REPORT -> direct_campaigns.

    Читает из direct/campaigns/ (новый помесячный формат). cost_rub —
    валютная конверсия (считается всегда); cost_normalized/vat_basis_applied
    заполняются в compute после Q01 (см. build_direct_queries).
    """
    campaign_dir = direct_dir / "campaigns"
    raw_rows = _read_tsv_dir(campaign_dir)
    if not raw_rows:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        cost_raw, cost_rub = _parse_cost(row.get("Cost"))
        rows.append({
            "date": _parse_date_field(row.get("Date")),
            "campaign_id": _to_optional_str(row.get("CampaignId")),
            "campaign_name": _to_optional_str(row.get("CampaignName")),
            "device": _to_optional_str(row.get("Device")),
            "cost_raw": cost_raw,
            "cost_rub": cost_rub,
            "cost_normalized": None,
            "vat_basis_applied": False,
            "clicks": _parse_int_field(row.get("Clicks")),
            "impressions": _parse_int_field(row.get("Impressions")),
            "conversions_all": _parse_int_field(row.get("Conversions")),
        })

    df = pd.DataFrame(rows)

    if macro_goals:
        goal_ids = [str(g["id"]) for g in macro_goals]
        key_cols = ["date", "campaign_id", "campaign_name", "device"]
        df = _join_goal_convs(df, campaign_dir / "goals", goal_ids, key_cols)

    return df


def build_direct_geo(
    direct_dir: Path, manifest_direct_entry: dict[str, Any] | None,
    macro_goals: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """GEO_PERFORMANCE_REPORT -> direct_geo.

    Читает из direct/geo/ (новый помесячный формат). cost_rub — валютная
    конверсия (считается всегда); cost_normalized/vat_basis_applied
    заполняются в compute после Q01 (см. build_direct_queries).
    """
    geo_dir = direct_dir / "geo"
    raw_rows = _read_tsv_dir(geo_dir)
    if not raw_rows:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        cost_raw, cost_rub = _parse_cost(row.get("Cost"))
        rows.append({
            "date": _parse_date_field(row.get("Date")),
            "campaign_id": _to_optional_str(row.get("CampaignId")),
            "campaign_name": _to_optional_str(row.get("CampaignName")),
            "location_of_presence_id": _to_optional_str(row.get("LocationOfPresenceId")),
            "location_of_presence_name": _to_optional_str(row.get("LocationOfPresenceName")),
            "device": _to_optional_str(row.get("Device")),
            "cost_raw": cost_raw,
            "cost_rub": cost_rub,
            "cost_normalized": None,
            "vat_basis_applied": False,
            "clicks": _parse_int_field(row.get("Clicks")),
            "impressions": _parse_int_field(row.get("Impressions")),
            "conversions_all": _parse_int_field(row.get("Conversions")),
        })

    df = pd.DataFrame(rows)

    if macro_goals:
        goal_ids = [str(g["id"]) for g in macro_goals]
        key_cols = ["date", "campaign_id", "campaign_name",
                    "location_of_presence_id", "location_of_presence_name", "device"]
        df = _join_goal_convs(df, geo_dir / "goals", goal_ids, key_cols)

    return df


def build_direct_placements(direct_dir: Path) -> pd.DataFrame:
    """placements/placement_performance.tsv -> direct_placements.

    PLACEMENT_FIELDS (см. src.extract.direct.PLACEMENT_FIELDS) не содержит
    Date/Impressions — отчёт агрегирован за весь период выгрузки, без
    разбивки по дням. cost_rub — валютная конверсия (считается всегда);
    cost_normalized/vat_basis_applied заполняются в compute после Q01
    (см. build_direct_queries) — тот же контракт, что и у queries/campaigns/geo.
    """
    path = direct_dir / "placements" / "placement_performance.tsv"
    raw_rows = _read_tsv(path)
    if not raw_rows:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        cost_raw, cost_rub = _parse_cost(row.get("Cost"))
        rows.append({
            "placement": _to_optional_str(row.get("Placement")),
            "ad_network_type": _to_optional_str(row.get("AdNetworkType")),
            "campaign_id": _to_optional_str(row.get("CampaignId")),
            "cost_raw": cost_raw,
            "cost_rub": cost_rub,
            "cost_normalized": None,
            "vat_basis_applied": False,
            "clicks": _parse_int_field(row.get("Clicks")),
            "conversions_all": _parse_int_field(row.get("Conversions")),
        })
    return pd.DataFrame(rows)


def build_direct_geo_monthly(direct_dir: Path) -> pd.DataFrame:
    """direct/geo/????-??.tsv (помесячные чанки) -> geo с явной колонкой month.

    Отдельная таблица от direct_geo (та же исходная выгрузка, но там месяц не
    зафиксирован явной колонкой) — читает КАЖДЫЙ месячный файл по отдельности,
    month берётся из имени файла-чанка (YYYY-MM). Исходные помесячные TSV не
    изменяются и не удаляются (только чтение). cost_rub/cost_normalized/
    vat_basis_applied — тот же контракт, что у build_direct_queries.
    """
    geo_dir = direct_dir / "geo"
    if not geo_dir.exists():
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for path in sorted(geo_dir.glob("????-??.tsv")):
        month = path.stem
        for row in _read_tsv(path):
            cost_raw, cost_rub = _parse_cost(row.get("Cost"))
            rows.append({
                "month": month,
                "date": _parse_date_field(row.get("Date")),
                "campaign_id": _to_optional_str(row.get("CampaignId")),
                "campaign_name": _to_optional_str(row.get("CampaignName")),
                "location_of_presence_id": _to_optional_str(row.get("LocationOfPresenceId")),
                "location_of_presence_name": _to_optional_str(row.get("LocationOfPresenceName")),
                "device": _to_optional_str(row.get("Device")),
                "cost_raw": cost_raw,
                "cost_rub": cost_rub,
                "cost_normalized": None,
                "vat_basis_applied": False,
                "clicks": _parse_int_field(row.get("Clicks")),
                "impressions": _parse_int_field(row.get("Impressions")),
                "conversions_all": _parse_int_field(row.get("Conversions")),
            })
    return pd.DataFrame(rows)


def _build_goal_frame(goal_dir: Path, conv_col_name: str = "conversions") -> pd.DataFrame:
    """Прочитать TSV-файлы из goal_dir и вернуть DF с каноническими именами колонок."""
    raw_rows = _read_tsv_dir(goal_dir)
    if not raw_rows:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        parsed: dict[str, Any] = {}
        if "Date" in row:
            parsed["date"] = _parse_date_field(row.get("Date"))
        if "CampaignId" in row:
            parsed["campaign_id"] = _to_optional_str(row.get("CampaignId"))
        if "CampaignName" in row:
            parsed["campaign_name"] = _to_optional_str(row.get("CampaignName"))
        if "AdGroupId" in row:
            parsed["ad_group_id"] = _to_optional_str(row.get("AdGroupId"))
        if "Query" in row:
            parsed["query"] = row.get("Query") or ""
        if "MatchType" in row:
            parsed["match_type"] = _to_optional_str(row.get("MatchType"))
        if "Device" in row:
            parsed["device"] = _to_optional_str(row.get("Device"))
        if "LocationOfPresenceId" in row:
            parsed["location_of_presence_id"] = _to_optional_str(row.get("LocationOfPresenceId"))
        if "LocationOfPresenceName" in row:
            parsed["location_of_presence_name"] = _to_optional_str(row.get("LocationOfPresenceName"))
        parsed[conv_col_name] = _parse_int_field(row.get("Conversions"))
        rows.append(parsed)
    return pd.DataFrame(rows)


def _join_goal_convs(
    base_df: pd.DataFrame,
    goals_base_dir: Path,
    goal_ids: list[str],
    key_cols: list[str],
) -> pd.DataFrame:
    """LEFT JOIN goal-отчётов на base_df по key_cols.

    Отсутствующая строка в goal-отчёте -> 0 (не null).
    Если ключ в base_df не уникален — raise (умножение строк расхода недопустимо).
    Проверяет сумму cost_rub до и после джойна (cost_normalized на этом слое
    всегда null — сравнивать его сумму бессмысленно, реальная валютная
    величина, которую нельзя терять/множить джойном — cost_rub).
    """
    key_subset = [c for c in key_cols if c in base_df.columns]
    if not base_df.empty and base_df.duplicated(subset=key_subset).any():
        dup_sample = base_df[base_df.duplicated(subset=key_subset, keep=False)].head(3)
        raise ValueError(
            f"Ключ direct-отчёта не уникален; джойн с целями умножит строки расхода. "
            f"Первые дубликаты: {dup_sample[key_subset].to_dict('records')}"
        )

    cost_before = float(base_df["cost_rub"].sum()) if "cost_rub" in base_df.columns else 0.0
    result = base_df.copy()

    for goal_id in goal_ids:
        col = f"goal_conv_{goal_id}"
        goal_dir = goals_base_dir / f"goal_{goal_id}"
        goal_df = _build_goal_frame(goal_dir, conv_col_name=col)

        if goal_df.empty:
            result[col] = 0
            continue

        merge_key = [c for c in key_subset if c in goal_df.columns]
        if not merge_key:
            result[col] = 0
            continue

        # При дублях в goal-отчёте по ключу — агрегируем (sum).
        if goal_df.duplicated(subset=merge_key).any():
            goal_df = goal_df.groupby(merge_key, dropna=False)[col].sum().reset_index()

        result = result.merge(
            goal_df[merge_key + [col]],
            on=merge_key, how="left",
        )
        result[col] = result[col].fillna(0).astype("Int64")

    # Инвариант: сумма cost_rub не должна измениться.
    if "cost_rub" in result.columns:
        cost_after = float(result["cost_rub"].sum())
        if abs(cost_before - cost_after) > 0.01:
            raise ValueError(
                f"cost_rub изменился после джойна с целями: "
                f"{cost_before:.4f} → {cost_after:.4f}. "
                "Возможно, ключ в goal-отчёте не уникален."
            )

    return result


def _write_direct_table(
    df: pd.DataFrame, base_table_name: str, out_path: Path,
    goal_ids: list[str] | None = None,
) -> None:
    """Записать direct-таблицу с динамическими goal_conv_<id> колонками."""
    schema = dict(SCHEMAS[base_table_name])
    for gid in (goal_ids or []):
        schema[f"goal_conv_{gid}"] = "int"

    fields_arrow = [pa.field(col, _ARROW_TYPES[t]) for col, t in schema.items()]
    arrow_schema = pa.schema(fields_arrow)
    arrays = [
        pa.array(_column_values(df, col, t), type=_ARROW_TYPES[t])
        for col, t in schema.items()
    ]
    table = pa.Table.from_arrays(arrays, schema=arrow_schema)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)


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


def build_seo_queries_gsc(
    gsc_dir: Path,
    config: dict[str, Any],
    manifest_gsc_entry: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """data/raw/gsc/gsc_*.{csv,parquet} -> строки seo_queries (engine=google).

    GSC даёт срез (query, page, device); device в канонической схеме нет,
    поэтому агрегируем по (query, page, month), суммируя clicks/impressions
    и беря средневзвешенную по impressions позицию. Месяц без device («unknown»)
    не отбрасывается — device не участвует в группировке.
    source_mode/completeness берутся из manifest_gsc_entry (по умолчанию api/verified).
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
    source_mode = (manifest_gsc_entry or {}).get("source_mode", "api")
    completeness = (manifest_gsc_entry or {}).get("completeness", "verified")
    return pd.DataFrame({
        "query": grouped["query"],
        "page": grouped["page"],
        "source": "gsc",
        "month": grouped["month"],
        "total_shows": grouped["impressions"].astype(int),
        "total_clicks": grouped["clicks"].astype(int),
        "avg_show_position": grouped["position_avg"],
        "is_brand": grouped["query"].apply(lambda q: is_brand_query(q, brand_terms)),
        "source_mode": source_mode,
        "completeness": completeness,
    })


def build_seo_queries_webmaster(
    webmaster_dir: Path,
    manifest_webmaster_entry: dict[str, Any] | None,
    config: dict[str, Any],
) -> pd.DataFrame:
    """data/raw/webmaster/search_queries_popular.json -> строки seo_queries.

    Популярные запросы Вебмастера агрегированы за всё окно. month фиксируется
    как месяц окончания окна выгрузки (манифест webmaster.date_to).
    Поле page берётся из JSON-объекта (новый контракт после 3B-patch);
    при отсутствии поля — null (обратная совместимость со старым форматом).
    ctr/demand — из indicators.CTR/DEMAND; null при отсутствии.
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
    source_mode = (manifest_webmaster_entry or {}).get("source_mode", "api")
    completeness = (manifest_webmaster_entry or {}).get("completeness", "verified")

    rows: list[dict[str, Any]] = []
    for item in queries:
        indicators = item.get("indicators") or {}
        query = item.get("query_text") or ""
        position_avg = indicators.get("AVG_CLICK_POSITION")
        if position_avg is None:
            position_avg = indicators.get("AVG_SHOW_POSITION")
        ctr_raw = indicators.get("CTR")
        demand_raw = indicators.get("DEMAND")
        rows.append({
            "query": query,
            "page": item.get("page"),   # null если поле отсутствует (старый формат)
            "source": "webmaster",
            "month": month,
            "total_shows": int(indicators.get("TOTAL_SHOWS") or 0),
            "total_clicks": int(indicators.get("TOTAL_CLICKS") or 0),
            "avg_show_position": float(position_avg) if position_avg is not None else None,
            "is_brand": is_brand_query(query, brand_terms),
            "source_mode": source_mode,
            "completeness": completeness,
            "ctr": float(ctr_raw) if ctr_raw is not None else None,
            "demand": int(demand_raw) if demand_raw is not None else None,
        })
    return pd.DataFrame(rows)


# ═══════════════════════════ Чтение сырья: site_crawl ═══════════════════════
def build_site_pages(site_crawl_dir: Path) -> pd.DataFrame:
    """data/raw/site_crawl/pages.parquet -> site_pages (URL-нормализация и дедуп по url)."""
    path = Path(site_crawl_dir) / "pages.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return dedupe_site_pages(df)


def build_site_link_graph(site_crawl_dir: Path) -> pd.DataFrame:
    """data/raw/site_crawl/link_graph.parquet -> site_link_graph (URL-нормализация и дедуп)."""
    path = Path(site_crawl_dir) / "link_graph.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    return dedupe_site_link_graph(df)


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
        # T02/T03: carry-forward источника для internal/undefined (resolve_traffic_source).
        "last_sign_traffic_source_raw": "string",
        "source_group_resolved": "string",
        "traffic_source_resolved": "bool",
    },
    "costs": {
        "date": "date", "source_tag": "string", "campaign_id": "string",
        "campaign_name": "string",
        "cost_raw": "float", "cost_normalized": "float", "cost_status": "string",
        "clicks": "int", "impressions": "int",
    },
    "direct_queries": {
        # cost_raw — микрорубли (int64), cost_rub — валютная конверсия (float64,
        # cost_raw/1_000_000, считается всегда). cost_normalized — НДС-нормализация,
        # null до ответа на Q01 (заполняется в compute); vat_basis_applied — флаг,
        # применён ли Q01 к этой строке. Не путать cost_rub с cost_normalized
        # (см. _parse_cost, задача 4X-direct-normalize-2).
        "date": "date",
        "campaign_id": "string", "campaign_name": "string",
        "ad_group_id": "string", "query": "string",
        "match_type": "string", "device": "string",
        "cost_raw": "int", "cost_rub": "float",
        "cost_normalized": "float", "vat_basis_applied": "bool",
        "clicks": "int", "impressions": "int", "conversions_all": "int",
        # goal_conv_<id> колонки добавляются динамически через _write_direct_table
    },
    "direct_campaigns": {
        "date": "date",
        "campaign_id": "string", "campaign_name": "string", "device": "string",
        "cost_raw": "int", "cost_rub": "float",
        "cost_normalized": "float", "vat_basis_applied": "bool",
        "clicks": "int", "impressions": "int", "conversions_all": "int",
    },
    "direct_geo": {
        "date": "date",
        "campaign_id": "string", "campaign_name": "string",
        "location_of_presence_id": "string", "location_of_presence_name": "string",
        "device": "string",
        "cost_raw": "int", "cost_rub": "float",
        "cost_normalized": "float", "vat_basis_applied": "bool",
        "clicks": "int", "impressions": "int", "conversions_all": "int",
    },
    "direct_placements": {
        "placement": "string", "ad_network_type": "string", "campaign_id": "string",
        "cost_raw": "int", "cost_rub": "float",
        "cost_normalized": "float", "vat_basis_applied": "bool",
        "clicks": "int", "conversions_all": "int",
    },
    "geo": {
        # Отдельная от direct_geo таблица: та же исходная выгрузка, но с явной
        # колонкой month (см. build_direct_geo_monthly, задача 4X-direct-normalize).
        "month": "string", "date": "date",
        "campaign_id": "string", "campaign_name": "string",
        "location_of_presence_id": "string", "location_of_presence_name": "string",
        "device": "string",
        "cost_raw": "int", "cost_rub": "float",
        "cost_normalized": "float", "vat_basis_applied": "bool",
        "clicks": "int", "impressions": "int", "conversions_all": "int",
    },
    "campaign_strategies": {
        "campaign_id": "string", "campaign_name": "string", "strategy_type": "string",
        "optimize_for": "string",
    },
    "seo_queries": {
        "query": "string", "page": "string", "source": "string", "month": "string",
        "total_shows": "int", "total_clicks": "int", "avg_show_position": "float",
        "is_brand": "bool",
        "source_mode": "string",    # api | manual
        "completeness": "string",   # verified | unverified
        "ctr": "float",             # из indicators.CTR (Вебмастер); null для GSC
        "demand": "int",            # из indicators.DEMAND (Вебмастер); null для GSC
    },
    "site_pages": {
        "url": "string", "http_status": "int", "redirect_chain": "string",
        "final_url": "string", "canonical_url": "string", "robots_directive": "string",
        "in_sitemap": "bool", "title": "string", "description": "string",
        "h1": "string", "crawled_at": "string", "js_content_diff": "string",
    },
    "site_link_graph": {
        "from_url": "string", "to_url": "string", "depth_from_home": "int",
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
            raw_dir / "metrika_logs", config, defaults, sources.get("metrika_logs")
        )
        if not visits_df.empty:
            # lookback-визиты (is_lookback_only=True) нужны build_visits только
            # для carry-forward источника (T02/T03) — в parquet, отдаваемый
            # compute, не попадают (см. докстринг build_visits).
            report_visits_df = visits_df[
                visits_df["is_lookback_only"] == False  # noqa: E712
            ].reset_index(drop=True)
        else:
            report_visits_df = visits_df
        if not report_visits_df.empty:
            write_canonical_table(report_visits_df, "visits", canonical_dir / "visits.parquet")
            built.append("visits")
            flags["utm_uncertain"] = utm_uncertain
            # Обязательный caveat T02/T03: доля internal/undefined визитов, которых
            # carry-forward не смог восстановить (см. resolve_traffic_source).
            flags["traffic_source_resolve"] = backfill_stats.pop("traffic_source_resolve", {})
            # Статистика склейки base+backfill (в т.ч. unmatched и недоступность
            # is_robot) — фиксируется для аналитика, а не «молча».
            flags["metrika_backfill"] = backfill_stats

    direct_dir = raw_dir / "direct" if "direct" in sources else None
    direct_entry = sources.get("direct")
    finance_cfg = config.get("finance") or {}
    vat_basis = finance_cfg.get("vat_basis_by_source") or []
    costs_df = build_costs(direct_dir, direct_entry, config, defaults, vat_basis)
    if not costs_df.empty:
        write_canonical_table(costs_df, "costs", canonical_dir / "costs.parquet")
        built.append("costs")

    if direct_dir is not None:
        direct_cfg = (config.get("sources") or {}).get("direct") or {}
        macro_goals = direct_cfg.get("macro_goals") or []
        goal_ids = [str(g["id"]) for g in macro_goals] if macro_goals else []

        dq_df = build_direct_queries(direct_dir, direct_entry, macro_goals=macro_goals)
        if not dq_df.empty:
            _write_direct_table(dq_df, "direct_queries",
                                canonical_dir / "direct_queries.parquet", goal_ids)
            built.append("direct_queries")

        dc_df = build_direct_campaigns(direct_dir, direct_entry, macro_goals=macro_goals)
        if not dc_df.empty:
            _write_direct_table(dc_df, "direct_campaigns",
                                canonical_dir / "direct_campaigns.parquet", goal_ids)
            built.append("direct_campaigns")

        dg_df = build_direct_geo(direct_dir, direct_entry, macro_goals=macro_goals)
        if not dg_df.empty:
            _write_direct_table(dg_df, "direct_geo",
                                canonical_dir / "direct_geo.parquet", goal_ids)
            built.append("direct_geo")

        dp_df = build_direct_placements(direct_dir)
        if not dp_df.empty:
            write_canonical_table(
                dp_df, "direct_placements", canonical_dir / "direct_placements.parquet"
            )
            built.append("direct_placements")

        geo_monthly_df = build_direct_geo_monthly(direct_dir)
        if not geo_monthly_df.empty:
            write_canonical_table(geo_monthly_df, "geo", canonical_dir / "geo.parquet")
            built.append("geo")

        cs_df = build_campaign_strategies(direct_dir)
        if not cs_df.empty:
            write_canonical_table(
                cs_df, "campaign_strategies", canonical_dir / "campaign_strategies.parquet"
            )
            built.append("campaign_strategies")

        # ad_texts: активные (State=="ACTIVE") -> canonical/ad_texts.json (для
        # будущей LLM-проверки A20-A24); остальные состояния не удаляются —
        # пишутся отдельно в canonical/ad_texts_archived.json. Ленивый импорт —
        # direct_normalize импортирует build_canonical на верхнем уровне, прямой
        # импорт здесь на верхнем уровне модуля дал бы циклический импорт.
        from . import direct_normalize as _direct_normalize
        active_ads, archived_ads = _direct_normalize.filter_ad_texts_by_state(direct_dir)
        if (direct_dir / "ad_texts.json").exists():
            with (canonical_dir / "ad_texts.json").open("w", encoding="utf-8") as fh:
                json.dump({"ads": active_ads}, fh, ensure_ascii=False, indent=2)
            with (canonical_dir / "ad_texts_archived.json").open("w", encoding="utf-8") as fh:
                json.dump({"ads": archived_ads}, fh, ensure_ascii=False, indent=2)
            flags["ad_texts"] = {"active_count": len(active_ads), "archived_count": len(archived_ads)}

    seo_frames: list[pd.DataFrame] = []
    if "gsc" in sources:
        seo_frames.append(build_seo_queries_gsc(raw_dir / "gsc", config, sources.get("gsc")))
    if "webmaster" in sources:
        seo_frames.append(
            build_seo_queries_webmaster(raw_dir / "webmaster", sources.get("webmaster"), config)
        )
    seo_frames = [f for f in seo_frames if not f.empty]
    if seo_frames:
        seo_df = pd.concat(seo_frames, ignore_index=True)
        # Дедуп по натуральному ключу (query, page, source): одна запись на пару
        # (запрос, страница) внутри каждого источника. Webmaster+GSC с одинаковым
        # (query, page) — разные строки (разный source), не конфликт.
        seo_df = seo_df.drop_duplicates(
            subset=["query", "page", "source"], keep="first"
        ).reset_index(drop=True)
        write_canonical_table(seo_df, "seo_queries", canonical_dir / "seo_queries.parquet")
        built.append("seo_queries")

    if "crm" in sources:
        crm_df = build_crm(raw_dir / "crm")
        if not crm_df.empty:
            write_canonical_table(crm_df, "crm", canonical_dir / "crm.parquet")
            built.append("crm")

    if "site_crawl" in sources:
        site_crawl_dir = raw_dir / "site_crawl"
        sp_df = build_site_pages(site_crawl_dir)
        if not sp_df.empty:
            write_canonical_table(sp_df, "site_pages", canonical_dir / "site_pages.parquet")
            built.append("site_pages")
        slg_df = build_site_link_graph(site_crawl_dir)
        if not slg_df.empty:
            write_canonical_table(slg_df, "site_link_graph", canonical_dir / "site_link_graph.parquet")
            built.append("site_link_graph")

    _write_canonical_manifest(canonical_dir, built, flags)
    return built
