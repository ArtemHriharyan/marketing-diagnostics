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


def test_funnels_precede_block3_in_compute_order():
    assert common.BLOCK_MODULE_NAMES.index("funnels") < common.BLOCK_MODULE_NAMES.index("block3")
