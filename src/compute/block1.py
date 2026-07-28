"""Блок 1 — экономика и эффективность платной рекламы (каталог v2 §6, A01–A26).

Задача 5D закрывает только A01–A11 (первая часть экономики рекламы).
A12–A26 — вне скоупа этой задачи, не реализуются.

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
подмена (прямое требование промта задачи). Для direct_queries/direct_campaigns
cost_normalized в текущем состоянии пайплайна всегда null (заполняется в
compute только после отдельной будущей задачи нормализации Q01 для этих
таблиц — см. build_canonical.py:1131 и docs/implementation_status.md; для
costs.parquet cost_normalized уже считается в transform через _apply_vat_to_rows).
Это значит, что на реальных данных клиента прямо сейчас A09–A11 будут писать
явную деградацию по деньгам, пока эта отдельная задача не закрыта — это
осознанное следствие правила, а не баг данного модуля.

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
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from scipy import stats

from . import common
from ..pipeline import degradation as degradation_mod
from ..pipeline import orchestrator as orchestrator_mod

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
    common.write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


def _money(cost_sum: float | None, null_rows: int) -> float | None:
    """SUM(cost_normalized) валиден, только если ни одна строка группы не null."""
    if cost_sum is None or null_rows > 0:
        return None
    return round(float(cost_sum), 2)


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
        "GROUP BY campaign_id"
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
        "WHERE campaign_id IS NOT NULL GROUP BY campaign_id"
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

    common.write_metric_artifact(metrics_dir, "a01", rows, confidence_cap=confidence_cap)


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
        common.write_metric_artifact(metrics_dir, "a02", [], confidence_cap=confidence_cap)
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

    common.write_metric_artifact(metrics_dir, "a02", rows, confidence_cap=confidence_cap)


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

    common.write_metric_artifact(metrics_dir, "a03", rows, confidence_cap=confidence_cap)


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

    common.write_metric_artifact(metrics_dir, "a04", rows, confidence_cap=confidence_cap)


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
        common.write_metric_artifact(metrics_dir, "a05", [], confidence_cap=confidence_cap)
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

    common.write_metric_artifact(metrics_dir, "a05", rows, confidence_cap=confidence_cap)


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
        common.write_metric_artifact(metrics_dir, "a06", [], confidence_cap=confidence_cap)
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

    common.write_metric_artifact(metrics_dir, "a06", rows, confidence_cap=confidence_cap)


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
        common.write_metric_artifact(metrics_dir, "a08", rows, confidence_cap=confidence_cap)
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
    common.write_metric_artifact(metrics_dir, "a08", rows, confidence_cap=confidence_cap)


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

    con = common.open_duckdb(paths)
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
            "GROUP BY query, match_type"
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

    common.write_metric_artifact(metrics_dir, "a09", rows, confidence_cap=confidence_cap)


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

    con = common.open_duckdb(paths)
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
            "GROUP BY query, match_type, month"
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

    common.write_metric_artifact(metrics_dir, "a10", rows, confidence_cap=confidence_cap)


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

    con = common.open_duckdb(paths)
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
            "GROUP BY match_type"
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

    common.write_metric_artifact(metrics_dir, "a11", rows, confidence_cap=confidence_cap)


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

    return artifacts
