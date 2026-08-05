"""Блок 1 — экономика и эффективность платной рекламы (каталог v2 §6, A01–A26).

Задача 5D реализовала A01–A11 (первая часть экономики рекламы). Задача 5E
добавляет A12–A26 (гео, устройства, расписание, РСЯ, ретаргетинг, бренд,
структура кампаний, CPC/CTR, запрос-объявление-посадочная, фид, лаг/
сезонность) — см. докстринг каждой `_run_aXX` ниже, там же источник данных и
структурные ограничения конкретной проверки.

Три источника, заявленные каталогом/спецификацией для A12–A26, физически НЕ
входят в canonical-слой (extract пишет их в data/raw/direct/, но
build_canonical.py не строит из них ни одной таблицы — расширение схемы вне
allowed_files обеих задач 5D/5E): `campaign_targeting.json` (гео/устройства/
расписание/корректировки ставок как НАСТРОЙКИ — не факт. показатели),
`keywords.parquet` (ключевые фразы с типом соответствия отдельно от search
queries), `product_feed.parquet` (товарный фид). Прямое следствие:
- A16 (ретаргетинг) пишет только unavailable — без campaign_targeting нет
  способа даже определить, какие кампании ретаргетинговые, тот же принцип,
  что у A07 (нет данных — проверка не придумывается).
- A25 (товарный фид) пишет только unavailable по той же причине.
- A18 (пересечение кампаний за один спрос) реализован только через
  direct_queries (поисковые запросы) — пересечение по отдельным ключевым
  фразам/аудиториям/гео не проверяется (keywords/campaign_targeting
  недоступны); это сужение уже отражено в config/methodology.yaml
  (`A18.requires == ["direct_queries"]`, не выдумано этой задачей).

Четыре ДРУГИЕ таблицы (direct_geo, direct_placements, ad_texts, seo_queries)
физически существуют в canonical (см. SCHEMAS build_canonical.py) и
используются здесь как ДОПОЛНИТЕЛЬНЫЕ входы блока — тот же прецедент, что
A02/A04–A08 читают canonical["direct_campaigns"] сверх голого
`requires: [costs, visits]` реестра (методология не запрещает читать больше
таблиц, чем в `requires`, — `requires` определяет только грубые
runnable_ids/деградацию верхнего уровня, конкретный набор входов проверки —
дело её реализации, с явной деградацией при отсутствии таблицы).

`ad_texts.parquet` уже отфильтрован по State=ON на этапе transform (см.
build_ad_texts/build.py) — A20–A24 читают ТОЛЬКО canonical["ad_texts"],
никогда canonical["ad_texts_archived"] (архивные объявления не считаются
текущей рекламой, повторная фильтрация здесь не нужна и не производится).

`direct_placements.cost_normalized` — проверено по коду (build_direct_placements,
build_canonical.py): контракт уже единый с direct_queries/campaigns/geo
(cost_raw/cost_rub всегда, cost_normalized=null до Q01, vat_basis_applied=False)
— терминологический gap, описанный в data-export-spec-v2.md как "открытый"
(валютная семантика cost_normalized), на практике уже закрыт задачей
4X-direct-placements-align/4X-direct-reconcile (см. docs/implementation_status.md).
Поэтому A15 использует cost_normalized через тот же `_money()`, что и
A09–A11, а не cost_raw с оговоркой — оговорка из промта была актуальна ДО
этой сверки, не после.

Целевой регион для A12 берётся из `config.client.geo` (единственное
структурированное — точнее, полу-структурированное — поле; свободный текст,
разделённый запятой/точкой с запятой/слэшем) сопоставлением подстрокой с
`direct_geo.location_of_presence_name`. Отдельного списка "целевых регионов"
в схеме клиентского конфига нет (вне allowed_files — `clients/_template/
config.yaml` не в списке этой задачи), это единственный доступный сигнал.

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §6):
    A01  кампания оптимизируется на неверную цель            [campaign_strategies, visits]
    A02  "максимум кликов" там, где есть стабильная цель      [campaign_strategies, visits]
    A03  автостратегия учится на переотрабатывающей/смешанной [campaign_strategies, visits]
         цели (сверка с D01/D03)
    A04  кампания тратит и не даёт ни одной чистой конверсии  [costs, visits]
    A05  CPA кампании устойчиво хуже сопоставимых             [costs, visits]
    A06  бюджет распределён не по эффективности               [costs, visits]
    A07  эффективная кампания теряет показы из-за бюджета/    [costs]
         ставок — ВСЕГДА unavailable, см. ниже
    A08  структура раздроблена на слишком много малых кампаний[costs, visits]
    A09  оплачиваются нецелевые поисковые запросы             [direct_queries]
    A10  не хватает минус-слов (повтор мусора по месяцам)     [direct_queries]
    A11  автотаргетинг/широкие соответствия размывают семантику[direct_queries, visits]

Легаси-метрика 1.2 «разрыв платный трафик vs весь сайт» (marketing-diagnostics-
methodology-v2.md §4, «Новый ID | Было» в §6) считается ЦЕЛИКОМ здесь (внутри
A01, единственное место в пайплайне — не дублируется в block3.py).

Контракт:
    Читает   — data/canonical/{visits,costs,campaign_strategies,direct_campaigns,
               direct_queries}.parquet, data/metrics/{d01,d03}.json (только A03 —
               сверка со стратегией по каталогу: «Проверить цели стратегии и их
               качество по D01–D03»; блок 0 гарантированно пишет эти файлы раньше
               block1 в одном прогоне compute — см. common.BLOCK_MODULE_NAMES),
               config.yaml клиента (sources.direct.macro_goals — список
               {id, name} валидированных бизнес-целей для расчёта Директом
               goal_conv_<id> по кампании/запросу; НЕ config.goals.* — те
               относятся к визит-уровневым флагам блока 0, это разные списки),
               пороги defaults (min_sample_visits, significance_alpha),
               data/metrics/degradation_report.json (confidence_cap на проверку).
    Пишет    — data/metrics/{a01..a11}.csv/.json. БЕЗ LLM.

Деньги — ТОЛЬКО через cost_normalized (НДС-нормализация). Если cost_normalized
для группы (кампания/фраза/match_type) не полностью посчитан (хотя бы одна
строка группы имеет cost_normalized IS NULL — база НДС источника не установлена,
см. D06/_apply_vat_to_rows), сумма по этой группе отдаётся как null, а не как
частичная сумма по ненулевым строкам и НЕ как cost_raw — деградация, а не
подмена (прямое требование промта задачи). Для costs.parquet cost_normalized
уже считается в transform через _apply_vat_to_rows. Для direct_queries/
direct_campaigns/direct_geo/direct_placements transform оставляет
cost_normalized=null/vat_basis_applied=False (задача 4X-direct-normalize-2,
осознанный контракт, закреплён тестами test_build_canonical.py) — задача
FIX-block1-cost-normalization реализовала отложенную Q01-нормализацию здесь,
в compute: cost_normalized = cost_rub * множитель, множитель считается той же
формулой _vat_lookup/_apply_vat_to_rows (переиспользована, не продублирована,
см. _direct_vat_multiplier/_open_duckdb_with_direct_vat) из inputs/
client_answers.yaml: finance.vat_basis_by_source для source_tag="direct" (тот
же источник, что читает D06 в block0.py — НЕ config.yaml, там секции finance
нет ни у одного клиента). Множитель неизвестен (vat_basis_unknown для
"direct") -> cost_normalized остаётся null для всех строк этих 4 таблиц,
проверки (A09–A15, A17–A19) пишут явную денежную деградацию — та же
семантика null-как-деградация, что и раньше, просто её причина теперь
"источник не ответил на Q01", а не "нормализация нигде не реализована".

«Чистые конверсии» (rule 11 catalog: «конверсия по любой цели» не бизнес-
результат) — ТОЛЬКО сумма goal_conv_<id> по id из config.sources.direct.
macro_goals (собственная атрибуция Директа на уровне кампании/запроса,
проставляется в direct_campaigns/direct_queries при transform). НИКОГДА
conversions_all (сырое поле «Conversions» отчёта Директа — по любой настроенной
цели, без разбора «бизнес vs микро», ровно то, что запрещено правилом 11).
Если macro_goals не настроены (пусто) — конверсии для проверки недоступны,
проверка пишет явную запись unavailable, а не 0 (0 конверсий — это факт про
кампанию, «нет данных о конверсиях» — факт про конфигурацию, их нельзя путать).

Отклонение от data-export-spec (задокументированный разрыв, по прецеденту
block0 D02/D03/D08 — ограничение extract/transform-слоя, не в allowed_files
этой задачи, не устраняется здесь): data-export-spec-v2.md §Блок1 предлагает
join visits->campaign через ym:s:lastSignDirectClickOrder, но этого поля нет
в SCHEMAS["visits"] (build_canonical.py) — экстрактор читает его из Logs API
(src/extract/metrika_logs.py), но canonical-слой его не переносит. Поэтому
кампанийная экономика (A02, A04–A06, A08) считает «чистые конверсии» из
goal_conv_<id> самих direct_campaigns (server-side атрибуция Директа), а не
из join визитов на campaign_id — этого канонического join-ключа физически нет.

Уровень уверенности (CLAUDE.md, «Уверенность находок»): HIGH зарезервирован
за находками визит-уровня с выборкой >= min_sample_visits. Кампанийная/
фразовая экономика (A02–A11, за исключением paid_vs_site_gap из A01) — это
отчётные агрегаты Директа, не визит-уровень, поэтому по определению не выше
MED даже при большом объёме данных; это базовый уровень ДО capping через
confidence_cap проверки (_cap может только понизить дальше, никогда не поднять).
Единственное исключение — paid_vs_site_gap внутри A01: он вычисляется по
visits.parquet визит-в-визит, поэтому может быть HIGH при достаточной выборке.

A07 (WeightedImpressions/LostImpressionShare — «эффективная кампания теряет
показы из-за бюджета/ставок») пишет ТОЛЬКО unavailable, всегда, независимо от
type_downgrade_if/confidence_cap: сырые поля WeightedImpressions/
LostImpressionShare не входят ни в SCHEMAS["costs"], ни в
SCHEMAS["direct_campaigns"] (build_canonical.py) — экстрактор пробует их
получить и фиксирует факт в manifest (campaign_report_has_lost_impression_share,
src/extract/direct.py), но само значение поля в canonical-слое не сохраняется
ни для одной строки. Придумывать проверку без данных нельзя (CLAUDE.md,
протокол микрозадач п.5) — это тот же принцип, которым block0 объяснял
отсутствие tz-проверки в D09.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from scipy import stats

from . import common
from ..pipeline import degradation as degradation_mod
from ..pipeline import orchestrator as orchestrator_mod
from ..transform.build_canonical import _apply_vat_to_rows, _vat_lookup, is_brand_query

# ── Match type (AUDIT-match-type, docs/implementation_status.md, подтверждено
# документацией Yandex Direct API v5): KEYWORD/SYNONYM/RELATED_KEYWORD — три
# степени соответствия КОНКРЕТНОЙ заданной фразе, участвуют в разрезе "по
# фразе" как есть, без склейки в одну категорию. NONE — официальная категория
# "прочее" (показ без атрибуции на заданную фразу), НЕ "неточное совпадение",
# НЕ синоним автотаргетинга конкретно — считается отдельным агрегатом, не
# смешивается с разрезом "по фразе". RELATED_KEYWORD может отсутствовать в
# данных конкретного аккаунта — не ошибка парсинга.
_PHRASE_MATCH_TYPES: tuple[str, ...] = ("KEYWORD", "SYNONYM", "RELATED_KEYWORD")
_NONE_MATCH_TYPE = "NONE"
_ALL_MATCH_TYPES: tuple[str, ...] = (*_PHRASE_MATCH_TYPES, _NONE_MATCH_TYPE)

# ── Константы-эвристики (каталог не даёт точных чисел; см. обоснование у
# каждой проверки — тот же подход, что и у block0 для D08/D10/D11) ──────────

# A01: legacy 1.2 давал прецедент "дельта >= 3 п.п." как порог обсуждения
# (00_general-data/marketing-diagnostics-methodology-v1.md, проверка 1.2).
_A01_PAID_VS_SITE_GAP_PP = 0.03

# A02: сколько чистых конверсий у кампании считать "стабильной конверсионной
# целью" (не случайным единичным попаданием) — эвристика, не из каталога.
_A02_MIN_NET_CONVERSIONS_FOR_STABLE_GOAL = 5

# A05: во сколько раз CPA кампании должен быть хуже медианы сопоставимых
# кампаний (с достаточной выборкой), чтобы считаться "устойчиво хуже".
_A05_MIN_NET_CONVERSIONS_FOR_COMPARISON = 5
_A05_CPA_OUTLIER_RATIO = 1.5

# A06: разрыв между долей расхода и долей чистых конверсий кампании (п.п.),
# начиная с которого распределение бюджета считается неэффективным.
_A06_MISALLOCATION_GAP_PP = 0.15

# A08: минимум кампаний в выгрузке, чтобы вообще оценивать раздробленность
# структуры (на 2-3 кампаниях "раздробленность" не определить); доля
# "мелких" (доля расхода ниже A08_SMALL_CAMPAIGN_SPEND_SHARE) кампаний,
# начиная с которой структура считается раздробленной.
_A08_MIN_CAMPAIGNS_FOR_FRAGMENTATION_CHECK = 5
_A08_SMALL_CAMPAIGN_SPEND_SHARE = 0.05
_A08_FRAGMENTED_SHARE_THRESHOLD = 0.5

# A10: минимум РАЗНЫХ календарных месяцев, где у фразы расход есть, а чистых
# конверсий нет, чтобы считать это "повторяющимся мусором" (каталог: "одни и
# те же классы мусорных запросов повторяются ежемесячно"), а не разовым
# провалом одного месяца.
_A10_MIN_RECURRING_MONTHS = 2

# A11: тот же принцип outlier-порога, что и A05, применён к CPA по match_type
# вместо CPA по кампании.
_A11_MIN_NET_CONVERSIONS_FOR_COMPARISON = 5
_A11_CPA_OUTLIER_RATIO = 1.5

# ── Пороги A12–A26 (задача 5E) — тот же принцип, что у A01–A11: каталог не
# даёт точных чисел, эвристики повторно используют уже принятые в блоке
# коэффициенты (5 конверсий на сравнение, 1.5x на "устойчиво хуже") там, где
# смысл проверки идентичен по структуре (CPA/CPC/CTR outlier по новому срезу).

# A12: минимум чистых конверсий у НЕцелевого региона, чтобы вообще сравнивать
# его CPA с целевым (тот же порог материальности, что A05/A11).
_A12_MIN_NET_CONVERSIONS_FOR_COMPARISON = 5
_A12_CPA_OUTLIER_RATIO = 1.5

# A13: тот же порог/коэффициент, применённый к CPA по дню недели вместо кампании.
_A13_MIN_NET_CONVERSIONS_FOR_COMPARISON = 5
_A13_CPA_OUTLIER_RATIO = 1.5
_WEEKDAY_NAMES: tuple[str, ...] = (
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
)

# A14: тот же порог/коэффициент, применённый к CPA по устройству.
_A14_MIN_NET_CONVERSIONS_FOR_COMPARISON = 5
_A14_CPA_OUTLIER_RATIO = 1.5

# A15: минимум кликов по площадке, чтобы включить её в ранжирование (отсев
# статистического шума единичных кликов); доля расхода блока площадок,
# начиная с которой площадка считается "заметной" в структуре расхода.
_A15_MIN_CLICKS_FOR_RANKING = 10
_A15_NOTABLE_SPEND_SHARE = 0.05

# A17: позиция органики в топ-N считается уже "видимой" пользователю —
# гипотеза каннибализации возможна только при видимой органике.
_A17_ORGANIC_TOP_POSITION = 3

# A18: минимум суммарных кликов по запросу (across кампаний), чтобы
# пересечение кампаний за один спрос считалось материальным, не шумом.
_A18_MIN_CLICKS_FOR_OVERLAP = 10

# A19: тот же принцип outlier-порога, что A05/A11, применён к CPC вместо CPA;
# минимум кликов, чтобы CPC фразы считался статистически осмысленным.
_A19_MIN_CLICKS_FOR_COMPARISON = 20
_A19_CPC_OUTLIER_RATIO = 1.5

# A20: минимум показов для сравнения CTR; CTR <= (медиана * этот коэффициент)
# считается аномально низким относительно сопоставимых фраз.
_A20_MIN_IMPRESSIONS_FOR_COMPARISON = 50
_A20_CTR_LOW_RATIO = 0.5

# A21: минимум кликов, чтобы CTR фразы вообще сравнивать; во сколько раз CTR
# должен быть выше медианы, чтобы считаться "высоким".
_A21_MIN_CLICKS_FOR_HIGH_CTR = 5
_A21_HIGH_CTR_RATIO = 1.5

# A22: минимум кликов по топ-запросу группы объявлений, чтобы вообще
# проверять пересечение токенов запрос/объявление (иначе шум на единичных
# кликах). Короткие частицы/предлоги — не несут смысловой нагрузки для
# сравнения, отбрасываются при токенизации.
_A22_MIN_CLICKS_FOR_MISMATCH_CHECK = 20
_STOPWORDS_RU: frozenset[str] = frozenset({
    "и", "в", "на", "с", "по", "для", "от", "до", "из", "у", "о", "за",
    "как", "что", "это", "или", "не", "к", "а", "но",
})

# A24: эвристики-кандидаты на ручную проверку устаревшей цены/акции в тексте
# объявления (regex по цифрам+валюте/проценту, ключевые слова акций) — НЕ
# автоматический вердикт "устарело", только список для аналитика (тип A+B).
_A24_PRICE_PATTERN = re.compile(r"\d[\d\s]{0,6}\s?(₽|руб|%)", re.IGNORECASE)
_A24_PROMO_WORDS: tuple[str, ...] = (
    "акция", "скидка", "распродажа", "только сегодня", "спецпредложение",
)

# A26: минимум месяцев/чистых конверсий, ниже которого оценка кампании
# считается статистически преждевременной (каталог: "без учёта... малого
# объёма выборки").
_A26_MIN_MONTHS_FOR_JUDGMENT = 2
_A26_MIN_NET_CONVERSIONS_FOR_JUDGMENT = 5

_A_CANDIDATE_FLAGS: tuple[str, ...] = (
    "paid_underperforms",
    "suspect_wrong_objective",
    "clicks_strategy_despite_stable_goal",
    "auto_strategy_at_risk",
    "at_risk_of_contaminated_signal",
    "zero_conversion_campaign",
    "cpa_persistently_worse",
    "budget_misallocated",
    "fragmented_structure",
    "no_net_conversions",
    "missing_negative_keyword_candidate",
    "match_type_dilutes_semantics",
    "zero_conversion_region",
    "off_target_geo_worse",
    "weekday_persistently_worse",
    "device_cr_worse_than_overall",
    "device_cpa_persistently_worse",
    "notable_spend_share",
    "possible_cannibalization",
    "competing_campaigns",
    "cpc_anomalously_high",
    "anomalously_low_ctr",
    "high_ctr_low_conversion",
    "query_ad_keyword_mismatch",
    "generic_landing_underperforms",
    "insufficient_sample_for_judgment",
)
_A_CANDIDATE_FINDINGS: frozenset[str] = frozenset({"manual_check_candidate"})
_A_LIMITATION_FINDINGS: frozenset[str] = frozenset({
    "hour_of_day_unavailable",
    "cpa_by_device_unavailable",
    "net_conversions_unavailable",
    "competitor_ads_not_checked",
    "organic_brand_data_unavailable",
    "manual_verification_required",
    "wordstat_seasonality_unavailable",
})
_A_SUMMARY_FINDINGS: frozenset[str] = frozenset({
    "summary", "paid_vs_site_gap", "outside_named_phrases",
})


def _analysis_signal(row: dict[str, Any]) -> str | None:
    """Вернуть стабильный код уже рассчитанного проблемного сигнала строки."""
    for field in _A_CANDIDATE_FLAGS:
        if row.get(field) is True:
            return field
    finding = row.get("finding")
    if finding in _A_CANDIDATE_FINDINGS:
        return str(finding)
    return None


def _analysis_role(row: dict[str, Any], signal: str | None) -> str:
    if signal is not None:
        return "candidate"
    finding = row.get("finding")
    if (
        row.get("status") == "unavailable"
        or finding in _A_LIMITATION_FINDINGS
        or row.get("insufficient_campaigns_for_check") is True
    ):
        return "limitation"
    if finding in _A_SUMMARY_FINDINGS:
        return "summary"
    return "baseline"


def _annotate_analysis_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавить единый аналитический контракт, не меняя значения метрик."""
    annotated: list[dict[str, Any]] = []
    evidence_ids = common.assign_evidence_ids(name, [dict(row) for row in rows])
    for source_row, evidence_id in zip(rows, evidence_ids):
        row = dict(source_row)
        check_id = str(row.get("check_id") or name).lower()
        signal = _analysis_signal(row)
        role = _analysis_role(row, signal)
        reason_token = signal or row.get("finding") or row.get("status") or role
        row.update({
            "evidence_id": evidence_id,
            "evidence_label": common.evidence_label(row),
            "row_ref": evidence_id,
            "candidate": signal is not None,
            "row_role": role,
            "candidate_reason": f"{check_id}_{reason_token}",
            "context_refs": [],
        })
        annotated.append(row)

    context_row = next(
        (row for role in ("summary", "limitation", "baseline", "context")
         for row in annotated if row["row_role"] == role),
        None,
    )
    if context_row is not None:
        for row in annotated:
            if row["candidate"] and row["evidence_id"] != context_row["evidence_id"]:
                row["context_refs"] = [context_row["evidence_id"]]
    return annotated


