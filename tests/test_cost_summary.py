from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import yaml

from src.compute.cost_summary import build_cost_summary, run, validate_spend_components
from src.pipeline.orchestrator import ClientPaths


def _config(components: list[dict]) -> dict:
    return {
        "data_window": {
            "mode": "explicit",
            "date_from": "2026-01-01",
            "date_to": "2026-02-28",
        },
        "spend_components": components,
    }


def _write_costs(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    try:
        con.execute(
            """
            CREATE TABLE costs AS SELECT * FROM (VALUES
                (DATE '2026-01-03', 'direct', 120.0, 100.0),
                (DATE '2026-01-20', 'direct', 80.0, 66.67),
                (DATE '2026-02-10', 'direct', 50.0, 41.67),
                (DATE '2026-01-15', 'seo_fee', 40.0, 33.33)
            ) AS t(date, source_tag, cost_raw, cost_normalized)
            """
        )
        con.execute("COPY costs TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        con.close()


def test_canonical_and_fixed_components_are_summed_once_without_vat_recalculation(tmp_path):
    costs_path = tmp_path / "costs.parquet"
    _write_costs(costs_path)
    config = _config(
        [
            {"id": "direct_media", "channel": "direct", "kind": "media", "source": "canonical_costs", "source_tag": "direct"},
            {"id": "direct_management", "channel": "direct", "kind": "management", "source": "monthly_fixed", "amount_rub_month": 30},
        ]
    )

    result = build_cost_summary(config, costs_path)

    assert result["money_basis"] == "gross_final_rub"
    assert result["component_rows"] == [
        ["2026-01", "direct_management", "direct", "management", 30.0],
        ["2026-01", "direct_media", "direct", "media", 200.0],
        ["2026-02", "direct_management", "direct", "management", 30.0],
        ["2026-02", "direct_media", "direct", "media", 50.0],
    ]
    assert result["monthly_total_rows"] == [["2026-01", 230.0], ["2026-02", 80.0]]
    assert result["channel_total_rows"] == [["direct", 310.0]]
    assert result["total_rub"] == 310.0


@pytest.mark.parametrize(
    "components, message",
    [
        (
            [
                {"id": "same", "channel": "direct", "kind": "media", "source": "monthly_fixed", "amount_rub_month": 1},
                {"id": "same", "channel": "seo", "kind": "management", "source": "monthly_fixed", "amount_rub_month": 2},
            ],
            "дублирующийся spend_components.id",
        ),
        (
            [
                {"id": "one", "channel": "direct", "kind": "media", "source": "canonical_costs", "source_tag": "direct"},
                {"id": "two", "channel": "direct", "kind": "management", "source": "canonical_costs", "source_tag": "direct"},
            ],
            "дублирующийся canonical source_tag",
        ),
    ],
)
def test_duplicate_ids_and_source_tags_are_rejected(components, message):
    with pytest.raises(ValueError, match=message):
        validate_spend_components(components)


def test_missing_canonical_source_degrades_but_fixed_component_is_kept():
    config = _config(
        [
            {"id": "direct_media", "channel": "direct", "kind": "media", "source": "canonical_costs", "source_tag": "direct"},
            {"id": "seo_management", "channel": "seo", "kind": "management", "source": "monthly_fixed", "amount_rub_month": 25},
        ]
    )

    result = build_cost_summary(config, None)

    assert result["component_rows"] == [
        ["2026-01", "seo_management", "seo", "management", 25.0],
        ["2026-02", "seo_management", "seo", "management", 25.0],
    ]
    assert result["total_rub"] == 50.0
    assert result["limitations"] == [
        {"code": "canonical_costs_unavailable", "component_ids": ["direct_media"]}
    ]


def test_present_costs_with_missing_source_tag_is_explicit_limitation(tmp_path):
    costs_path = tmp_path / "costs.parquet"
    _write_costs(costs_path)
    config = _config(
        [
            {"id": "maps_media", "channel": "maps", "kind": "media", "source": "canonical_costs", "source_tag": "maps"}
        ]
    )

    result = build_cost_summary(config, costs_path)

    assert result["component_rows"] == []
    assert result["limitations"] == [
        {"code": "canonical_source_tag_not_found", "component_id": "maps_media", "source_tag": "maps"}
    ]


def test_absent_spend_components_returns_empty_summary():
    result = build_cost_summary({}, None)

    assert result["component_rows"] == []
    assert result["channel_total_rows"] == []
    assert result["total_rub"] == 0.0
    assert result["limitations"] == []


def test_run_writes_cost_summary_json(tmp_path):
    canonical = tmp_path / "canonical"
    metrics = tmp_path / "metrics"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            _config(
                [
                    {"id": "management", "channel": "direct", "kind": "management", "source": "monthly_fixed", "amount_rub_month": 10}
                ]
            ),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    paths = SimpleNamespace(canonical=canonical, metrics=metrics, config=config_path)

    artifacts = run(paths, {}, set())

    assert artifacts == ["cost_summary.json"]
    payload = json.loads((metrics / "cost_summary.json").read_text(encoding="utf-8"))
    assert payload["total_rub"] == 20.0


def test_run_reads_spend_components_from_client_paths_config_file(tmp_path):
    paths = ClientPaths("_template")
    paths.config_file = tmp_path / "config.yaml"
    paths.canonical = tmp_path / "canonical"
    paths.metrics = tmp_path / "metrics"
    paths.config_file.write_text(
        yaml.safe_dump(
            _config(
                [
                    {
                        "id": "management",
                        "channel": "direct",
                        "kind": "management",
                        "source": "monthly_fixed",
                        "amount_rub_month": 10,
                    }
                ]
            ),
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    run(paths, {}, set())

    payload = json.loads((paths.metrics / "cost_summary.json").read_text(encoding="utf-8"))
    assert payload["total_rub"] == 20.0
    assert payload["total_rub"] > 0
