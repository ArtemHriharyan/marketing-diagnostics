"""Целевые тесты единого контракта analysis-кандидатов (P06)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import candidates, common  # noqa: E402


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _decoded(result: dict) -> list[dict]:
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def _candidate(row_ref: str, *, context_refs: list[str] | None = None) -> dict:
    return {
        "row_ref": row_ref,
        "check_id": "A01",
        "candidate": True,
        "row_role": "candidate",
        "candidate_reason": "high_cost",
        "context_refs": context_refs or [],
        "value": 1200.0,
    }


def test_collects_candidates_referenced_context_and_all_limitations(tmp_path):
    _write(
        tmp_path / "a01.json",
        [
            {
                "row_ref": "A01:baseline",
                "check_id": "A01",
                "candidate": False,
                "row_role": "baseline",
                "candidate_reason": "comparison_baseline",
                "context_refs": [],
                "value": 700.0,
            },
            _candidate("A01:campaign", context_refs=["A01:baseline"]),
            {
                "row_ref": "A01:unrelated",
                "candidate": False,
                "row_role": "context",
                "candidate_reason": "unrelated_context",
                "context_refs": [],
            },
            {
                "row_ref": "A01:limit",
                "candidate": False,
                "row_role": "limitation",
                "candidate_reason": "small_sample",
                "context_refs": [],
            },
        ],
    )

    result = candidates.build_analysis_candidates(tmp_path)
    rows = _decoded(result)

    assert result["columns"][:6] == [
        "artifact",
        "row_ref",
        "candidate",
        "row_role",
        "candidate_reason",
        "context_refs",
    ]
    assert [row["row_ref"] for row in rows] == [
        "A01:baseline",
        "A01:campaign",
        "A01:limit",
    ]
    assert rows[1]["context_refs"] == ["A01:baseline"]
    assert result["coverage"]["candidate_rows_included"] == 1
    assert result["coverage"]["context_rows_included"] == 1
    assert result["coverage"]["limitation_rows_included"] == 1


def test_missing_baseline_reference_is_audited_without_dropping_candidate(tmp_path):
    _write(tmp_path / "c06.json", [_candidate("C06:segment", context_refs=["C06:baseline"])])

    result = candidates.build_analysis_candidates(tmp_path)
    rows = _decoded(result)

    assert [row["row_ref"] for row in rows] == ["C06:segment"]
    assert rows[0]["context_refs"] == []
    assert result["coverage"]["context_refs_total"] == 1
    assert result["coverage"]["context_refs_resolved"] == 0
    assert result["coverage"]["missing_context_refs"] == ["C06:baseline"]


def test_exact_duplicate_rows_are_emitted_once_and_references_follow_alias(tmp_path):
    duplicate_a = {
        "row_ref": "A01:base-a",
        "candidate": False,
        "row_role": "baseline",
        "candidate_reason": "comparison_baseline",
        "context_refs": [],
        "value": 10,
    }
    duplicate_b = {**duplicate_a, "row_ref": "A01:base-b"}
    _write(
        tmp_path / "a01.json",
        [duplicate_a, duplicate_b, _candidate("A01:campaign", context_refs=["A01:base-b"])],
    )

    result = candidates.build_analysis_candidates(tmp_path)
    rows = _decoded(result)

    assert [row["row_ref"] for row in rows] == ["A01:base-a", "A01:campaign"]
    assert rows[1]["context_refs"] == ["A01:base-a"]
    assert result["coverage"]["duplicate_rows_excluded"] == 1


def test_legacy_and_partially_marked_artifacts_are_safe_and_visible_in_coverage(tmp_path):
    _write(tmp_path / "legacy.json", [{"check_id": "D01", "value": 7}])
    _write(
        tmp_path / "partial.json",
        [
            {"row_role": "candidate", "candidate_reason": "role_only"},
            {"candidate": True, "check_id": "S06", "value": 2},
        ],
    )
    _write(tmp_path / "nested_legacy.json", {"monthly": {"2026-01": 1}})

    result = candidates.build_analysis_candidates(tmp_path)
    rows = _decoded(result)
    audits = {row["artifact"]: row for row in result["coverage"]["artifacts"]}

    assert len(rows) == 1
    assert rows[0]["check_id"] == "S06"
    assert rows[0]["row_role"] == "candidate"
    assert audits["legacy"]["status"] == "legacy"
    assert audits["partial"]["status"] == "partial"
    assert audits["nested_legacy"]["status"] == "legacy"
    assert result["coverage"]["artifacts_legacy"] == 2
    assert result["coverage"]["artifacts_partial"] == 1
    assert result["coverage"]["contract_coverage"] == 0.0


def test_duplicate_row_ref_keeps_first_row_deterministically(tmp_path):
    _write(
        tmp_path / "a01.json",
        [
            _candidate("A01:duplicate"),
            {**_candidate("A01:duplicate"), "value": 9999.0},
        ],
    )

    result = candidates.build_analysis_candidates(tmp_path)
    rows = _decoded(result)

    assert len(rows) == 1
    assert rows[0]["value"] == 1200.0
    assert result["coverage"]["duplicate_row_refs_excluded"] == 1


class _Paths:
    def __init__(self, metrics: Path):
        self.metrics = metrics


def test_run_writes_artifact_and_does_not_read_previous_output(tmp_path):
    _write(tmp_path / "a01.json", [_candidate("A01:campaign")])
    _write(tmp_path / "degradation_report.json", {"candidate": True})
    _write(tmp_path / "metrics_summary.json", {"candidate": True})
    _write(tmp_path / "analysis_candidates.json", {"stale": True})

    assert candidates.run(_Paths(tmp_path), {}, {"A01"}) == ["analysis_candidates"]

    result = json.loads((tmp_path / "analysis_candidates.json").read_text(encoding="utf-8"))
    assert result["coverage"]["artifacts_scanned"] == 1
    assert _decoded(result)[0]["artifact"] == "a01"
    assert common.BLOCK_MODULE_NAMES[-1] == "candidates"
