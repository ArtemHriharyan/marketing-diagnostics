"""Тесты сверки Logs↔Reports (scripts/verify_metrika.py) на фикстурах.

Без сети: кладём во временный data/raw/ пару csv.gz визитов и goals_by_month.json
как их отдаёт Reports API, и проверяем математику дельт, пороги и код возврата.

Семантика (после исправления): цели сравниваются reaches-против-reaches —
Logs считает СУММАРНЫЕ срабатывания (все вхождения goal_id в ym:s:goalsID, с
дублями), Reports отдаёт reaches напрямую. Переотработка (reaches намного
больше уникальных визитов с целью) — нормальное явление и сама по себе
verdict не портит; она видна только в информационном goal_inflation_preview.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import verify_metrika as vm  # noqa: E402

FORM_SUBMIT_ID = 371497275
CONFIG = {"goals": {"form_submit_goal_ids": [FORM_SUBMIT_ID]}}

# Порядок колонок как в реальной выгрузке Logs API.
HEADER = ["ym:s:visitID", "ym:s:clientID", "ym:s:dateTime",
          "ym:s:lastsignTrafficSource", "ym:s:lastsignUTMSource",
          "ym:s:lastsignUTMMedium", "ym:s:lastsignUTMCampaign",
          "ym:s:lastSignDirectClickOrder", "ym:s:deviceCategory",
          "ym:s:startURL", "ym:s:goalsID", "ym:s:referer",
          "ym:s:isNewUser", "ym:s:pageViews", "ym:s:visitDuration"]


def _row(visit_id, dt, goals_cell):
    cells = ["x"] * len(HEADER)
    cells[0] = visit_id
    cells[2] = dt
    cells[10] = goals_cell
    return "\t".join(cells)


def _write_logs(raw_dir: Path, month_file: str, rows: list[str]):
    logs_dir = raw_dir / "metrika_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    body = "\t".join(HEADER) + "\n" + "\n".join(rows) + "\n"
    with gzip.open(logs_dir / month_file, "wt", encoding="utf-8", newline="") as fh:
        fh.write(body)


def _write_reports(raw_dir: Path, entries: list[dict]):
    rep_dir = raw_dir / "metrika_reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "goals_by_month.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _report_entry(month, visits, goal_reaches, goal_id=FORM_SUBMIT_ID):
    # totals = [visits, reaches(goal_id)]; goal_ids выравнены с хвостом totals.
    return {"month": month, "date1": f"{month}-01", "date2": f"{month}-28",
            "goal_ids": [goal_id],
            "data": {"totals": [float(visits), float(goal_reaches)], "sampled": False}}


@pytest.fixture
def raw_dir(tmp_path):
    return tmp_path / "data" / "raw"


# ── _row_goal_hit_count: считает вхождения с дублями ───────────────────────
def test_row_goal_hit_count_counts_duplicates():
    ids = {FORM_SUBMIT_ID}
    assert vm._row_goal_hit_count(f"[{FORM_SUBMIT_ID}]", ids) == 1
    assert vm._row_goal_hit_count(f"[{FORM_SUBMIT_ID},{FORM_SUBMIT_ID}]", ids) == 2
    assert vm._row_goal_hit_count(f"[{FORM_SUBMIT_ID},{FORM_SUBMIT_ID},{FORM_SUBMIT_ID}]", ids) == 3
    assert vm._row_goal_hit_count("[]", ids) == 0
    assert vm._row_goal_hit_count("", ids) == 0
    assert vm._row_goal_hit_count(f"[{FORM_SUBMIT_ID},999]", ids) == 1
    assert vm._row_goal_hit_count("[999]", ids) == 0


# ── load_logs_monthly: reaches (с дублями) отделены от уникальных визитов ──
def test_load_logs_monthly_separates_reaches_from_unique_visits(raw_dir):
    _write_logs(raw_dir, "visits_2025-04-01_2025-04-30_part000.csv.gz", [
        _row("v1", "2025-04-02 10:00:00", f"[{FORM_SUBMIT_ID}]"),
        _row("v2", "2025-04-03 11:00:00", f"[{FORM_SUBMIT_ID},{FORM_SUBMIT_ID}]"),
        _row("v3", "2025-04-04 12:00:00", "[]"),
    ])
    logs = vm.load_logs_monthly(raw_dir, {FORM_SUBMIT_ID})
    m = logs["2025-04"]
    assert m["visits"] == 3
    assert m["goal_reaches"] == 3   # 1 (v1) + 2 (v2, дубль в массиве)
    assert m["goal_visits"] == 2    # v1, v2 (v3 без достижений)


def test_load_logs_monthly_no_form_submit_ids_configured(raw_dir):
    _write_logs(raw_dir, "visits_2025-04-01_2025-04-30_part000.csv.gz", [
        _row("v1", "2025-04-02 10:00:00", f"[{FORM_SUBMIT_ID}]"),
    ])
    logs = vm.load_logs_monthly(raw_dir, set())
    assert logs["2025-04"] == {"visits": 1, "goal_reaches": 0, "goal_visits": 0}


# ── Основная сверка: reaches-против-reaches ─────────────────────────────────
def test_goal_reaches_match_despite_inflation_no_fail(raw_dir):
    """Reaches Logs==Reports (3==3), хотя уникальных визитов с целью всего 2 —

    переотработка (ratio=1.5) видна только в goal_inflation_preview и НЕ
    портит verdict/exit_code.
    """
    _write_logs(raw_dir, "visits_2025-04-01_2025-04-30_part000.csv.gz", [
        _row("v1", "2025-04-02 10:00:00", f"[{FORM_SUBMIT_ID}]"),
        _row("v2", "2025-04-03 11:00:00", f"[{FORM_SUBMIT_ID},{FORM_SUBMIT_ID}]"),
        _row("v3", "2025-04-04 12:00:00", "[]"),
    ])
    _write_reports(raw_dir, [_report_entry("2025-04", visits=3, goal_reaches=3)])

    report = vm.reconcile(raw_dir, CONFIG)
    row = report["months"][0]
    assert row["visits_logs"] == 3 and row["visits_reports"] == 3
    assert row["visits_status"] == "OK"
    assert row["goal_reaches_logs"] == 3
    assert row["goal_reaches_reports"] == 3
    assert row["goal_reaches_status"] == "OK"
    assert report["verdict"] == "OK"
    assert vm.exit_code(report) == 0

    preview = row["goal_inflation_preview"]
    assert preview["reaches_logs"] == 3
    assert preview["visits_with_goal_logs"] == 2
    assert preview["ratio"] == pytest.approx(1.5)


def test_goal_reaches_real_mismatch_gives_fail(raw_dir):
    """Reaches Logs=2 vs Reports=10 (Δ=80%) -> реальное расхождение -> FAIL."""
    _write_logs(raw_dir, "visits_2025-04-01_2025-04-30_part000.csv.gz", [
        _row("v1", "2025-04-02 10:00:00", f"[{FORM_SUBMIT_ID}]"),
        _row("v2", "2025-04-03 11:00:00", f"[{FORM_SUBMIT_ID}]"),
    ])
    _write_reports(raw_dir, [_report_entry("2025-04", visits=2, goal_reaches=10)])

    report = vm.reconcile(raw_dir, CONFIG)
    row = report["months"][0]
    assert row["visits_status"] == "OK"           # визиты совпали
    assert row["goal_reaches_logs"] == 2
    assert row["goal_reaches_reports"] == 10
    assert row["goal_reaches_status"] == "FAIL"    # |2-10|/10 = 80% > 5%
    assert report["verdict"] == "FAIL"
    assert vm.exit_code(report) == 1


def test_all_ok_gives_zero_exit(raw_dir):
    """Совпадение в пределах 2% по обоим метрикам -> OK и код 0."""
    rows = [_row(f"v{i}", "2025-05-02 10:00:00",
                 f"[{FORM_SUBMIT_ID}]" if i < 50 else "[]") for i in range(100)]
    _write_logs(raw_dir, "visits_2025-05-01_2025-05-31_part000.csv.gz", rows)
    _write_reports(raw_dir, [_report_entry("2025-05", visits=100, goal_reaches=50)])

    report = vm.reconcile(raw_dir, CONFIG)
    assert report["verdict"] == "OK"
    assert vm.exit_code(report) == 0
    row = report["months"][0]
    assert row["goal_reaches_logs"] == 50
    assert row["goal_reaches_reports"] == 50


def test_multi_batch_goal_lookup(raw_dir):
    """form_submit-цель во втором батче месяца находится по индексу в totals."""
    _write_logs(raw_dir, "visits_2025-06-01_2025-06-30_part000.csv.gz", [
        _row("v1", "2025-06-02 10:00:00", f"[{FORM_SUBMIT_ID}]"),
    ])
    other = 111111111
    _write_reports(raw_dir, [
        # батч 1: другая цель, визиты те же
        {"month": "2025-06", "date1": "2025-06-01", "date2": "2025-06-30",
         "goal_ids": [other], "data": {"totals": [1.0, 999.0]}},
        # батч 2: наша form_submit-цель -> reaches=1
        {"month": "2025-06", "date1": "2025-06-01", "date2": "2025-06-30",
         "goal_ids": [FORM_SUBMIT_ID], "data": {"totals": [1.0, 1.0]}},
    ])
    report = vm.reconcile(raw_dir, CONFIG)
    row = report["months"][0]
    assert row["visits_logs"] == 1 and row["visits_reports"] == 1
    assert row["goal_reaches_logs"] == 1 and row["goal_reaches_reports"] == 1
    assert report["verdict"] == "OK"


def test_month_intersection_only(raw_dir):
    """Сверяются только месяцы, присутствующие и в Logs, и в Reports."""
    _write_logs(raw_dir, "visits_2025-04-01_2025-04-30_part000.csv.gz", [
        _row("v1", "2025-04-02 10:00:00", "[]")])
    _write_reports(raw_dir, [
        _report_entry("2025-04", visits=1, goal_reaches=0),
        _report_entry("2025-05", visits=500, goal_reaches=10),  # логов за май нет
    ])
    report = vm.reconcile(raw_dir, CONFIG)
    assert [r["month"] for r in report["months"]] == ["2025-04"]
    assert any("только в Reports" in n for n in report["notes"])


def test_status_thresholds():
    """Границы порогов: <2 OK, 2..5 WARN, >5 FAIL."""
    assert vm.status_of(0.0) == "OK"
    assert vm.status_of(1.99) == "OK"
    assert vm.status_of(2.0) == "WARN"
    assert vm.status_of(5.0) == "WARN"
    assert vm.status_of(5.01) == "FAIL"
    assert vm.status_of(float("inf")) == "FAIL"


def test_pct_delta_zero_division():
    assert vm.pct_delta(0, 0) == 0.0
    assert vm.pct_delta(5, 0) == float("inf")
    assert vm.pct_delta(102, 100) == pytest.approx(2.0)
