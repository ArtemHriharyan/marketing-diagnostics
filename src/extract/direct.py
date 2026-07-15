"""Экстрактор: Яндекс.Директ (расходы, запросы, площадки, таргетинг, тексты).

Контракт:
    Читает   — config.sources.direct (client_login), DIRECT_TOKEN, окно дат.
    Пишет    — data/raw/direct/ (расходы по кампаниям, отчёт по поисковым
               запросам, площадки РСЯ, стратегии кампаний, настройки таргетинга,
               тексты объявлений, ключевые фразы, товарный фид) + manifest.json
               (canonical_tables: [costs, direct_queries]).
    Деградация — опционален; без Директа проверки с requires=[costs] опираются
                 только на config.costs_manual, а requires=[direct_queries]
                 уходят в degradation_report. Отдельные ВТОРИЧНЫЕ отчёты
                 (площадки/таргетинг/тексты/фид/ключи) при недоступности не
                 роняют весь источник: пишется note в manifest, ядро (расходы +
                 запросы + стратегии) остаётся.
    LLM      — не используется.

Что выгружаем:
    1. campaign_performance.tsv  — CAMPAIGN_PERFORMANCE_REPORT по дням:
       CampaignId, CampaignName, Cost, Clicks, Impressions
       (+ ПРОБНЫЕ WeightedImpressions/LostImpressionShare — см. приёмочный тест).
    2. search_query_performance.tsv — SEARCH_QUERY_PERFORMANCE_REPORT:
       Query, CampaignId, AdGroupId, Cost, Clicks (+ Conversions по целям).
    3. placements/placement_performance.tsv — отчёт по площадкам РСЯ/сетей (A15).
    4. campaign_strategies.json  — campaigns.get (BiddingStrategy) с фильтром
       States=ALL (включая ARCHIVED) — для 0.4 «клики vs конверсии» и D08.
    5. campaign_targeting.json    — гео (adgroups.RegionIds), устройства/расписание/
       корректировки ставок (bidmodifiers.get) по кампании/группе (A12–A14, A16).
    6. ad_texts.json              — тексты объявлений + расширения (ads.get,
       adextensions.get) (A20–A24).
    7. keywords.parquet           — ключевые фразы с типом соответствия
       (keywords.get) — ОТДЕЛЬНО от search queries (A11, A18).
    8. product_feed.parquet       — товарный фид, если используется (feeds.get);
       если фида нет — файл не создаётся, manifest.feed_used=false (A25).

Приёмочные флаги в manifest (проверяются на первом реальном прогоне):
    campaign_report_has_lost_impression_share — читается методологией A07 через
        механизм type_downgrade_if (A -> B, если доли потерянных показов нет).
    archived_campaigns_retrievable — свойство API (не клиента): реально ли
        campaigns.get со States=ALL отдаёт архивные/удалённые кампании (D08).

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
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.3.0"
SOURCE = "direct"
CANONICAL_TABLES = ["costs", "direct_queries"]

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

# Число повторов при статусе «отчёт готовится» (201/202 + Retry-In).
REPORT_MAX_POLLS = 60
REPORT_POLL_DEFAULT_SEC = 10.0

# CAMPAIGN_PERFORMANCE: базовые поля + ПРОБНЫЕ поля доли потерянных показов.
# Наличие/имя LostImpressionShare в Директ API не гарантировано (открытый вопрос
# спецификации: это может быть только UI-метрика). Поэтому поля ПРОБНЫЕ —
# заказываем полный состав, при отклонении откатываемся на базовый, а
# manifest.campaign_report_has_lost_impression_share фиксирует факт наличия
# (принцип «проверь фактическую доступность, не предполагай»).
CAMPAIGN_FIELDS = ["CampaignId", "CampaignName", "Cost", "Clicks", "Impressions", "Date"]
CAMPAIGN_FIELDS_LOST_IS = ["WeightedImpressions", "LostImpressionShare"]

QUERY_FIELDS = ["Query", "CampaignId", "AdGroupId", "Cost", "Clicks", "Conversions"]

# Отчёт по площадкам РСЯ/сетей (A15). Placement — домен площадки/имя приложения,
# AdNetworkType отделяет сети от поиска. Строится через CUSTOM_REPORT.
PLACEMENT_FIELDS = ["Placement", "AdNetworkType", "CampaignId", "Cost", "Clicks", "Conversions"]

# campaigns.get: фильтр States=ALL — по правилу D08 отбор по активности в периоде,
# а не по текущему статусу; ARCHIVED обязателен, иначе расход остановленных
# кампаний за прошлые месяцы потеряется.
CAMPAIGN_STATES_ALL = ["ON", "OFF", "SUSPENDED", "ENDED", "CONVERTED", "ARCHIVED"]
CAMPAIGN_FIELD_NAMES = ["Id", "Name", "State", "Status",
                        "StatisticsStartDate", "StatisticsEndDate"]

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


def _error_code(err: dict[str, Any]) -> int | None:
    """error_code Директа как int (в Reports API он приходит строкой, напр. "513")."""
    try:
        return int(err.get("error_code"))
    except (TypeError, ValueError):
        return None


def _api_error(resp: Any) -> dict[str, Any] | None:
    """Вернуть блок error из JSON-ответа Директа, если он есть (иначе None)."""
    try:
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

    token = C.get_token(env, "DIRECT_TOKEN", SOURCE)
    headers = _auth_headers(token, client_login)

    (date_from, date_to), compare_window = C.resolve_windows(
        paths.raw, config, defaults, today=today
    )
    has_compare = compare_window is not None
    base_dir = C.source_dir(paths, SOURCE)

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
        )

    return last_result


def _run_window_extract(
    session, headers, sleeper, date_from, date_to, out_dir, source_key, paths, log,
) -> dict[str, Any]:
    """Выгрузить все отчёты одного временного окна в out_dir."""
    notes: list[str] = []

    # 1. Расходы по кампаниям по дням (+ ПРОБНЫЕ поля доли потерянных показов).
    campaign_tsv, lost_is_requested = _fetch_campaign_report(
        session, headers, sleeper, date_from=date_from, date_to=date_to,
    )
    (out_dir / "campaign_performance.tsv").write_text(campaign_tsv, encoding="utf-8")
    campaign_rows = C.count_data_rows(campaign_tsv, has_header=True)
    has_lost_is = _has_lost_impression_share(campaign_tsv, lost_is_requested)
    if not lost_is_requested:
        notes.append("поля доли потерянных показов (LostImpressionShare) не приняты "
                     "Reports API — CAMPAIGN_PERFORMANCE_REPORT выгружен без них (A07 -> тип B)")

    # 2. Отчёт по поисковым запросам.
    query_tsv = _fetch_report(
        session, headers, sleeper,
        report_name="search_query_performance",
        report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
        fields=QUERY_FIELDS,
        date_from=date_from, date_to=date_to,
    )
    (out_dir / "search_query_performance.tsv").write_text(query_tsv, encoding="utf-8")
    query_rows = C.count_data_rows(query_tsv, has_header=True)

    # 3. Площадки РСЯ/сетей (A15). Вторичный отчёт — при недоступности деградируем.
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

    # 4. Стратегии кампаний со States=ALL (0.4 «клики vs конверсии» + D08).
    campaigns = _fetch_strategies(session, headers)
    with (out_dir / "campaign_strategies.json").open("w", encoding="utf-8") as fh:
        json.dump(campaigns, fh, ensure_ascii=False, indent=2)
    archived_retrievable = _archived_retrievable(campaigns)
    if not archived_retrievable:
        notes.append("campaigns.get со States=ALL не вернул ни одной ARCHIVED-кампании: "
                     "это либо отсутствие архивных у клиента, либо ограничение доступа — "
                     "D08 нельзя утверждать как достоверный, см. archived_campaigns_retrievable")

    # 5. Настройки таргетинга (гео/устройства/расписание/корректировки).
    try:
        targeting = _fetch_targeting(session, headers)
        with (out_dir / "campaign_targeting.json").open("w", encoding="utf-8") as fh:
            json.dump(targeting, fh, ensure_ascii=False, indent=2)
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"настройки таргетинга недоступны: {exc}")

    # 6. Тексты объявлений + расширения.
    try:
        ad_texts = _fetch_ad_texts(session, headers, notes)
        with (out_dir / "ad_texts.json").open("w", encoding="utf-8") as fh:
            json.dump(ad_texts, fh, ensure_ascii=False, indent=2)
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"тексты объявлений недоступны: {exc}")

    # 7. Ключевые фразы с типом соответствия (ОТДЕЛЬНО от search queries).
    keyword_rows = 0
    try:
        keywords = _fetch_keywords(session, headers)
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

    # 8. Товарный фид (если используется). Нет фида -> файл не создаём.
    feed_used = False
    try:
        feed_rows = _fetch_feed(session, headers)
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

    rows = campaign_rows + query_rows
    manifest = _record_manifest(
        paths, source_key, date_from, date_to, rows,
        has_lost_is=has_lost_is,
        archived_retrievable=archived_retrievable,
        feed_used=feed_used,
        notes=notes,
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
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "notes": notes,
        "manifest": manifest,
    }


# ── Reports API (TSV, оффлайн-отчёты) ──────────────────────────────────────
def _fetch_campaign_report(
    session, headers, sleeper, *, date_from, date_to,
) -> tuple[str, bool]:
    """CAMPAIGN_PERFORMANCE_REPORT с ПРОБНЫМИ полями доли потерянных показов.

    Сначала пробуем полный состав (базовые + LostImpressionShare); если API не
    принял поля (любая недоступность, кроме мёртвого токена) — откатываемся на
    базовый состав. Возвращает (tsv, были ли реально запрошены lost-IS-поля).
    """
    try:
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name="campaign_performance",
            report_type="CAMPAIGN_PERFORMANCE_REPORT",
            fields=CAMPAIGN_FIELDS + CAMPAIGN_FIELDS_LOST_IS,
            date_from=date_from, date_to=date_to,
        )
        return tsv, True
    except C.AuthError:
        raise
    except C.SourceUnavailable:
        # Состав с LostImpressionShare не принят — базовый отчёт без него.
        tsv = _fetch_report(
            session, headers, sleeper,
            report_name="campaign_performance",
            report_type="CAMPAIGN_PERFORMANCE_REPORT",
            fields=CAMPAIGN_FIELDS,
            date_from=date_from, date_to=date_to,
        )
        return tsv, False


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
) -> str:
    """Заказать TSV-отчёт и дождаться готовности (201/202 + Retry-In -> 200)."""
    body = {
        "params": {
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
    }

    for _poll in range(REPORT_MAX_POLLS):
        resp = C.http_request(
            session, "POST", REPORTS_URL,
            source=SOURCE, headers=headers, json=body, timeout=300,
        )
        status = getattr(resp, "status_code", None)
        if status == 200:
            # Reports API отдаёт ошибки статусами 4xx, но подстрахуемся: если в
            # 200 прилетел JSON-error вместо TSV — не пишем его как отчёт.
            text = resp.text
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
) -> list[dict[str, Any]]:
    """Постраничный get JSON-API v5: тянем страницы по LimitedBy до исчерпания."""
    items: list[dict[str, Any]] = []
    offset = 0
    page_limit = 10000
    while True:
        page_params = dict(params)
        page_params.setdefault("SelectionCriteria", {})
        page_params["Page"] = {"Limit": page_limit, "Offset": offset}
        resp = C.http_request(
            session, "POST", url,
            source=SOURCE, headers=headers, json={"method": "get", "params": page_params},
            timeout=60,
        )
        C.ensure_ok(resp, SOURCE, context)
        _raise_for_api_error(resp, context)  # ошибка приходит как 200+error
        result = resp.json().get("result") or {}
        items.extend(result.get(result_key) or [])
        limited = result.get("LimitedBy")
        if limited is None:
            break
        offset = limited
    return items


def _fetch_strategies(session, headers) -> list[dict[str, Any]]:
    """campaigns.get со States=ALL (включая ARCHIVED) и стратегиями (D08 + 0.4)."""
    return _get_all(
        session, headers, CAMPAIGNS_URL,
        {
            "SelectionCriteria": {"States": CAMPAIGN_STATES_ALL},
            "FieldNames": CAMPAIGN_FIELD_NAMES,
            "TextCampaignFieldNames": ["BiddingStrategy"],
        },
        result_key="Campaigns", context="campaigns.get",
    )


def _archived_retrievable(campaigns: list[dict[str, Any]]) -> bool:
    """Приёмочный тест D08: реально ли в ответе есть ARCHIVED-кампании.

    True — API отдал хотя бы одну архивную (retrievable доказан). False — может
    значить как отсутствие архивных у клиента, так и фильтрацию их API; поэтому
    в manifest это трактуется как свойство доступа, требующее ручной проверки.
    """
    return any((c.get("State") or "").upper() == "ARCHIVED" for c in campaigns)


def _fetch_targeting(session, headers) -> dict[str, Any]:
    """Гео (adgroups.RegionIds) + корректировки ставок/устройства/расписание
    (bidmodifiers.get) по кампании/группе (A12–A14, A16).
    """
    ad_groups = _get_all(
        session, headers, ADGROUPS_URL,
        {"FieldNames": ["Id", "Name", "CampaignId", "RegionIds", "NegativeKeywords"]},
        result_key="AdGroups", context="adgroups.get",
    )
    bid_modifiers = _get_all(
        session, headers, BIDMODIFIERS_URL,
        {
            "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type"],
            "MobileAdjustmentFieldNames": ["BidModifier"],
            "DesktopAdjustmentFieldNames": ["BidModifier"],
            "DemographicsAdjustmentFieldNames": ["Age", "Gender", "BidModifier"],
            "RegionalAdjustmentFieldNames": ["RegionId", "BidModifier"],
        },
        result_key="BidModifiers", context="bidmodifiers.get",
    )
    return {"ad_groups": ad_groups, "bid_modifiers": bid_modifiers}


def _fetch_ad_texts(session, headers, notes: list[str]) -> dict[str, Any]:
    """Тексты объявлений (ads.get) + расширения (adextensions.get, best-effort)."""
    ads = _get_all(
        session, headers, ADS_URL,
        {
            "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status"],
            "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "DisplayUrlPath"],
        },
        result_key="Ads", context="ads.get",
    )
    extensions: list[dict[str, Any]] = []
    try:
        extensions = _get_all(
            session, headers, ADEXTENSIONS_URL,
            {
                "FieldNames": ["Id", "Type", "State", "Status", "StatusClarification"],
                "CalloutFieldNames": ["CalloutText"],
            },
            result_key="AdExtensions", context="adextensions.get",
        )
    except C.AuthError:
        raise
    except C.SourceUnavailable as exc:
        notes.append(f"расширения объявлений (цена/акция/наличие) недоступны: {exc}")
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


def _fetch_keywords(session, headers) -> list[dict[str, Any]]:
    """keywords.get -> нормализованные строки ключевых фраз с типом соответствия."""
    raw = _get_all(
        session, headers, KEYWORDS_URL,
        {"FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId"]},
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


def _fetch_feed(session, headers) -> list[dict[str, Any]]:
    """feeds.get -> метаданные товарных фидов (пусто, если фидов нет).

    Директ API отдаёт метаданные фида (id/имя/источник/статус синхронизации),
    но НЕ построчные офферы; per-offer поля (offer_id/price/availability) берутся
    из источника фида отдельно и в этот слой не входят.
    """
    feeds = _get_all(
        session, headers, FEEDS_URL,
        {"FieldNames": ["Id", "Name", "BusinessType", "SourceType",
                        "FileFeedParameters", "UrlFeedParameters", "UpdateStatus"]},
        result_key="Feeds", context="feeds.get",
    )
    rows: list[dict[str, Any]] = []
    for feed in feeds:
        url_params = feed.get("UrlFeedParameters") or {}
        rows.append({
            "feed_id": str(feed.get("Id")) if feed.get("Id") is not None else None,
            "feed_name": feed.get("Name") or "",
            "business_type": feed.get("BusinessType") or "",
            "source_url": url_params.get("Url") or "",
            "offers_count": feed.get("OffersCount"),
            "updated_at": (feed.get("UpdateStatus") or {}).get("LastUpdate")
            if isinstance(feed.get("UpdateStatus"), dict) else feed.get("UpdateStatus"),
        })
    return rows


def _record_manifest(
    paths, source_key, date_from, date_to, rows, *,
    has_lost_is: bool, archived_retrievable: bool, feed_used: bool,
    notes: list[str],
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    extra: dict[str, Any] = {
        # Базис расхода: НЕТТО без НДС, Cost в микрорублях (деление — в transform).
        "cost_basis": COST_BASIS,
        "cost_micros_per_rub": COST_MICROS_PER_RUB,
        # Приёмочные флаги (первый реальный прогон).
        "campaign_report_has_lost_impression_share": has_lost_is,
        "archived_campaigns_retrievable": archived_retrievable,
        "feed_used": feed_used,
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
