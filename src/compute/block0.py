"""Блок 0 — минимальное доверие к данным (D01–D12, задача 5C завершает блок).

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §5):
    D01  переотработка ключевой цели внутри визита         [visits]
    D02  цель = клик/открытие, а не подтверждённая отправка [visits, goals]
    D03  смешаны бизнес-цели и микроконверсии               [visits, goals]
    D04  часть форм/страниц/устройств не покрыта трекингом  [visits]
    D05  UTM/click ID/источник теряются или перезаписываются[visits]
    D06  расходы посчитаны на разной базе НДС               [costs, client_answers]
    D07  расходы неполные или задвоены                      [costs, client_answers]
    D08  архивные/остановленные кампании исключены из истории[costs]
    D09  периоды/часовые пояса/даты не приведены к правилу  [visits, costs?]
    D10  выгрузка неполная (пагинация/лимиты/семплирование) [visits]
    D11  сотрудники/тесты/боты в данных                     [visits]
    D12  таблицы соединены на неверном уровне детализации   [visits, costs?]

D07–D12 читают ТОЛЬКО то, что реально есть в canonical-слое (см. docstring
каждой функции): у compute нет доступа к статусу кампании Директа
(``campaigns.get``/``archived_campaigns_retrievable``) — это флаг сырого
manifest.json источника direct, который canonical-manifest не переносит
(вне allowed_files этой задачи, см. src/transform/build_canonical.py). D08
поэтому строит сигнал из истории расходов campaign_id внутри costs.parquet,
а не из статуса кампании напрямую — ограничение зафиксировано в самой
находке (finding="summary"), не скрыто.

Контракт:
    Читает   — data/canonical/{visits,goals,costs}.parquet,
               data/canonical/manifest.json (flags.utm_uncertain — переносится
               в вывод D05 по прямому указанию докстринга build_canonical.py),
               inputs/client_answers.yaml (D06, D07), config.yaml клиента
               (goals.* группы для D03), пороги defaults (min_sample_visits,
               goal_inflation_warning, utm_undefined_threshold),
               data/metrics/degradation_report.json (confidence_cap на проверку).
    Пишет    — data/metrics/{d01..d12}.csv/.json. БЕЗ LLM.

Разрыв D02/D03 vs runnable_ids (см. docs/implementation_status.md, запись
4I-goals-canonical): src/extract/metrika_reports.py::CANONICAL_TABLES пока не
объявляет "goals", поэтому data/raw/manifest.json никогда не перечисляет
"goals" среди available_tables и degradation.build_degradation_report держит
D02/D03 в состоянии runnable=False даже когда data/canonical/goals.parquet
физически существует и непуст. Это ограничение extract-слоя (вне
allowed_files этой задачи), не факт отсутствия данных — поэтому D02/D03
НЕ гейтятся здесь через runnable_ids: доступность проверяется напрямую по
канонической таблице goals. Если goals.parquet отсутствует или пуст, D02/D03
получают явную запись со статусом "unavailable" и причиной "goals metadata
недоступна" — не пропускаются молча.
"""

from __future__ import annotations

import calendar
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from . import common
from ..pipeline import degradation as degradation_mod
from ..pipeline import orchestrator as orchestrator_mod

# Группы ключевых целей визит-уровня (см. src/transform/build_canonical.goal_flags).
GOAL_GROUPS: tuple[str, ...] = ("form_open", "form_submit", "call_click", "messenger_click")

# Значения utm_source, которые считаются "не заданными" — зеркалит
# src/transform/build_canonical._UTM_UNDEFINED_TOKENS (не импортируется напрямую,
# т.к. compute не должен цепляться за внутренности другого слоя — только за его
# выход, см. CLAUDE.md принцип 2).
_UTM_UNDEFINED_TOKENS: frozenset[str] = frozenset(
    {"", "не определено", "(not set)", "not set", "undefined", "none"}
)

# goals.type (реальные значения выгрузки Management API — см.
# docs/implementation_status.md, 4I-goals-canonical): "action"/"button"/"phone"/
# "email"/"messenger"/"social" — автоцели/JS-события, срабатывают на клик или
# переход, не доказывают успешную отправку. "url"/"step" — переход на страницу
# (в т.ч. success-страницу) или составное условие — более сильное доказательство.
_WEAK_GOAL_TYPES: frozenset[str] = frozenset(
    {"action", "button", "phone", "email", "messenger", "social"}
)