def _write_metric_artifact(
    metrics_dir: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    confidence_cap: str | None = None,
) -> tuple[Path, Path]:
    return common.write_metric_artifact(
        metrics_dir,
        name,
        _annotate_analysis_rows(name, rows),
        confidence_cap=confidence_cap,
    )


# ── Общие хелперы (дублируют паттерн block0.py — блоки compute не делят
# приватные хелперы через common.py, см. CLAUDE.md принцип 2) ───────────────
def _table_nonempty(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return pq.ParquetFile(path).metadata.num_rows > 0
    except OSError:
        return False


def _confidence_caps(paths: Any) -> dict[str, str]:
    """{check_id: confidence_cap} из уже записанного degradation_report.json."""
    report = common.load_degradation(paths)
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (report.get("checks") or [])
        if c.get("check_id")
    }


def _cap(confidence: str, confidence_cap: str) -> str:
    """Прижать confidence к потолку проверки (compute капает вниз, не поднимает)."""
    return degradation_mod.min_confidence(confidence, confidence_cap)


def _sample_confidence(sample_size: int, min_sample_visits: int) -> str:
    """HIGH при визит-уровневой выборке >= порога, иначе MED."""
    return "HIGH" if sample_size >= min_sample_visits else "MED"


def _write_unavailable(metrics_dir: Path, check_id: str, reason: str) -> None:
    """Явная запись «проверка недоступна» вместо молчаливого пропуска."""
    _write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


def _money(cost_sum: float | None, null_rows: int) -> float | None:
    """SUM(cost_normalized) валиден, только если ни одна строка группы не null."""
    if cost_sum is None or null_rows > 0:
        return None
    return round(float(cost_sum), 2)


# ── Отложенная Q01-нормализация 4 Direct-таблиц (см. докстринг модуля) ─────
_DIRECT_COST_TABLES: tuple[str, ...] = (
    "direct_queries", "direct_campaigns", "direct_geo", "direct_placements",
)


def _direct_vat_multiplier(paths: Any) -> float | None:
    """cost_rub -> cost_normalized множитель для source_tag="direct" (Q01).

    Переиспользует _vat_lookup/_apply_vat_to_rows (build_canonical.py) — ту же
    формулу, что уже нормализует costs.parquet в transform (не пишет вторую
    независимую реализацию, см. AUDIT-cost-normalized-formula-for-queries-geo,
    docs/implementation_status.md) — подаёт cost_raw=1.0 как "пробную" строку
    и забирает обратно множитель, который _apply_vat_to_rows к ней применил.
    Источник ответа Q01 — inputs/client_answers.yaml: finance.vat_basis_by_source
    (common.load_inputs, тот же путь, что читает D06 в block0.py), НЕ
    config.yaml клиента — там секции finance нет ни у одного клиента (сверено
    по clients/*/config.yaml и src/pipeline/orchestrator.load_client_config).
    None — база НДС для "direct" не установлена (vat_basis_unknown).
    """
    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    vat_basis = (client_answers.get("finance") or {}).get("vat_basis_by_source") or []
    vat_map = _vat_lookup(vat_basis)
    probe: list[dict[str, Any]] = [{"source_tag": "direct", "cost_raw": 1.0}]
    _apply_vat_to_rows(probe, vat_map)
    return probe[0]["cost_normalized"]


