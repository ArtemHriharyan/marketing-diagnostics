from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml


REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import seasonality  # noqa: E402


class _Paths:
    def __init__(self, root: Path):
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


def _write_table(paths: _Paths, name: str, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / f"{name}.parquet")


def test_peaks_troughs_yoy_and_direction_conflict(tmp_path):
    paths = _Paths(tmp_path)
    _write_table(paths, "wordstat", [
        {"month": "2025-01", "count": 100, "purpose": "seasonality"},
        {"month": "2025-02", "count": 100, "purpose": "seasonality"},
        {"month": "2026-01", "count": 300, "purpose": "seasonality"},
    ])
    _write_table(paths, "visits", [
        {"visit_id": f"old-{index}", "date": "2025-01-10"} for index in range(100)
    ] + [
        {"visit_id": f"base-{index}", "date": "2025-02-10"} for index in range(100)
    ] + [
        {"visit_id": f"new-{index}", "date": "2026-01-10"} for index in range(20)
    ])

    result = seasonality.compute_seasonality(paths)

    demand = result["series"]["wordstat_demand"]
    visits = result["series"]["visits"]
    assert demand["peaks"] == [{"month": "2026-01", "index": 300.0}]
    assert visits["troughs"] == [{"month": "2026-01", "index": 20.0}]
    assert next(row for row in demand["months"] if row["month"] == "2026-01")["yoy_index"] == 300.0
    assert next(row for row in visits["months"] if row["month"] == "2026-01")["yoy_index"] == 20.0
    january = next(row for row in result["monthly_indices"] if row["month"] == "2026-01")
    assert january["wordstat_demand_index"] == 300.0
    assert january["visits_index"] == 20.0
    assert result["direction_conflicts"] == [{
        "month": "2026-01",
        "comparison": "yoy",
        "directions": {"wordstat_demand": "up", "visits": "down"},
    }]


def test_funnel_final_stage_and_crm_series(tmp_path):
    paths = _Paths(tmp_path)
    paths.config_file.write_text(yaml.safe_dump({
        "funnels": {
            "booking": [
                {"stage": "opened", "goal_ids": ["1"]},
                {"stage": "submitted", "goal_ids": ["2"]},
            ],
        },
    }), encoding="utf-8")
    _write_table(paths, "visits", [
        {"visit_id": "v1", "date": "2026-01-10"},
        {"visit_id": "v2", "date": "2026-02-10"},
        {"visit_id": "v3", "date": "2026-02-11"},
    ])
    _write_table(paths, "visit_goals", [
        {"visit_id": visit_id, "goal_id": goal_id, "achievement_count": 1}
        for visit_id in ("v1", "v2", "v3") for goal_id in ("1", "2")
    ])
    _write_table(paths, "crm_records", [
        {"lead_date": "2026-01-12", "amount_rub": 100.0},
        {"lead_date": "2026-02-12", "amount_rub": 100.0},
        {"lead_date": "2026-02-13", "amount_rub": 300.0},
    ])

    result = seasonality.compute_seasonality(paths)

    funnel = result["series"]["funnel_final_stage"]
    assert funnel["status"] == "ok"
    assert funnel["funnel_id"] == "booking"
    assert funnel["stage"] == "submitted"
    crm_bookings = result["series"]["crm_records"]
    crm_revenue = result["series"]["crm_revenue"]
    assert crm_bookings["status"] == "ok"
    assert crm_bookings["months"] == [
        {"month": "2026-01", "index": 66.7},
        {"month": "2026-02", "index": 133.3, "mom_direction": "up"},
    ]
    assert crm_revenue["status"] == "ok"
    assert crm_revenue["months"] == [
        {"month": "2026-01", "index": 40.0},
        {"month": "2026-02", "index": 160.0, "mom_direction": "up"},
    ]


def test_each_missing_source_degrades_independently_and_no_phrases_leak(tmp_path):
    paths = _Paths(tmp_path)
    _write_table(paths, "visits", [{"visit_id": "v1", "date": "2026-01-10"}])

    artifacts = seasonality.run(paths, {}, set())
    result = json.loads((paths.metrics / "seasonality.json").read_text(encoding="utf-8"))

    assert artifacts == ["seasonality"]
    assert result["status"] == "partial"
    assert result["series"]["visits"]["status"] == "ok"
    assert result["series"]["wordstat_demand"]["status"] == "unavailable"
    assert result["series"]["funnel_final_stage"]["status"] == "unavailable"
    assert result["series"]["crm_records"]["status"] == "unavailable"
    assert result["series"]["crm_revenue"]["status"] == "unavailable"
    assert "phrase" not in json.dumps(result, ensure_ascii=False).lower()