# Эвристика для D02: имя цели похоже на бизнес-отправку (заявка/заказ/оплата...).
_SUBMIT_NAME_KEYWORDS: tuple[str, ...] = (
    "отправ", "заявк", "заказ", "оформ", "submit", "send", "оплат", "бронир",
)

# D08: кампания считается «остановленной до конца периода», если последний день
# с расходом отстоит от конца окна (последний день с расходом Директа в costs)
# больше, чем на этот запас — защита от кампаний, которые просто не потратили
# в самый последний день окна (не значит «остановлена»).
_D08_STOPPED_CAMPAIGN_BUFFER_DAYS = 14

# D10: сколько дат без визитов показывать построчно в артефакте — сам факт и
# число пропусков не усекаются, усекается только список для читаемости.
_D10_MISSING_DATES_SAMPLE_LIMIT = 20

# D11: без ym:s:isRobot (недоступен постоянно, см. docstring модуля и D11 в
# CLAUDE.md) единственные проверяемые в canonical-слое прокси — частота
# визитов на clientID и явные тестовые/служебные метки в utm_source. Порог
# частоты — эвристика (не из каталога дословно): реальный посетитель редко
# даёт от 50 визитов за окно анализа (обычно 6-12 мес), сотрудник/тестовый
# аккаунт — типично даёт.
_D11_HIGH_FREQUENCY_VISITS_THRESHOLD = 50
_D11_TOP_CLIENT_IDS_LIMIT = 20
_D11_TEST_MARKER_TOKENS: tuple[str, ...] = ("test", "internal", "employee", "qa")


def _sample_confidence(sample_size: int, min_sample_visits: int) -> str:
    """HIGH при выборке >= порога, иначе MED (CLAUDE.md, «Уверенность находок»)."""
    return "HIGH" if sample_size >= min_sample_visits else "MED"


def _cap(confidence: str, confidence_cap: str) -> str:
    """Прижать confidence к потолку проверки (compute капает вниз, не поднимает)."""
    return degradation_mod.min_confidence(confidence, confidence_cap)


def _confidence_caps(paths: Any) -> dict[str, str]:
    """{check_id: confidence_cap} из уже записанного degradation_report.json."""
    report = common.load_degradation(paths)
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (report.get("checks") or [])
        if c.get("check_id")
    }


def _load_canonical_manifest(paths: Any) -> dict[str, Any]:
    path = Path(paths.canonical) / "manifest.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _table_nonempty(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return pq.ParquetFile(path).metadata.num_rows > 0
    except OSError:
        return False


def _write_unavailable(metrics_dir: Path, check_id: str, reason: str) -> None:
    """Явная запись «проверка недоступна» вместо молчаливого пропуска."""
    common.write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


# ── D01 — переотработка ключевой цели ───────────────────────────────────────
def _run_d01(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))
    inflation_threshold = float(defaults.get("goal_inflation_warning", 1.3))

    con = common.open_duckdb(paths)
    try:
        rows: list[dict[str, Any]] = []
        for group in GOAL_GROUPS:
            count_col = f"{group}_count"
            visits_with_goal, achievements = con.execute(
                f'SELECT SUM(CASE WHEN "{group}" THEN 1 ELSE 0 END), '
                f'SUM("{count_col}") FROM visits'
            ).fetchone()
            visits_with_goal = int(visits_with_goal or 0)
            achievements = int(achievements or 0)
            ratio = achievements / visits_with_goal if visits_with_goal > 0 else None
            overtrigger = ratio is not None and ratio >= inflation_threshold
            confidence = (
                _sample_confidence(visits_with_goal, min_sample)
                if visits_with_goal > 0
                else "LOW"
            )
            rows.append({
                "check_id": "D01",
                "goal_group": group,
                "achievements": achievements,
                "visits_with_goal": visits_with_goal,
                "achievements_per_visit": round(ratio, 3) if ratio is not None else None,
                "goal_inflation_warning": inflation_threshold,
                "overtrigger": overtrigger,
                "confidence": _cap(confidence, confidence_cap),
            })
    finally:
        con.close()

    common.write_metric_artifact(metrics_dir, "d01", rows, confidence_cap=confidence_cap)