def _open_duckdb_with_direct_vat(paths: Any, canonical: dict[str, Path]) -> Any:
    """common.open_duckdb + отложенная Q01-нормализация direct_queries/campaigns/geo/placements.

    Подменяет view каждой из 4 таблиц так, чтобы cost_normalized = cost_rub *
    множитель и vat_basis_applied = true — вместо null/False, как их пишет
    transform (осознанный контракт этого слоя, см. докстринг модуля). Множитель
    неизвестен (_direct_vat_multiplier вернул None) -> view не подменяется,
    cost_normalized остаётся null (деградация, не подмена). Используется ТОЛЬКО
    проверками, которые реально читают cost_normalized из этих 4 таблиц
    (A09–A15, A17–A19); остальные вызовы common.open_duckdb в блоке 1 (A01–A08
    читают деньги из уже нормализованной transform'ом costs.parquet, A16/A20–A26
    её вовсе не читают) в подмене не нуждаются и её не получают.
    """
    con = common.open_duckdb(paths)
    multiplier = _direct_vat_multiplier(paths)
    if multiplier is None:
        return con
    for table in _DIRECT_COST_TABLES:
        path = canonical.get(table)
        if path is None:
            continue
        view = common._sql_quote_identifier(table)
        file_literal = common._sql_quote_literal(str(path))
        con.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * REPLACE "
            f"({multiplier!r} * cost_rub AS cost_normalized, "
            f"true AS vat_basis_applied) FROM read_parquet({file_literal})"
        )
    return con


def _macro_goal_ids(config: dict[str, Any]) -> list[str]:
    """config.sources.direct.macro_goals -> [id, ...] (строками, как в goal_conv_<id>)."""
    direct_cfg = ((config.get("sources") or {}).get("direct") or {})
    return [
        str(g["id"]) for g in (direct_cfg.get("macro_goals") or [])
        if g.get("id") is not None
    ]


def _table_columns(con: Any, table: str) -> set[str]:
    cur = con.execute(f'SELECT * FROM "{table}" LIMIT 0')
    return {d[0] for d in cur.description}


def _net_conversions_expr(con: Any, table: str, goal_ids: list[str]) -> str | None:
    """SQL-выражение "сумма goal_conv_<id> по всем macro_goals" или None,

    если macro_goals не настроены или колонки goal_conv_<id> физически нет в
    таблице (например transform писал её без goal_ids на момент прогона).
    """
    if not goal_ids:
        return None
    columns = _table_columns(con, table)
    parts: list[str] = []
    for gid in goal_ids:
        col = f"goal_conv_{gid}"
        if col not in columns:
            return None
        parts.append(f'COALESCE("{col}", 0)')
    return "(" + " + ".join(parts) + ")"


def _client_geo_target_terms(config: dict[str, Any]) -> list[str]:
    """config.client.geo (свободный текст) -> список нормализованных термов.

    Единственное доступное структурированное-ли поле для целевого региона
    (см. докстринг модуля) — разделители запятая/точка с запятой/слэш.
    """
    geo_raw = str(((config.get("client") or {}).get("geo")) or "")
    parts = re.split(r"[,;/]", geo_raw)
    return [p.strip().lower() for p in parts if p.strip()]


def _is_target_region(location_name: str | None, terms: list[str]) -> bool:
    """Регистронезависимое двустороннее вхождение подстроки (термин <-> имя)."""
    name = (location_name or "").strip().lower()
    if not name or not terms:
        return False
    return any(term in name or name in term for term in terms)


def _tokenize(text: str | None) -> set[str]:
    """Текст -> множество значимых токенов (для сравнения запрос vs объявление, A22)."""
    tokens = re.findall(r"[а-яёa-z0-9]+", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS_RU and len(t) > 1}


