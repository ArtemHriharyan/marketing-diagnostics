"""Блок 0 — минимальное доверие к данным (D01–D06, задача 5B).

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §5):
    D01  переотработка ключевой цели внутри визита         [visits]
    D02  цель = клик/открытие, а не подтверждённая отправка [visits, goals]
    D03  смешаны бизнес-цели и микроконверсии               [visits, goals]
    D04  часть форм/страниц/устройств не покрыта трекингом  [visits]
    D05  UTM/click ID/источник теряются или перезаписываются[visits]
    D06  расходы посчитаны на разной базе НДС               [costs, client_answers]

D07–D12 — вне скоупа этой задачи (см. следующие микрозадачи блока 0).

Контракт:
    Читает   — data/canonical/{visits,goals,costs}.parquet,
               data/canonical/manifest.json (flags.utm_uncertain — переносится
               в вывод D05 по прямому указанию докстринга build_canonical.py),
               inputs/client_answers.yaml (D06), config.yaml клиента (goals.*
               группы для D03), пороги defaults (min_sample_visits,
               goal_inflation_warning, utm_undefined_threshold),
               data/metrics/degradation_report.json (confidence_cap на проверку).
    Пишет    — data/metrics/{d01..d06}.csv/.json. БЕЗ LLM.

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

import json
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

    return artifacts
