from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq

from src.compute import acquisition_economics, common


def _write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _paths(tmp_path: Path) -> SimpleNamespace:
    canonical = tmp_path / "canonical"
    metrics = tmp_path / "metrics"
    canonical.mkdir()
    metrics.mkdir()
    return SimpleNamespace(canonical=canonical, metrics=metrics)


def _config(models: list[dict], *, record_unit: str = "paid_booking") -> dict:
    return {
        "crm_csv": {"record_unit": record_unit, "attribution_reliable": True},
        "spend_components": [
            {"id": "media", "channel": "direct", "kind": "media", "source": "monthly_fixed", "amount_rub_month": 1},
            {"id": "management", "channel": "direct", "kind": "management", "source": "monthly_fixed", "amount_rub_month": 1},
        ],
        "funnels": {
            "booking": [
                {"stage": "form_open", "goal_ids": [10]},
                {"stage": "form_submit", "goal_ids": [20]},
            ]
        },
        "acquisition_models": models,
    }


def _cost_summary(*, include_management: bool = True) -> dict:
    rows = [["media", "direct", "media", 900.0]]
    if include_management:
        rows.append(["management", "direct", "management", 100.0])
    return {
        "component_total_columns": ["component_id", "channel", "kind", "amount_rub"],
        "component_total_rows": rows,
    }


def _funnel(visits: int) -> dict:
    return {
        "status": "ok",
        "funnels": [{
            "funnel_id": "booking",
            "stages": [
                {"stage": "form_open", "visits": visits + 2},
                {"stage": "form_submit", "visits": visits},
            ],
        }],
    }


def _write_crm(paths: SimpleNamespace) -> None:
    _write_parquet(Path(paths.canonical) / "crm.parquet", [
        {"source_norm": "direct", "amount_rub": 100.0, "phone_hash": "a"},
        {"source_norm": "direct", "amount_rub": 200.0, "phone_hash": "b"},
        {"source_norm": "organic", "amount_rub": 600.0, "phone_hash": "b"},
    ])


