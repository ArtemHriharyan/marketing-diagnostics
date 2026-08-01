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

D02/D03 запускаются только по ``runnable_ids`` из degradation. Если проверка
штатно runnable, но goals.parquet отсутствует или пуст, пишется явный результат
со статусом "unavailable", а не молчаливый пропуск.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
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

# D08: последний положительный расход — только supporting evidence: он не
# устанавливает API-статус кампании и не создаёт finding самостоятельно.
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


def _parse_declared_costs(value: Any, field_name: str) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Проверить контракт Q02 и вернуть нормализованные записи либо ошибки.

    Неизвестный формат не подменяется нулевым расходом: при любой ошибке поля
    возвращается ``None``, а D07 записывает отдельную диагностическую строку.
    """
    if not isinstance(value, list):
        return None, [{
            "field": field_name,
            "entry_index": None,
            "reason": "ожидался список записей",
        }]

    entries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            issues.append({
                "field": field_name,
                "entry_index": index,
                "reason": "запись должна быть объектом",
            })
            continue

        name = entry.get("name")
        source_tag = entry.get("source_tag")
        rub_month = entry.get("rub_month")
        if not isinstance(name, str) or not name.strip():
            issues.append({
                "field": field_name,
                "entry_index": index,
                "reason": "name должен быть непустой строкой",
            })
        if not isinstance(source_tag, str) or not source_tag.strip():
            issues.append({
                "field": field_name,
                "entry_index": index,
                "reason": "source_tag должен быть непустой строкой",
            })
        if (
            isinstance(rub_month, bool)
            or not isinstance(rub_month, (int, float))
            or not math.isfinite(float(rub_month))
            or rub_month < 0
        ):
            issues.append({
                "field": field_name,
                "entry_index": index,
                "reason": "rub_month должен быть конечным неотрицательным числом",
            })

        if not any(issue["entry_index"] == index for issue in issues):
            entries.append({
                "name": name.strip(),
                "source_tag": source_tag.strip(),
                "rub_month": float(rub_month),
            })

    return (None, issues) if issues else (entries, [])


def _declared_costs_signature(entries: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    """Сравнить два Q02-поля как набор статей, не зависящий от порядка YAML."""
    return sorted(
        (entry["name"], entry["source_tag"], entry["rub_month"])
        for entry in entries
    )


def _run_d07(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Q02 против costs.parquet + правило §4.8 каталога.

    Два независимых сигнала: (1) заявленная клиентом статья расхода отсутствует
    или занижена в costs.parquet (сверка Q02 с фактом, тот же паттерн, что D06
    для vat_basis_by_source); (2) source_tag='yandex_business' и 'direct' оба
    ненулевые одновременно — кандидат двойного счёта одного бюджета (каталог
    §4, правило 8). Оба сигнала — MED: требуют подтверждения аналитиком, не
    автоматический факт (нет прямого счёта на сверку в canonical-слое).
    """
    inputs = common.load_inputs(paths)
    client_answers = inputs.get("client_answers") or {}
    finance = client_answers.get("finance") or {}
    rows: list[dict[str, Any]] = []

    if not isinstance(finance, dict):
        canonical_costs = legacy_costs = None
        input_issues = [{
            "field": "finance",
            "entry_index": None,
            "reason": "ожидался объект с ответами Q02",
        }]
    else:
        canonical_present = "hidden_costs_rub_month" in finance
        legacy_present = "costs_outside_cabinet" in finance
        canonical_costs, canonical_issues = (
            _parse_declared_costs(finance["hidden_costs_rub_month"], "hidden_costs_rub_month")
            if canonical_present else (None, [])
        )
        legacy_costs, legacy_issues = (
            _parse_declared_costs(finance["costs_outside_cabinet"], "costs_outside_cabinet")
            if legacy_present else (None, [])
        )
        input_issues = canonical_issues + legacy_issues

    for issue in input_issues:
        rows.append({
            "check_id": "D07",
            "finding": "malformed_declared_cost_input",
            **issue,
            "confidence": _cap("MED", confidence_cap),
        })

    declared_costs: list[dict[str, Any]] = []
    declared_cost_field: str | None = None
    if canonical_costs is not None:
        declared_costs = canonical_costs
        declared_cost_field = "hidden_costs_rub_month"
        if legacy_costs is not None and _declared_costs_signature(canonical_costs) != _declared_costs_signature(legacy_costs):
            rows.append({
                "check_id": "D07",
                "finding": "conflicting_declared_cost_inputs",
                "canonical_field": "hidden_costs_rub_month",
                "legacy_field": "costs_outside_cabinet",
                "canonical_entry_count": len(canonical_costs),
                "legacy_entry_count": len(legacy_costs),
                "using_field": declared_cost_field,
                "confidence": _cap("MED", confidence_cap),
            })
    elif legacy_costs is not None:
        declared_costs = legacy_costs
        declared_cost_field = "costs_outside_cabinet"

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

    for entry in declared_costs:
        source_tag = entry["source_tag"]
        rub_month = entry["rub_month"]
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
            "declared_cost_field": declared_cost_field,
            "name": entry["name"],
            "source_tag": source_tag,
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
    """D08 по подтверждённому API-снимку campaign_status и historical costs.

    Только non-active ``State`` вместе с положительным историческим расходом
    образует проблему. Отсутствующий/неизвестный статус — coverage gap, а
    last-positive-date остаётся контекстом, а не доказательством остановки.
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
        statuses = con.execute(
            "SELECT campaign_id, state, status, status_payment, "
            "status_clarification, observed_at, source, requested_states "
            "FROM campaign_status WHERE campaign_id IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    status_by_campaign: dict[str, tuple[Any, ...]] = {}
    duplicate_status_ids: set[str] = set()
    for status_row in statuses:
        campaign_id = str(status_row[0])
        if campaign_id in status_by_campaign:
            duplicate_status_ids.add(campaign_id)
            continue
        status_by_campaign[campaign_id] = status_row[1:]

    rows: list[dict[str, Any]] = []
    confirmed_count = 0
    coverage_gap_count = 0
    problem_count = 0
    for campaign_id, campaign_name, first_active, last_active, total_cost in campaigns:
        status_row = status_by_campaign.get(str(campaign_id))
        has_provenance = bool(
            status_row
            and status_row[0]
            and status_row[4]
            and status_row[5]
            and status_row[6]
            and str(campaign_id) not in duplicate_status_ids
        )
        state = str(status_row[0]).strip().upper() if has_provenance else None
        coverage_gap = state in (None, "", "UNKNOWN")
        if coverage_gap:
            coverage_gap_count += 1
        else:
            confirmed_count += 1
        has_historical_spend = last_active is not None
        non_active = state is not None and state != "ON"
        has_problem = bool(non_active and has_historical_spend)
        if has_problem:
            problem_count += 1
        last_positive_over_14_days = (
            period_end is not None
            and last_active is not None
            and (period_end - last_active).days > _D08_STOPPED_CAMPAIGN_BUFFER_DAYS
        )
        row_confidence = _cap("MED" if coverage_gap_count else "HIGH", confidence_cap)
        rows.append({
            "check_id": "D08",
            "finding": "campaign_status_evidence",
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "state": state,
            "api_status": status_row[1] if status_row and has_provenance else None,
            "status_payment": status_row[2] if status_row and has_provenance else None,
            "status_clarification": status_row[3] if status_row and has_provenance else None,
            "observed_at": status_row[4] if status_row and has_provenance else None,
            "status_source": status_row[5] if status_row and has_provenance else None,
            "requested_states": status_row[6] if status_row and has_provenance else None,
            "coverage_gap": coverage_gap,
            "coverage_gap_reason": (
                "status_not_returned" if status_row is None else
                "status_snapshot_invalid" if not has_provenance else
                "state_unknown"
            ) if coverage_gap else None,
            "first_active_date": first_active.isoformat() if first_active else None,
            "last_active_date": last_active.isoformat() if last_active else None,
            "total_cost_rub": round(float(total_cost or 0.0), 2),
            "has_historical_spend": has_historical_spend,
            "last_positive_over_14_days": last_positive_over_14_days,
            "has_problem": has_problem,
            "status": "unverifiable" if coverage_gap else ("problem" if has_problem else "pass"),
            "confidence": row_confidence,
        })

    if coverage_gap_count:
        for row in rows:
            row["confidence"] = _cap("MED", confidence_cap)

    total_campaigns = len(campaigns)
    coverage_complete = total_campaigns == confirmed_count
    rows.append({
        "check_id": "D08",
        "finding": "summary",
        "total_campaigns": total_campaigns,
        "confirmed_status_count": confirmed_count,
        "coverage_gap_count": coverage_gap_count,
        "coverage_complete": coverage_complete,
        "confirmed_non_active_with_spend_count": problem_count,
        "period_end": period_end.isoformat() if period_end else None,
        "has_problem": problem_count > 0,
        "status": "problem" if problem_count else ("unverifiable" if coverage_gap_count else "pass"),
        "confidence": _cap("HIGH" if coverage_complete else "MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "d08", rows, confidence_cap=confidence_cap)


# ── D09 — периоды/часовые пояса/даты не приведены к единому правилу ────────
def _d09_status(*, problem: bool = False, unknown: bool = False) -> str:
    return "problem" if problem else ("unverifiable" if unknown else "pass")


def _d09_row(
    finding: str,
    *,
    confidence_cap: str,
    problem: bool = False,
    unknown: bool = False,
    reason: str | None = None,
    **evidence: Any,
) -> dict[str, Any]:
    status = _d09_status(problem=problem, unknown=unknown)
    row = {
        "check_id": "D09",
        "finding": finding,
        "status": status,
        "has_problem": problem,
        "confidence": _cap("HIGH" if status != "unverifiable" else "LOW", confidence_cap),
        **evidence,
    }
    if reason is not None:
        row["reason"] = reason
    return row


def _d09_requested_window(contract: Any, label: str) -> tuple[date | None, date | None, str | None]:
    if not isinstance(contract, dict):
        return None, None, f"{label}_raw_temporal_provenance_missing"
    if contract.get("status") == "unknown":
        return None, None, f"{label}_{contract.get('reason') or 'raw_temporal_provenance_unknown'}"
    window = contract.get("requested_window")
    if not isinstance(window, dict):
        return None, None, f"{label}_requested_window_missing"
    try:
        return (
            date.fromisoformat(str(window["date_from"])),
            date.fromisoformat(str(window["date_to"])),
            None,
        )
    except (KeyError, TypeError, ValueError):
        return None, None, f"{label}_requested_window_invalid"


def _d09_raw_field(contract: Any, field_name: str, label: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(contract, dict) or contract.get("status") == "unknown":
        return None, f"{label}_raw_temporal_provenance_missing"
    field = (contract.get("fields") or {}).get(field_name)
    if not isinstance(field, dict):
        return None, f"{label}_{field_name}_contract_missing"
    return field, None


def _d09_known_visit_offset(field: dict[str, Any] | None) -> int | None:
    timezone = (field or {}).get("timezone")
    if not isinstance(timezone, dict) or timezone.get("status") != "known":
        return None
    offset = timezone.get("time_zone_offset")
    return offset if isinstance(offset, int) and not isinstance(offset, bool) else None


def _d09_range_row(
    finding: str,
    observed_min: date | None,
    observed_max: date | None,
    window_from: date | None,
    window_to: date | None,
    window_reason: str | None,
    confidence_cap: str,
) -> dict[str, Any]:
    evidence = {
        "observed_date_from": observed_min.isoformat() if observed_min else None,
        "observed_date_to": observed_max.isoformat() if observed_max else None,
        "requested_date_from": window_from.isoformat() if window_from else None,
        "requested_date_to": window_to.isoformat() if window_to else None,
    }
    if window_reason is not None:
        return _d09_row(finding, confidence_cap=confidence_cap, unknown=True, reason=window_reason, **evidence)
    if observed_min is None or observed_max is None:
        return _d09_row(finding, confidence_cap=confidence_cap, unknown=True,
                        reason=f"{finding}_observed_range_empty", **evidence)
    outside = observed_min < window_from or observed_max > window_to
    return _d09_row(finding, confidence_cap=confidence_cap, problem=outside,
                    reason="canonical_dates_outside_requested_window" if outside else None,
                    **evidence)


def _d09_partial_month_row(
    partial_months: Any, source_name: str, confidence_cap: str,
) -> dict[str, Any]:
    partial = (partial_months or {}).get(source_name) if isinstance(partial_months, dict) else None
    if not isinstance(partial, dict) or partial.get("status") == "unknown":
        return _d09_row(
            f"{source_name}_partial_months", confidence_cap=confidence_cap,
            unknown=True, reason=f"{source_name}_partial_months_missing",
        )
    first = partial.get("first_month")
    last = partial.get("last_month")
    if not isinstance(first, dict) or not isinstance(last, dict):
        return _d09_row(
            f"{source_name}_partial_months", confidence_cap=confidence_cap,
            unknown=True, reason=f"{source_name}_partial_months_invalid",
        )
    return _d09_row(
        f"{source_name}_partial_months", confidence_cap=confidence_cap,
        declared_first_month=first, declared_last_month=last,
        basis=partial.get("basis"),
    )


def _d09_semantics_row(
    finding: str, value: Any, unknown_reason: str, confidence_cap: str,
) -> dict[str, Any]:
    return _d09_row(
        finding, confidence_cap=confidence_cap,
        unknown=not isinstance(value, str) or value == "unknown",
        reason=unknown_reason if not isinstance(value, str) or value == "unknown" else None,
        declared_value=value,
    )


def _run_d09(
    paths: Any,
    confidence_cap: str,
    metrics_dir: Path,
    has_costs: bool,
    has_goal_achievements: bool,
) -> None:
    """Проверить D09 только по temporal_provenance canonical-слоя.

    MIN/MAX таблиц — доказательство только выхода за заявленное окно. Пропуск
    края окна, отдельного дня расходов или declared_partial не доказывает
    проблему и не подменяется нулём.
    """
    manifest_path = Path(paths.canonical) / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        rows = [_d09_row("manifest", confidence_cap=confidence_cap, unknown=True,
                         reason="canonical_temporal_manifest_missing")]
        rows.append(_d09_row("summary", confidence_cap=confidence_cap, unknown=True,
                             reason="canonical_temporal_manifest_missing", subcheck_count=1))
        common.write_metric_artifact(metrics_dir, "d09", rows, confidence_cap=confidence_cap)
        return
    except (OSError, json.JSONDecodeError):
        rows = [_d09_row("manifest", confidence_cap=confidence_cap, unknown=True,
                         reason="canonical_temporal_manifest_unreadable")]
        rows.append(_d09_row("summary", confidence_cap=confidence_cap, unknown=True,
                             reason="canonical_temporal_manifest_unreadable", subcheck_count=1))
        common.write_metric_artifact(metrics_dir, "d09", rows, confidence_cap=confidence_cap)
        return

    temporal = manifest.get("temporal_provenance") if isinstance(manifest, dict) else None
    if not isinstance(temporal, dict):
        rows = [_d09_row("manifest", confidence_cap=confidence_cap, unknown=True,
                         reason="canonical_temporal_provenance_missing")]
        rows.append(_d09_row("summary", confidence_cap=confidence_cap, unknown=True,
                             reason="canonical_temporal_provenance_missing", subcheck_count=1))
        common.write_metric_artifact(metrics_dir, "d09", rows, confidence_cap=confidence_cap)
        return

    raw_sources = temporal.get("raw_sources") or {}
    canonical_fields = temporal.get("canonical_fields") or {}
    partial_months = temporal.get("partial_months") or {}
    visits_contract = raw_sources.get("metrika_logs")
    visits_from, visits_to, visits_window_reason = _d09_requested_window(visits_contract, "visits")
    visits_raw_field, visits_field_reason = _d09_raw_field(
        visits_contract, "ym:s:dateTime", "visits"
    )
    visits_mapping = (canonical_fields.get("visits") or {})
    dt_mapping = visits_mapping.get("dt") if isinstance(visits_mapping, dict) else None
    date_mapping = visits_mapping.get("date") if isinstance(visits_mapping, dict) else None

    visits_problem = False
    visits_reason = visits_window_reason or visits_field_reason
    if visits_reason is None:
        if not isinstance(dt_mapping, dict) or not isinstance(date_mapping, dict):
            visits_reason = "visits_canonical_mapping_missing"
        elif (
            dt_mapping.get("raw_source") != "metrika_logs"
            or dt_mapping.get("raw_field") != "ym:s:dateTime"
            or dt_mapping.get("raw_field_contract") != visits_raw_field
            or dt_mapping.get("timezone_conversion") != "none"
            or dt_mapping.get("local_time_basis") != "counter_local_time"
            or date_mapping.get("derived_from") != "visits.dt"
            or date_mapping.get("operation") != "calendar_date_without_timezone_conversion"
        ):
            visits_problem = True
            visits_reason = "visits_event_date_mapping_conflict"
        elif visits_raw_field.get("event") != "visit" or visits_raw_field.get("data_type") != "datetime":
            visits_problem = True
            visits_reason = "visits_event_date_mapping_conflict"
        elif _d09_known_visit_offset(visits_raw_field) is None:
            visits_reason = "visits_timezone_unknown"

    rows: list[dict[str, Any]] = [_d09_row(
        "visits_contract", confidence_cap=confidence_cap,
        problem=visits_problem, unknown=visits_reason is not None and not visits_problem,
        reason=visits_reason,
    )]

    con = common.open_duckdb(paths)
    try:
        visits_min, visits_max = con.execute("SELECT MIN(date), MAX(date) FROM visits").fetchone()
        costs_min = costs_max = None
        if has_costs:
            costs_min, costs_max = con.execute("SELECT MIN(date), MAX(date) FROM costs").fetchone()
    finally:
        con.close()
    rows.append(_d09_range_row(
        "visits_observed_range", visits_min, visits_max, visits_from, visits_to,
        visits_window_reason, confidence_cap,
    ))
    rows.append(_d09_semantics_row(
        "visits_boundary_semantics",
        (visits_contract.get("requested_window") or {}).get("boundary_semantics")
        if isinstance(visits_contract, dict) else None,
        "visits_boundary_semantics_unknown", confidence_cap,
    ))
    rows.append(_d09_partial_month_row(partial_months, "metrika_logs", confidence_cap))

    visit_offset = _d09_known_visit_offset(visits_raw_field)

    if has_costs:
        costs_contract = raw_sources.get("direct")
        costs_from, costs_to, costs_window_reason = _d09_requested_window(costs_contract, "costs")
        costs_raw_field, costs_field_reason = _d09_raw_field(costs_contract, "Date", "costs")
        costs_mapping = ((canonical_fields.get("costs") or {}).get("date"))
        costs_problem = False
        costs_reason = costs_window_reason or costs_field_reason
        if costs_reason is None:
            if not isinstance(costs_mapping, dict):
                costs_reason = "costs_canonical_mapping_missing"
            elif (
                costs_mapping.get("raw_source") != "direct"
                or costs_mapping.get("raw_field") != "Date"
                or costs_mapping.get("raw_field_contract") != costs_raw_field
                or costs_mapping.get("timezone_conversion") != "none"
            ):
                costs_problem = True
                costs_reason = "costs_event_date_mapping_conflict"
            elif (
                costs_raw_field.get("event") != "direct_statistics_day"
                or costs_raw_field.get("data_type") != "date"
                or costs_raw_field.get("timezone_contract") != "Europe/Moscow"
            ):
                costs_problem = True
                costs_reason = "costs_event_date_mapping_conflict"
            elif visit_offset is None:
                costs_reason = "visits_timezone_unknown"
            elif visit_offset != 180:
                costs_problem = True
                costs_reason = "visits_costs_timezone_incompatible"
        rows.append(_d09_row(
            "costs_contract", confidence_cap=confidence_cap,
            problem=costs_problem, unknown=costs_reason is not None and not costs_problem,
            reason=costs_reason,
        ))
        rows.append(_d09_range_row(
            "costs_observed_range", costs_min, costs_max, costs_from, costs_to,
            costs_window_reason, confidence_cap,
        ))
        rows.append(_d09_semantics_row(
            "costs_boundary_semantics",
            (costs_contract.get("requested_window") or {}).get("boundary_semantics")
            if isinstance(costs_contract, dict) else None,
            "costs_boundary_semantics_unknown", confidence_cap,
        ))
        rows.append(_d09_semantics_row(
            "costs_zero_day_policy",
            costs_contract.get("zero_day_policy") if isinstance(costs_contract, dict) else None,
            "costs_zero_day_policy_unknown", confidence_cap,
        ))
        rows.append(_d09_partial_month_row(partial_months, "direct", confidence_cap))
        if visits_window_reason is None and costs_window_reason is None:
            windows_differ = (visits_from, visits_to) != (costs_from, costs_to)
            rows.append(_d09_row(
                "visits_costs_requested_window", confidence_cap=confidence_cap,
                problem=windows_differ,
                reason="requested_window_mismatch" if windows_differ else None,
                visits_requested_window={"date_from": visits_from.isoformat(), "date_to": visits_to.isoformat()},
                costs_requested_window={"date_from": costs_from.isoformat(), "date_to": costs_to.isoformat()},
            ))
        else:
            rows.append(_d09_row(
                "visits_costs_requested_window", confidence_cap=confidence_cap,
                unknown=True, reason=visits_window_reason or costs_window_reason,
            ))
    else:
        rows.append(_d09_row(
            "costs_contract", confidence_cap=confidence_cap,
            reason="costs_optional_source_absent",
        ))
        rows[-1]["status"] = "not_applicable"

    goal_flags = manifest.get("flags") or {}
    goal_status = goal_flags.get("goal_achievements") if isinstance(goal_flags, dict) else None
    if not isinstance(goal_status, dict):
        rows.append(_d09_row(
            "goal_achievements_contract", confidence_cap=confidence_cap,
            unknown=True, reason="goal_achievements_status_missing",
        ))
    elif (
        goal_status.get("status") in {"degraded", "unavailable"}
        or int(goal_status.get("mismatched_visits") or 0) > 0
        or int(goal_status.get("malformed_goal_datetime") or 0) > 0
    ):
        reason = (
            "goal_achievements_mismatched_visits" if int(goal_status.get("mismatched_visits") or 0) > 0 else
            "goal_achievements_malformed_goal_datetime" if int(goal_status.get("malformed_goal_datetime") or 0) > 0 else
            f"goal_achievements_{goal_status.get('status')}"
        )
        rows.append(_d09_row(
            "goal_achievements_contract", confidence_cap=confidence_cap,
            unknown=True, reason=reason, goal_achievements_status=goal_status.get("status"),
        ))
    elif has_goal_achievements:
        goals_raw_field, goals_field_reason = _d09_raw_field(
            visits_contract, "ym:s:goalsDateTime", "goal_achievements"
        )
        goal_mapping = ((canonical_fields.get("goal_achievements") or {}).get("goal_datetime"))
        goal_problem = False
        goal_reason = goals_field_reason
        if goal_reason is None:
            if not isinstance(goal_mapping, dict):
                goal_reason = "goal_achievements_canonical_mapping_missing"
            elif (
                goal_mapping.get("raw_source") != "metrika_logs"
                or goal_mapping.get("raw_field") != "ym:s:goalsDateTime"
                or goal_mapping.get("raw_field_contract") != goals_raw_field
                or goal_mapping.get("timezone_conversion") != "none"
            ):
                goal_problem = True
                goal_reason = "goal_achievements_event_date_mapping_conflict"
            elif (
                goals_raw_field.get("event") != "goal_achievement"
                or goals_raw_field.get("timezone_contract") != "UTC+03:00"
            ):
                goal_problem = True
                goal_reason = "goal_achievements_event_date_mapping_conflict"
            elif visit_offset is None:
                goal_reason = "visits_timezone_unknown"
            elif visit_offset != 180:
                goal_problem = True
                goal_reason = "visits_goal_timezone_incompatible"
        rows.append(_d09_row(
            "goal_achievements_contract", confidence_cap=confidence_cap,
            problem=goal_problem, unknown=goal_reason is not None and not goal_problem,
            reason=goal_reason,
        ))
    else:
        rows.append(_d09_row(
            "goal_achievements_contract", confidence_cap=confidence_cap,
            reason="goal_achievements_optional_table_absent",
        ))
        rows[-1]["status"] = "not_applicable"

    problem_count = sum(row["status"] == "problem" for row in rows)
    unknown_count = sum(row["status"] == "unverifiable" for row in rows)
    rows.append(_d09_row(
        "summary", confidence_cap=confidence_cap,
        problem=problem_count > 0,
        unknown=problem_count == 0 and unknown_count > 0,
        has_costs=has_costs,
        has_goal_achievements=has_goal_achievements,
        problem_subcheck_count=problem_count,
        unverifiable_subcheck_count=unknown_count,
    ))
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
def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_join_frame(frame: Any, controls: list[str], name: str) -> list[str]:
    """Вернуть отсутствующие обязательные части агрегатов JOIN-record."""
    if not isinstance(frame, dict):
        return [f"{name}_controls_absent"]
    errors: list[str] = []
    for field in ("rows", "distinct_keys"):
        if not _is_non_negative_int(frame.get(field)):
            errors.append(f"{name}_{field}_absent")
    checksums = frame.get("checksums")
    counts = frame.get("non_null_counts")
    if not isinstance(checksums, dict) or not isinstance(counts, dict):
        return errors + [f"{name}_checksums_or_counts_absent"]
    for control in controls:
        if not isinstance(checksums.get(control), (int, float)):
            errors.append(f"{name}_checksum_{control}_absent")
        if not _is_non_negative_int(counts.get(control)):
            errors.append(f"{name}_count_{control}_absent")
    return errors


def _validate_join_record(record: Any) -> tuple[list[str], list[str]]:
    """Вернуть (missing_controls, violations) для одного PASS JOIN-record."""
    if not isinstance(record, dict):
        return ["record_not_object"], []
    missing: list[str] = []
    violations: list[str] = []
    if not isinstance(record.get("join_id"), str) or not record["join_id"]:
        missing.append("join_id_absent")
    tables = record.get("tables")
    if not isinstance(tables, dict) or any(not isinstance(tables.get(k), str) for k in ("left", "right", "output")):
        missing.append("tables_absent")
    keys = record.get("keys")
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key for key in keys):
        missing.append("keys_absent")
    cardinality = record.get("expected_cardinality")
    if cardinality not in {"1:1", "N:1"}:
        missing.append("expected_cardinality_absent")
    controls = record.get("preserved_controls")
    if not isinstance(controls, list) or any(not isinstance(control, str) for control in controls):
        missing.append("preserved_controls_absent")
        controls = []
    missing.extend(_validate_join_frame(record.get("pre"), controls, "pre"))
    missing.extend(_validate_join_frame(record.get("right"), [], "right"))
    missing.extend(_validate_join_frame(record.get("post"), controls, "post"))
    unmatched = record.get("unmatched")
    policy = record.get("unmatched_policy")
    if not isinstance(unmatched, dict) or not all(_is_non_negative_int(unmatched.get(side)) for side in ("left", "right")):
        missing.append("unmatched_absent")
    if not isinstance(policy, dict) or any(policy.get(side) not in {"allowed", "forbidden"} for side in ("left", "right")):
        missing.append("unmatched_policy_absent")
    if not _is_non_negative_int(record.get("matched")):
        missing.append("matched_absent")
    if missing:
        return missing, violations

    pre = record["pre"]
    right = record["right"]
    post = record["post"]
    if post["rows"] > pre["rows"]:
        violations.append("fan_out_rows")
    if cardinality == "1:1":
        for name, frame in (("pre", pre), ("right", right), ("post", post)):
            if frame["rows"] != frame["distinct_keys"]:
                violations.append(f"{name}_key_not_unique")
    elif cardinality == "N:1":
        if right["rows"] != right["distinct_keys"]:
            violations.append("right_key_not_unique")
        if post["rows"] != pre["rows"] or post["distinct_keys"] != pre["distinct_keys"]:
            violations.append("left_grain_changed")
    for control in controls:
        if abs(float(pre["checksums"][control]) - float(post["checksums"][control])) > 0.01:
            violations.append(f"checksum_mismatch_{control}")
        if pre["non_null_counts"][control] != post["non_null_counts"][control]:
            violations.append(f"count_mismatch_{control}")
    if record["matched"] + unmatched["left"] != pre["distinct_keys"]:
        violations.append("left_match_accounting_mismatch")
    if record["matched"] + unmatched["right"] != right["distinct_keys"]:
        violations.append("right_match_accounting_mismatch")
    for side in ("left", "right"):
        if policy[side] == "forbidden" and unmatched[side] > 0:
            violations.append(f"required_match_unmatched_{side}")
    return missing, violations


def _run_d12(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Проверить агрегированные доказательства JOIN из canonical manifest.

    D12 не выводит риск из дублей произвольной canonical-таблицы: без записи
    фактического JOIN это не доказывает fan-out (например, сегментация costs).
    Отсутствующий или неполный контракт даёт explicit unavailable/unverifiable.
    """
    manifest_path = Path(paths.canonical) / "manifest.json"
    if not manifest_path.exists():
        rows = [{"check_id": "D12", "status": "unavailable", "reason": "join_integrity_manifest_absent",
                 "confidence": _cap("LOW", confidence_cap)}]
        common.write_metric_artifact(metrics_dir, "d12", rows, confidence_cap=confidence_cap)
        return
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError):
        rows = [{"check_id": "D12", "status": "unavailable", "reason": "join_integrity_manifest_unreadable",
                 "confidence": _cap("LOW", confidence_cap)}]
        common.write_metric_artifact(metrics_dir, "d12", rows, confidence_cap=confidence_cap)
        return

    records = manifest.get("join_integrity") if isinstance(manifest, dict) else None
    if not isinstance(records, list) or not records:
        rows = [{"check_id": "D12", "status": "unavailable", "reason": "join_integrity_records_absent",
                 "confidence": _cap("LOW", confidence_cap)}]
        common.write_metric_artifact(metrics_dir, "d12", rows, confidence_cap=confidence_cap)
        return

    rows: list[dict[str, Any]] = []
    for index, record in enumerate(sorted(records, key=lambda item: str(item.get("join_id", "")) if isinstance(item, dict) else ""), start=1):
        join_id = record.get("join_id") if isinstance(record, dict) else None
        if isinstance(record, dict) and record.get("status") == "NOT_APPLICABLE":
            rows.append({"check_id": "D12", "join_id": join_id, "status": "not_applicable",
                         "has_problem": False, "confidence": _cap("HIGH", confidence_cap)})
            continue
        if not isinstance(record, dict) or record.get("status") != "PASS":
            rows.append({"check_id": "D12", "join_id": join_id or f"record_{index}", "status": "unverifiable",
                         "reason": "join_integrity_status_invalid", "has_problem": False,
                         "confidence": _cap("LOW", confidence_cap)})
            continue
        missing, violations = _validate_join_record(record)
        if missing:
            rows.append({"check_id": "D12", "join_id": join_id, "status": "unverifiable",
                         "missing_controls": sorted(set(missing)), "has_problem": False,
                         "confidence": _cap("LOW", confidence_cap)})
        else:
            rows.append({"check_id": "D12", "join_id": join_id,
                         "status": "problem" if violations else "pass",
                         "violations": sorted(set(violations)), "has_problem": bool(violations),
                         "confidence": _cap("HIGH", confidence_cap)})
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

    visits_available = "visits" in canonical
    goals_available = _table_nonempty(canonical.get("goals"))
    for check_id, artifact, runner in (
        ("D02", "d02", _run_d02),
        ("D03", "d03", _run_d03),
    ):
        if check_id not in runnable_ids:
            continue
        if visits_available and goals_available:
            runner(paths, caps.get(check_id, "HIGH"), metrics_dir)
        else:
            reason = "goals metadata недоступна" if not goals_available else "визиты недоступны"
            _write_unavailable(metrics_dir, check_id, reason)
        artifacts.append(artifact)

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

    if "D08" in runnable_ids:
        if "costs" in canonical and "campaign_status" in canonical:
            _run_d08(paths, caps.get("D08", "HIGH"), metrics_dir)
        else:
            missing = []
            if "costs" not in canonical:
                missing.append("расходы")
            if "campaign_status" not in canonical:
                missing.append("статусы кампаний Директа")
            _write_unavailable(metrics_dir, "D08", "нет источника: " + "; ".join(missing))
        artifacts.append("d08")

    if "D09" in runnable_ids and "visits" in canonical:
        _run_d09(
            paths, caps.get("D09", "HIGH"), metrics_dir,
            "costs" in canonical, "goal_achievements" in canonical,
        )
        artifacts.append("d09")

    if "D10" in runnable_ids and "visits" in canonical:
        _run_d10(paths, defaults, caps.get("D10", "HIGH"), metrics_dir)
        artifacts.append("d10")

    if "D11" in runnable_ids and "visits" in canonical:
        _run_d11(paths, caps.get("D11", "HIGH"), metrics_dir)
        artifacts.append("d11")

    if "D12" in runnable_ids and "visits" in canonical:
        _run_d12(paths, caps.get("D12", "HIGH"), metrics_dir)
        artifacts.append("d12")

    return artifacts