def _median(values: list[float]) -> float | None:
    """Медиана отсортированного списка (не изменяет исходный список)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _two_proportion_p_value(count1: int, n1: int, count2: int, n2: int) -> float | None:
    """Двусторонний z-тест разницы двух долей (significance_alpha сравнивается снаружи)."""
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = count1 / n1, count2 / n2
    p_pool = (count1 + count2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p1 - p2) / se
    return float(2 * stats.norm.sf(abs(z)))


def _campaign_costs(con: Any) -> dict[str, dict[str, Any]]:
    """{campaign_id: {name, cost_normalized (None если группа неполная)}} из costs (direct)."""
    rows = con.execute(
        "SELECT campaign_id, MAX(campaign_name), "
        "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
        "COUNT(*) FILTER (WHERE cost_normalized IS NULL) "
        "FROM costs WHERE source_tag = 'direct' AND campaign_id IS NOT NULL "
        "GROUP BY campaign_id ORDER BY campaign_id"
    ).fetchall()
    return {
        campaign_id: {
            "campaign_name": name,
            "cost_normalized": _money(cost_sum, null_rows),
        }
        for campaign_id, name, cost_sum, null_rows in rows
    }


def _campaign_net_conversions(
    con: Any, canonical: dict[str, Path], goal_ids: list[str],
) -> dict[str, int] | None:
    """{campaign_id: чистые конверсии} из direct_campaigns.goal_conv_<id>, или None,

    если direct_campaigns недоступна/пуста или macro_goals не настроены.
    """
    if "direct_campaigns" not in canonical or not _table_nonempty(canonical["direct_campaigns"]):
        return None
    expr = _net_conversions_expr(con, "direct_campaigns", goal_ids)
    if expr is None:
        return None
    rows = con.execute(
        f"SELECT campaign_id, SUM({expr}) FROM direct_campaigns "
        "WHERE campaign_id IS NOT NULL GROUP BY campaign_id ORDER BY campaign_id"
    ).fetchall()
    return {campaign_id: int(conv or 0) for campaign_id, conv in rows}


# ── A01 — кампания оптимизируется на неверную цель ──────────────────────────
def _run_a01(
    paths: Any, config: dict[str, Any], defaults: dict[str, Any],
    canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path,
) -> None:
    """paid_vs_site_gap (легаси 1.2, единственное место в пайплайне для этой

    метрики — см. докстринг модуля) + campaign_strategy_mismatch (легаси 0.4):
    кампании со стратегией "клики" при наличии подтверждённых конверсий сайта.
    """
    min_sample = int(defaults.get("min_sample_visits", 500))
    alpha = float(defaults.get("significance_alpha", 0.05))

    con = common.open_duckdb(paths)
    try:
        total_visits, ad_visits, total_submit, ad_submit = con.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE source_group = 'ad'), "
            "SUM(CASE WHEN form_submit THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN form_submit AND source_group = 'ad' THEN 1 ELSE 0 END) "
            "FROM visits"
        ).fetchone()

        strategies: list[tuple[str, str, str]] = []
        if "campaign_strategies" in canonical and _table_nonempty(canonical["campaign_strategies"]):
            strategies = con.execute(
                "SELECT campaign_id, campaign_name, optimize_for FROM campaign_strategies"
            ).fetchall()
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    ad_visits = int(ad_visits or 0)
    total_submit = int(total_submit or 0)
    ad_submit = int(ad_submit or 0)
    site_rate = total_submit / total_visits if total_visits > 0 else None
    ad_rate = ad_submit / ad_visits if ad_visits > 0 else None
    gap_pp = (site_rate - ad_rate) if (site_rate is not None and ad_rate is not None) else None
    p_value = (
        _two_proportion_p_value(total_submit, total_visits, ad_submit, ad_visits)
        if ad_visits > 0 else None
    )
    significant = p_value is not None and p_value < alpha and ad_visits >= min_sample
    threshold_exceeded = gap_pp is not None and gap_pp >= _A01_PAID_VS_SITE_GAP_PP

    rows: list[dict[str, Any]] = [{
        "check_id": "A01",
        "finding": "paid_vs_site_gap",
        "total_visits": total_visits,
        "ad_visits": ad_visits,
        "site_form_submit_rate": round(site_rate, 4) if site_rate is not None else None,
        "ad_form_submit_rate": round(ad_rate, 4) if ad_rate is not None else None,
        "gap_pp": round(gap_pp, 4) if gap_pp is not None else None,
        "gap_pp_threshold": _A01_PAID_VS_SITE_GAP_PP,
        "p_value": round(p_value, 6) if p_value is not None else None,
        "significance_alpha": alpha,
        "paid_underperforms": bool(threshold_exceeded and significant),
        "confidence": _cap(_sample_confidence(ad_visits, min_sample) if ad_visits > 0 else "LOW", confidence_cap),
    }]

    validated_goal_exists = total_submit > 0
    for campaign_id, campaign_name, optimize_for in strategies:
        if optimize_for != "clicks":
            continue
        rows.append({
            "check_id": "A01",
            "finding": "campaign_strategy_mismatch",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "optimize_for": optimize_for,
            "validated_goal_exists_on_site": validated_goal_exists,
            "suspect_wrong_objective": validated_goal_exists,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a01", rows, confidence_cap=confidence_cap)


# ── A02 — "максимум кликов" там, где есть стабильная конверсионная цель ────
def _run_a02(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)

    con = common.open_duckdb(paths)
    try:
        strategies = []
        if "campaign_strategies" in canonical and _table_nonempty(canonical["campaign_strategies"]):
            strategies = con.execute(
                "SELECT campaign_id, campaign_name, optimize_for FROM campaign_strategies "
                "WHERE optimize_for = 'clicks'"
            ).fetchall()
        conversions = _campaign_net_conversions(con, canonical, goal_ids)
    finally:
        con.close()

    if not strategies:
        _write_metric_artifact(metrics_dir, "a02", [], confidence_cap=confidence_cap)
        return

    if conversions is None:
        _write_unavailable(
            metrics_dir, "A02",
            "нет чистых конверсий по кампаниям: macro_goals не настроены в "
            "config.sources.direct или direct_campaigns недоступна",
        )
        return

    rows: list[dict[str, Any]] = []
    for campaign_id, campaign_name, optimize_for in strategies:
        net_conv = conversions.get(campaign_id, 0)
        stable_goal = net_conv >= _A02_MIN_NET_CONVERSIONS_FOR_STABLE_GOAL
        rows.append({
            "check_id": "A02",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "optimize_for": optimize_for,
            "net_conversions": net_conv,
            "stable_goal_threshold": _A02_MIN_NET_CONVERSIONS_FOR_STABLE_GOAL,
            "clicks_strategy_despite_stable_goal": stable_goal,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a02", rows, confidence_cap=confidence_cap)


# ── A03 — автостратегия учится на переотрабатывающей/смешанной цели ────────
def _run_a03(
    paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path,
) -> None:
    """Каталог: "Проверить цели стратегии и их качество по D01–D03" — сверка

    с уже записанными артефактами блока 0 (d01.json/d03.json). Блок 0 всегда
    выполняется раньше block1 в одном прогоне compute (common.BLOCK_MODULE_NAMES,
    порядок фиксирован) — чтение чужого, но уже готового вывода того же
    compute-слоя, не нарушение принципа 2 (слои неизменяемы), т.к. блоки
    внутри одного слоя compute, не разные слои пайплайна.
    """
    d01_path = Path(paths.metrics) / "d01.json"
    d03_path = Path(paths.metrics) / "d03.json"
    if not d01_path.exists() or not d03_path.exists():
        _write_unavailable(
            metrics_dir, "A03",
            "артефакты d01/d03 блока 0 недоступны (блок 0 ещё не выполнялся в этом прогоне)",
        )
        return

    with d01_path.open("r", encoding="utf-8") as fh:
        d01_rows = json.load(fh)
    with d03_path.open("r", encoding="utf-8") as fh:
        d03_rows = json.load(fh)

    has_overtrigger = any(r.get("overtrigger") is True for r in d01_rows)
    d03_summary = next((r for r in d03_rows if r.get("finding") == "goal_mix_summary"), {})
    has_mixed_goals = bool(d03_summary.get("has_overlap") or d03_summary.get("has_uncategorized"))

    con = common.open_duckdb(paths)
    try:
        strategies = []
        if "campaign_strategies" in canonical and _table_nonempty(canonical["campaign_strategies"]):
            strategies = con.execute(
                "SELECT campaign_id, campaign_name FROM campaign_strategies "
                "WHERE optimize_for = 'conversions'"
            ).fetchall()
    finally:
        con.close()

    at_risk = has_overtrigger or has_mixed_goals
    rows: list[dict[str, Any]] = [{
        "check_id": "A03",
        "finding": "summary",
        "d01_has_overtrigger": has_overtrigger,
        "d03_has_mixed_goals": has_mixed_goals,
        "auto_strategy_at_risk": at_risk,
        "auto_strategy_campaign_count": len(strategies),
        "confidence": _cap("MED", confidence_cap),
    }]
    for campaign_id, campaign_name in strategies:
        rows.append({
            "check_id": "A03",
            "finding": "auto_strategy_campaign",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "optimize_for": "conversions",
            "at_risk_of_contaminated_signal": at_risk,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a03", rows, confidence_cap=confidence_cap)


# ── A04 — кампания тратит и не даёт ни одной чистой конверсии ──────────────
def _run_a04(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)

    con = common.open_duckdb(paths)
    try:
        costs_by_campaign = _campaign_costs(con)
        conversions = _campaign_net_conversions(con, canonical, goal_ids)
    finally:
        con.close()

    if conversions is None:
        _write_unavailable(
            metrics_dir, "A04",
            "нет чистых конверсий по кампаниям: macro_goals не настроены в "
            "config.sources.direct или direct_campaigns недоступна",
        )
        return

    rows: list[dict[str, Any]] = []
    for campaign_id, info in costs_by_campaign.items():
        net_conv = conversions.get(campaign_id, 0)
        cost = info["cost_normalized"]
        zero_conversion = cost is not None and cost > 0 and net_conv == 0
        rows.append({
            "check_id": "A04",
            "campaign_id": campaign_id,
            "campaign_name": info["campaign_name"],
            "cost_normalized_rub": cost,
            "net_conversions": net_conv,
            "zero_conversion_campaign": zero_conversion,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a04", rows, confidence_cap=confidence_cap)


# ── A05 — CPA кампании устойчиво хуже сопоставимых ──────────────────────────
def _run_a05(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)

    con = common.open_duckdb(paths)
    try:
        costs_by_campaign = _campaign_costs(con)
        conversions = _campaign_net_conversions(con, canonical, goal_ids)
    finally:
        con.close()

    if conversions is None:
        _write_unavailable(
            metrics_dir, "A05",
            "нет чистых конверсий по кампаниям: macro_goals не настроены в "
            "config.sources.direct или direct_campaigns недоступна",
        )
        return

    comparable: list[tuple[str, str, float, int]] = []
    for campaign_id, info in costs_by_campaign.items():
        cost = info["cost_normalized"]
        net_conv = conversions.get(campaign_id, 0)
        if cost is None or net_conv < _A05_MIN_NET_CONVERSIONS_FOR_COMPARISON:
            continue
        comparable.append((campaign_id, info["campaign_name"], cost, net_conv))

    if not comparable:
        _write_metric_artifact(metrics_dir, "a05", [], confidence_cap=confidence_cap)
        return

    cpas = sorted((cost / net_conv) for _, _, cost, net_conv in comparable)
    mid = len(cpas) // 2
    median_cpa = cpas[mid] if len(cpas) % 2 == 1 else (cpas[mid - 1] + cpas[mid]) / 2

    rows: list[dict[str, Any]] = []
    for campaign_id, campaign_name, cost, net_conv in comparable:
        cpa = cost / net_conv
        ratio = cpa / median_cpa if median_cpa > 0 else None
        outlier = ratio is not None and ratio >= _A05_CPA_OUTLIER_RATIO
        rows.append({
            "check_id": "A05",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "cost_normalized_rub": round(cost, 2),
            "net_conversions": net_conv,
            "cpa_rub": round(cpa, 2),
            "median_cpa_rub": round(median_cpa, 2),
            "cpa_to_median_ratio": round(ratio, 3) if ratio is not None else None,
            "outlier_ratio_threshold": _A05_CPA_OUTLIER_RATIO,
            "cpa_persistently_worse": outlier,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a05", rows, confidence_cap=confidence_cap)


# ── A06 — бюджет распределён не по эффективности ────────────────────────────
def _run_a06(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)

    con = common.open_duckdb(paths)
    try:
        costs_by_campaign = _campaign_costs(con)
        conversions = _campaign_net_conversions(con, canonical, goal_ids)
    finally:
        con.close()

    if conversions is None:
        _write_unavailable(
            metrics_dir, "A06",
            "нет чистых конверсий по кампаниям: macro_goals не настроены в "
            "config.sources.direct или direct_campaigns недоступна",
        )
        return

    priced = [
        (cid, info["campaign_name"], info["cost_normalized"], conversions.get(cid, 0))
        for cid, info in costs_by_campaign.items()
        if info["cost_normalized"] is not None
    ]
    total_cost = sum(cost for _, _, cost, _ in priced)
    total_conv = sum(conv for _, _, _, conv in priced)

    if total_cost <= 0:
        _write_metric_artifact(metrics_dir, "a06", [], confidence_cap=confidence_cap)
        return

    rows: list[dict[str, Any]] = []
    for campaign_id, campaign_name, cost, net_conv in priced:
        spend_share = cost / total_cost
        conv_share = (net_conv / total_conv) if total_conv > 0 else 0.0
        gap_pp = spend_share - conv_share
        misallocated = gap_pp >= _A06_MISALLOCATION_GAP_PP
        rows.append({
            "check_id": "A06",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "cost_normalized_rub": round(cost, 2),
            "net_conversions": net_conv,
            "spend_share": round(spend_share, 4),
            "conversion_share": round(conv_share, 4),
            "gap_pp": round(gap_pp, 4),
            "gap_pp_threshold": _A06_MISALLOCATION_GAP_PP,
            "budget_misallocated": misallocated,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a06", rows, confidence_cap=confidence_cap)


# ── A07 — эффективная кампания теряет показы (данных нет, см. докстринг) ───
def _run_a07(metrics_dir: Path) -> None:
    _write_unavailable(
        metrics_dir, "A07",
        "WeightedImpressions/LostImpressionShare не входят в canonical-слой "
        "(costs/direct_campaigns) — экстрактор фиксирует только факт доступности "
        "поля в manifest, не само значение; расширение схемы вне allowed_files этой задачи",
    )


# ── A08 — структура раздроблена на слишком много малых кампаний ────────────
def _run_a08(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)

    con = common.open_duckdb(paths)
    try:
        costs_by_campaign = _campaign_costs(con)
        conversions = _campaign_net_conversions(con, canonical, goal_ids)
    finally:
        con.close()

    priced = {
        cid: info["cost_normalized"] for cid, info in costs_by_campaign.items()
        if info["cost_normalized"] is not None
    }
    total_campaigns = len(costs_by_campaign)
    total_cost = sum(priced.values())

    if total_campaigns < _A08_MIN_CAMPAIGNS_FOR_FRAGMENTATION_CHECK or total_cost <= 0:
        rows = [{
            "check_id": "A08",
            "total_campaigns": total_campaigns,
            "insufficient_campaigns_for_check": True,
            "min_campaigns_threshold": _A08_MIN_CAMPAIGNS_FOR_FRAGMENTATION_CHECK,
            "confidence": _cap("LOW", confidence_cap),
        }]
        _write_metric_artifact(metrics_dir, "a08", rows, confidence_cap=confidence_cap)
        return

    small_campaigns = [
        cid for cid, cost in priced.items() if cost / total_cost < _A08_SMALL_CAMPAIGN_SPEND_SHARE
    ]
    small_share = len(small_campaigns) / len(priced) if priced else 0.0
    fragmented = small_share >= _A08_FRAGMENTED_SHARE_THRESHOLD

    total_conversions = None
    avg_conversions_per_campaign = None
    if conversions is not None:
        total_conversions = sum(conversions.get(cid, 0) for cid in costs_by_campaign)
        avg_conversions_per_campaign = (
            round(total_conversions / total_campaigns, 2) if total_campaigns else None
        )

    rows = [{
        "check_id": "A08",
        "total_campaigns": total_campaigns,
        "priced_campaigns": len(priced),
        "small_campaign_count": len(small_campaigns),
        "small_campaign_spend_share_threshold": _A08_SMALL_CAMPAIGN_SPEND_SHARE,
        "small_campaign_share_of_priced": round(small_share, 4),
        "fragmented_share_threshold": _A08_FRAGMENTED_SHARE_THRESHOLD,
        "fragmented_structure": fragmented,
        "total_net_conversions": total_conversions,
        "avg_conversions_per_campaign": avg_conversions_per_campaign,
        "confidence": _cap("MED", confidence_cap),
    }]
    _write_metric_artifact(metrics_dir, "a08", rows, confidence_cap=confidence_cap)


# ── A09 — оплачиваются нецелевые поисковые запросы ──────────────────────────
def _run_a09(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """"Расход без чистых конверсий" по конкретной фразе (обязательный денежный

    срез каталога §6) для KEYWORD/SYNONYM/RELATED_KEYWORD. NONE ("прочее",
    AUDIT-match-type) — отдельный агрегат "вне заданных фраз", не смешивается
    с разрезом "по фразе" и не трактуется как "неточное совпадение".
    """
    goal_ids = _macro_goal_ids(config)

    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        expr = _net_conversions_expr(con, "direct_queries", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A09",
                "нет чистых конверсий по запросам: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_queries",
            )
            return

        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        by_query = con.execute(
            f"SELECT query, match_type, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}), SUM(clicks) "
            "FROM direct_queries "
            f"WHERE match_type IN ({phrase_types_sql}) "
            "GROUP BY query, match_type ORDER BY query, match_type"
        ).fetchall()

        none_summary = con.execute(
            "SELECT SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}), SUM(clicks), COUNT(*) "
            "FROM direct_queries WHERE match_type = ?",
            [_NONE_MATCH_TYPE],
        ).fetchone()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for query, match_type, cost_sum, null_rows, net_conv, clicks in by_query:
        cost = _money(cost_sum, null_rows)
        net_conv = int(net_conv or 0)
        no_net_conversions = cost is not None and cost > 0 and net_conv == 0
        rows.append({
            "check_id": "A09",
            "finding": "query_spend_vs_conversions",
            "query": query,
            "match_type": match_type,
            "cost_normalized_rub": cost,
            "clicks": int(clicks or 0),
            "net_conversions": net_conv,
            "no_net_conversions": no_net_conversions,
            "confidence": _cap("MED", confidence_cap),
        })

    if none_summary is not None:
        none_cost_sum, none_null_rows, none_conv, none_clicks, none_row_count = none_summary
        rows.append({
            "check_id": "A09",
            "finding": "outside_named_phrases",
            "match_type": _NONE_MATCH_TYPE,
            "row_count": int(none_row_count or 0),
            "cost_normalized_rub": _money(none_cost_sum, none_null_rows),
            "clicks": int(none_clicks or 0),
            "net_conversions": int(none_conv or 0),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a09", rows, confidence_cap=confidence_cap)


# ── A10 — не хватает минус-слов ──────────────────────────────────────────────
def _run_a10(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Фразы (KEYWORD/SYNONYM/RELATED_KEYWORD — не NONE, см. A09), у которых

    расход без чистых конверсий повторяется в >= _A10_MIN_RECURRING_MONTHS
    разных календарных месяцах — кандидат на минус-слово/чистку семантики.
    """
    goal_ids = _macro_goal_ids(config)

    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        expr = _net_conversions_expr(con, "direct_queries", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A10",
                "нет чистых конверсий по запросам: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_queries",
            )
            return

        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        by_query_month = con.execute(
            "SELECT query, match_type, strftime(date, '%Y-%m') AS month, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}) "
            "FROM direct_queries "
            f"WHERE match_type IN ({phrase_types_sql}) "
            "GROUP BY query, match_type, month ORDER BY query, match_type, month"
        ).fetchall()
    finally:
        con.close()

    zero_conv_months: dict[tuple[str, str], list[str]] = {}
    total_wasted_spend: dict[tuple[str, str], float] = {}
    for query, match_type, month, cost_sum, null_rows, net_conv in by_query_month:
        cost = _money(cost_sum, null_rows)
        if cost is None or cost <= 0:
            continue
        if int(net_conv or 0) > 0:
            continue
        key = (query, match_type)
        zero_conv_months.setdefault(key, []).append(month)
        total_wasted_spend[key] = total_wasted_spend.get(key, 0.0) + cost

    rows: list[dict[str, Any]] = []
    for (query, match_type), months in sorted(zero_conv_months.items()):
        recurring_months = len(months)
        rows.append({
            "check_id": "A10",
            "query": query,
            "match_type": match_type,
            "zero_conversion_months": sorted(months),
            "recurring_months_count": recurring_months,
            "recurring_months_threshold": _A10_MIN_RECURRING_MONTHS,
            "wasted_spend_rub": round(total_wasted_spend[(query, match_type)], 2),
            "missing_negative_keyword_candidate": recurring_months >= _A10_MIN_RECURRING_MONTHS,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a10", rows, confidence_cap=confidence_cap)


# ── A11 — автотаргетинг/широкие соответствия размывают семантику ───────────
def _run_a11(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Разрез по match_type (все 4 значения — здесь, в отличие от A09/A10,

    NONE участвует наравне с остальными, т.к. проверка именно про сравнение
    типов соответствия между собой, каталог: "Разнести запросы по источнику
    подбора, типу соответствия и конверсии"). visits — контекстная база
    сравнения (общая конверсия сайта), не участвует в самом сравнении типов.
    """
    goal_ids = _macro_goal_ids(config)

    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        expr = _net_conversions_expr(con, "direct_queries", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A11",
                "нет чистых конверсий по запросам: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_queries",
            )
            return

        match_types_sql = ", ".join(f"'{t}'" for t in _ALL_MATCH_TYPES)
        by_match_type = con.execute(
            "SELECT match_type, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}), SUM(clicks) "
            "FROM direct_queries "
            f"WHERE match_type IN ({match_types_sql}) "
            "GROUP BY match_type ORDER BY match_type"
        ).fetchall()

        total_visits, total_submit = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN form_submit THEN 1 ELSE 0 END) FROM visits"
        ).fetchone()
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    site_rate = (total_submit or 0) / total_visits if total_visits > 0 else None

    by_type: dict[str, dict[str, Any]] = {}
    for match_type, cost_sum, null_rows, net_conv, clicks in by_match_type:
        by_type[match_type] = {
            "cost_normalized_rub": _money(cost_sum, null_rows),
            "net_conversions": int(net_conv or 0),
            "clicks": int(clicks or 0),
        }

    keyword_info = by_type.get("KEYWORD")
    keyword_cpa = None
    if keyword_info and keyword_info["cost_normalized_rub"] is not None:
        if keyword_info["net_conversions"] >= _A11_MIN_NET_CONVERSIONS_FOR_COMPARISON:
            keyword_cpa = keyword_info["cost_normalized_rub"] / keyword_info["net_conversions"]

    rows: list[dict[str, Any]] = []
    for match_type in _ALL_MATCH_TYPES:
        info = by_type.get(match_type)
        if info is None:
            continue
        cost = info["cost_normalized_rub"]
        net_conv = info["net_conversions"]
        cpa = cost / net_conv if (cost is not None and net_conv > 0) else None
        cpa_ratio = (cpa / keyword_cpa) if (cpa is not None and keyword_cpa) else None
        no_conversions_with_spend = cost is not None and cost > 0 and net_conv == 0
        dilutes_semantics = (
            no_conversions_with_spend
            or (cpa_ratio is not None and cpa_ratio >= _A11_CPA_OUTLIER_RATIO)
        )
        rows.append({
            "check_id": "A11",
            "match_type": match_type,
            "cost_normalized_rub": cost,
            "clicks": info["clicks"],
            "net_conversions": net_conv,
            "cpa_rub": round(cpa, 2) if cpa is not None else None,
            "keyword_cpa_rub": round(keyword_cpa, 2) if keyword_cpa is not None else None,
            "cpa_to_keyword_ratio": round(cpa_ratio, 3) if cpa_ratio is not None else None,
            "outlier_ratio_threshold": _A11_CPA_OUTLIER_RATIO,
            "match_type_dilutes_semantics": (match_type != "KEYWORD") and dilutes_semantics,
            "site_form_submit_rate_context": round(site_rate, 4) if site_rate is not None else None,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a11", rows, confidence_cap=confidence_cap)


# ── A12 — реклама показывается в нерелевантной географии ────────────────────
def _run_a12(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Обязательная последовательность методологии v2 §4 (не одно условие

    "регион != целевой"): (1) CPA нецелевого региона, (2) CPA целевого региона
    за тот же период, (3) сравнение — находка только при кратном ухудшении.
    Источник факт. гео — direct_geo.location_of_presence_name (реальные
    показы/расход по региону), не campaign_targeting.json (настройки таргетинга
    структурно недоступны, см. докстринг модуля). Целевой регион — из
    config.client.geo (см. _client_geo_target_terms).
    """
    if "direct_geo" not in canonical or not _table_nonempty(canonical["direct_geo"]):
        _write_unavailable(
            metrics_dir, "A12", "direct_geo недоступна (гео-отчёт Директа не выгружен)"
        )
        return

    terms = _client_geo_target_terms(config)
    if not terms:
        _write_unavailable(
            metrics_dir, "A12",
            "config.client.geo не заполнен — целевой регион неизвестен, находка "
            "без сравнения с целевым CPA запрещена методологией (см. §4 A12)",
        )
        return

    goal_ids = _macro_goal_ids(config)
    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        expr = _net_conversions_expr(con, "direct_geo", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A12",
                "нет чистых конверсий по гео: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_geo",
            )
            return
        rows = con.execute(
            "SELECT location_of_presence_name, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}), SUM(clicks) "
            "FROM direct_geo WHERE location_of_presence_name IS NOT NULL "
            "GROUP BY location_of_presence_name ORDER BY location_of_presence_name"
        ).fetchall()
    finally:
        con.close()

    target_rows: list[tuple[str, float | None, int, int]] = []
    nontarget_rows: list[tuple[str, float | None, int, int]] = []
    for name, cost_sum, null_rows, net_conv, clicks in rows:
        cost = _money(cost_sum, null_rows)
        entry = (name, cost, int(net_conv or 0), int(clicks or 0))
        (target_rows if _is_target_region(name, terms) else nontarget_rows).append(entry)

    target_cost_valid = bool(target_rows) and all(c is not None for _, c, _, _ in target_rows)
    target_cost = sum(c for _, c, _, _ in target_rows if c is not None) if target_cost_valid else None
    target_conv = sum(v for _, _, v, _ in target_rows)
    target_cpa = (
        target_cost / target_conv if (target_cost_valid and target_conv > 0 and target_cost) else None
    )

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A12",
        "finding": "summary",
        "target_region_terms": terms,
        "target_regions_matched": sorted({n for n, _, _, _ in target_rows}),
        "target_region_cost_normalized_rub": round(target_cost, 2) if target_cost is not None else None,
        "target_region_net_conversions": target_conv,
        "target_region_cpa_rub": round(target_cpa, 2) if target_cpa is not None else None,
        "confidence": _cap("MED", confidence_cap),
    }]
    for name, cost, net_conv, clicks in sorted(nontarget_rows, key=lambda r: r[0] or ""):
        cpa = (
            cost / net_conv
            if (cost is not None and net_conv >= _A12_MIN_NET_CONVERSIONS_FOR_COMPARISON)
            else None
        )
        ratio = (cpa / target_cpa) if (cpa is not None and target_cpa) else None
        rows_out.append({
            "check_id": "A12",
            "finding": "region_detail",
            "location_of_presence_name": name,
            "cost_normalized_rub": round(cost, 2) if cost is not None else None,
            "clicks": clicks,
            "net_conversions": net_conv,
            "cpa_rub": round(cpa, 2) if cpa is not None else None,
            "target_region_cpa_rub": round(target_cpa, 2) if target_cpa is not None else None,
            "cpa_to_target_ratio": round(ratio, 3) if ratio is not None else None,
            "outlier_ratio_threshold": _A12_CPA_OUTLIER_RATIO,
            "zero_conversion_region": bool(cost is not None and cost > 0 and net_conv == 0),
            "off_target_geo_worse": bool(ratio is not None and ratio >= _A12_CPA_OUTLIER_RATIO),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a12", rows_out, confidence_cap=confidence_cap)


# ── A13 — день недели/время показа даёт устойчиво слабую экономику ─────────
def _run_a13(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Час показа НЕ проверяется этим пайплайном ни для одного клиента:

    CAMPAIGN_PERFORMANCE_REPORT Директа отдаётся только по дням (Date), без
    измерения Hour (src/extract/direct.py, п.1) — структурное ограничение
    источника, тот же принцип, что у A07. День недели — из
    direct_campaigns.date (кампанийный агрегат Директа, не визит-уровень,
    поэтому MED-потолок, как и остальная кампанийная экономика блока).
    """
    if "direct_campaigns" not in canonical or not _table_nonempty(canonical["direct_campaigns"]):
        _write_unavailable(
            metrics_dir, "A13",
            "direct_campaigns недоступна (отчёт по кампаниям Директа не выгружен)",
        )
        return

    goal_ids = _macro_goal_ids(config)
    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        expr = _net_conversions_expr(con, "direct_campaigns", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A13",
                "нет чистых конверсий по дням: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_campaigns",
            )
            return
        rows = con.execute(
            "SELECT date, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
            f"SUM({expr}) "
            "FROM direct_campaigns WHERE date IS NOT NULL GROUP BY date ORDER BY date"
        ).fetchall()
    finally:
        con.close()

    weekday_cost: dict[int, float] = {i: 0.0 for i in range(7)}
    weekday_cost_valid: dict[int, bool] = {i: True for i in range(7)}
    weekday_conv: dict[int, int] = {i: 0 for i in range(7)}
    weekday_seen: dict[int, bool] = {i: False for i in range(7)}
    for day, cost_sum, null_rows, net_conv in rows:
        wd = day.weekday()
        weekday_seen[wd] = True
        cost = _money(cost_sum, null_rows)
        if cost is None:
            weekday_cost_valid[wd] = False
        else:
            weekday_cost[wd] += cost
        weekday_conv[wd] += int(net_conv or 0)

    comparable_cpas = [
        weekday_cost[i] / weekday_conv[i]
        for i in range(7)
        if weekday_seen[i] and weekday_cost_valid[i]
        and weekday_conv[i] >= _A13_MIN_NET_CONVERSIONS_FOR_COMPARISON
    ]
    median_cpa = _median(comparable_cpas)

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A13",
        "finding": "hour_of_day_unavailable",
        "reason": "CAMPAIGN_PERFORMANCE_REPORT Директа не содержит измерения Hour "
                  "— структурное ограничение источника, не текущего прогона",
        "confidence": "LOW",
    }]
    for i in range(7):
        if not weekday_seen[i]:
            continue
        cost = weekday_cost[i] if weekday_cost_valid[i] else None
        net_conv = weekday_conv[i]
        cpa = (
            cost / net_conv
            if (cost is not None and net_conv >= _A13_MIN_NET_CONVERSIONS_FOR_COMPARISON)
            else None
        )
        ratio = (cpa / median_cpa) if (cpa is not None and median_cpa) else None
        rows_out.append({
            "check_id": "A13",
            "finding": "weekday_economics",
            "weekday": _WEEKDAY_NAMES[i],
            "weekday_index": i,
            "cost_normalized_rub": round(cost, 2) if cost is not None else None,
            "net_conversions": net_conv,
            "cpa_rub": round(cpa, 2) if cpa is not None else None,
            "median_weekday_cpa_rub": round(median_cpa, 2) if median_cpa is not None else None,
            "cpa_to_median_ratio": round(ratio, 3) if ratio is not None else None,
            "outlier_ratio_threshold": _A13_CPA_OUTLIER_RATIO,
            "weekday_persistently_worse": bool(ratio is not None and ratio >= _A13_CPA_OUTLIER_RATIO),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a13", rows_out, confidence_cap=confidence_cap)


# ── A14 — устройства различаются по CPA и конверсии ─────────────────────────
def _run_a14(
    paths: Any, config: dict[str, Any], defaults: dict[str, Any],
    canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path,
) -> None:
    """Два независимых среза: cr_by_device — визит-уровень (source_group='ad'

    визиты по device из visits.parquet), может быть HIGH при достаточной
    выборке (тот же принцип-исключение, что paid_vs_site_gap в A01).
    cpa_by_device — кампанийный агрегат (direct_campaigns.device + goal_conv),
    поэтому не выше MED, как остальная кампанийная экономика блока.
    """
    min_sample = int(defaults.get("min_sample_visits", 500))
    alpha = float(defaults.get("significance_alpha", 0.05))

    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        device_rows = con.execute(
            "SELECT device, COUNT(*), SUM(CASE WHEN form_submit THEN 1 ELSE 0 END) "
            "FROM visits WHERE source_group = 'ad' AND device IS NOT NULL GROUP BY device ORDER BY device"
        ).fetchall()
        total_ad_visits, total_ad_submit = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN form_submit THEN 1 ELSE 0 END) "
            "FROM visits WHERE source_group = 'ad'"
        ).fetchone()

        cpa_rows = None
        if "direct_campaigns" in canonical and _table_nonempty(canonical["direct_campaigns"]):
            goal_ids = _macro_goal_ids(config)
            expr = _net_conversions_expr(con, "direct_campaigns", goal_ids)
            if expr is not None:
                cpa_rows = con.execute(
                    "SELECT device, "
                    "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
                    "COUNT(*) FILTER (WHERE cost_normalized IS NULL), "
                    f"SUM({expr}), SUM(clicks) "
                    "FROM direct_campaigns WHERE device IS NOT NULL GROUP BY device ORDER BY device"
                ).fetchall()
    finally:
        con.close()

    total_ad_visits = int(total_ad_visits or 0)
    total_ad_submit = int(total_ad_submit or 0)
    overall_rate = (total_ad_submit / total_ad_visits) if total_ad_visits > 0 else None

    rows_out: list[dict[str, Any]] = []
    for device, n, submit in device_rows:
        n = int(n or 0)
        submit = int(submit or 0)
        rate = (submit / n) if n > 0 else None
        p_value = (
            _two_proportion_p_value(submit, n, total_ad_submit, total_ad_visits)
            if n > 0 and total_ad_visits > 0 else None
        )
        significant = p_value is not None and p_value < alpha and n >= min_sample
        worse = bool(
            significant and rate is not None and overall_rate is not None and rate < overall_rate
        )
        rows_out.append({
            "check_id": "A14",
            "finding": "cr_by_device",
            "device": device,
            "ad_visits": n,
            "ad_form_submit_rate": round(rate, 4) if rate is not None else None,
            "overall_ad_form_submit_rate": round(overall_rate, 4) if overall_rate is not None else None,
            "p_value": round(p_value, 6) if p_value is not None else None,
            "significance_alpha": alpha,
            "device_cr_worse_than_overall": worse,
            "confidence": _cap(_sample_confidence(n, min_sample) if n > 0 else "LOW", confidence_cap),
        })

    if cpa_rows is None:
        rows_out.append({
            "check_id": "A14",
            "finding": "cpa_by_device_unavailable",
            "reason": "нет чистых конверсий по устройствам: macro_goals не настроены "
                      "в config.sources.direct, direct_campaigns недоступна, или "
                      "колонки goal_conv_<id> отсутствуют",
            "confidence": "LOW",
        })
    else:
        by_device: dict[str, dict[str, Any]] = {
            device: {"cost": _money(cost_sum, null_rows), "net_conv": int(net_conv or 0), "clicks": int(clicks or 0)}
            for device, cost_sum, null_rows, net_conv, clicks in cpa_rows
        }
        comparable_cpas = [
            v["cost"] / v["net_conv"] for v in by_device.values()
            if v["cost"] is not None and v["net_conv"] >= _A14_MIN_NET_CONVERSIONS_FOR_COMPARISON
        ]
        median_cpa = _median(comparable_cpas)
        for device, v in sorted(by_device.items()):
            cpa = (
                v["cost"] / v["net_conv"]
                if (v["cost"] is not None and v["net_conv"] >= _A14_MIN_NET_CONVERSIONS_FOR_COMPARISON)
                else None
            )
            ratio = (cpa / median_cpa) if (cpa is not None and median_cpa) else None
            rows_out.append({
                "check_id": "A14",
                "finding": "cpa_by_device",
                "device": device,
                "cost_normalized_rub": round(v["cost"], 2) if v["cost"] is not None else None,
                "net_conversions": v["net_conv"],
                "clicks": v["clicks"],
                "cpa_rub": round(cpa, 2) if cpa is not None else None,
                "median_device_cpa_rub": round(median_cpa, 2) if median_cpa is not None else None,
                "cpa_to_median_ratio": round(ratio, 3) if ratio is not None else None,
                "outlier_ratio_threshold": _A14_CPA_OUTLIER_RATIO,
                "device_cpa_persistently_worse": bool(
                    ratio is not None and ratio >= _A14_CPA_OUTLIER_RATIO
                ),
                "confidence": _cap("MED", confidence_cap),
            })

    _write_metric_artifact(metrics_dir, "a14", rows_out, confidence_cap=confidence_cap)


# ── A15 — площадки РСЯ/приложения дают мусорный трафик ──────────────────────
def _run_a15(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """direct_placements не несёт goal_conv_<id> (записана через

    write_canonical_table, не _write_direct_table — см. build_canonical.build())
    — "чистые цели" по площадке структурно недоступны, только conversions_all
    (сырое "по любой цели", запрещено правилом 11 каталога как бизнес-
    результат — поэтому не используется вовсе). Ранжирование — по расходу и
    вовлечению (клики) с явной пометкой недоступности net-конверсий.
    """
    if "direct_placements" not in canonical or not _table_nonempty(canonical["direct_placements"]):
        _write_unavailable(
            metrics_dir, "A15", "direct_placements недоступна (отчёт по площадкам РСЯ не выгружен)"
        )
        return

    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        rows = con.execute(
            "SELECT placement, ad_network_type, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), SUM(clicks) "
            "FROM direct_placements WHERE placement IS NOT NULL "
            "GROUP BY placement, ad_network_type ORDER BY placement, ad_network_type"
        ).fetchall()
        total_cost_sum, total_null_rows = con.execute(
            "SELECT SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL) FROM direct_placements"
        ).fetchone()
    finally:
        con.close()

    total_cost = _money(total_cost_sum, total_null_rows)

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A15",
        "finding": "net_conversions_unavailable",
        "reason": "direct_placements не содержит goal_conv_<id> (без динамических "
                  "колонок целей) — ранжирование только по расходу/кликам",
        "confidence": "LOW",
    }]
    for placement, ad_network_type, cost_sum, null_rows, clicks in sorted(
        rows, key=lambda r: r[0] or ""
    ):
        clicks = int(clicks or 0)
        if clicks < _A15_MIN_CLICKS_FOR_RANKING:
            continue
        cost = _money(cost_sum, null_rows)
        cost_share = (cost / total_cost) if (cost is not None and total_cost) else None
        rows_out.append({
            "check_id": "A15",
            "finding": "placement_ranking",
            "placement": placement,
            "ad_network_type": ad_network_type,
            "cost_normalized_rub": round(cost, 2) if cost is not None else None,
            "clicks": clicks,
            "cost_share_of_placements_total": round(cost_share, 4) if cost_share is not None else None,
            "notable_spend_share": bool(cost_share is not None and cost_share >= _A15_NOTABLE_SPEND_SHARE),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a15", rows_out, confidence_cap=confidence_cap)


# ── A16 — ретаргетинг (данных нет структурно, см. докстринг модуля) ────────
def _run_a16(metrics_dir: Path) -> None:
    _write_unavailable(
        metrics_dir, "A16",
        "campaign_targeting.json (ретаргетинг-аудитории, частота, окно) не входит "
        "в canonical-слой — extract пишет его в data/raw/direct/, но build_canonical.py "
        "не строит из него ни одной таблицы; расширение схемы вне allowed_files "
        "этой задачи, тот же принцип, что у A07",
    )


# ── A17 — брендовая реклама каннибализирует бесплатный спрос ───────────────
def _run_a17(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """"Наличие рекламы конкурентов" не проверяется этим источником (нет

    данных о чужой рекламе) — явная запись competitor_ads_not_checked.
    Сопоставляется только платный брендовый расход (direct_queries,
    классификация is_brand_query — той же функцией, что build_canonical
    использует для seo_queries.is_brand) с органическими позициями бренда
    (seo_queries.is_brand, взвешенная по показам средняя позиция).
    """
    brand_terms = config.get("brand_terms") or []
    if not brand_terms:
        _write_unavailable(
            metrics_dir, "A17", "config.brand_terms не заполнен — классификация бренд/небренд невозможна"
        )
        return

    goal_ids = _macro_goal_ids(config)
    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        query_rows = con.execute(
            "SELECT query, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), SUM(clicks) "
            f"FROM direct_queries WHERE match_type IN ({phrase_types_sql}) GROUP BY query ORDER BY query"
        ).fetchall()

        net_conv_by_query: dict[str, int] = {}
        expr = _net_conversions_expr(con, "direct_queries", goal_ids)
        if expr is not None:
            conv_rows = con.execute(
                f"SELECT query, SUM({expr}) FROM direct_queries "
                f"WHERE match_type IN ({phrase_types_sql}) GROUP BY query ORDER BY query"
            ).fetchall()
            net_conv_by_query = {q: int(v or 0) for q, v in conv_rows}

        organic_rows = []
        if "seo_queries" in canonical and _table_nonempty(canonical["seo_queries"]):
            organic_rows = con.execute(
                "SELECT query, "
                "SUM(total_shows * avg_show_position) / NULLIF(SUM(total_shows), 0), "
                "SUM(total_clicks), SUM(total_shows) "
                "FROM seo_queries WHERE is_brand = true GROUP BY query ORDER BY query"
            ).fetchall()
    finally:
        con.close()

    brand_paid = [
        (q, _money(cost_sum, null_rows), int(clicks or 0), net_conv_by_query.get(q))
        for q, cost_sum, null_rows, clicks in query_rows
        if is_brand_query(q, brand_terms)
    ]
    organic_by_query = {
        q: {"avg_position": pos, "clicks": int(clicks or 0), "shows": int(shows or 0)}
        for q, pos, clicks, shows in organic_rows
    }

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A17",
        "finding": "competitor_ads_not_checked",
        "reason": "наличие рекламы конкурентов по бренду не проверяется доступными "
                  "источниками (нет данных о чужой рекламе)",
        "confidence": "LOW",
    }]
    if not organic_rows:
        rows_out.append({
            "check_id": "A17",
            "finding": "organic_brand_data_unavailable",
            "reason": "seo_queries недоступна или без брендовых запросов — сравнение "
                      "с органикой невозможно, показан только платный брендовый расход",
            "confidence": "LOW",
        })
    for q, cost, clicks, net_conv in sorted(brand_paid, key=lambda r: r[0] or ""):
        organic = organic_by_query.get(q)
        organic_top = bool(
            organic and organic["avg_position"] is not None
            and organic["avg_position"] <= _A17_ORGANIC_TOP_POSITION
        )
        rows_out.append({
            "check_id": "A17",
            "finding": "brand_query_paid_vs_organic",
            "query": q,
            "cost_normalized_rub": round(cost, 2) if cost is not None else None,
            "clicks": clicks,
            "net_conversions": net_conv,
            "organic_avg_position": (
                round(organic["avg_position"], 2) if organic and organic["avg_position"] is not None else None
            ),
            "organic_top_position_threshold": _A17_ORGANIC_TOP_POSITION,
            "organic_already_visible": organic_top,
            "possible_cannibalization": bool(cost is not None and cost > 0 and organic_top),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a17", rows_out, confidence_cap=confidence_cap)


# ── A18 — кампании конкурируют друг с другом за одинаковый спрос ───────────
def _run_a18(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Только пересечение по поисковому запросу (direct_queries) — по

    ключевым фразам/аудиториям/гео не проверяется (keywords.parquet/
    campaign_targeting.json структурно недоступны, см. докстринг модуля);
    это ограничение уже отражено в config/methodology.yaml
    (A18.requires == ["direct_queries"]).
    """
    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        rows = con.execute(
            "SELECT query, campaign_id, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), SUM(clicks) "
            "FROM direct_queries WHERE campaign_id IS NOT NULL "
            "GROUP BY query, campaign_id ORDER BY query, campaign_id"
        ).fetchall()
    finally:
        con.close()

    by_query: dict[str, list[tuple[str, float | None, int]]] = {}
    for query, campaign_id, cost_sum, null_rows, clicks in rows:
        by_query.setdefault(query, []).append((campaign_id, _money(cost_sum, null_rows), int(clicks or 0)))

    rows_out: list[dict[str, Any]] = []
    for query, campaigns in sorted(by_query.items()):
        total_clicks = sum(c for _, _, c in campaigns)
        if len(campaigns) < 2 or total_clicks < _A18_MIN_CLICKS_FOR_OVERLAP:
            continue
        rows_out.append({
            "check_id": "A18",
            "query": query,
            "campaign_count": len(campaigns),
            "total_clicks": total_clicks,
            "campaigns": [
                {
                    "campaign_id": cid,
                    "cost_normalized_rub": round(cost, 2) if cost is not None else None,
                    "clicks": clicks,
                }
                for cid, cost, clicks in sorted(campaigns, key=lambda r: r[0] or "")
            ],
            "competing_campaigns": True,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a18", rows_out, confidence_cap=confidence_cap)


# ── A19 — CPC аномально высок относительно близких запросов ────────────────
def _run_a19(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    con = _open_duckdb_with_direct_vat(paths, canonical)
    try:
        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        rows = con.execute(
            "SELECT query, match_type, "
            "SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL), "
            "COUNT(*) FILTER (WHERE cost_normalized IS NULL), SUM(clicks) "
            f"FROM direct_queries WHERE match_type IN ({phrase_types_sql}) "
            "GROUP BY query, match_type ORDER BY query, match_type"
        ).fetchall()
    finally:
        con.close()

    entries: list[tuple[str, str, float | None, int]] = []
    comparable_cpcs: list[float] = []
    for query, match_type, cost_sum, null_rows, clicks in rows:
        cost = _money(cost_sum, null_rows)
        clicks = int(clicks or 0)
        entries.append((query, match_type, cost, clicks))
        if cost is not None and clicks >= _A19_MIN_CLICKS_FOR_COMPARISON:
            comparable_cpcs.append(cost / clicks)
    median_cpc = _median(comparable_cpcs)

    rows_out: list[dict[str, Any]] = []
    for query, match_type, cost, clicks in sorted(entries, key=lambda r: (r[0] or "", r[1] or "")):
        cpc = (
            cost / clicks if (cost is not None and clicks >= _A19_MIN_CLICKS_FOR_COMPARISON) else None
        )
        ratio = (cpc / median_cpc) if (cpc is not None and median_cpc) else None
        rows_out.append({
            "check_id": "A19",
            "query": query,
            "match_type": match_type,
            "cost_normalized_rub": round(cost, 2) if cost is not None else None,
            "clicks": clicks,
            "cpc_rub": round(cpc, 2) if cpc is not None else None,
            "median_cpc_rub": round(median_cpc, 2) if median_cpc is not None else None,
            "cpc_to_median_ratio": round(ratio, 3) if ratio is not None else None,
            "outlier_ratio_threshold": _A19_CPC_OUTLIER_RATIO,
            "cpc_anomalously_high": bool(ratio is not None and ratio >= _A19_CPC_OUTLIER_RATIO),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a19", rows_out, confidence_cap=confidence_cap)


# ── A20 — низкий CTR у релевантных показов ──────────────────────────────────
def _run_a20(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        rows = con.execute(
            "SELECT query, match_type, SUM(clicks), SUM(impressions) "
            f"FROM direct_queries WHERE match_type IN ({phrase_types_sql}) "
            "GROUP BY query, match_type ORDER BY query, match_type"
        ).fetchall()
    finally:
        con.close()

    entries: list[tuple[str, str, int, int, float | None]] = []
    comparable_ctrs: list[float] = []
    for query, match_type, clicks, impressions in rows:
        clicks = int(clicks or 0)
        impressions = int(impressions or 0)
        ctr = (clicks / impressions) if impressions > 0 else None
        entries.append((query, match_type, clicks, impressions, ctr))
        if impressions >= _A20_MIN_IMPRESSIONS_FOR_COMPARISON and ctr is not None:
            comparable_ctrs.append(ctr)
    median_ctr = _median(comparable_ctrs)

    rows_out: list[dict[str, Any]] = []
    for query, match_type, clicks, impressions, ctr in sorted(
        entries, key=lambda r: (r[0] or "", r[1] or "")
    ):
        eligible = impressions >= _A20_MIN_IMPRESSIONS_FOR_COMPARISON and ctr is not None
        ratio = (ctr / median_ctr) if (eligible and median_ctr) else None
        rows_out.append({
            "check_id": "A20",
            "query": query,
            "match_type": match_type,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(ctr, 4) if ctr is not None else None,
            "median_ctr": round(median_ctr, 4) if median_ctr is not None else None,
            "ctr_to_median_ratio": round(ratio, 3) if ratio is not None else None,
            "low_ctr_ratio_threshold": _A20_CTR_LOW_RATIO,
            "min_impressions_for_comparison": _A20_MIN_IMPRESSIONS_FOR_COMPARISON,
            "anomalously_low_ctr": bool(eligible and ratio is not None and ratio <= _A20_CTR_LOW_RATIO),
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a20", rows_out, confidence_cap=confidence_cap)


# ── A21 — высокий CTR сочетается с низкой конверсией ────────────────────────
def _run_a21(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    goal_ids = _macro_goal_ids(config)
    con = common.open_duckdb(paths)
    try:
        expr = _net_conversions_expr(con, "direct_queries", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A21",
                "нет чистых конверсий по запросам: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_queries",
            )
            return
        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        rows = con.execute(
            "SELECT query, match_type, SUM(clicks), SUM(impressions), "
            f"SUM({expr}) "
            f"FROM direct_queries WHERE match_type IN ({phrase_types_sql}) "
            "GROUP BY query, match_type ORDER BY query, match_type"
        ).fetchall()
        total_visits, total_submit = con.execute(
            "SELECT COUNT(*), SUM(CASE WHEN form_submit THEN 1 ELSE 0 END) FROM visits"
        ).fetchone()
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    site_rate = (total_submit or 0) / total_visits if total_visits > 0 else None

    entries: list[tuple[str, str, int, int, float | None, int]] = []
    comparable_ctrs: list[float] = []
    for query, match_type, clicks, impressions, net_conv in rows:
        clicks = int(clicks or 0)
        impressions = int(impressions or 0)
        net_conv = int(net_conv or 0)
        ctr = (clicks / impressions) if impressions > 0 else None
        entries.append((query, match_type, clicks, impressions, ctr, net_conv))
        if clicks >= _A21_MIN_CLICKS_FOR_HIGH_CTR and ctr is not None:
            comparable_ctrs.append(ctr)
    median_ctr = _median(comparable_ctrs)

    rows_out: list[dict[str, Any]] = []
    for query, match_type, clicks, impressions, ctr, net_conv in sorted(
        entries, key=lambda r: (r[0] or "", r[1] or "")
    ):
        eligible = clicks >= _A21_MIN_CLICKS_FOR_HIGH_CTR and ctr is not None
        ratio = (ctr / median_ctr) if (eligible and median_ctr) else None
        high_ctr = bool(eligible and ratio is not None and ratio >= _A21_HIGH_CTR_RATIO)
        rows_out.append({
            "check_id": "A21",
            "query": query,
            "match_type": match_type,
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(ctr, 4) if ctr is not None else None,
            "median_ctr": round(median_ctr, 4) if median_ctr is not None else None,
            "ctr_to_median_ratio": round(ratio, 3) if ratio is not None else None,
            "high_ctr_ratio_threshold": _A21_HIGH_CTR_RATIO,
            "net_conversions": net_conv,
            "high_ctr": high_ctr,
            "high_ctr_low_conversion": bool(high_ctr and net_conv == 0),
            "site_form_submit_rate_context": round(site_rate, 4) if site_rate is not None else None,
            "confidence": _cap("MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a21", rows_out, confidence_cap=confidence_cap)


# ── A22 — запрос, объявление и посадочная не соответствуют друг другу ──────
def _run_a22(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Только запрос vs текст объявления (ad_texts, ТОЛЬКО active — см.

    докстринг модуля). Соответствие запрос->посадочная — в A23 (не
    дублируется здесь, один источник правды на одну цифру, тот же принцип,
    что paid_vs_site_gap/C06 в methodology-v2 §6). Поведение на странице
    после клика не проверяется: канонического join визита на конкретный
    запрос нет (ym:s:lastSignDirectClickOrder отсутствует в SCHEMAS["visits"],
    см. докстринг модуля про A02–A08). Эвристика (пересечение токенов
    запрос/объявление) — LOW по построению, требует ручного подтверждения
    (каталог: тип A+B).
    """
    if "ad_texts" not in canonical or not _table_nonempty(canonical["ad_texts"]):
        _write_unavailable(
            metrics_dir, "A22", "ad_texts недоступна (нет активных объявлений State=ON)"
        )
        return
    if "direct_queries" not in canonical or not _table_nonempty(canonical["direct_queries"]):
        _write_unavailable(metrics_dir, "A22", "direct_queries недоступна")
        return

    con = common.open_duckdb(paths)
    try:
        ad_rows = con.execute(
            "SELECT campaign_id, ad_group_id, title, title2, text "
            "FROM ad_texts WHERE ad_group_id IS NOT NULL"
        ).fetchall()
        phrase_types_sql = ", ".join(f"'{t}'" for t in _PHRASE_MATCH_TYPES)
        query_rows = con.execute(
            "SELECT campaign_id, ad_group_id, query, SUM(clicks) "
            f"FROM direct_queries WHERE match_type IN ({phrase_types_sql}) "
            "AND ad_group_id IS NOT NULL GROUP BY campaign_id, ad_group_id, query ORDER BY campaign_id, ad_group_id, query"
        ).fetchall()
    finally:
        con.close()

    ads_by_group: dict[tuple[str, str], list[set[str]]] = {}
    for campaign_id, ad_group_id, title, title2, text in ad_rows:
        ads_by_group.setdefault((campaign_id, ad_group_id), []).append(
            _tokenize(" ".join(filter(None, [title, title2, text])))
        )

    top_query_by_group: dict[tuple[str, str], tuple[str, int]] = {}
    for campaign_id, ad_group_id, query, clicks in query_rows:
        key = (campaign_id, ad_group_id)
        clicks = int(clicks or 0)
        current = top_query_by_group.get(key)
        if current is None or clicks > current[1]:
            top_query_by_group[key] = (query, clicks)

    rows_out: list[dict[str, Any]] = []
    for key, (query, clicks) in sorted(top_query_by_group.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        ad_token_sets = ads_by_group.get(key)
        if ad_token_sets is None or clicks < _A22_MIN_CLICKS_FOR_MISMATCH_CHECK:
            continue
        campaign_id, ad_group_id = key
        query_tokens = _tokenize(query)
        best_overlap = max((len(query_tokens & ad_tokens) for ad_tokens in ad_token_sets), default=0)
        rows_out.append({
            "check_id": "A22",
            "campaign_id": campaign_id,
            "ad_group_id": ad_group_id,
            "top_query": query,
            "top_query_clicks": clicks,
            "ads_in_group": len(ad_token_sets),
            "shared_keyword_tokens": best_overlap,
            "query_ad_keyword_mismatch": bool(query_tokens) and best_overlap == 0,
            "confidence": _cap("LOW", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a22", rows_out, confidence_cap=confidence_cap)


# ── A23 — конкретный спрос ведётся на слишком общую страницу ───────────────
def _run_a23(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Без конфигурации "общих/тематических" страниц (её нет в config.yaml,

    вне allowed_files этой задачи) — эвристика по глубине entry_page (число
    непустых сегментов пути): depth<=1 — "общая" (главная/категория),
    depth>=2 — "тематическая" (конкретная страница). Визит-уровень
    (source_group='ad'), поэтому может быть HIGH при достаточной выборке —
    тот же принцип-исключение, что paid_vs_site_gap в A01.
    """
    min_sample = int(defaults.get("min_sample_visits", 500))
    alpha = float(defaults.get("significance_alpha", 0.05))

    con = common.open_duckdb(paths)
    try:
        rows = con.execute(
            "SELECT entry_page, COUNT(*), SUM(CASE WHEN form_submit THEN 1 ELSE 0 END) "
            "FROM visits WHERE source_group = 'ad' AND entry_page IS NOT NULL GROUP BY entry_page ORDER BY entry_page"
        ).fetchall()
    finally:
        con.close()

    general_n = general_submit = specific_n = specific_submit = 0
    for entry_page, n, submit in rows:
        depth = len([seg for seg in (entry_page or "").split("/") if seg])
        n = int(n or 0)
        submit = int(submit or 0)
        if depth <= 1:
            general_n += n
            general_submit += submit
        else:
            specific_n += n
            specific_submit += submit

    general_rate = (general_submit / general_n) if general_n > 0 else None
    specific_rate = (specific_submit / specific_n) if specific_n > 0 else None
    p_value = (
        _two_proportion_p_value(general_submit, general_n, specific_submit, specific_n)
        if general_n > 0 and specific_n > 0 else None
    )
    significant = (
        p_value is not None and p_value < alpha
        and general_n >= min_sample and specific_n >= min_sample
    )
    generic_underperforms = bool(
        significant and general_rate is not None and specific_rate is not None
        and general_rate < specific_rate
    )

    rows_out = [{
        "check_id": "A23",
        "general_landing_ad_visits": general_n,
        "general_landing_form_submit_rate": round(general_rate, 4) if general_rate is not None else None,
        "specific_landing_ad_visits": specific_n,
        "specific_landing_form_submit_rate": round(specific_rate, 4) if specific_rate is not None else None,
        "p_value": round(p_value, 6) if p_value is not None else None,
        "significance_alpha": alpha,
        "min_sample_visits": min_sample,
        "generic_landing_underperforms": generic_underperforms,
        "confidence": _cap(
            "HIGH" if (general_n >= min_sample and specific_n >= min_sample) else "MED",
            confidence_cap,
        ),
    }]
    _write_metric_artifact(metrics_dir, "a23", rows_out, confidence_cap=confidence_cap)


# ── A24 — устаревшая цена/акция/наличие в объявлении ────────────────────────
def _run_a24(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Тип каталога A+B: компьютер не подтверждает актуальность цены/акции —

    site_pages не хранит цену/наличие товара (см. SCHEMAS["site_pages"]).
    Функция только отбирает КАНДИДАТОВ на ручную проверку (regex по цифра+
    валюта/процент, ключевые слова акций) — не автоматический вердикт.
    """
    if "ad_texts" not in canonical or not _table_nonempty(canonical["ad_texts"]):
        _write_unavailable(
            metrics_dir, "A24", "ad_texts недоступна (нет активных объявлений State=ON)"
        )
        return

    con = common.open_duckdb(paths)
    try:
        ad_rows = con.execute(
            "SELECT ad_id, campaign_id, ad_group_id, title, title2, text, href FROM ad_texts"
        ).fetchall()
    finally:
        con.close()

    has_site_crawl = "site_pages" in canonical and _table_nonempty(canonical["site_pages"])

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A24",
        "finding": "manual_verification_required",
        "reason": "автоматическая сверка цены/наличия с сайтом невозможна: site_pages "
                  "не хранит цену/наличие товара — только ручная проверка (тип A+B)",
        "site_crawl_available_for_manual_check": has_site_crawl,
        "confidence": "LOW",
    }]
    for ad_id, campaign_id, ad_group_id, title, title2, text, href in ad_rows:
        full_text = " ".join(filter(None, [title, title2, text]))
        has_price = bool(_A24_PRICE_PATTERN.search(full_text))
        has_promo_word = any(w in full_text.lower() for w in _A24_PROMO_WORDS)
        if not has_price and not has_promo_word:
            continue
        rows_out.append({
            "check_id": "A24",
            "finding": "manual_check_candidate",
            "ad_id": ad_id,
            "campaign_id": campaign_id,
            "ad_group_id": ad_group_id,
            "href": href,
            "has_price_pattern": has_price,
            "has_promo_word": has_promo_word,
            "confidence": "LOW",
        })

    _write_metric_artifact(metrics_dir, "a24", rows_out, confidence_cap=confidence_cap)


# ── A25 — товарный фид (данных нет структурно, см. докстринг модуля) ───────
def _run_a25(metrics_dir: Path) -> None:
    _write_unavailable(
        metrics_dir, "A25",
        "product_feed.parquet не входит в canonical-слой — extract пишет его в "
        "data/raw/direct/ (если фид используется), но build_canonical.py не строит "
        "из него ни одной таблицы; расширение схемы вне allowed_files этой задачи, "
        "тот же принцип, что у A07/A16",
    )


# ── A26 — кампании оценены без учёта лага, сезонности или малого объёма ────
def _run_a26(
    paths: Any, config: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Сезонность (Wordstat) НЕ проверяется этим источником — нет канонической

    таблицы wordstat (extract пишет данные только в raw, join сюда не введён —
    optional=["wordstat"] в реестре относится к грубой доступности источника
    для степени деградации, не к физическому join в этой функции). Лаг до
    цели на визит-уровне не проверяется — тот же join-пробел, что у A02–A08
    (ym:s:lastSignDirectClickOrder отсутствует в SCHEMAS["visits"]). Проверяется
    только объём выборки (месяцы + чистые конверсии) по direct_campaigns.
    """
    if "direct_campaigns" not in canonical or not _table_nonempty(canonical["direct_campaigns"]):
        _write_unavailable(
            metrics_dir, "A26",
            "direct_campaigns недоступна (отчёт по кампаниям Директа не выгружен)",
        )
        return

    goal_ids = _macro_goal_ids(config)
    con = common.open_duckdb(paths)
    try:
        expr = _net_conversions_expr(con, "direct_campaigns", goal_ids)
        if expr is None:
            con.close()
            _write_unavailable(
                metrics_dir, "A26",
                "нет чистых конверсий по кампаниям: macro_goals не настроены в "
                "config.sources.direct или колонки goal_conv_<id> отсутствуют в direct_campaigns",
            )
            return
        rows = con.execute(
            "SELECT campaign_id, MAX(campaign_name), strftime(date, '%Y-%m'), "
            f"SUM({expr}) "
            "FROM direct_campaigns WHERE campaign_id IS NOT NULL "
            "GROUP BY campaign_id, strftime(date, '%Y-%m') ORDER BY campaign_id, strftime(date, '%Y-%m')"
        ).fetchall()
    finally:
        con.close()

    by_campaign: dict[str, dict[str, Any]] = {}
    for campaign_id, campaign_name, month, net_conv in rows:
        info = by_campaign.setdefault(campaign_id, {"name": campaign_name, "months": {}})
        info["months"][month] = int(net_conv or 0)

    rows_out: list[dict[str, Any]] = [{
        "check_id": "A26",
        "finding": "wordstat_seasonality_unavailable",
        "reason": "нет канонической таблицы wordstat (сезонный спрос) — join с "
                  "кампанийной экономикой здесь не реализован",
        "confidence": "LOW",
    }]
    for campaign_id, info in sorted(by_campaign.items()):
        months = info["months"]
        total_conv = sum(months.values())
        months_tracked = len(months)
        insufficient = (
            months_tracked < _A26_MIN_MONTHS_FOR_JUDGMENT
            or total_conv < _A26_MIN_NET_CONVERSIONS_FOR_JUDGMENT
        )
        rows_out.append({
            "check_id": "A26",
            "finding": "campaign_sample_check",
            "campaign_id": campaign_id,
            "campaign_name": info["name"],
            "months_tracked": months_tracked,
            "min_months_threshold": _A26_MIN_MONTHS_FOR_JUDGMENT,
            "total_net_conversions": total_conv,
            "min_net_conversions_threshold": _A26_MIN_NET_CONVERSIONS_FOR_JUDGMENT,
            "insufficient_sample_for_judgment": insufficient,
            "confidence": _cap("LOW" if insufficient else "MED", confidence_cap),
        })

    _write_metric_artifact(metrics_dir, "a26", rows_out, confidence_cap=confidence_cap)


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить A01–A11 из числа доступных; вернуть имена записанных артефактов."""
    canonical = common.load_canonical(paths)
    config = orchestrator_mod.load_client_config(paths)
    caps = _confidence_caps(paths)
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[str] = []

    if "A01" in runnable_ids and "visits" in canonical:
        _run_a01(paths, config, defaults, canonical, caps.get("A01", "HIGH"), metrics_dir)
        artifacts.append("a01")

    if "A02" in runnable_ids and "visits" in canonical:
        _run_a02(paths, config, canonical, caps.get("A02", "HIGH"), metrics_dir)
        artifacts.append("a02")

    if "A03" in runnable_ids and "visits" in canonical:
        _run_a03(paths, canonical, caps.get("A03", "HIGH"), metrics_dir)
        artifacts.append("a03")

    if "A04" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a04(paths, config, canonical, caps.get("A04", "HIGH"), metrics_dir)
        artifacts.append("a04")

    if "A05" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a05(paths, config, canonical, caps.get("A05", "HIGH"), metrics_dir)
        artifacts.append("a05")

    if "A06" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a06(paths, config, canonical, caps.get("A06", "HIGH"), metrics_dir)
        artifacts.append("a06")

    if "A07" in runnable_ids and "costs" in canonical:
        _run_a07(metrics_dir)
        artifacts.append("a07")

    if "A08" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a08(paths, config, canonical, caps.get("A08", "HIGH"), metrics_dir)
        artifacts.append("a08")

    if "A09" in runnable_ids and "direct_queries" in canonical:
        _run_a09(paths, config, canonical, caps.get("A09", "HIGH"), metrics_dir)
        artifacts.append("a09")

    if "A10" in runnable_ids and "direct_queries" in canonical:
        _run_a10(paths, config, canonical, caps.get("A10", "HIGH"), metrics_dir)
        artifacts.append("a10")

    if "A11" in runnable_ids and "direct_queries" in canonical and "visits" in canonical:
        _run_a11(paths, config, canonical, caps.get("A11", "HIGH"), metrics_dir)
        artifacts.append("a11")

    if "A12" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a12(paths, config, canonical, caps.get("A12", "HIGH"), metrics_dir)
        artifacts.append("a12")

    if "A13" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a13(paths, config, canonical, caps.get("A13", "HIGH"), metrics_dir)
        artifacts.append("a13")

    if "A14" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a14(paths, config, defaults, canonical, caps.get("A14", "HIGH"), metrics_dir)
        artifacts.append("a14")

    if "A15" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a15(paths, canonical, caps.get("A15", "HIGH"), metrics_dir)
        artifacts.append("a15")

    if "A16" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a16(metrics_dir)
        artifacts.append("a16")

    if "A17" in runnable_ids and "direct_queries" in canonical:
        _run_a17(paths, config, canonical, caps.get("A17", "HIGH"), metrics_dir)
        artifacts.append("a17")

    if "A18" in runnable_ids and "direct_queries" in canonical:
        _run_a18(paths, canonical, caps.get("A18", "HIGH"), metrics_dir)
        artifacts.append("a18")

    if "A19" in runnable_ids and "costs" in canonical and "direct_queries" in canonical:
        _run_a19(paths, canonical, caps.get("A19", "HIGH"), metrics_dir)
        artifacts.append("a19")

    if "A20" in runnable_ids and "direct_queries" in canonical:
        _run_a20(paths, canonical, caps.get("A20", "HIGH"), metrics_dir)
        artifacts.append("a20")

    if "A21" in runnable_ids and "direct_queries" in canonical and "visits" in canonical:
        _run_a21(paths, config, canonical, caps.get("A21", "HIGH"), metrics_dir)
        artifacts.append("a21")

    if "A22" in runnable_ids and "direct_queries" in canonical and "visits" in canonical:
        _run_a22(paths, canonical, caps.get("A22", "HIGH"), metrics_dir)
        artifacts.append("a22")

    if "A23" in runnable_ids and "visits" in canonical:
        _run_a23(paths, defaults, canonical, caps.get("A23", "HIGH"), metrics_dir)
        artifacts.append("a23")

    if "A24" in runnable_ids and "direct_queries" in canonical:
        _run_a24(paths, canonical, caps.get("A24", "HIGH"), metrics_dir)
        artifacts.append("a24")

    if "A25" in runnable_ids and "costs" in canonical:
        _run_a25(metrics_dir)
        artifacts.append("a25")

    if "A26" in runnable_ids and "costs" in canonical and "visits" in canonical:
        _run_a26(paths, config, canonical, caps.get("A26", "HIGH"), metrics_dir)
        artifacts.append("a26")

    return artifacts