def test_three_modes_and_revenue_statistics(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_crm(paths)
    models = [
        {"id": "actual", "mode": "crm_attributed", "crm_source": "direct", "spend_components": ["media"]},
        {"id": "estimate", "mode": "crm_share_estimate", "crm_share": 0.5, "spend_components": ["media", "management"]},
        {"id": "tracked", "mode": "tracked_funnel", "funnel": "booking", "stage": "form_submit", "spend_components": ["media", "management"]},
    ]

    result = acquisition_economics.compute_acquisition_economics(
        paths, _config(models), _cost_summary(), _funnel(4)
    )

    assert result["crm"]["record_count"] == 3
    assert result["crm"]["total_revenue_rub"] == 900.0
    assert result["crm"]["average_revenue_rub"] == 300.0
    assert result["crm"]["median_revenue_rub"] == 200.0
    assert result["crm"]["unique_customers"] == 2
    by_id = {row["id"]: row for row in result["models"]}
    assert by_id["actual"]["basis"] == "actual"
    assert by_id["actual"]["result_name"] == "стоимость оплаченной брони"
    assert by_id["actual"]["value_rub"] == 450.0
    assert by_id["estimate"]["basis"] == "estimate"
    assert by_id["estimate"]["result_name"] == "оценочная стоимость сайт-брони"
    assert by_id["estimate"]["value_rub"] == 1000.0 / 1.5
    assert by_id["tracked"]["basis"] == "tracked_proxy"
    assert by_id["tracked"]["result_name"] == "стоимость отслеженной отправки формы"
    assert by_id["tracked"]["value_rub"] == 250.0
    assert all("CAC" not in row["result_name"] for row in result["models"])


def test_zero_denominator_is_unavailable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_parquet(Path(paths.canonical) / "crm.parquet", [
        {"source_norm": "organic", "amount_rub": 100.0, "phone_hash": "a"},
    ])
    models = [{"id": "actual", "mode": "crm_attributed", "crm_source": "direct", "spend_components": ["media"]}]

    row = acquisition_economics.compute_acquisition_economics(
        paths, _config(models), _cost_summary(), _funnel(1)
    )["models"][0]

    assert row["status"] == "unavailable"
    assert row["value_rub"] is None
    assert {item["code"] for item in row["limitations"]} == {"zero_denominator"}


def test_unknown_crm_does_not_block_tracked_model(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    models = [
        {"id": "estimate", "mode": "crm_share_estimate", "crm_share": 0.9, "spend_components": ["media"]},
        {"id": "tracked", "mode": "tracked_funnel", "funnel": "booking", "stage": "form_submit", "spend_components": ["media"]},
    ]

    rows = acquisition_economics.compute_acquisition_economics(
        paths, _config(models, record_unit="unknown"), _cost_summary(), _funnel(3)
    )["models"]

    assert rows[0]["status"] == "unavailable"
    assert rows[0]["limitations"] == [{"code": "crm_record_unit_unknown"}]
    assert rows[1]["status"] == "ok"
    assert rows[1]["value_rub"] == 300.0


def test_tracked_traffic_filter_counts_unique_matching_visits(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_parquet(Path(paths.canonical) / "visits.parquet", [
        {"visit_id": "1", "source_group_resolved": "ad"},
        {"visit_id": "2", "source_group_resolved": "organic"},
        {"visit_id": "3", "source_group_resolved": "ad"},
    ])
    _write_parquet(Path(paths.canonical) / "visit_goals.parquet", [
        {"visit_id": "1", "goal_id": "20", "achievement_count": 2},
        {"visit_id": "2", "goal_id": "20", "achievement_count": 1},
        {"visit_id": "3", "goal_id": "10", "achievement_count": 1},
    ])
    model = {
        "id": "tracked_direct", "mode": "tracked_funnel", "funnel": "booking",
        "stage": "form_submit", "channel": "direct",
        "traffic_filter": {"field": "source_group_resolved", "value": "ad"},
        "spend_components": ["media"],
    }

    row = acquisition_economics.compute_acquisition_economics(
        paths, _config([model]), _cost_summary(), _funnel(99)
    )["models"][0]

    assert row["denominator"]["value"] == 1
    assert row["value_rub"] == 900.0


def test_channel_tracked_model_without_filter_is_unavailable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    model = {
        "id": "tracked_direct", "mode": "tracked_funnel", "funnel": "booking",
        "stage": "form_submit", "channel": "direct", "spend_components": ["media"],
    }

    row = acquisition_economics.compute_acquisition_economics(
        paths, _config([model]), _cost_summary(), _funnel(5)
    )["models"][0]

    assert row["status"] == "unavailable"
    assert row["limitations"] == [{"code": "channel_tracked_filter_required", "channel": "direct"}]


def test_missing_spend_component_is_unavailable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    model = {
        "id": "tracked", "mode": "tracked_funnel", "funnel": "booking",
        "stage": "form_submit", "spend_components": ["media", "management"],
    }

    row = acquisition_economics.compute_acquisition_economics(
        paths, _config([model]), _cost_summary(include_management=False), _funnel(2)
    )["models"][0]

    assert row["status"] == "unavailable"
    assert row["value_rub"] is None
    assert row["limitations"] == [
        {"code": "spend_component_unavailable", "component_ids": ["management"]}
    ]


def test_run_writes_compact_artifact(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_file = tmp_path / "config.yaml"
    paths.config_file.write_text(
        "crm_csv:\n  record_unit: unknown\nacquisition_models: []\n", encoding="utf-8"
    )
    (Path(paths.metrics) / "cost_summary.json").write_text("{}", encoding="utf-8")
    (Path(paths.metrics) / "funnels.json").write_text("{}", encoding="utf-8")

    produced = acquisition_economics.run(paths, {}, set())

    assert produced == ["acquisition_economics.json"]
    artifact = json.loads((Path(paths.metrics) / produced[0]).read_text(encoding="utf-8"))
    assert artifact["models"] == []
    assert artifact["crm"]["status"] == "unavailable"


def test_dispatch_runs_economics_after_cost_summary() -> None:
    assert common.BLOCK_MODULE_NAMES.index("acquisition_economics") > common.BLOCK_MODULE_NAMES.index(
        "cost_summary"
    )
