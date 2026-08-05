from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import common, funnels  # noqa: E402


class _Paths:
    def __init__(self, root: Path):
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.config_file = root / "config.yaml"


def _write_parquet(paths: _Paths, name: str, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / f"{name}.parquet")


def _write_config(paths: _Paths, funnels_config: dict) -> None:
    paths.config_file.write_text(
        yaml.safe_dump({"funnels": funnels_config}, sort_keys=False), encoding="utf-8"
    )


def test_multiple_goal_ids_funnels_repeats_anomalies_and_segments(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, {
        "booking": [
            {"stage": "open", "goal_ids": [1, 11]},
            {"stage": "start", "goal_ids": [2]},
            {"stage": "submit", "goal_ids": [3]},
        ],
        "call": [
            {"stage": "call_click", "goal_ids": [4]},
            {"stage": "call_connected", "goal_ids": [5]},
        ],
    })
    _write_parquet(paths, "visits", [
        {"visit_id": "v1", "date": "2026-01-10", "source_group": "ad", "device": "mobile", "entry_page": "/a"},
        {"visit_id": "v2", "date": "2026-01-11", "source_group": "ad", "device": "desktop", "entry_page": "/a"},
        {"visit_id": "v3", "date": "2026-02-01", "source_group": "organic", "device": "mobile", "entry_page": "/b"},
        {"visit_id": "v4", "date": "2026-02-02", "source_group": "organic", "device": "desktop", "entry_page": "/b"},
    ])
    _write_parquet(paths, "visit_goals", [
        {"visit_id": "v1", "goal_id": 1, "achievement_count": 2},
        {"visit_id": "v1", "goal_id": 2, "achievement_count": 1},
        {"visit_id": "v1", "goal_id": 3, "achievement_count": 1},
        {"visit_id": "v2", "goal_id": 11, "achievement_count": 1},
        {"visit_id": "v2", "goal_id": 2, "achievement_count": 1},
        {"visit_id": "v3", "goal_id": 3, "achievement_count": 1},
        {"visit_id": "v4", "goal_id": 4, "achievement_count": 1},
        {"visit_id": "v4", "goal_id": 5, "achievement_count": 1},
    ])
    _write_parquet(paths, "goal_achievements", [
        {"visit_id": "v1", "goal_id": 1, "goal_datetime": "2026-01-10T10:00:00"},
        {"visit_id": "v1", "goal_id": 2, "goal_datetime": "2026-01-10T10:02:00"},
        {"visit_id": "v1", "goal_id": 3, "goal_datetime": "2026-01-10T10:01:00"},
    ])

    artifacts = funnels.run(paths, {}, set())

    assert artifacts == ["funnels"]
    result = json.loads((paths.metrics / "funnels.json").read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert [item["funnel_id"] for item in result["funnels"]] == ["booking", "call"]

    booking = result["funnels"][0]
    assert booking["stages"] == [
        {"stage": "open", "visits": 2},
        {"stage": "start", "visits": 2},
        {"stage": "submit", "visits": 2},
    ]
    assert booking["first_to_last"]["completed_visits"] == 1
    assert booking["first_to_last"]["conversion_rate"] == 0.5
    assert booking["first_to_last"]["later_without_first_visits"] == 1
    assert booking["transitions"][1]["previous_without_next_visits"] == 1
    assert booking["transitions"][1]["event_order"]["out_of_order_visits"] == 1
    assert booking["repeat_achievements"][0]["visits_with_repeats"] == 1
    assert booking["repeat_achievements"][0]["extra_achievements"] == 1

    dimensions = {segment["dimension"] for segment in booking["segments"]}
    assert dimensions == {"month", "channel", "device", "entry_page"}
    january = next(
        segment for segment in booking["segments"]
        if segment["dimension"] == "month" and segment["value"] == "2026-01"
    )
    assert january["first_to_last"]["conversion_rate"] == 0.5


def test_absent_config_is_explicitly_unavailable(tmp_path):
    paths = _Paths(tmp_path)

    result = funnels.compute_funnels(paths)

    assert result == {
        "status": "unavailable",
        "reason": "funnels не настроены",
        "funnels": [],
    }


def _segment_rows(funnel: dict, dimension: str) -> list[dict]:
    return [item for item in funnel["segments"] if item["dimension"] == dimension]


def _high_cardinality_client(tmp_path, values: int, visits_per_value: int) -> _Paths:
    """Клиент с одним разрезом entry_page на `values` значений."""
    paths = _Paths(tmp_path)
    _write_config(paths, {"booking": [
        {"stage": "open", "goal_ids": [1]},
        {"stage": "submit", "goal_ids": [2]},
    ]})
    visit_rows = []
    goal_rows = []
    for index in range(values):
        for repeat in range(visits_per_value):
            visit_id = f"v{index}_{repeat}"
            visit_rows.append({
                "visit_id": visit_id,
                "date": "2026-01-10",
                "source_group": "ad",
                "device": "mobile",
                "entry_page": f"/p{index:05d}",
            })
            goal_rows.append({"visit_id": visit_id, "goal_id": 1, "achievement_count": 1})
            if repeat == 0:
                goal_rows.append({"visit_id": visit_id, "goal_id": 2, "achievement_count": 1})
    _write_parquet(paths, "visits", visit_rows)
    _write_parquet(paths, "visit_goals", goal_rows)
    return paths


def test_high_cardinality_dimension_is_capped_and_sums_converge(tmp_path):
    # 30 000 значений по 1 визиту: ни одно не проходит segment_min_visits,
    # но итог разреза обязан сойтись с итогом до фильтрации.
    paths = _high_cardinality_client(tmp_path, values=30_000, visits_per_value=1)

    defaults = {"segment_min_visits": 2, "segment_max_values": 300,
                "segment_coverage_target": 0.80,
                "segment_filtered_dimensions": ["entry_page"]}
    result = funnels.compute_funnels(paths, defaults)
    booking = result["funnels"][0]
    rows = _segment_rows(booking, "entry_page")

    assert len(rows) <= 300 + 1
    assert rows[-1]["value"] == "__other__"
    for stage_index, stage in enumerate(booking["stages"]):
        assert sum(row["stages"][stage_index]["visits"] for row in rows) == stage["visits"]
    assert sum(
        row["first_to_last"]["completed_visits"] for row in rows
    ) == booking["first_to_last"]["completed_visits"]
    assert sum(
        row["transitions"][0]["continued_visits"] for row in rows
    ) == booking["transitions"][0]["continued_visits"]


def test_collapsed_row_reports_null_for_non_additive_values(tmp_path):
    paths = _high_cardinality_client(tmp_path, values=40, visits_per_value=3)

    defaults = {"segment_min_visits": 3, "segment_max_values": 5,
                "segment_coverage_target": 0.80,
                "segment_filtered_dimensions": ["entry_page"]}
    booking = funnels.compute_funnels(paths, defaults)["funnels"][0]
    other = next(
        row for row in _segment_rows(booking, "entry_page") if row["value"] == "__other__"
    )

    # Доли неаддитивны — null, а не сумма; счётчики при этом посчитаны.
    assert other["first_to_last"]["conversion_rate"] is None
    assert other["transitions"][0]["transition_rate"] is None
    assert other["collapsed_values"] == 35
    assert other["collapsed_visits"] == 105
    assert other["stages"][0]["visits"] == 105


def test_segment_coverage_is_reported_in_manifest(tmp_path):
    paths = _high_cardinality_client(tmp_path, values=40, visits_per_value=3)

    defaults = {"segment_min_visits": 3, "segment_max_values": 5,
                "segment_coverage_target": 0.80,
                "segment_filtered_dimensions": ["entry_page"]}
    result = funnels.compute_funnels(paths, defaults)

    manifest = result["segment_selection"]
    assert manifest["policy"]["max_values"] == 5
    entry_page = next(
        item for item in manifest["dimensions"] if item["dimension"] == "entry_page"
    )
    assert entry_page["rule_applied"] is True
    assert entry_page["values_total"] == 40
    assert entry_page["values_selected"] == 5
    assert entry_page["coverage_ratio"] == 0.125
    # Разрез вне segment_filtered_dimensions считается целиком.
    device = next(item for item in manifest["dimensions"] if item["dimension"] == "device")
    assert device["rule_applied"] is False
    assert device["values_selected"] == device["values_total"] == 1
    assert device["coverage_ratio"] == 1.0
    assert result["funnels"][0]["segment_coverage"] == manifest["dimensions"]


def test_low_cardinality_dimensions_keep_every_value(tmp_path):
    paths = _high_cardinality_client(tmp_path, values=40, visits_per_value=3)

    defaults = {"segment_min_visits": 500, "segment_max_values": 300,
                "segment_coverage_target": 0.80,
                "segment_filtered_dimensions": ["entry_page"]}
    booking = funnels.compute_funnels(paths, defaults)["funnels"][0]

    for dimension in ("month", "channel", "device"):
        rows = _segment_rows(booking, dimension)
        assert len(rows) == 1
        assert [row["value"] for row in rows] != ["__other__"]
        assert rows[0]["first_to_last"]["conversion_rate"] is not None


def test_funnels_precede_block3_in_compute_order():
    assert common.BLOCK_MODULE_NAMES.index("funnels") < common.BLOCK_MODULE_NAMES.index("block3")


def test_funnels_are_computed_once_per_run(tmp_path, monkeypatch):
    """PERF-1B: за прогон ровно один compute_funnels, потребители читают артефакт."""
    from src.compute import block3, seasonality

    paths = _high_cardinality_client(tmp_path, values=4, visits_per_value=3)
    calls: list[int] = []
    original = funnels.compute_funnels

    def _counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(funnels, "compute_funnels", _counted)

    # Порядок как в dispatch: funnels -> seasonality -> block3.
    funnels.run(paths, {}, set())
    series = seasonality._funnel_series(paths)
    block3._run_c06(paths, {"min_sample_visits": 3}, "HIGH", paths.metrics)

    assert len(calls) == 1
    artifact = json.loads((paths.metrics / "funnels.json").read_text(encoding="utf-8"))
    assert series["status"] == "ok"
    assert series["funnel_id"] == artifact["funnels"][0]["funnel_id"]
    c06 = json.loads((paths.metrics / "c06.json").read_text(encoding="utf-8"))
    summary = next(row for row in c06 if row["finding"] == "funnel_summary")
    assert summary["completed_visits"] == artifact["funnels"][0]["first_to_last"]["completed_visits"]


def test_consumers_fall_back_to_computing_when_artifact_absent(tmp_path):
    """Без артефакта (блок funnels упал / вызов вне dispatch) потребитель считает сам."""
    paths = _high_cardinality_client(tmp_path, values=4, visits_per_value=3)

    assert not (paths.metrics / "funnels.json").exists()
    assert funnels.load_funnels(paths)["status"] == "ok"


def test_damaged_artifact_is_not_used(tmp_path):
    paths = _high_cardinality_client(tmp_path, values=4, visits_per_value=3)
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "funnels.json").write_text("{", encoding="utf-8")

    assert funnels.load_funnels(paths)["status"] == "ok"
