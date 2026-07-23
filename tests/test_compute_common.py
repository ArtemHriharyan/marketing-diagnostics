"""Тесты общей инфраструктуры compute (task 5A: src/compute/common.py).

Сценарии:
1. runnable   — dispatch_blocks передаёт runnable_ids блокам и собирает
                артефакты, которые блок вернул.
2. skipped    — недоступная проверка сохраняет причину в metrics_summary.
3. cap violation — confidence выше confidence_cap запрещена (не пишется).
4. output schema — write_metric_artifact пишет валидные csv+json атомарно.

Бизнес-проверки D/A/T/C/S здесь не участвуют — только каркас.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from src.compute import common  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths для тестов common.py."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.inputs = root / "inputs"
        self.metrics = root / "data" / "metrics"


# ── Загрузка входов ──────────────────────────────────────────────────────────

def test_load_canonical_returns_empty_dict_when_missing(tmp_path):
    paths = _Paths(tmp_path)
    assert common.load_canonical(paths) == {}


def test_load_canonical_lists_parquet_files(tmp_path):
    import pandas as pd

    paths = _Paths(tmp_path)
    paths.canonical.mkdir(parents=True)
    pd.DataFrame({"a": [1, 2]}).to_parquet(paths.canonical / "visits.parquet")

    tables = common.load_canonical(paths)
    assert set(tables) == {"visits"}
    assert tables["visits"] == paths.canonical / "visits.parquet"


def test_open_duckdb_registers_view_over_parquet(tmp_path):
    import pandas as pd

    paths = _Paths(tmp_path)
    paths.canonical.mkdir(parents=True)
    pd.DataFrame({"visit_id": [1, 2, 3]}).to_parquet(paths.canonical / "visits.parquet")

    con = common.open_duckdb(paths)
    try:
        rows = con.execute("SELECT COUNT(*) FROM visits").fetchone()
        assert rows[0] == 3
    finally:
        con.close()


def test_load_inputs_reads_yaml_files(tmp_path):
    paths = _Paths(tmp_path)
    paths.inputs.mkdir(parents=True)
    (paths.inputs / "client_answers.yaml").write_text(
        "avg_check: 1000\n", encoding="utf-8"
    )

    inputs = common.load_inputs(paths)
    assert inputs["client_answers"] == {"avg_check": 1000}


def test_load_degradation_missing_file_returns_empty_dict(tmp_path):
    paths = _Paths(tmp_path)
    assert common.load_degradation(paths) == {}


def test_load_degradation_reads_written_report(tmp_path):
    paths = _Paths(tmp_path)
    paths.metrics.mkdir(parents=True)
    report = {"counts": {"total": 1, "runnable": 1, "skipped": 0}}
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    assert common.load_degradation(paths) == report


# ── Единый валидатор выходного числа ─────────────────────────────────────────

def test_validate_metric_value_accepts_finite_numbers():
    common.validate_metric_value(42)
    common.validate_metric_value(3.14)
    common.validate_metric_value(None)
    common.validate_metric_value("text")


def test_validate_metric_value_rejects_nan():
    with pytest.raises(ValueError):
        common.validate_metric_value(math.nan)


def test_validate_metric_value_rejects_infinity():
    with pytest.raises(ValueError):
        common.validate_metric_value(math.inf)


def test_validate_metric_value_rejects_none_when_disallowed():
    with pytest.raises(ValueError):
        common.validate_metric_value(None, allow_none=False)


def test_validate_row_reports_offending_field_name():
    with pytest.raises(ValueError, match="cpa"):
        common.validate_row({"check_id": "A01", "cpa": math.nan})


# ── Запрет confidence > confidence_cap ───────────────────────────────────────

def test_confidence_within_cap_does_not_raise():
    common.assert_confidence_within_cap("MED", "MED")
    common.assert_confidence_within_cap("LOW", "HIGH")


def test_confidence_above_cap_raises_violation():
    with pytest.raises(common.ConfidenceCapViolation):
        common.assert_confidence_within_cap("HIGH", "MED")


# ── Атомарная запись csv/json (output schema) ────────────────────────────────

def test_write_metric_artifact_writes_matching_csv_and_json(tmp_path):
    rows = [
        {"check_id": "A01", "campaign": "brand", "cpa": 500.0},
        {"check_id": "A01", "campaign": "generic", "cpa": 750.5},
    ]
    csv_path, json_path = common.write_metric_artifact(tmp_path, "a01", rows)

    assert csv_path == tmp_path / "a01.csv"
    assert json_path == tmp_path / "a01.json"

    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded == rows

    csv_text = csv_path.read_text(encoding="utf-8")
    assert "check_id,campaign,cpa" in csv_text.splitlines()[0]
    assert "A01,brand,500.0" in csv_text
    assert "A01,generic,750.5" in csv_text


def test_write_metric_artifact_empty_rows_writes_empty_csv_and_json_list(tmp_path):
    csv_path, json_path = common.write_metric_artifact(tmp_path, "empty", [])
    assert json.loads(json_path.read_text(encoding="utf-8")) == []
    assert csv_path.read_text(encoding="utf-8") == ""


def test_write_metric_artifact_rejects_invalid_value_and_writes_nothing(tmp_path):
    rows = [{"check_id": "A01", "cpa": math.nan}]
    with pytest.raises(ValueError):
        common.write_metric_artifact(tmp_path, "bad", rows)
    assert not (tmp_path / "bad.csv").exists()
    assert not (tmp_path / "bad.json").exists()


def test_write_metric_artifact_enforces_confidence_cap(tmp_path):
    rows = [{"check_id": "A01", "confidence": "HIGH"}]
    with pytest.raises(common.ConfidenceCapViolation):
        common.write_metric_artifact(tmp_path, "capped", rows, confidence_cap="MED")
    assert not (tmp_path / "capped.csv").exists()
    assert not (tmp_path / "capped.json").exists()


# ── Dispatch по runnable / skipped ───────────────────────────────────────────

class _FakeBlockOk:
    __name__ = "fake_ok"

    @staticmethod
    def run(paths, defaults, runnable_ids):
        return ["a01"]


class _FakeBlockChecksRunnable:
    __name__ = "fake_checks_runnable"

    @staticmethod
    def run(paths, defaults, runnable_ids):
        if "A01" not in runnable_ids:
            raise AssertionError("A01 not in runnable_ids")
        return ["a01"]


class _FakeBlockNotImplemented:
    __name__ = "fake_stub"

    @staticmethod
    def run(paths, defaults, runnable_ids):
        raise NotImplementedError


class _FakeBlockErrors:
    __name__ = "fake_broken"

    @staticmethod
    def run(paths, defaults, runnable_ids):
        raise RuntimeError("boom")


def test_dispatch_blocks_passes_runnable_ids_and_collects_artifacts(tmp_path):
    paths = _Paths(tmp_path)
    degradation_report = {
        "runnable_check_ids": ["A01"],
        "skipped": [],
        "counts": {"total": 1, "runnable": 1, "skipped": 0},
    }

    result = common.dispatch_blocks(
        paths, {}, degradation_report, modules=[_FakeBlockChecksRunnable()]
    )

    assert result["runnable_ids"] == ["A01"]
    assert result["artifacts"] == ["a01"]
    assert result["block_status"] == {"fake_checks_runnable": "ok"}


def test_dispatch_blocks_not_implemented_block_does_not_stop_others(tmp_path):
    paths = _Paths(tmp_path)
    degradation_report = {"runnable_check_ids": [], "skipped": [], "counts": {}}

    result = common.dispatch_blocks(
        paths,
        {},
        degradation_report,
        modules=[_FakeBlockNotImplemented(), _FakeBlockOk()],
    )

    assert result["block_status"]["fake_stub"] == "not_implemented"
    assert result["block_status"]["fake_ok"] == "ok"
    assert result["artifacts"] == ["a01"]


def test_dispatch_blocks_records_error_without_raising(tmp_path):
    paths = _Paths(tmp_path)
    degradation_report = {"runnable_check_ids": [], "skipped": [], "counts": {}}

    result = common.dispatch_blocks(
        paths, {}, degradation_report, modules=[_FakeBlockErrors(), _FakeBlockOk()]
    )

    assert result["block_status"]["fake_broken"] == "error"
    assert "RuntimeError" in result["block_errors"]["fake_broken"]
    assert result["block_status"]["fake_ok"] == "ok"


def test_build_metrics_summary_has_no_business_numbers(tmp_path):
    degradation_report = {
        "counts": {"total": 2, "runnable": 1, "skipped": 1},
        "skipped": [{"id": "A02", "block": 1, "reason": "нет источника: расходы"}],
    }
    dispatch_result = {
        "artifacts": ["a01"],
        "block_status": {"block1": "ok"},
    }

    summary = common.build_metrics_summary(degradation_report, dispatch_result)

    assert summary["counts"] == {"total": 2, "runnable": 1, "skipped": 1}
    assert summary["skipped"] == [
        {"id": "A02", "block": 1, "reason": "нет источника: расходы"}
    ]
    assert summary["block_status"] == {"block1": "ok"}
    assert summary["artifacts"] == ["a01"]
    # Только структура/причины — ни одного числового бизнес-поля (cpa, cost, ...).
    dumped = json.dumps(summary)
    for forbidden in ("cpa", "cost_rub", "revenue", "margin"):
        assert forbidden not in dumped