# ── D02 — цель = клик/открытие, а не отправка ───────────────────────────────
def _run_d02(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        goals = con.execute("SELECT goal_id, name, type FROM goals").fetchall()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for goal_id, name, gtype in goals:
        gtype_norm = (gtype or "").strip().lower()
        name_norm = (name or "").strip().lower()
        is_weak_type = gtype_norm in _WEAK_GOAL_TYPES
        name_suggests_submission = any(kw in name_norm for kw in _SUBMIT_NAME_KEYWORDS)
        suspect = is_weak_type and name_suggests_submission
        rows.append({
            "check_id": "D02",
            "goal_id": goal_id,
            "goal_name": name,
            "goal_type": gtype,
            "is_weak_type": is_weak_type,
            "name_suggests_submission": name_suggests_submission,
            "suspect_click_not_submit": suspect,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "d02", rows, confidence_cap=confidence_cap)


# ── D03 — смешаны бизнес-цели и микроконверсии ──────────────────────────────
def _run_d03(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    config = orchestrator_mod.load_client_config(paths)
    goals_cfg = config.get("goals") or {}
    groups: dict[str, set[str]] = {
        "form_open": {str(g) for g in goals_cfg.get("form_open_goal_ids") or []},
        "form_submit": {str(g) for g in goals_cfg.get("form_submit_goal_ids") or []},
        "call_click": {str(g) for g in goals_cfg.get("call_click_goal_ids") or []},
        "messenger": {str(g) for g in goals_cfg.get("messenger_goal_ids") or []},
    }

    con = common.open_duckdb(paths)
    try:
        all_goal_ids = {str(r[0]) for r in con.execute("SELECT goal_id FROM goals").fetchall()}
    finally:
        con.close()

    manifest_flags = (_load_canonical_manifest(paths).get("flags") or {})
    goals_qa = manifest_flags.get("goals_qa") or {}

    names = list(groups)
    rows: list[dict[str, Any]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            group_a, group_b = names[i], names[j]
            overlap = sorted(groups[group_a] & groups[group_b])
            if overlap:
                rows.append({
                    "check_id": "D03",
                    "finding": "goal_group_overlap",
                    "group_a": group_a,
                    "group_b": group_b,
                    "overlapping_goal_ids": ",".join(overlap),
                    "confidence": _cap("MED", confidence_cap),
                })

    categorized = set().union(*groups.values()) if groups else set()
    uncategorized = sorted(all_goal_ids - categorized)
    rows.append({
        "check_id": "D03",
        "finding": "goal_mix_summary",
        "has_overlap": any(r["finding"] == "goal_group_overlap" for r in rows),
        "uncategorized_goal_ids": ",".join(uncategorized),
        "has_uncategorized": bool(uncategorized),
        "goals_qa_mismatch": bool(goals_qa.get("mismatch", False)),
        "goals_missing_in_visits": ",".join(goals_qa.get("missing_in_visits") or []),
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "d03", rows, confidence_cap=confidence_cap)


# ── D04 — покрытие трекингом по устройствам ─────────────────────────────────
def _run_d04(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        result = con.execute(
            'SELECT device, COUNT(*), '
            'SUM(CASE WHEN form_open OR form_submit OR call_click OR messenger_click '
            'THEN 1 ELSE 0 END) '
            "FROM visits GROUP BY device"
        ).fetchall()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for device, total_visits, visits_with_goal in result:
        total_visits = int(total_visits or 0)
        visits_with_goal = int(visits_with_goal or 0)
        goal_rate = visits_with_goal / total_visits if total_visits > 0 else None
        no_tracked_conversions = total_visits > 0 and visits_with_goal == 0
        rows.append({
            "check_id": "D04",
            "device": device,
            "total_visits": total_visits,
            "visits_with_any_goal": visits_with_goal,
            "goal_rate": round(goal_rate, 4) if goal_rate is not None else None,
            "no_tracked_conversions": no_tracked_conversions,
            "confidence": _cap(_sample_confidence(total_visits, min_sample), confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "d04", rows, confidence_cap=confidence_cap)


# ── D05 — UTM/click ID/источник теряются или перезаписываются ──────────────
def _run_d05(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    threshold = float(defaults.get("utm_undefined_threshold", 0.25))
    min_sample = int(defaults.get("min_sample_visits", 500))
    utm_uncertain_flag = bool(
        (_load_canonical_manifest(paths).get("flags") or {}).get("utm_uncertain", False)
    )

    tokens_sql = ", ".join("'" + t.replace("'", "''") + "'" for t in _UTM_UNDEFINED_TOKENS)
    con = common.open_duckdb(paths)
    try:
        ad_visits, ad_undefined = con.execute(
            "SELECT COUNT(*) FILTER (WHERE source_group = 'ad'), "
            "COUNT(*) FILTER (WHERE source_group = 'ad' "
            f"AND lower(trim(coalesce(utm_source_raw, ''))) IN ({tokens_sql})) "
            "FROM visits"
        ).fetchone()
    finally:
        con.close()

    ad_visits = int(ad_visits or 0)
    ad_undefined = int(ad_undefined or 0)
    frac_undefined = ad_undefined / ad_visits if ad_visits > 0 else None
    threshold_exceeded = frac_undefined is not None and frac_undefined >= threshold
    confidence = _sample_confidence(ad_visits, min_sample) if ad_visits > 0 else "LOW"

    row = {
        "check_id": "D05",
        "ad_visits": ad_visits,
        "ad_visits_undefined_utm": ad_undefined,
        "frac_undefined_utm": round(frac_undefined, 4) if frac_undefined is not None else None,
        "utm_undefined_threshold": threshold,
        "threshold_exceeded": threshold_exceeded,
        "utm_uncertain": utm_uncertain_flag,
        "confidence": _cap(confidence, confidence_cap),
    }
    common.write_metric_artifact(metrics_dir, "d05", [row], confidence_cap=confidence_cap)


# ── D06 — расходы на разной базе НДС ────────────────────────────────────────
def _run_d06(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    vat_entries = (client_answers.get("finance") or {}).get("vat_basis_by_source") or []

    declared: dict[str, dict[str, Any]] = {}
    for entry in vat_entries:
        src = str(entry.get("source") or "").strip()
        if not src:
            continue
        vat_included = entry.get("vat_included")
        if vat_included is True:
            expected_status = "gross"
        elif vat_included is False:
            expected_status = "net"
        else:
            expected_status = "vat_basis_unknown"
        declared[src] = {
            "expected_status": expected_status,
            "has_evidence": bool(entry.get("evidence")),
        }

    con = common.open_duckdb(paths)
    try:
        result = con.execute(
            "SELECT source_tag, cost_status, COUNT(*) FROM costs "
            "GROUP BY source_tag, cost_status"
        ).fetchall()
    finally:
        con.close()

    status_by_source: dict[str, set[str]] = {}
    for source_tag, cost_status, _cnt in result:
        status_by_source.setdefault(source_tag, set()).add(cost_status)

    rows: list[dict[str, Any]] = []
    known_statuses: set[str] = set()
    for source_tag in sorted(status_by_source):
        statuses = status_by_source[source_tag]
        actual_status = next(iter(statuses)) if len(statuses) == 1 else "mixed_within_source"
        if actual_status in ("gross", "net"):
            known_statuses.add(actual_status)
        decl = declared.get(source_tag)
        expected_status = decl["expected_status"] if decl else "vat_basis_unknown"
        has_evidence = decl["has_evidence"] if decl else False
        answer_not_applied = decl is not None and expected_status != actual_status
        rows.append({
            "check_id": "D06",
            "source_tag": source_tag,
            "actual_cost_status": actual_status,
            "expected_cost_status": expected_status,
            "has_client_evidence": has_evidence,
            "answer_not_applied": answer_not_applied,
            "confidence": _cap("MED", confidence_cap),
        })

    mixed_basis_across_sources = len(known_statuses) > 1
    for row in rows:
        row["mixed_basis_across_sources"] = mixed_basis_across_sources

    common.write_metric_artifact(metrics_dir, "d06", rows, confidence_cap=confidence_cap)


# ── D07 — расходы неполные или задвоены ─────────────────────────────────────
def _months_in_range(date_from: Any, date_to: Any) -> int:
    """Число календарных месяцев, покрытых [date_from, date_to] (включительно)."""
    if date_from is None or date_to is None:
        return 0
    return (date_to.year - date_from.year) * 12 + (date_to.month - date_from.month) + 1


def _run_d07(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Q02 (hidden_costs_rub_month) против costs.parquet + правило §4.8 каталога.

    Два независимых сигнала: (1) заявленная клиентом статья расхода отсутствует
    или занижена в costs.parquet (сверка Q02 с фактом, тот же паттерн, что D06
    для vat_basis_by_source); (2) source_tag='yandex_business' и 'direct' оба
    ненулевые одновременно — кандидат двойного счёта одного бюджета (каталог
    §4, правило 8). Оба сигнала — MED: требуют подтверждения аналитиком, не
    автоматический факт (нет прямого счёта на сверку в canonical-слое).
    """
    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    hidden_costs = (client_answers.get("finance") or {}).get("hidden_costs_rub_month") or []

    con = common.open_duckdb(paths)
    try:
        by_source = dict(
            con.execute("SELECT source_tag, SUM(cost_raw) FROM costs GROUP BY source_tag").fetchall()
        )
        date_min, date_max = con.execute("SELECT MIN(date), MAX(date) FROM costs").fetchone()
        direct_total, yb_total = con.execute(
            "SELECT SUM(cost_raw) FILTER (WHERE source_tag = 'direct'), "
            "SUM(cost_raw) FILTER (WHERE source_tag = 'yandex_business') FROM costs"
        ).fetchone()
    finally:
        con.close()

    months = _months_in_range(date_min, date_max)
    rows: list[dict[str, Any]] = []

    for entry in hidden_costs:
        source_tag = str(entry.get("source_tag") or "").strip()
        rub_month = float(entry.get("rub_month") or 0)
        actual_total = float(by_source.get(source_tag) or 0.0)
        expected_total = rub_month * months if months > 0 else None
        missing_in_data = actual_total <= 0.0
        amount_mismatch = (
            not missing_in_data
            and expected_total is not None
            and expected_total > 0
            and actual_total < 0.5 * expected_total
        )
        rows.append({
            "check_id": "D07",
            "finding": "declared_cost_check",
            "name": entry.get("name"),
            "source_tag": source_tag or None,
            "declared_rub_month": rub_month,
            "months_in_window": months,
            "expected_total_rub": round(expected_total, 2) if expected_total is not None else None,
            "actual_total_rub": round(actual_total, 2),
            "missing_in_data": missing_in_data,
            "amount_mismatch": amount_mismatch,
            "confidence": _cap("MED", confidence_cap),
        })

    direct_total = float(direct_total or 0.0)
    yb_total = float(yb_total or 0.0)
    rows.append({
        "check_id": "D07",
        "finding": "possible_double_counted_budget",
        "direct_total_rub": round(direct_total, 2),
        "yandex_business_total_rub": round(yb_total, 2),
        "both_present": direct_total > 0 and yb_total > 0,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "d07", rows, confidence_cap=confidence_cap)


# ── D08 — архивные/остановленные кампании исключены из истории ─────────────
def _run_d08(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Сигнал из costs.parquet: есть ли среди кампаний Директа хоть одна,

    переставшая тратить задолго до конца окна (доказательство того, что
    остановленные кампании ПОПАЛИ в выгрузку, а не исключены по текущему
    статусу). Compute не имеет доступа к campaigns.get/archived_campaigns_
    retrievable (сырой manifest Директа, не canonical) — см. docstring модуля.
    Отсутствие ни одной такой кампании при наличии >=2 кампаний — не
    доказательство проблемы, но и не молчаливо пропускается: finding
    no_stopped_campaigns_detected фиксирует ограничение проверки явно.
    """
    con = common.open_duckdb(paths)
    try:
        campaigns = con.execute(
            "SELECT campaign_id, MAX(campaign_name), "
            "MIN(date) FILTER (WHERE cost_raw > 0), "
            "MAX(date) FILTER (WHERE cost_raw > 0), "
            "SUM(cost_raw) "
            "FROM costs WHERE source_tag = 'direct' AND campaign_id IS NOT NULL "
            "GROUP BY campaign_id"
        ).fetchall()
        period_end = con.execute(
            "SELECT MAX(date) FROM costs WHERE source_tag = 'direct'"
        ).fetchone()[0]
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    stopped_count = 0
    for campaign_id, campaign_name, first_active, last_active, total_cost in campaigns:
        stopped_before_end = (
            period_end is not None
            and last_active is not None
            and (period_end - last_active).days > _D08_STOPPED_CAMPAIGN_BUFFER_DAYS
        )
        if stopped_before_end:
            stopped_count += 1
        rows.append({
            "check_id": "D08",
            "finding": "campaign_activity",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "first_active_date": first_active.isoformat() if first_active else None,
            "last_active_date": last_active.isoformat() if last_active else None,
            "total_cost_rub": round(float(total_cost or 0.0), 2),
            "stopped_before_window_end": stopped_before_end,
            "confidence": _cap("HIGH", confidence_cap),
        })

    total_campaigns = len(campaigns)
    no_stopped_campaigns_detected = total_campaigns >= 2 and stopped_count == 0
    rows.append({
        "check_id": "D08",
        "finding": "summary",
        "total_campaigns": total_campaigns,
        "stopped_campaign_count": stopped_count,
        "period_end": period_end.isoformat() if period_end else None,
        "no_stopped_campaigns_detected": no_stopped_campaigns_detected,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "d08", rows, confidence_cap=confidence_cap)


# ── D09 — периоды/часовые пояса/даты не приведены к единому правилу ────────
def _run_d09(paths: Any, confidence_cap: str, metrics_dir: Path, has_costs: bool) -> None:
    """Неполный текущий месяц (каталог §4.1) + рассинхрон периодов visits/costs.

    Часовой пояс не проверяется: canonical visits.dt не несёт явного tz-поля
    (нормализация — забота extract/transform, вне allowed_files этой задачи),
    придумывать проверку без данных нельзя (CLAUDE.md, протокол микрозадач п.5).
    """
    con = common.open_duckdb(paths)
    try:
        visits_min, visits_max = con.execute("SELECT MIN(date), MAX(date) FROM visits").fetchone()
        costs_min = costs_max = None
        if has_costs:
            costs_min, costs_max = con.execute("SELECT MIN(date), MAX(date) FROM costs").fetchone()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []

    if visits_max is not None:
        days_in_month = calendar.monthrange(visits_max.year, visits_max.month)[1]
        is_incomplete = visits_max.day < days_in_month
        rows.append({
            "check_id": "D09",
            "finding": "incomplete_last_month",
            "last_month": visits_max.strftime("%Y-%m"),
            "last_date": visits_max.isoformat(),
            "days_in_month": days_in_month,
            "days_elapsed": visits_max.day,
            "is_incomplete": is_incomplete,
            "confidence": _cap("HIGH", confidence_cap),
        })

    if has_costs and costs_min is not None and visits_min is not None:
        mismatch = (
            visits_min.strftime("%Y-%m") != costs_min.strftime("%Y-%m")
            or visits_max.strftime("%Y-%m") != costs_max.strftime("%Y-%m")
        )
        rows.append({
            "check_id": "D09",
            "finding": "visits_costs_period_mismatch",
            "visits_date_from": visits_min.isoformat(),
            "visits_date_to": visits_max.isoformat(),
            "costs_date_from": costs_min.isoformat(),
            "costs_date_to": costs_max.isoformat() if costs_max else None,
            "mismatch": mismatch,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "d09", rows, confidence_cap=confidence_cap)


# ── D10 — выгрузка неполная (пагинация/лимиты/фильтры/семплирование) ───────
def _run_d10(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    """Календарные дни без единого визита внутри [date_min, date_max] visits.

    Без сверки с UI-агрегатом Метрики (не в canonical-слое) — только
    внутренняя полнота построчных данных, как явно допускает каталог
    («даты без данных» — один из перечисленных признаков, не единственный).
    """
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        total_visits, date_min, date_max = con.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM visits"
        ).fetchone()
        present_dates = {r[0] for r in con.execute("SELECT DISTINCT date FROM visits").fetchall()}
    finally:
        con.close()

    total_visits = int(total_visits or 0)

    if date_min is None or date_max is None:
        rows = [{
            "check_id": "D10",
            "date_from": None,
            "date_to": None,
            "days_in_range": 0,
            "days_with_visits": 0,
            "missing_days_count": 0,
            "missing_dates_sample": [],
            "missing_dates_truncated": False,
            "has_gap": False,
            "confidence": _cap("LOW", confidence_cap),
        }]
        common.write_metric_artifact(metrics_dir, "d10", rows, confidence_cap=confidence_cap)
        return

    days_in_range = (date_max - date_min).days + 1
    all_dates = {date_min + timedelta(days=i) for i in range(days_in_range)}
    missing_dates = sorted(all_dates - present_dates)

    rows = [{
        "check_id": "D10",
        "date_from": date_min.isoformat(),
        "date_to": date_max.isoformat(),
        "days_in_range": days_in_range,
        "days_with_visits": len(present_dates),
        "missing_days_count": len(missing_dates),
        "missing_dates_sample": [d.isoformat() for d in missing_dates[:_D10_MISSING_DATES_SAMPLE_LIMIT]],
        "missing_dates_truncated": len(missing_dates) > _D10_MISSING_DATES_SAMPLE_LIMIT,
        "has_gap": len(missing_dates) > 0,
        "confidence": _cap(_sample_confidence(total_visits, min_sample), confidence_cap),
    }]
    common.write_metric_artifact(metrics_dir, "d10", rows, confidence_cap=confidence_cap)


# ── D11 — сотрудники/тесты/боты в данных ────────────────────────────────────
def _run_d11(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Прокси без isRobot: частота визитов на clientID + тестовые UTM-метки.

    confidence всегда LOW по существу находки (не только из-за потолка) —
    без ym:s:isRobot это гипотеза, а не подтверждённый факт (CLAUDE.md,
    «Уверенность находок»: LOW — гипотеза). _cap оставлен для единообразия с
    остальными проверками блока и на случай ещё более низкого потолка.
    """
    con = common.open_duckdb(paths)
    try:
        total_visits, distinct_clients = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT client_id) FROM visits"
        ).fetchone()
        by_client = con.execute(
            "SELECT client_id, COUNT(*) AS c FROM visits GROUP BY client_id ORDER BY c DESC"
        ).fetchall()
        marker_filter = " OR ".join(
            "lower(coalesce(utm_source_raw, '')) LIKE '%' || ? || '%'"
            for _ in _D11_TEST_MARKER_TOKENS
        )
        test_marker_visits = con.execute(
            f"SELECT COUNT(*) FROM visits WHERE {marker_filter}",
            list(_D11_TEST_MARKER_TOKENS),
        ).fetchone()[0]
    finally:
        con.close()

    total_visits = int(total_visits or 0)
    high_freq = [(cid, int(cnt)) for cid, cnt in by_client if cnt >= _D11_HIGH_FREQUENCY_VISITS_THRESHOLD]
    high_freq_visits_total = sum(cnt for _, cnt in high_freq)

    rows: list[dict[str, Any]] = []
    for client_id, visit_count in high_freq[:_D11_TOP_CLIENT_IDS_LIMIT]:
        rows.append({
            "check_id": "D11",
            "finding": "high_frequency_client_id",
            "client_id": client_id,
            "visit_count": visit_count,
            "share_of_total_visits": round(visit_count / total_visits, 4) if total_visits else None,
            "confidence": _cap("LOW", confidence_cap),
        })

    rows.append({
        "check_id": "D11",
        "finding": "summary",
        "total_visits": total_visits,
        "distinct_client_ids": int(distinct_clients or 0),
        "high_frequency_client_id_count": len(high_freq),
        "high_frequency_visits_total": high_freq_visits_total,
        "high_frequency_visits_share": (
            round(high_freq_visits_total / total_visits, 4) if total_visits else None
        ),
        "test_marker_visit_count": int(test_marker_visits or 0),
        "is_robot_available": False,
        "confidence": _cap("LOW", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "d11", rows, confidence_cap=confidence_cap)


# ── D12 — таблицы соединяются на неверном уровне детализации ───────────────
def _run_d12(paths: Any, confidence_cap: str, metrics_dir: Path, has_costs: bool) -> None:
    """Уникальность ключей внутри canonical-таблиц самого блока 0.

    visits.visit_id уже дедуплицирован в transform (dedupe_visits) — здесь
    независимая защитная проверка, не переиспользующая внутренности другого
    слоя (CLAUDE.md, принцип 2: слои читают только выход предыдущего слоя).
    costs — составной ключ (date, source_tag, campaign_id): дубль = вероятный
    фан-аут join выше по конвейеру (каталог: «JOIN один-ко-многим размножает
    расходы»), проверяем на самой таблице, доступной этому блоку.
    """
    con = common.open_duckdb(paths)
    try:
        visits_total, visits_distinct = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT visit_id) FROM visits"
        ).fetchone()
        costs_total = costs_distinct = None
        if has_costs:
            costs_total = con.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
            costs_distinct = con.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT date, source_tag, campaign_id FROM costs) t"
            ).fetchone()[0]
    finally:
        con.close()

    visits_total = int(visits_total or 0)
    visits_distinct = int(visits_distinct or 0)
    rows: list[dict[str, Any]] = [{
        "check_id": "D12",
        "table": "visits",
        "key": "visit_id",
        "total_rows": visits_total,
        "distinct_key_count": visits_distinct,
        "duplicate_key_count": visits_total - visits_distinct,
        "has_duplicate_keys": visits_total != visits_distinct,
        "confidence": _cap("HIGH", confidence_cap),
    }]

    if has_costs:
        costs_total = int(costs_total or 0)
        costs_distinct = int(costs_distinct or 0)
        rows.append({
            "check_id": "D12",
            "table": "costs",
            "key": "date+source_tag+campaign_id",
            "total_rows": costs_total,
            "distinct_key_count": costs_distinct,
            "duplicate_key_count": costs_total - costs_distinct,
            "has_duplicate_keys": costs_total != costs_distinct,
            "confidence": _cap("HIGH", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "d12", rows, confidence_cap=confidence_cap)


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить D01–D06 из числа доступных; вернуть имена записанных артефактов."""
    canonical = common.load_canonical(paths)
    caps = _confidence_caps(paths)
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[str] = []

    if "D01" in runnable_ids and "visits" in canonical:
        _run_d01(paths, defaults, caps.get("D01", "HIGH"), metrics_dir)
        artifacts.append("d01")

    # D02/D03: не гейтятся через runnable_ids — см. докстринг модуля.
    visits_available = "visits" in canonical
    goals_available = _table_nonempty(canonical.get("goals"))
    if visits_available and goals_available:
        _run_d02(paths, caps.get("D02", "HIGH"), metrics_dir)
        artifacts.append("d02")
        _run_d03(paths, caps.get("D03", "HIGH"), metrics_dir)
        artifacts.append("d03")
    else:
        reason = "goals metadata недоступна" if not goals_available else "визиты недоступны"
        _write_unavailable(metrics_dir, "D02", reason)
        _write_unavailable(metrics_dir, "D03", reason)
        artifacts.extend(["d02", "d03"])

    if "D04" in runnable_ids and "visits" in canonical:
        _run_d04(paths, defaults, caps.get("D04", "HIGH"), metrics_dir)
        artifacts.append("d04")

    if "D05" in runnable_ids and "visits" in canonical:
        _run_d05(paths, defaults, caps.get("D05", "HIGH"), metrics_dir)
        artifacts.append("d05")

    if "D06" in runnable_ids and "costs" in canonical:
        _run_d06(paths, caps.get("D06", "HIGH"), metrics_dir)
        artifacts.append("d06")

    if "D07" in runnable_ids and "costs" in canonical:
        _run_d07(paths, caps.get("D07", "HIGH"), metrics_dir)
        artifacts.append("d07")

    if "D08" in runnable_ids and "costs" in canonical:
        _run_d08(paths, caps.get("D08", "HIGH"), metrics_dir)
        artifacts.append("d08")

    if "D09" in runnable_ids and "visits" in canonical:
        _run_d09(paths, caps.get("D09", "HIGH"), metrics_dir, "costs" in canonical)
        artifacts.append("d09")

    if "D10" in runnable_ids and "visits" in canonical:
        _run_d10(paths, defaults, caps.get("D10", "HIGH"), metrics_dir)
        artifacts.append("d10")

    if "D11" in runnable_ids and "visits" in canonical:
        _run_d11(paths, caps.get("D11", "HIGH"), metrics_dir)
        artifacts.append("d11")

    if "D12" in runnable_ids and "visits" in canonical:
        _run_d12(paths, caps.get("D12", "HIGH"), metrics_dir, "costs" in canonical)
        artifacts.append("d12")

    return artifacts
