"""Экстрактор: Яндекс.Директ (расходы, запросы, площадки, таргетинг, тексты).

Контракт:
    Читает   — config.sources.direct (client_login, attribution_type, macro_goals),
               DIRECT_TOKEN, окно дат.
    Пишет    — data/raw/direct/ (расходы по кампаниям, отчёт по поисковым
               запросам, GEO-отчёт, площадки РСЯ, стратегии кампаний, настройки
               таргетинга, тексты объявлений, ключевые фразы, товарный фид) +
               manifest.json (canonical_tables: [costs, direct_queries]).
    Деградация — опционален; без Директа проверки с requires=[costs] опираются
                 только на config.costs_manual, а requires=[direct_queries]
                 уходят в degradation_report. Отдельные ВТОРИЧНЫЕ отчёты
                 (площадки/таргетинг/тексты/фид/ключи) при недоступности не
                 роняют весь источник: пишется note в manifest, ядро (расходы +
                 запросы + стратегии) остаётся.
    LLM      — не используется.

Что выгружаем:
    1. campaigns/YYYY-MM.tsv — CAMPAIGN_PERFORMANCE_REPORT по дням (+ ПРОБНЫЕ
       WeightedImpressions/LostImpressionShare). Помесячные чанки.
       Legacy: campaign_performance.tsv — слияние чанков для обратной совместимости.
    2. queries/YYYY-MM.tsv — SEARCH_QUERY_PERFORMANCE_REPORT с полным набором
       измерений (Date, CampaignId, CampaignName, AdGroupId, Query, MatchType).
       Device не запрашивается — API его отклоняет (error 4000). Помесячные
       чанки. Legacy: search_query_performance.tsv.
    3. geo/YYYY-MM.tsv — гео-отчёт через ReportType=CUSTOM_REPORT (Date,
       CampaignId, CampaignName, LocationOfPresenceId, LocationOfPresenceName,
       Device) — ReportType=GEO_PERFORMANCE_REPORT не существует (error 8000).
       Помесячные чанки.
    4. queries/goals/goal_<id>/YYYY-MM.tsv — конверсии по цели для запросов.
    5. campaigns/goals/goal_<id>/YYYY-MM.tsv — конверсии по цели для кампаний.
    6. geo/goals/goal_<id>/YYYY-MM.tsv — конверсии по цели для гео.
    7. placements/placement_performance.tsv — отчёт по площадкам РСЯ/сетей (A15).
    8. campaign_strategies.json  — campaigns.get (BiddingStrategy) с фильтром
       States=ALL (включая ARCHIVED) — для 0.4 «клики vs конверсии» и D08.
    9. campaign_targeting.json    — гео (adgroups.RegionIds), устройства/расписание/
       корректировки ставок (bidmodifiers.get) по кампании/группе (A12–A14, A16).
    10. ad_texts.json              — тексты объявлений (ads.get: Title/Title2/
        Text/Href) + уточнения-расширения (adextensions.get: только CalloutText,
        единственный тип расширения CALLOUT). Цену/акцию/срок/наличие API не
        отдаёт — см. ADEXTENSION_TYPES и manifest.ad_extensions_caveat (A20–A24).
    11. keywords.parquet           — ключевые фразы с типом соответствия
        (keywords.get) — ОТДЕЛЬНО от search queries (A11, A18).
    12. product_feed.parquet       — метаданные всех товарных фидов кабинета
        (feeds.get БЕЗ SelectionCriteria — Ids заранее не нужен, см. _fetch_feed);
        если фидов нет — файл не создаётся, manifest.feed_used=false (A25).

Приёмочные флаги в manifest (проверяются на первом реальном прогоне):
    campaign_report_has_lost_impression_share — читается методологией A07 через
        механизм type_downgrade_if (A -> B, если доли потерянных показов нет).
    archived_campaigns_retrievable — свойство API (не клиента): реально ли
        campaigns.get со States=ALL отдаёт архивные/удалённые кампании (D08).
    macro_goals_configured — True если config.sources.direct.macro_goals не пуст.
    period_logs — список {date_from, date_to, rows} по каждому чанку каждого отчёта
        (для диагностики расхождений типа «UI vs export» — step0 issue).
    strategy_field_present (2A-direct-strategy-fix) — True если хотя бы одна
        кампания в ответе campaigns.get вернула вложенное поле
        TextCampaign.BiddingStrategy (запрошено через отдельный параметр
        TextCampaignFieldNames, НЕ через верхнеуровневый FieldNames — "Strategy"
        не входит в enum campaigns.get, подтверждено боевым error 8000 2026-07-22,
        см. CAMPAIGNS_FIELD_NAMES_ENUM). Сырые образцы (Search/Network ->
        BiddingStrategyType) фиксируются по факту в strategy_field_samples,
        имя поля не угадывается.
    statistics_field_scope (2A-direct-strategy) — период поля Statistics в
        campaigns.get: "rolling_window" | "all_time" | "unknown". Код НЕ передаёт
        StatisticsCrit в campaigns.get (такого параметра там нет — см.
        _fetch_strategies), поэтому сравнение «с явным периодом vs без» невозможно
        без живого прогона с реальным токеном; до этого поле = "unknown" (не
        угадывается, principle 5.d).
    ad_extensions_price_fields_available (FIX-ad-extensions-coverage) — всегда
        False: adextensions.get в Директ API v5 отдаёт только текст уточнений
        (CALLOUT / CalloutText), структурных полей цены/акции/срока/наличия для
        A24 в API нет (постоянное ограничение, не зависит от прогона — как D11).
        A21/A23 не затронуты (им расширения не нужны), A22 — отдельная LLM-
        проверка. Список затронутых проверок и причина — в
        manifest.ad_extensions_caveat; тип(ы) — ad_extensions_types_available.

ВНИМАНИЕ ПРО ДЕНЬГИ (принцип 7):
    Поле Cost в отчётах Директа приходит в МИКРОРУБЛЯХ — рубли = Cost / 1_000_000.
    Это расход НЕТТО, без НДС. Само деление выполняет слой transform (raw хранит
    ответ как есть); здесь мы лишь фиксируем базис в manifest:
        cost_basis = "net_no_vat"
    чтобы ниже по конвейеру никто не сложил его с суммами, включающими НДС.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.4.0"
SOURCE = "direct"
CANONICAL_TABLES = ["costs", "direct_queries", "campaign_status"]

# Базис расхода: НЕТТО без НДС, Cost в микрорублях (деление — в transform).
COST_BASIS = "net_no_vat"
COST_MICROS_PER_RUB = 1_000_000

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"
CAMPAIGNS_URL = "https://api.direct.yandex.com/json/v5/campaigns"
ADGROUPS_URL = "https://api.direct.yandex.com/json/v5/adgroups"
ADS_URL = "https://api.direct.yandex.com/json/v5/ads"
ADEXTENSIONS_URL = "https://api.direct.yandex.com/json/v5/adextensions"
KEYWORDS_URL = "https://api.direct.yandex.com/json/v5/keywords"
BIDMODIFIERS_URL = "https://api.direct.yandex.com/json/v5/bidmodifiers"
FEEDS_URL = "https://api.direct.yandex.com/json/v5/feeds"

TARGETING_RAW_ARTIFACT = "direct/campaign_targeting.json"
ADGROUPS_TARGETING_FIELDS = ["Id", "Name", "CampaignId", "RegionIds", "NegativeKeywords"]
BID_MODIFIERS_TARGETING_FIELDS = ["Id", "CampaignId", "AdGroupId", "Type"]
BID_MODIFIER_TARGETING_TYPES = ["DEMOGRAPHICS", "DESKTOP", "MOBILE", "REGIONAL"]

# Число повторов при статусе «отчёт готовится» (201/202 + Retry-In).
REPORT_MAX_POLLS = 60
REPORT_POLL_DEFAULT_SEC = 10.0

# CAMPAIGN_PERFORMANCE: базовые поля + ПРОБНЫЕ поля доли потерянных показов.
# Наличие/имя LostImpressionShare в Директ API не гарантировано (открытый вопрос
# спецификации: это может быть только UI-метрика). Поэтому поля ПРОБНЫЕ —
# заказываем полный состав, при отклонении откатываемся на базовый, а
# manifest.campaign_report_has_lost_impression_share фиксирует факт наличия
# (принцип «проверь фактическую доступность, не предполагай»).
CAMPAIGN_FIELDS = [
    "Date", "CampaignId", "CampaignName", "Device",
    "Cost", "Clicks", "Impressions", "Conversions",
]
CAMPAIGN_FIELDS_LOST_IS = ["WeightedImpressions", "LostImpressionShare"]

# Измерения CAMPAIGN для goal-отчётов (только Conversions из метрик).
CAMPAIGN_FIELDS_GOAL = [
    "Date", "CampaignId", "CampaignName", "Device", "Conversions",
]

# SEARCH_QUERY_PERFORMANCE: полный набор измерений + метрики.
# Device убран (2B-patch-2): подтверждено на реальном аккаунте (error 4000) —
# Device не допустим для SEARCH_QUERY_PERFORMANCE_REPORT.
QUERY_FIELDS = [
    "Date", "CampaignId", "CampaignName", "AdGroupId", "Query", "MatchType",
    "Cost", "Clicks", "Impressions", "Conversions",
]

# Измерения QUERY для goal-отчётов (только Conversions из метрик).
QUERY_FIELDS_GOAL = [
    "Date", "CampaignId", "CampaignName", "AdGroupId", "Query", "MatchType",
    "Conversions",
]

# GEO_PERFORMANCE: местоположение пользователя (LocationOfPresence = город показа).
GEO_FIELDS = [
    "Date", "CampaignId", "CampaignName",
    "LocationOfPresenceId", "LocationOfPresenceName", "Device",
    "Cost", "Clicks", "Impressions", "Conversions",
]

# Измерения GEO для goal-отчётов.
GEO_FIELDS_GOAL = [
    "Date", "CampaignId", "CampaignName",
    "LocationOfPresenceId", "LocationOfPresenceName", "Device",
    "Conversions",
]

# Отчёт по площадкам РСЯ/сетей (A15). Placement — домен площадки/имя приложения,
# AdNetworkType отделяет сети от поиска. Строится через CUSTOM_REPORT.
PLACEMENT_FIELDS = ["Placement", "AdNetworkType", "CampaignId", "Cost", "Clicks", "Conversions"]

# campaigns.get: фильтр States=ALL — по правилу D08 отбор по активности в периоде,
# а не по текущему статусу; ARCHIVED обязателен, иначе расход остановленных
# кампаний за прошлые месяцы потеряется.
CAMPAIGN_STATES_ALL = ["ON", "OFF", "SUSPENDED", "ENDED", "CONVERTED", "ARCHIVED"]

# CAMPAIGN_FIELD_NAMES — верхнеуровневые поля campaigns.get. "Strategy" сюда НЕ
# входит: боевой прогон 2026-07-22 подтвердил error 8000 (см.
# clients/pognali.rent/logs/extract_20260722_012238.log:63) — API вернул полный
# перечень допустимых значений enum, Strategy среди них нет. Список ниже —
# ровно этот перечень (см. CAMPAIGNS_FIELD_NAMES_ENUM), без CreateTime (не
# нужен для текущих проверок, но валиден и может быть добавлен позже).
CAMPAIGN_FIELD_NAMES = [
    "Id", "Name", "ClientInfo", "StartDate", "EndDate", "Type",
    "Status", "State", "StatusPayment", "StatusClarification",
    "SourceId", "Currency", "DailyBudget", "Funds", "Statistics",
    "RepresentedBy", "Notification", "BlockedIps", "ExcludedSites",
    "TimeTargeting", "TimeZone",
]

# Enum допустимых значений верхнеуровневого FieldNames campaigns.get — взят
# ДОСЛОВНО из текста ошибки error 8000 боевого прогона 2026-07-22 (единственный
# надёжный источник на момент задачи 2A-direct-strategy-fix, см. лог выше).
# Используется _validate_field_names для проверки ДО отправки запроса, чтобы
# невалидное имя поля (опечатка, устаревшее поле вроде Strategy) не роняло
# источник целиком повторно в будущем.
CAMPAIGNS_FIELD_NAMES_ENUM = frozenset({
    "BlockedIps", "ExcludedSites", "Currency", "DailyBudget", "Notification",
    "EndDate", "Funds", "ClientInfo", "Id", "Name", "NegativeKeywords",
    "RepresentedBy", "StartDate", "CreateTime", "Statistics", "State",
    "Status", "StatusPayment", "StatusClarification", "SourceId",
    "TimeTargeting", "TimeZone", "Type",
})

# BiddingStrategy запрашивается ОТДЕЛЬНО через TextCampaignFieldNames — это не
# элемент enum верхнеуровневого FieldNames (оттуда и была ошибка 8000 со
# "Strategy"). TEXT_CAMPAIGN — единственный тип кампаний, встречающийся у
# клиента на данный момент. Если позже появятся кампании других типов
# (MOBILE_APP_CAMPAIGN, CPM_BANNER_CAMPAIGN, UNIFIED_CAMPAIGN) — потребуется
# соответствующий свой *CampaignFieldNames для каждого типа; это НЕ
# реализовано сейчас — известное ограничение для однотипных (TEXT_CAMPAIGN)
# клиентов, а не молчаливый пробел.
TEXT_CAMPAIGN_FIELD_NAMES = ["BiddingStrategy"]

# statistics_field_scope: период поля Statistics в campaigns.get не задокументирован
# эмпирически, а JSON API v5 campaigns.get не принимает параметр периода —
# StatisticsCrit нигде не передаётся (см. _fetch_strategies ниже). Определить
# rolling_window/all_time можно только сравнением двух живых вызовов; до этого —
# "unknown". Значение НЕ может быть null по контракту задачи 2A-direct-strategy.
STATISTICS_FIELD_SCOPE_UNKNOWN = "unknown"
STATISTICS_FIELD_SCOPE_VALUES = ("rolling_window", "all_time", "unknown")

# ── adextensions.get (расширения объявлений) — A24 ────────────────────────────
# ФАКТ ПО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИИ (ref-v5/adextensions/get.html, сверено
# 2026-07-30, задача FIX-ad-extensions-coverage): Яндекс.Директ API v5 отдаёт
# РОВНО ОДИН тип расширения — CALLOUT (уточнение), и через adextensions.get
# возвращает только его текст (CalloutText) плюс служебные поля (Id / Type /
# Status / StatusClarification / Associated). Цена, акция, срок действия и
# наличие товара через adextensions.get НЕ возвращаются ни в каком поле —
# отдельного типа расширения (PRICE / PROMOTION / …) в API попросту нет.
# Поэтому «цена/акция/срок/наличие» из A24 в Директ API как СТРУКТУРНЫЕ поля
# отсутствуют: на стороне Директа сверять актуальность можно только по тексту
# объявления (ads.get: Title/Title2/Text, уже выгружается) и тексту уточнений
# (CalloutText); структурированной сверки цены/наличия против Директа не
# существует — только против сайта (A24.optional = site_crawl). Это постоянное
# свойство API (не зависит от конкретного прогона, как ограничение D11), поэтому
# фиксируется явным caveat в manifest, а не остаётся молчаливым «не проверено».
# Обходной путь не изобретаем (принцип 5.d: не угадывать несуществующее).
ADEXTENSION_TYPES = ["CALLOUT"]  # единственный существующий тип расширения

# Валидные значения FieldNames для adextensions.get (ДОСЛОВНО из документации).
# "State" сюда НЕ входит: оно присутствует в объекте ответа, но не может быть
# запрошено через FieldNames. Прежний код ошибочно просил "State" — невалидный
# элемент FieldNames роняет ВЕСЬ вызов (error 8000), ровно как было со "Strategy"
# в campaigns.get (2A-direct-strategy-fix). Валидируем состав ДО запроса через
# _validate_field_names, чтобы один невалидный элемент не убивал расширения.
ADEXTENSIONS_FIELD_NAMES_ENUM = frozenset({
    "Id", "Type", "Status", "StatusClarification", "Associated",
})
ADEXTENSIONS_FIELD_NAMES = ["Id", "Type", "Status", "StatusClarification"]

# A24 (и текстовая, не-LLM часть про «расширения» в A22) на стороне Директа не
# получают структурных полей цены/акции/срока/наличия — их нет в API; фиксируем
# явным флагом. A21 (высокий CTR + низкая CR) и A23 (конкретный спрос ->
# слишком общая страница) ЭТИМ ограничением НЕ затронуты: поля расширений им не
# нужны (A21 — CTR/CR из отчётов Директа+Метрики; A23 — посадочные URL из
# ads.get Href + визиты). Это подтверждается requires в config/methodology.yaml
# (A21: direct_queries+visits; A23: visits+costs; A24: direct_queries).
AD_EXTENSIONS_PRICE_FIELDS_AVAILABLE = False
AD_EXTENSIONS_AFFECTED_CHECKS = ["A24"]

# ── feeds.get (товарные фиды) — A25 ───────────────────────────────────────────
# ФАКТ ПО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИИ (ref-v5/feeds/get.html, сверено 2026-07-30,
# задача FIX-feeds-get-contradiction): чтобы получить ВСЕ фиды кабинета, надо
# НЕ передавать SelectionCriteria вовсе («Чтобы получить все фиды пользователя,
# не указывайте SelectionCriteria»). Поле Ids обязательно ТОЛЬКО когда
# SelectionCriteria присутствует — то есть предварительный список Id знать не
# нужно, вопреки прежнему коду. Поэтому вызываем feeds.get без SelectionCriteria
# и НЕ через _get_all (тот форсирует пустой SelectionCriteria={} через
# setdefault, что вернуло бы «Ids обязателен»). feeds.get отдаёт МЕТАДАННЫЕ
# фида (id/имя/бизнес-тип/источник/число офферов/дата обновления), но НЕ
# построчные офферы — per-offer offer_id/price/availability в API v5 нет и они
# берутся из источника фида отдельно (в этот слой не входят).
# Валидные значения FieldNames (ДОСЛОВНО из документации): Id | Name |
# BusinessType | SourceType | FilterSchema | UpdatedAt | CampaignIds |
# NumberOfItems | Status | TitleAndTextSources. Источник фида — во вложенных
# объектах UrlFeed (Url/Login/RemoveUtmTags) и FileFeed (Filename), которые
# запрашиваются отдельными UrlFeedFieldNames / FileFeedFieldNames.
FEEDS_FIELD_NAMES = ["Id", "Name", "BusinessType", "SourceType", "NumberOfItems", "UpdatedAt"]
URL_FEED_FIELD_NAMES = ["Url"]
FILE_FEED_FIELD_NAMES = ["Filename"]

# Директ отдаёт ошибки JSON-API как HTTP 200 с телом {"error": {...}} — статус
# 200 сам по себе НЕ значит успех. Разбираем error_code, чтобы падать внятно и
# не писать пустое/битое сырьё.
REG_INCOMPLETE_CODE = 58              # «Незавершённая регистрация»: приложению не
                                      # выдан доступ к API (заявка в интерфейсе
                                      # Директа + подтверждение Яндекса)
NOT_CONNECTED_CODE = 513              # «Ваш логин не подключён к Яндекс.Директу»:
                                      # у аккаунта токена нет кабинета Директа
                                      # (или нужен Client-Login кабинета клиента)
AUTH_ERROR_CODES = {53, 54, 55, 57}   # нет/просрочен/невалиден токен, нет прав
RATE_LIMIT_CODES = {56, 152, 506}     # лимит запросов / баллов -> недоступность

# Лимит окна по типу отчёта (подтверждено на реальном аккаунте).
# SEARCH_QUERY_PERFORMANCE_REPORT — 180 дней (API error 4001 при ранней дате).
# Типы без ограничения отсутствуют в словаре.
REPORT_WINDOW_LIMIT_DAYS: dict[str, int] = {
    "SEARCH_QUERY_PERFORMANCE_REPORT": 180,
}


def _error_code(err: dict[str, Any]) -> int | None:
    """error_code Директа как int (в Reports API он приходит строкой, напр. "513")."""
    try:
        return int(err.get("error_code"))
    except (TypeError, ValueError):
        return None


def _api_error(resp: Any) -> dict[str, Any] | None:
    """Вернуть блок error из JSON-ответа Директа, если он есть (иначе None).

    Декодируем content как UTF-8 явно — сервер всегда шлёт UTF-8, но requests
    может определить encoding как latin-1 (text/* без charset в заголовке).
    """
    try:
        raw = getattr(resp, "content", None)
        if raw is not None:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        else:
            payload = resp.json()
    except Exception:
        return None
    if isinstance(payload, dict):
        err = payload.get("error")
        return err if isinstance(err, dict) else None
    return None


def _raise_for_api_error(resp: Any, context: str = "") -> None:
    """Разобрать ошибку JSON-API Директа (HTTP 200 + error) и упасть внятно.

    error 58 и коды прав/токена -> «источник недоступен» (не крэш пайплайна).
    Сообщения Директа не содержат токен, поэтому их безопасно показывать.
    """
    err = _api_error(resp)
    if not err:
        return
    code = _error_code(err)
    detail = err.get("error_detail") or err.get("error_string") or ""
    if code == REG_INCOMPLETE_CODE:
        raise C.SourceUnavailable(
            SOURCE,
            "Директу не выдан доступ к API (error 58, «Незавершённая регистрация»): "
            "оформи заявку на доступ к API в интерфейсе Яндекс.Директа и дождись "
            "подтверждения. Токен валиден, но приложение ещё не одобрено.",
        )
    if code == NOT_CONNECTED_CODE:
        raise C.SourceUnavailable(
            SOURCE,
            "Логин аккаунта не подключён к Яндекс.Директу (error 513): подключи "
            "кабинет Директа к этому логину либо укажи client_login кабинета "
            "клиента в config.sources.direct.",
        )
    if code in AUTH_ERROR_CODES:
        raise C.AuthError(SOURCE, C.auth_dead_message(SOURCE))
    context_suffix = f" [{context}]" if context else ""
    raise C.SourceUnavailable(SOURCE, f"Директ API error {code}: {detail}{context_suffix}")


def _auth_headers(token: str, client_login: str | None) -> dict[str, str]:
    """Заголовки Директа. Токен нигде не логируется."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "processingMode": "auto",
        # Расход БЕЗ НДС и в валюте кабинета (рубли), Cost — в микрорублях.
        "returnMoneyInMicros": "true",
        # skipReportHeader истинно ломает выгрузку SEARCH_QUERY_PERFORMANCE_REPORT
        # и CUSTOM_REPORT (геоотчёт, площадки) на боевом аккаунте — API отвечает
        # ошибкой для этих двух типов отчёта (CAMPAIGN_PERFORMANCE_REPORT при этом
        # отрабатывает нормально). Возвращено на "false": защита от служебной
        # строки-названия отчёта реализована на стороне transform (_read_tsv в
        # build_canonical.py — отбрасывает первую/последнюю строку без табуляции)
        # и не зависит от этого заголовка API вообще.
        "skipReportHeader": "false",
        "skipReportSummaryRow": "true",
    }
    if client_login:
        headers["Client-Login"] = client_login
    return headers


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости DIRECT_TOKEN (campaigns.get с limit=0)."""
    import requests

    direct = (config.get("sources") or {}).get("direct") or {}
    try:
        token = C.get_token(env, "DIRECT_TOKEN", SOURCE)
    except C.AuthError:
        return False

    headers = _auth_headers(token, direct.get("client_login"))
    body = {"method": "get", "params": {
        "SelectionCriteria": {}, "FieldNames": ["Id"], "Page": {"Limit": 1}}}
    session = requests.Session()
    try:
        resp = C.http_request(
            session, "POST", CAMPAIGNS_URL,
            source=SOURCE, headers=headers, json=body, timeout=30,
        )
        if getattr(resp, "status_code", 500) >= 400:
            return False
        # HTTP 200 ещё не успех: Директ кладёт ошибку (в т.ч. error 58) в тело.
        _raise_for_api_error(resp, "ping")
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
    """Выгрузить расходы, запросы, площадки, стратегии, таргетинг, тексты,
    ключевые фразы и товарный фид в data/raw/direct/.

    Если manifest.json содержит primary_window + compare_window (записал intake),
    выгрузка выполняется дважды: primary -> .../primary/, compare -> .../compare/.
    Все шаги (ретраи, чанкинг отчётов) применяются к каждому окну независимо.
    """
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    direct = (config.get("sources") or {}).get("direct") or {}
    client_login = direct.get("client_login")

    # Валидация конфига целей до начала выгрузки.
    attribution_type = (direct.get("attribution_type") or "").strip()
    macro_goals = direct.get("macro_goals") or []
    if macro_goals and not attribution_type:
        raise C.SourceUnavailable(
            SOURCE,
            "attribution_type не указан в config.sources.direct, но macro_goals не пуст. "
            "Укажи явно тип атрибуции: LSC | LC | FC | LYDC.",
        )

    token = C.get_token(env, "DIRECT_TOKEN", SOURCE)
    headers = _auth_headers(token, client_login)

    (date_from, date_to), compare_window = C.resolve_windows(
        paths.raw, config, defaults, today=today
    )
    has_compare = compare_window is not None
    base_dir = C.source_dir(paths, SOURCE)

    from datetime import date as _date_cls
    _today = today if isinstance(today, _date_cls) else _date_cls.today()

    windows: list[tuple] = [(date_from, date_to, "primary")]
    if has_compare:
        windows.append((compare_window[0], compare_window[1], "compare"))

    last_result: dict[str, Any] = {}
    for win_from, win_to, slot in windows:
        if has_compare:
            out_dir = C.reset_dir(base_dir / slot)
            source_key = SOURCE if slot == "primary" else f"{SOURCE}/compare"
        else:
            out_dir = C.reset_dir(base_dir)
            source_key = SOURCE
        log(f"{SOURCE}: окно {C.fmt(win_from)}..{C.fmt(win_to)}, логин {client_login or '—'}")
        last_result = _run_window_extract(
            session, headers, sleeper, win_from, win_to, out_dir, source_key, paths, log,
            direct_cfg=direct, today=_today,
        )

    return last_result


def _run_window_extract(
    session, headers, sleeper, date_from, date_to, out_dir, source_key, paths, log,
    direct_cfg=None, today: date | None = None,
) -> dict[str, Any]:
    """Выгрузить все отчёты одного временного окна в out_dir."""
    direct_cfg = direct_cfg or {}
    attribution_type = (direct_cfg.get("attribution_type") or "").strip()
    macro_goals = direct_cfg.get("macro_goals") or []
    notes: list[str] = []
    report_status: dict[str, str] = {}
    window_infos: dict[str, dict] = {}
    _today = today or date.today()

    # 1. Расходы по кампаниям — изолированно: ошибка не роняет остальные отчёты.
    campaign_dir = out_dir / "campaigns"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    campaign_period_logs: list[dict] = []
    campaign_rows = 0
    has_lost_is = False
    lost_is_requested = False

    try:
        campaign_period_logs, lost_is_requested, campaign_rows, _cw = \
            _fetch_campaign_report_monthly(
                session, headers, sleeper,
                date_from=date_from, date_to=date_to, out_dir=campaign_dir,
                log=log, today=_today,
            )
        merged_campaign = _merge_monthly_tsv(campaign_dir)
        # Legacy-файл для обратной совместимости с тестами и downstream кодом.
        (out_dir / "campaign_performance.tsv").write_text(merged_campaign, encoding="utf-8")
        has_lost_is = _has_lost_impression_share(merged_campaign, lost_is_requested)
        if not lost_is_requested:
            notes.append("поля доли потерянных показов (LostImpressionShare) не приняты "
                         "Reports API — CAMPAIGN_PERFORMANCE_REPORT выгружен без них (A07 -> тип B)")
        report_status["campaigns"] = "ok"
        window_infos["campaigns"] = _cw
    except C.AuthError:
        raise
    except C.SourceUnavailable as _exc:
        notes.append(f"CAMPAIGN_PERFORMANCE_REPORT недоступен: {_exc}")
        report_status["campaigns"] = "failed"
        window_infos["campaigns"] = {}

    # 2. Отчёт по поисковым запросам — изолированно.
    queries_dir = out_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    query_period_logs: list[dict] = []
    query_rows = 0

    try:
        query_period_logs, query_rows, _qw = _fetch_report_monthly(
            session, headers, sleeper,
            report_name="search_query_performance",
            report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
            fields=QUERY_FIELDS,
            date_from=date_from, date_to=date_to,
            out_dir=queries_dir, log=log, today=_today,
        )
        # Legacy-файл для обратной совместимости.
        (out_dir / "search_query_performance.tsv").write_text(
            _merge_monthly_tsv(queries_dir), encoding="utf-8",
        )
        report_status["queries"] = "ok"
        window_infos["queries"] = _qw
        if _qw.get("window_truncated"):
            notes.append(
                f"SEARCH_QUERY_PERFORMANCE_REPORT: окно обрезано с "
                f"{_qw['window_requested_from']} до {_qw['window_effective_from']} "
                f"(API лимит {REPORT_WINDOW_LIMIT_DAYS['SEARCH_QUERY_PERFORMANCE_REPORT']} дней) — "
                "методологическое ограничение источника, не проблема данных"
            )
    except C.AuthError:
        raise
    except C.SourceUnavailable as _exc:
        notes.append(f"SEARCH_QUERY_PERFORMANCE_REPORT недоступен: {_exc}")
        report_status["queries"] = "failed"
        window_infos["queries"] = {}

    # 3. GEO_PERFORMANCE_REPORT — изолированно; недоступность пишется в manifest.
    geo_dir = out_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    geo_period_logs: list[dict] = []
    geo_report_available = False

    try:
        geo_period_logs, _geo_rows, _gw = _fetch_report_monthly(
            session, headers, sleeper,
            report_name="geo_performance",
            report_type="CUSTOM_REPORT",
            fields=GEO_FIELDS,
            date_from=date_from, date_to=date_to,
            out_dir=geo_dir, log=log, today=_today,
        )
        geo_report_available = True
        report_status["geo"] = "ok"
        window_infos["geo"] = _gw
    except C.AuthError:
        raise
    except C.SourceUnavailable as _exc:
        notes.append(f"GEO_PERFORMANCE_REPORT недоступен: {_exc}")
        report_status["geo"] = "failed"
        window_infos["geo"] = {"reason": str(_exc)}

    # Если все три основных типа упали — источник действительно недоступен.
    if all(v == "failed" for v in [
        report_status.get("campaigns"),
        report_status.get("queries"),
        report_status.get("geo"),
    ]):
        raise C.SourceUnavailable(
            SOURCE,
            "все основные типы отчётов Директа недоступны; "
            + "; ".join(notes[-3:]),
        )

    # 4. Площадки РСЯ/сетей (A15). Вторичный отчёт — при недоступности деградируем.
    placement_rows = 0
    try:
        placement_tsv = _fetch_report(
            session, headers, sleeper,
            report_name="placement_performance",
            report_type="CUSTOM_REPORT",
            fields=PLACEMENT_FIELDS,
            date_from=date_from, date_to=date_to,
        )
        placements_dir = out_dir / "placements"
        placements_dir.mkdir(parents=True, exist_ok=True)
        (placements_dir / "placement_performance.tsv").write_text(placement_tsv, encoding="utf-8")
        placement_rows = C.count_data_rows(placement_tsv, has_header=True)
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"отчёт по площадкам РСЯ недоступен: {exc}")

    # 5. Стратегии кампаний со States=ALL (0.4 «клики vs конверсии» + D08).
    campaigns = _fetch_strategies(session, headers, log=log)
    with (out_dir / "campaign_strategies.json").open("w", encoding="utf-8") as fh:
        json.dump(campaigns, fh, ensure_ascii=False, indent=2)
    archived_retrievable = _archived_retrievable(campaigns)
    if not archived_retrievable:
        notes.append("campaigns.get со States=ALL не вернул ни одной ARCHIVED-кампании: "
                     "это либо отсутствие архивных у клиента, либо ограничение доступа — "
                     "D08 нельзя утверждать как достоверный, см. archived_campaigns_retrievable")

    # 2A-direct-strategy-fix: BiddingStrategy запрошен через TextCampaignFieldNames
    # (не верхнеуровневый FieldNames, см. CAMPAIGNS_FIELD_NAMES_ENUM) — фиксируем
    # факт наличия и сырую структуру, не угадывая форму вложенного объекта.
    strategy_field_present = _strategy_field_present(campaigns)
    strategy_field_samples = _strategy_field_samples(campaigns)
    if not strategy_field_present:
        notes.append("campaigns.get вернул кампании без вложенного "
                     "TextCampaign.BiddingStrategy, хотя оно запрошено в "
                     "TextCampaignFieldNames — либо кампании не TEXT_CAMPAIGN, либо "
                     "поле пустое; нужна проверка на живом аккаунте, см. "
                     "strategy_field_present")
    # campaigns.get не принимает параметр периода (StatisticsCrit нигде не
    # передаётся) — без живого сравнения двух вызовов период поля Statistics
    # не определить; фиксируем как unknown, а не угадываем (2A-direct-strategy).
    statistics_field_scope = STATISTICS_FIELD_SCOPE_UNKNOWN

    # CampaignIds для вторичных вызовов JSON API v5 (adgroups/ads/keywords.get
    # требуют непустой SelectionCriteria — Ids/CampaignIds/AdGroupIds, error 4001).
    campaign_ids = [c["Id"] for c in campaigns if c.get("Id") is not None]

    # 6. Настройки таргетинга (гео/устройства/расписание/корректировки).
    targeting_progress = _new_targeting_progress(campaign_ids)
    try:
        targeting = _fetch_targeting(session, headers, campaign_ids, targeting_progress)
        with (out_dir / "campaign_targeting.json").open("w", encoding="utf-8") as fh:
            json.dump(targeting, fh, ensure_ascii=False, indent=2)
        targeting_provenance = _build_targeting_provenance(
            targeting_progress, raw_artifact_saved=True,
        )
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        targeting_provenance = _build_targeting_provenance(
            targeting_progress, raw_artifact_saved=False,
        )
        notes.append(f"настройки таргетинга недоступны: {exc}")

    # 7. Тексты объявлений + расширения.
    try:
        ad_texts = _fetch_ad_texts(session, headers, notes, campaign_ids, log=log)
        with (out_dir / "ad_texts.json").open("w", encoding="utf-8") as fh:
            json.dump(ad_texts, fh, ensure_ascii=False, indent=2)
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"тексты объявлений недоступны: {exc}")

    # 8. Ключевые фразы с типом соответствия (ОТДЕЛЬНО от search queries).
    keyword_rows = 0
    try:
        keywords = _fetch_keywords(session, headers, campaign_ids)
        keyword_rows = len(keywords)
        C.write_table(
            out_dir / "keywords", keywords,
            fields=["keyword_id", "campaign_id", "ad_group_id", "phrase", "match_type"],
            fmt="parquet",
        )
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"ключевые фразы недоступны: {exc}")

    # 9. Товарный фид (если используется). Нет фида -> файл не создаём.
    # feeds.get вызывается БЕЗ SelectionCriteria и отдаёт все фиды кабинета
    # (ref-v5/feeds/get: Ids обязателен только при наличии SelectionCriteria) —
    # предварительный список Id не нужен. feed_used выставляется по факту
    # непустого ответа (см. _fetch_feed).
    feed_used = False
    try:
        feed_rows = _fetch_feed(session, headers, notes)
        feed_used = bool(feed_rows)
        if feed_used:
            C.write_table(
                out_dir / "product_feed", feed_rows,
                fields=["feed_id", "feed_name", "business_type", "source_url",
                        "offers_count", "updated_at"],
                fmt="parquet",
            )
            notes.append("товарный фид: feeds.get отдаёт метаданные фида (id/источник/"
                         "статус синхронизации); построчные offer_id/price/availability "
                         "не выдаются Директ API и берутся из источника фида отдельно")
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"товарный фид недоступен: {exc}")

    # 10. Целевые отчёты — по каждой цели отдельный запрос для каждого типа отчёта.
    macro_goals_configured = bool(macro_goals)
    goal_period_logs: dict[str, list[dict]] = {}
    if macro_goals and attribution_type:
        for goal in macro_goals:
            goal_id = str(goal["id"])
            goals_param = [goal_id]

            # Запросы / поиск по цели.
            gq_dir = queries_dir / "goals" / f"goal_{goal_id}"
            gq_dir.mkdir(parents=True, exist_ok=True)
            gq_logs, _, _ = _fetch_report_monthly(
                session, headers, sleeper,
                report_name=f"query_goal_{goal_id}",
                report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
                fields=QUERY_FIELDS_GOAL,
                date_from=date_from, date_to=date_to,
                out_dir=gq_dir, log=log,
                goals=goals_param, attribution_type=attribution_type,
                today=_today,
            )
            goal_period_logs[f"query_goal_{goal_id}"] = gq_logs

            # Кампании по цели.
            gc_dir = campaign_dir / "goals" / f"goal_{goal_id}"
            gc_dir.mkdir(parents=True, exist_ok=True)
            gc_logs, _, _ = _fetch_report_monthly(
                session, headers, sleeper,
                report_name=f"campaign_goal_{goal_id}",
                report_type="CAMPAIGN_PERFORMANCE_REPORT",
                fields=CAMPAIGN_FIELDS_GOAL,
                date_from=date_from, date_to=date_to,
                out_dir=gc_dir, log=log,
                goals=goals_param, attribution_type=attribution_type,
                today=_today,
            )
            goal_period_logs[f"campaign_goal_{goal_id}"] = gc_logs

            # Гео по цели.
            gg_dir = geo_dir / "goals" / f"goal_{goal_id}"
            gg_dir.mkdir(parents=True, exist_ok=True)
            gg_logs, _, _ = _fetch_report_monthly(
                session, headers, sleeper,
                report_name=f"geo_goal_{goal_id}",
                report_type="CUSTOM_REPORT",
                fields=GEO_FIELDS_GOAL,
                date_from=date_from, date_to=date_to,
                out_dir=gg_dir, log=log,
                goals=goals_param, attribution_type=attribution_type,
                today=_today,
            )
            goal_period_logs[f"geo_goal_{goal_id}"] = gg_logs
    elif not macro_goals:
        notes.append(
            "целевые конверсии не выгружены — macro_goals не настроен; CPA не рассчитывается"
        )

    rows = campaign_rows + query_rows
    manifest = _record_manifest(
        paths, source_key, date_from, date_to, rows,
        has_lost_is=has_lost_is,
        archived_retrievable=archived_retrievable,
        feed_used=feed_used,
        macro_goals_configured=macro_goals_configured,
        strategy_field_present=strategy_field_present,
        strategy_field_samples=strategy_field_samples,
        statistics_field_scope=statistics_field_scope,
        campaign_period_logs=campaign_period_logs,
        query_period_logs=query_period_logs,
        geo_period_logs=geo_period_logs,
        goal_period_logs=goal_period_logs,
        notes=notes,
        report_status=report_status,
        geo_report_available=geo_report_available,
        window_infos=window_infos,
        targeting_provenance=targeting_provenance,
    )
    log(
        f"{SOURCE}: готово — расходы {campaign_rows} строк, запросы {query_rows} строк, "
        f"площадки {placement_rows}, кампаний {len(campaigns)}, ключей {keyword_rows}, "
        f"фид={'да' if feed_used else 'нет'}, "
        f"lost_impression_share={'есть' if has_lost_is else 'нет'} (cost_basis={COST_BASIS})"
    )

    return {
        "source": SOURCE,
        "rows": rows,
        "campaign_rows": campaign_rows,
        "query_rows": query_rows,
        "placement_rows": placement_rows,
        "keyword_rows": keyword_rows,
        "strategies": len(campaigns),
        "cost_basis": COST_BASIS,
        "campaign_report_has_lost_impression_share": has_lost_is,
        "archived_campaigns_retrievable": archived_retrievable,
        "feed_used": feed_used,
        "macro_goals_configured": macro_goals_configured,
        "strategy_field_present": strategy_field_present,
        "statistics_field_scope": statistics_field_scope,
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "notes": notes,
        "manifest": manifest,
    }


# ── Reports API (TSV, оффлайн-отчёты) ──────────────────────────────────────
def _fetch_campaign_report_monthly(
    session, headers, sleeper, *, date_from, date_to, out_dir: Path, log, today=None,
) -> tuple[list[dict], bool, int, dict]:
    """CAMPAIGN_PERFORMANCE_REPORT помесячно с пробой LostImpressionShare.

    Первый чанк проверяет, принимает ли API расширенные поля; если нет —
    дальнейшие чанки идут с базовым составом. Даты каждого чанка логируются.
    Возвращает (period_logs, lost_is_requested, total_rows, window_info).
    CAMPAIGN_PERFORMANCE_REPORT не имеет ограничения окна (нет в REPORT_WINDOW_LIMIT_DAYS).
    """
    window_info: dict = {
        "window_requested_from": C.fmt(date_from),
        "window_requested_to": C.fmt(date_to),
        "window_effective_from": C.fmt(date_from),
        "window_effective_to": C.fmt(date_to),
        "window_truncated": False,
    }
    chunks = C.month_chunks(date_from, date_to)
    if not chunks:
        return [], False, 0, window_info

    chunk_from, chunk_to = chunks[0]
    month_label = C.fmt(chunk_from)[:7]
    log(f"    CAMPAIGN_PERFORMANCE_REPORT: {C.fmt(chunk_from)}..{C.fmt(chunk_to)}")

    try:
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name=f"campaign_{month_label}",
            report_type="CAMPAIGN_PERFORMANCE_REPORT",
            fields=CAMPAIGN_FIELDS + CAMPAIGN_FIELDS_LOST_IS,
            date_from=chunk_from, date_to=chunk_to,
        )
        lost_is_requested = True
        effective_fields = CAMPAIGN_FIELDS + CAMPAIGN_FIELDS_LOST_IS
    except C.AuthError:
        raise
    except C.SourceUnavailable:
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name=f"campaign_{month_label}",
            report_type="CAMPAIGN_PERFORMANCE_REPORT",
            fields=CAMPAIGN_FIELDS,
            date_from=chunk_from, date_to=chunk_to,
        )
        lost_is_requested = False
        effective_fields = CAMPAIGN_FIELDS

    rows = C.count_data_rows(tsv, has_header=True)
    (out_dir / f"{month_label}.tsv").write_text(tsv, encoding="utf-8")
    period_logs = [{"date_from": C.fmt(chunk_from), "date_to": C.fmt(chunk_to), "rows": rows}]
    total_rows = rows

    for chunk_from, chunk_to in chunks[1:]:
        month_label = C.fmt(chunk_from)[:7]
        log(f"    CAMPAIGN_PERFORMANCE_REPORT: {C.fmt(chunk_from)}..{C.fmt(chunk_to)}")
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name=f"campaign_{month_label}",
            report_type="CAMPAIGN_PERFORMANCE_REPORT",
            fields=effective_fields,
            date_from=chunk_from, date_to=chunk_to,
        )
        rows = C.count_data_rows(tsv, has_header=True)
        (out_dir / f"{month_label}.tsv").write_text(tsv, encoding="utf-8")
        period_logs.append({"date_from": C.fmt(chunk_from), "date_to": C.fmt(chunk_to), "rows": rows})
        total_rows += rows

    return period_logs, lost_is_requested, total_rows, window_info


def _fetch_report_monthly(
    session, headers, sleeper, *,
    report_name: str, report_type: str, fields: list[str],
    date_from, date_to, out_dir: Path,
    goals: list[str] | None = None, attribution_type: str | None = None,
    log=None, today: date | None = None,
) -> tuple[list[dict], int, dict]:
    """Выгрузить отчёт помесячно, писать каждый чанк в out_dir/YYYY-MM.tsv.

    Логирует date_from, date_to, rows каждого чанка — ключ для диагностики
    расхождений «API export vs UI» (step0 findings).
    При наличии REPORT_WINDOW_LIMIT_DAYS для данного типа отчёта — обрезает
    date_from до max(requested, today - limit_days) до формирования чанков.
    Возвращает (period_logs, total_rows, window_info).
    """
    log = log or (lambda _: None)

    limit_days = REPORT_WINDOW_LIMIT_DAYS.get(report_type)
    window_requested_from = C.fmt(date_from)
    window_requested_to = C.fmt(date_to)
    effective_from = date_from
    window_truncated = False

    if limit_days is not None and today is not None:
        earliest = today - timedelta(days=limit_days)
        if date_from < earliest:
            effective_from = earliest
            window_truncated = True

    window_info: dict = {
        "window_requested_from": window_requested_from,
        "window_requested_to": window_requested_to,
        "window_effective_from": C.fmt(effective_from),
        "window_effective_to": window_requested_to,
        "window_truncated": window_truncated,
    }

    chunks = C.month_chunks(effective_from, date_to)
    period_logs: list[dict] = []
    total_rows = 0

    for chunk_from, chunk_to in chunks:
        month_label = C.fmt(chunk_from)[:7]
        log(f"    {report_type}: {C.fmt(chunk_from)}..{C.fmt(chunk_to)}")
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name=f"{report_name}_{month_label}",
            report_type=report_type,
            fields=fields,
            date_from=chunk_from, date_to=chunk_to,
            goals=goals,
            attribution_type=attribution_type,
        )
        rows = C.count_data_rows(tsv, has_header=True)
        (out_dir / f"{month_label}.tsv").write_text(tsv, encoding="utf-8")
        period_logs.append({
            "date_from": C.fmt(chunk_from),
            "date_to": C.fmt(chunk_to),
            "rows": rows,
        })
        total_rows += rows

    return period_logs, total_rows, window_info


def _merge_monthly_tsv(src_dir: Path) -> str:
    """Слить все YYYY-MM.tsv из src_dir в одну TSV-строку (без дублирования заголовка)."""
    tsv_files = sorted(src_dir.glob("????-??.tsv"))
    if not tsv_files:
        return ""
    header: str | None = None
    data_rows: list[str] = []
    for path in tsv_files:
        lines = [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()]
        if not lines:
            continue
        if header is None:
            header = lines[0]
        data_rows.extend(lines[1:])
    if header is None:
        return ""
    if data_rows:
        return header + "\n" + "\n".join(data_rows) + "\n"
    return header + "\n"


def _has_lost_impression_share(campaign_tsv: str, lost_is_requested: bool) -> bool:
    """Приёмочный тест A07: есть ли непустой LostImpressionShare у кампании с показами>0.

    False, если поля не запрашивались (API их не принял) или ни у одной строки с
    Impressions>0 значение LostImpressionShare не заполнено.
    """
    if not lost_is_requested:
        return False
    lines = [ln for ln in campaign_tsv.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    header = lines[0].split("\t")
    try:
        imp_idx = header.index("Impressions")
        lis_idx = header.index("LostImpressionShare")
    except ValueError:
        return False
    for line in lines[1:]:
        cells = line.split("\t")
        if max(imp_idx, lis_idx) >= len(cells):
            continue
        imp_raw = cells[imp_idx].strip()
        lis_raw = cells[lis_idx].strip()
        try:
            impressions = float(imp_raw)
        except ValueError:
            continue
        if impressions > 0 and lis_raw not in ("", "--", "0", "0.0"):
            return True
    return False


def _fetch_report(
    session, headers, sleeper, *,
    report_name, report_type, fields, date_from, date_to,
    goals: list[str] | None = None, attribution_type: str | None = None,
) -> str:
    """Заказать TSV-отчёт и дождаться готовности (201/202 + Retry-In -> 200)."""
    params: dict[str, Any] = {
        "SelectionCriteria": {
            "DateFrom": C.fmt(date_from),
            "DateTo": C.fmt(date_to),
        },
        "FieldNames": fields,
        "ReportName": f"{report_name}_{C.fmt(date_from)}_{C.fmt(date_to)}",
        "ReportType": report_type,
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "NO",       # расход НЕТТО, без НДС (cost_basis=net_no_vat)
        "IncludeDiscount": "NO",
    }
    if goals:
        params["Goals"] = goals
    if attribution_type:
        params["AttributionType"] = attribution_type
    body = {"params": params}

    for _poll in range(REPORT_MAX_POLLS):
        resp = C.http_request(
            session, "POST", REPORTS_URL,
            source=SOURCE, headers=headers, json=body, timeout=300,
        )
        status = getattr(resp, "status_code", None)
        if status == 200:
            # Reports API отдаёт ошибки статусами 4xx, но подстрахуемся: если в
            # 200 прилетел JSON-error вместо TSV — не пишем его как отчёт.
            # Декодируем явно как UTF-8: Директ всегда шлёт UTF-8, но requests
            # иногда определяет encoding как latin-1 (text/* без charset).
            raw = getattr(resp, "content", None)
            text = raw.decode("utf-8", errors="replace") if raw is not None else resp.text
            if text.lstrip().startswith("{") and '"error"' in text[:200]:
                _raise_for_api_error(resp, report_type)
            return text
        if status in (201, 202):
            # Отчёт формируется в фоне; ждём Retry-In секунд и повторяем запрос.
            wait = _retry_in(resp)
            sleeper(wait)
            continue
        # 400/500 и прочее (401/403 уже отсеяны в http_request -> AuthError).
        # У Reports API ошибка приходит телом {"error":{...}} (text/json) —
        # разбираем его для внятного сообщения (напр. error 513), иначе общий сбой.
        _raise_for_api_error(resp, report_type)
        C.ensure_ok(resp, SOURCE, f"{report_type} HTTP {status}")

    raise C.SourceUnavailable(
        SOURCE, f"{report_type}: отчёт не готов за {REPORT_MAX_POLLS} опросов"
    )


def _retry_in(response: Any) -> float:
    """Секунды ожидания из заголовка retryIn (или дефолт)."""
    try:
        header = response.headers.get("retryIn")
    except Exception:
        header = None
    try:
        return float(header) if header else REPORT_POLL_DEFAULT_SEC
    except (TypeError, ValueError):
        return REPORT_POLL_DEFAULT_SEC


# ── JSON API v5 (campaigns / adgroups / ads / keywords / bidmodifiers / feeds)
def _get_all(
    session, headers, url, params: dict[str, Any], *, result_key: str, context: str,
    progress: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Постраничный get JSON-API v5: тянем страницы по LimitedBy до исчерпания."""
    items: list[dict[str, Any]] = []
    offset = 0
    page_limit = 10000
    while True:
        page_params = dict(params)
        page_params.setdefault("SelectionCriteria", {})
        page_params["Page"] = {"Limit": page_limit, "Offset": offset}
        try:
            resp = C.http_request(
                session, "POST", url,
                source=SOURCE, headers=headers, json={"method": "get", "params": page_params},
                timeout=60,
            )
            C.ensure_ok(resp, SOURCE, context)
            _raise_for_api_error(resp, context)  # ошибка приходит как 200+error
            result = resp.json().get("result") or {}
        except Exception:
            if progress is not None:
                progress["failed"] = True
            raise
        page_items = result.get(result_key) or []
        items.extend(page_items)
        if progress is not None:
            progress["pages_fetched"] += 1
            progress["rows"] = len(items)
            progress["unique_id_values"].update(
                item.get(progress["id_field"])
                for item in page_items
                if item.get(progress["id_field"]) is not None
            )
            progress["returned_campaign_values"].update(
                item.get(progress["campaign_field"])
                for item in page_items
                if item.get(progress["campaign_field"]) is not None
            )
        limited = result.get("LimitedBy")
        if limited is None:
            if progress is not None:
                progress["pages_complete"] = True
            break
        offset = limited
    return items


def _validate_field_names(
    field_names: list[str], valid_names: frozenset[str], *,
    param_name: str, context: str, log: Callable[[str], None],
) -> list[str]:
    """Сверить имена полей с локально сохранённым enum ДО отправки запроса.

    Невалидное имя (опечатка, устаревшее поле вроде Strategy) роняло раньше
    источник целиком: error 8000 Директа приходит одной строкой на весь
    массив FieldNames, без указания конкретного элемента, и до этой задачи
    вызов ничем не был обёрнут. Отфильтровываем и логируем ДО вызова API —
    не разбираем ошибку постфактум — чтобы один невалидный элемент не блокировал
    остальные валидные поля и не убивал источник.
    """
    invalid = [f for f in field_names if f not in valid_names]
    if invalid:
        log(f"    {context}: невалидные {param_name} отфильтрованы до запроса "
            f"(нет в сохранённом enum, не будут отправлены): {invalid}")
    return [f for f in field_names if f in valid_names]


def _fetch_strategies(
    session, headers, log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """campaigns.get со States=ALL (включая ARCHIVED) и стратегиями (D08 + 0.4).

    BiddingStrategy запрашивается через TextCampaignFieldNames, а не через
    верхнеуровневый FieldNames (там валидного поля Strategy нет — error 8000,
    см. CAMPAIGNS_FIELD_NAMES_ENUM). TEXT_CAMPAIGN — единственный тип кампаний
    у клиента на данный момент; для остальных типов (MOBILE_APP_CAMPAIGN,
    CPM_BANNER_CAMPAIGN, UNIFIED_CAMPAIGN) свой *CampaignFieldNames не
    реализован — известное ограничение (2A-direct-strategy-fix).
    """
    log = log or (lambda _msg: None)
    field_names = _validate_field_names(
        CAMPAIGN_FIELD_NAMES, CAMPAIGNS_FIELD_NAMES_ENUM,
        param_name="FieldNames", context="campaigns.get", log=log,
    )
    return _get_all(
        session, headers, CAMPAIGNS_URL,
        {
            "SelectionCriteria": {"States": CAMPAIGN_STATES_ALL},
            "FieldNames": field_names,
            "TextCampaignFieldNames": TEXT_CAMPAIGN_FIELD_NAMES,
        },
        result_key="Campaigns", context="campaigns.get",
    )


def _text_campaign_bidding_strategy(campaign: dict[str, Any]) -> dict[str, Any] | None:
    """Извлечь TextCampaign.BiddingStrategy из кампании (или None, если нет).

    Ответ приходит вложенным (result.Campaigns[].TextCampaign.BiddingStrategy.
    Search/Network.BiddingStrategyType) — плоского поля Strategy на верхнем
    уровне объекта нет и не ожидается (2A-direct-strategy-fix).
    """
    text_campaign = campaign.get("TextCampaign")
    if not isinstance(text_campaign, dict):
        return None
    return text_campaign.get("BiddingStrategy")


def _strategy_field_present(campaigns: list[dict[str, Any]]) -> bool:
    """True, если хотя бы одна кампания вернула TextCampaign.BiddingStrategy.

    BiddingStrategy запрошен через TextCampaignFieldNames (2A-direct-strategy-fix),
    но API может как не вернуть его для не-TEXT_CAMPAIGN кампаний, так и не
    вернуть вовсе — сначала проверяем факт наличия, не предполагаем его.
    """
    return any(_text_campaign_bidding_strategy(c) is not None for c in campaigns)


def _strategy_field_samples(
    campaigns: list[dict[str, Any]], limit: int = 3,
) -> list[dict[str, Any]]:
    """Сырые значения TextCampaign.BiddingStrategy (до limit) — реальная структура.

    Не выдумываем форму объекта: в manifest пишутся фактические объекты
    BiddingStrategy, как их вернул API (Search/Network.BiddingStrategyType),
    чтобы аналитик/следующая задача читали факт, а не предположение.
    """
    samples: list[dict[str, Any]] = []
    for c in campaigns:
        strategy = _text_campaign_bidding_strategy(c)
        if strategy is not None:
            samples.append(strategy)
        if len(samples) >= limit:
            break
    return samples


def _archived_retrievable(campaigns: list[dict[str, Any]]) -> bool:
    """Приёмочный тест D08: реально ли в ответе есть ARCHIVED-кампании.

    True — API отдал хотя бы одну архивную (retrievable доказан). False — может
    значить как отсутствие архивных у клиента, так и фильтрацию их API; поэтому
    в manifest это трактуется как свойство доступа, требующее ручной проверки.
    """
    return any((c.get("State") or "").upper() == "ARCHIVED" for c in campaigns)


def _new_targeting_progress(campaign_ids: list[int]) -> dict[str, dict[str, Any]]:
    """Создать не содержащий строк ответа прогресс двух targeting-сервисов."""
    requested_campaign_values = {
        campaign_id for campaign_id in campaign_ids if campaign_id is not None
    }
    return {
        "ad_groups": {
            "service": "adgroups.get",
            "requested_fields": list(ADGROUPS_TARGETING_FIELDS),
            "requested_types": [],
            "requested_campaign_values": requested_campaign_values,
            "id_field": "Id",
            "campaign_field": "CampaignId",
            "attempted": False,
            "pages_fetched": 0,
            "pages_complete": False,
            "rows": 0,
            "unique_id_values": set(),
            "returned_campaign_values": set(),
            "failed": False,
        },
        "bid_modifiers": {
            "service": "bidmodifiers.get",
            "requested_fields": list(BID_MODIFIERS_TARGETING_FIELDS),
            "requested_types": sorted(BID_MODIFIER_TARGETING_TYPES),
            "requested_campaign_values": requested_campaign_values,
            "id_field": "Id",
            "campaign_field": "CampaignId",
            "attempted": False,
            "pages_fetched": 0,
            "pages_complete": False,
            "rows": 0,
            "unique_id_values": set(),
            "returned_campaign_values": set(),
            "failed": False,
        },
    }


def _build_targeting_provenance_entry(
    progress: dict[str, Any], *, raw_artifact_saved: bool,
) -> dict[str, Any]:
    """Построить детерминированную запись manifest без ID и строк клиента."""
    requested = progress["requested_campaign_values"]
    returned = progress["returned_campaign_values"]
    attempted = bool(progress["attempted"])
    pages_complete = bool(progress["pages_complete"])
    missing_count = None if not attempted else len(requested - returned)
    coverage_known = bool(attempted and pages_complete and not progress["failed"] and (
        not requested or requested.issubset(returned)
    ))

    if not attempted:
        status = "not_requested"
    elif progress["failed"]:
        status = "partial" if progress["pages_fetched"] else "failed"
    elif not pages_complete or not raw_artifact_saved:
        status = "partial"
    elif not coverage_known:
        status = "unknown"
    else:
        status = "complete"

    return {
        "service": progress["service"],
        "attempted": attempted,
        "status": status,
        "requested_fields": list(progress["requested_fields"]),
        "requested_types": list(progress["requested_types"]),
        "pages_complete": pages_complete,
        "rows": int(progress["rows"]),
        "unique_ids": {"count": len(progress["unique_id_values"])},
        "requested_campaigns": {"count": len(requested)},
        "returned_campaigns": {"count": len(returned)},
        "missing_campaigns": {"count": missing_count},
        "raw_artifact": TARGETING_RAW_ARTIFACT,
        "snapshot": {"raw_artifact_saved": raw_artifact_saved},
        "evidence": {
            "pages_fetched": int(progress["pages_fetched"]),
            "campaign_coverage": "known" if coverage_known else "unknown",
        },
    }


def _build_targeting_provenance(
    progress_by_service: dict[str, dict[str, Any]], *, raw_artifact_saved: bool,
) -> dict[str, dict[str, Any]]:
    return {
        service: _build_targeting_provenance_entry(
            progress, raw_artifact_saved=raw_artifact_saved,
        )
        for service, progress in progress_by_service.items()
    }


def _fetch_targeting(
    session, headers, campaign_ids: list[int], progress_by_service: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Гео (adgroups.RegionIds) + корректировки ставок/устройства/расписание
    (bidmodifiers.get) по кампании/группе (A12–A14, A16).

    adgroups.get требует непустой SelectionCriteria (error 4001) — фильтруем
    по CampaignIds, полученным из campaigns.get (шаг 5).
    """
    ad_groups_progress = progress_by_service["ad_groups"]
    ad_groups_progress["attempted"] = True
    ad_groups = _get_all(
        session, headers, ADGROUPS_URL,
        {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ADGROUPS_TARGETING_FIELDS,
        },
        result_key="AdGroups", context="adgroups.get", progress=ad_groups_progress,
    )
    bid_modifiers_progress = progress_by_service["bid_modifiers"]
    bid_modifiers_progress["attempted"] = True
    bid_modifiers = _get_all(
        session, headers, BIDMODIFIERS_URL,
        {
            "FieldNames": BID_MODIFIERS_TARGETING_FIELDS,
            "MobileAdjustmentFieldNames": ["BidModifier"],
            "DesktopAdjustmentFieldNames": ["BidModifier"],
            "DemographicsAdjustmentFieldNames": ["Age", "Gender", "BidModifier"],
            "RegionalAdjustmentFieldNames": ["RegionId", "BidModifier"],
        },
        result_key="BidModifiers", context="bidmodifiers.get", progress=bid_modifiers_progress,
    )
    return {"ad_groups": ad_groups, "bid_modifiers": bid_modifiers}


def _fetch_ad_texts(
    session, headers, notes: list[str], campaign_ids: list[int],
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Тексты объявлений (ads.get) + уточнения-расширения (adextensions.get).

    ВАЖНО про A24 (FIX-ad-extensions-coverage): Яндекс.Директ API v5 отдаёт
    единственный тип расширения — CALLOUT (уточнение) и только его текст
    (CalloutText). Цена, акция, срок действия и наличие товара через
    adextensions.get НЕ доступны ни в каком поле (см. ADEXTENSION_TYPES и
    комментарий над ним — сверено по ref-v5/adextensions/get.html). Поэтому для
    A24 структурных полей «цена/акция/срок/наличие» на стороне Директа нет:
    сверять можно только текст объявления (Title/Title2/Text из ads.get) и текст
    уточнений; сверка актуальности цены/наличия возможна лишь против сайта
    (A24.optional = site_crawl). Недоступность фиксируется явным caveat в
    manifest (ad_extensions_price_fields_available=false, ad_extensions_caveat),
    а не молчаливым отсутствием данных. Обходной путь не изобретаем.

    ads.get требует непустой SelectionCriteria (error 4001) — фильтруем по
    CampaignIds из campaigns.get (шаг 5). adextensions.get фильтруем по Types
    (единственный тип — CALLOUT), а FieldNames валидируем локально ДО запроса
    (тот же приём, что для Strategy), чтобы невалидное поле не роняло вызов.
    """
    log = log or (lambda _msg: None)
    ads = _get_all(
        session, headers, ADS_URL,
        {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status"],
            "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "DisplayUrlPath"],
        },
        result_key="Ads", context="ads.get",
    )
    extensions: list[dict[str, Any]] = []
    field_names = _validate_field_names(
        ADEXTENSIONS_FIELD_NAMES, ADEXTENSIONS_FIELD_NAMES_ENUM,
        param_name="FieldNames", context="adextensions.get", log=log,
    )
    try:
        extensions = _get_all(
            session, headers, ADEXTENSIONS_URL,
            {
                "SelectionCriteria": {"Types": ADEXTENSION_TYPES},
                "FieldNames": field_names,
                "CalloutFieldNames": ["CalloutText"],
            },
            result_key="AdExtensions", context="adextensions.get",
        )
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(
            "уточнения-расширения объявлений (adextensions.get, только текст "
            f"CalloutText) недоступны: {exc}"
        )
    return {"ads": ads, "extensions": extensions}


def _keyword_match_type(phrase: str | None) -> str:
    """Тип соответствия ключевой фразы по операторам Директа.

    Yandex не отдаёт MatchType отдельным полем — тип выражается операторами в
    самой фразе: кавычки/скобки/! /+ фиксируют форму/порядок/стоп-слова
    ("точное"); их отсутствие — «широкое». Автотаргетинг — отдельная сущность
    (RelevanceMatch), не ключевая фраза, поэтому здесь не появляется.
    """
    p = (phrase or "").strip()
    if not p:
        return "unknown"
    if any(op in p for op in ('"', "[", "]", "!", "+")):
        return "exact"
    return "broad"


def _fetch_keywords(session, headers, campaign_ids: list[int]) -> list[dict[str, Any]]:
    """keywords.get -> нормализованные строки ключевых фраз с типом соответствия.

    keywords.get требует непустой SelectionCriteria (error 4001) — фильтруем
    по CampaignIds, полученным из campaigns.get (шаг 5).
    """
    raw = _get_all(
        session, headers, KEYWORDS_URL,
        {
            "SelectionCriteria": {"CampaignIds": campaign_ids},
            "FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId"],
        },
        result_key="Keywords", context="keywords.get",
    )
    rows: list[dict[str, Any]] = []
    for kw in raw:
        phrase = kw.get("Keyword") or ""
        rows.append({
            "keyword_id": str(kw.get("Id")) if kw.get("Id") is not None else None,
            "campaign_id": str(kw.get("CampaignId")) if kw.get("CampaignId") is not None else None,
            "ad_group_id": str(kw.get("AdGroupId")) if kw.get("AdGroupId") is not None else None,
            "phrase": phrase,
            "match_type": _keyword_match_type(phrase),
        })
    return rows


def _feed_row(feed: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать один объект Feed из feeds.get в строку product_feed.

    source_url — из вложенного UrlFeed.Url (SourceType=URL) либо FileFeed.Filename
    (SourceType=FILE); Директ отдаёт ровно один из двух объектов на фид.
    offers_count — NumberOfItems (число офферов в фиде на момент синхронизации).
    """
    source_url = None
    url_feed = feed.get("UrlFeed")
    if isinstance(url_feed, dict):
        source_url = url_feed.get("Url")
    if source_url is None:
        file_feed = feed.get("FileFeed")
        if isinstance(file_feed, dict):
            source_url = file_feed.get("Filename")
    return {
        "feed_id": str(feed["Id"]) if feed.get("Id") is not None else None,
        "feed_name": feed.get("Name"),
        "business_type": feed.get("BusinessType"),
        "source_url": source_url,
        "offers_count": feed.get("NumberOfItems"),
        "updated_at": feed.get("UpdatedAt"),
    }


def _fetch_feed(session, headers, notes: list[str]) -> list[dict[str, Any]]:
    """feeds.get -> метаданные всех товарных фидов кабинета (пусто, если фидов нет).

    Директ API отдаёт метаданные фида (id/имя/бизнес-тип/источник/число офферов/
    дата обновления), но НЕ построчные офферы; per-offer поля
    (offer_id/price/availability) берутся из источника фида отдельно и в этот
    слой не входят.

    Чтобы получить ВСЕ фиды кабинета, SelectionCriteria НЕ передаётся вовсе
    (ref-v5/feeds/get.html: «Чтобы получить все фиды пользователя, не указывайте
    SelectionCriteria»; Ids обязателен ТОЛЬКО когда SelectionCriteria
    присутствует). Предварительный список Id знать не нужно — поэтому вызов идёт
    напрямую, а не через _get_all (тот форсирует пустой SelectionCriteria={} и
    получил бы «Ids обязателен»). feed_used выставляет вызывающий код по факту
    непустого ответа. Пагинация — по LimitedBy, как в _get_all.
    """
    items: list[dict[str, Any]] = []
    offset = 0
    page_limit = 10000
    while True:
        params: dict[str, Any] = {
            "FieldNames": FEEDS_FIELD_NAMES,
            "UrlFeedFieldNames": URL_FEED_FIELD_NAMES,
            "FileFeedFieldNames": FILE_FEED_FIELD_NAMES,
            "Page": {"Limit": page_limit, "Offset": offset},
        }
        resp = C.http_request(
            session, "POST", FEEDS_URL,
            source=SOURCE, headers=headers,
            json={"method": "get", "params": params}, timeout=60,
        )
        C.ensure_ok(resp, SOURCE, "feeds.get")
        _raise_for_api_error(resp, "feeds.get")  # ошибка приходит как 200+error
        result = resp.json().get("result") or {}
        items.extend(result.get("Feeds") or [])
        limited = result.get("LimitedBy")
        if limited is None:
            break
        offset = limited
    return [_feed_row(f) for f in items]


def _add_targeting_provenance(
    extra: dict[str, Any], targeting_provenance: dict[str, dict[str, Any]] | None,
) -> None:
    """Добавить изолированную provenance-запись без изменения остальных extra-полей."""
    extra["targeting_provenance"] = targeting_provenance or {}


def _record_manifest(
    paths, source_key, date_from, date_to, rows, *,
    has_lost_is: bool, archived_retrievable: bool, feed_used: bool,
    macro_goals_configured: bool,
    strategy_field_present: bool = False,
    strategy_field_samples: list[dict[str, Any]] | None = None,
    statistics_field_scope: str = STATISTICS_FIELD_SCOPE_UNKNOWN,
    campaign_period_logs: list[dict],
    query_period_logs: list[dict],
    geo_period_logs: list[dict],
    goal_period_logs: dict[str, list[dict]],
    notes: list[str],
    report_status: dict[str, str] | None = None,
    geo_report_available: bool = True,
    window_infos: dict[str, dict] | None = None,
    targeting_provenance: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    if statistics_field_scope not in STATISTICS_FIELD_SCOPE_VALUES:
        statistics_field_scope = STATISTICS_FIELD_SCOPE_UNKNOWN

    extra: dict[str, Any] = {
        "temporal_provenance": _temporal_provenance(date_from, date_to),
        # Базис расхода: НЕТТО без НДС, Cost в микрорублях (деление — в transform).
        "cost_basis": COST_BASIS,
        "cost_micros_per_rub": COST_MICROS_PER_RUB,
        # Приёмочные флаги (первый реальный прогон).
        "campaign_report_has_lost_impression_share": has_lost_is,
        "archived_campaigns_retrievable": archived_retrievable,
        # D08: снимок текущих API-статусов кампаний. observed_at берётся из
        # fetched_at самой записи manifest при transform, чтобы не дублировать
        # время и не связывать статус с произвольной датой отчёта расходов.
        "campaign_status_provenance": {
            "source": "direct.campaigns.get",
            "requested_states": list(CAMPAIGN_STATES_ALL),
            "raw_path": "direct/campaign_strategies.json",
        },
        "feed_used": feed_used,
        "geo_report_available": geo_report_available,
        # 2A-direct-strategy: Strategy — факт наличия + сырые образцы структуры
        # (не угадываем optimize_for и т.п.). statistics_field_scope — период
        # поля Statistics, "unknown" до живого сравнения (campaigns.get не
        # принимает StatisticsCrit).
        "strategy_field_present": strategy_field_present,
        "strategy_field_samples": strategy_field_samples or [],
        "statistics_field_scope": statistics_field_scope,
        # FIX-ad-extensions-coverage: adextensions.get отдаёт только уточнения
        # (CALLOUT -> CalloutText); структурных полей цены/акции/срока/наличия
        # в Директ API нет. Постоянное свойство API (как ограничение D11) —
        # фиксируем явно, чтобы A24 не оставался в молчаливом «не проверено».
        "ad_extensions_types_available": list(ADEXTENSION_TYPES),
        "ad_extensions_price_fields_available": AD_EXTENSIONS_PRICE_FIELDS_AVAILABLE,
        "ad_extensions_caveat": {
            "affected_checks": list(AD_EXTENSIONS_AFFECTED_CHECKS),
            "reason": (
                "Яндекс.Директ API v5 (adextensions.get) отдаёт единственный тип "
                "расширения — уточнение (CALLOUT) и только его текст (CalloutText). "
                "Цена, акция, срок действия и наличие как структурные поля через "
                "API недоступны. A24 на стороне Директа опирается только на текст "
                "объявления (Title/Title2/Text) и текст уточнений; сверка "
                "актуальности цены/акции/наличия возможна лишь против сайта "
                "(A24.optional = site_crawl), не против структурных полей Директа. "
                "A21/A23 этим ограничением не затронуты (поля расширений им не "
                "нужны). A22 — отдельная текстовая LLM-проверка."
            ),
        },
        # Целевые конверсии.
        "macro_goals_configured": macro_goals_configured,
        # Статус по типу отчёта (изоляция ошибок).
        "report_status": report_status or {},
        # Окна запросов по типу отчёта.
        "window_infos": window_infos or {},
        # Диагностические логи дат запросов (step0: сверка с UI-периодом).
        "campaign_period_logs": campaign_period_logs,
        "query_period_logs": query_period_logs,
        "geo_period_logs": geo_period_logs,
    }
    _add_targeting_provenance(extra, targeting_provenance)
    if goal_period_logs:
        extra["goal_period_logs"] = goal_period_logs
    if not macro_goals_configured:
        extra["caveat"] = (
            "целевые конверсии не выгружены — macro_goals не настроен; "
            "CPA не рассчитывается"
        )
    # Кавет для обрезанного окна запросов: методологическое ограничение источника.
    qw = (window_infos or {}).get("queries", {})
    if qw.get("window_truncated"):
        extra["query_window_caveat"] = {
            "caveat_type": "source_window_limit",
            "window_requested_from": qw.get("window_requested_from"),
            "window_effective_from": qw.get("window_effective_from"),
            "limit_days": REPORT_WINDOW_LIMIT_DAYS.get("SEARCH_QUERY_PERFORMANCE_REPORT"),
        }
    if not geo_report_available:
        extra["geo_caveat"] = {
            "geo_report_available": False,
            "reason": (window_infos or {}).get("geo", {}).get("reason", "неизвестно"),
        }
    if notes:
        extra["notes"] = notes
    return manifest_mod.update_source(
        Path(paths.raw), source_key,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra=extra,
    )


def _temporal_provenance(date_from, date_to) -> dict[str, Any]:
    """Детерминированный temporal-контракт Direct Reports для raw manifest.

    TimeZone из campaigns.get относится к расписанию кампании и намеренно не
    участвует в этом контракте статистического отчёта.
    """
    return {
        "requested_window": {
            "date_from": C.fmt(date_from),
            "date_to": C.fmt(date_to),
            "boundary_semantics": "unknown",
        },
        "zero_day_policy": "unknown",
        "fields": {
            "Date": {
                "event": "direct_statistics_day",
                "data_type": "date",
                "timezone_contract": "Europe/Moscow",
                "evidence": "direct_reports_contract",
            },
        },
    }
