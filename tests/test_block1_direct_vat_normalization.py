"""Тесты отложенной Q01-нормализации cost_normalized для direct_queries/
campaigns/geo/placements в compute (задача FIX-block1-cost-normalization).

До этой задачи cost_normalized оставался null на этих 4 таблицах (transform
осознанно не считает НДС здесь, см. build_canonical.py:_write_direct_table) и
block1 читал его как есть — то есть A09-A15/A17-A19 всегда деградировали по
деньгам на реальных клиентских данных (см. AUDIT-cost-normalized-queries-geo-
architecture, docs/implementation_status.md). Фикстуры здесь, в отличие от
остальных тестов test_block1.py, пишут direct_queries/direct_placements в
РЕАЛЬНОМ контракте transform (cost_normalized=None, vat_basis_applied=False,
только cost_rub посчитан) — нормализация должна произойти в block1
(_direct_vat_multiplier/_open_duckdb_with_direct_vat), а не быть уже готовой
в фикстуре.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from src.compute import block1  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_block1.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


DEFAULTS = {
    "min_sample_visits": 500,
    "significance_alpha": 0.05,
}

MACRO_GOAL_ID = "999"


def _write_config(paths: _Paths, macro_goals: list[dict] | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    config = {"sources": {"direct": {"macro_goals": macro_goals if macro_goals is not None else []}}}
    paths.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")


def _write_client_answers(paths: _Paths, vat_included: bool | None) -> None:
    """inputs/client_answers.yaml: finance.vat_basis_by_source для source="direct"."""
    paths.inputs.mkdir(parents=True, exist_ok=True)
    data = {
        "finance": {
            "vat_basis_by_source": [
                {"source": "direct", "vat_included": vat_included, "evidence": "счёт"},
            ],
        },
    }
    (paths.inputs / "client_answers.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8",
    )


def _write_dated_parquet(path: Path, rows: list[dict], date_field: str = "date") -> None:
    """Явный pyarrow date32 для date_field (см. tests/test_block1.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if date_field in df.columns:
        idx = table.schema.get_field_index(date_field)
        date_array = pa.array(list(df[date_field]), type=pa.date32())
        table = table.set_column(idx, pa.field(date_field, pa.date32()), date_array)
    pq.write_table(table, path)


def _write_direct_queries(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "direct_queries.parquet", rows)


def _write_direct_placements(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "direct_placements.parquet")


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visits.parquet")


def _write_costs(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "costs.parquet", rows)


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _direct_query_row(**overrides) -> dict:
    """Строка direct_queries в реальном контракте transform (cost_normalized
    всегда null, vat_basis_applied всегда False — см. build_canonical.py)."""
    row = {
        "date": date(2026, 1, 1), "campaign_id": "1", "campaign_name": "c1",
        "ad_group_id": "1", "query": "аренда авто", "match_type": "KEYWORD",
        "device": "desktop", "cost_raw": 1_200_000, "cost_rub": 1.2,
        "cost_normalized": None, "vat_basis_applied": False,
        "clicks": 10, "impressions": 100, "conversions_all": 0,
        f"goal_conv_{MACRO_GOAL_ID}": 0,
    }
    row.update(overrides)
    return row


def _direct_placement_row(**overrides) -> dict:
    row = {
        "placement": "site.ru", "ad_network_type": "CONTENT", "campaign_id": "1",
        "cost_raw": 1_200_000, "cost_rub": 1.2, "cost_normalized": None,
        "vat_basis_applied": False, "clicks": 20, "conversions_all": 0,
    }
    row.update(overrides)
    return row


# ── A09 (direct_queries): нормализация cost_normalized = cost_rub * множитель ─

def test_a09_cost_normalized_computed_from_cost_rub_when_vat_included(tmp_path):
    """vat_included=true -> cost_normalized_rub = cost_rub / 1.2 (та же формула,

    что _apply_vat_to_rows уже применяет к costs.parquet в transform).
    """
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_client_answers(paths, vat_included=True)
    _write_direct_queries(paths, [
        _direct_query_row(cost_raw=12_000_000, cost_rub=12.0),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A09"})
    assert "a09" in artifacts
    rows = _read_metric(paths, "a09")
    phrase_row = next(r for r in rows if r["finding"] == "query_spend_vs_conversions")
    assert phrase_row["cost_normalized_rub"] == pytest.approx(10.0)  # 12.0 / 1.2


def test_a09_cost_normalized_computed_from_cost_rub_when_vat_excluded(tmp_path):
    """vat_included=false -> cost_normalized_rub = cost_rub (без деления на 1.2)."""
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_client_answers(paths, vat_included=False)
    _write_direct_queries(paths, [
        _direct_query_row(cost_raw=12_000_000, cost_rub=12.0),
    ])

    block1.run(paths, DEFAULTS, {"A09"})
    rows = _read_metric(paths, "a09")
    phrase_row = next(r for r in rows if r["finding"] == "query_spend_vs_conversions")
    assert phrase_row["cost_normalized_rub"] == pytest.approx(12.0)


def test_a09_vat_included_flip_changes_result_by_1_2x(tmp_path):
    """Смена vat_included true->false в фикстуре client_answers.yaml численно

    меняет результат ровно в 1.2 раза (÷1.2 или обратно) — прямое требование
    промта задачи FIX-block1-cost-normalization.
    """
    def _run(vat_included: bool) -> float:
        p = _Paths(tmp_path / str(vat_included))
        _write_config(p, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
        _write_client_answers(p, vat_included=vat_included)
        _write_direct_queries(p, [
            _direct_query_row(cost_raw=24_000_000, cost_rub=24.0),
        ])
        block1.run(p, DEFAULTS, {"A09"})
        rows = _read_metric(p, "a09")
        phrase_row = next(r for r in rows if r["finding"] == "query_spend_vs_conversions")
        return phrase_row["cost_normalized_rub"]

    cost_gross = _run(True)
    cost_net = _run(False)
    assert cost_net == pytest.approx(cost_gross * 1.2)


def test_a09_vat_basis_unknown_stays_null_without_client_answers(tmp_path):
    """Без inputs/client_answers.yaml (нет ответа на Q01) cost_normalized_rub

    остаётся null — деградация, а не молчаливая подмена cost_rub.
    """
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_direct_queries(paths, [_direct_query_row()])

    block1.run(paths, DEFAULTS, {"A09"})
    rows = _read_metric(paths, "a09")
    phrase_row = next(r for r in rows if r["finding"] == "query_spend_vs_conversions")
    assert phrase_row["cost_normalized_rub"] is None


# ── A15 (direct_placements): подмена работает не только для direct_queries ──

def test_a15_placements_cost_normalized_from_cost_rub(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_client_answers(paths, vat_included=True)
    # A15 требует "costs"/"visits" в canonical (гейт run()) — сама проверка их
    # не читает (см. src/compute/block1.py:_run_a15), но dispatcher её пропустит
    # без этих двух таблиц. Минимальные валидные строки, не влияющие на A15.
    _write_visits(paths, [{"device": "desktop", "source_group": "organic", "form_submit": False}])
    _write_costs(paths, [{
        "date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
        "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
        "cost_status": "gross", "clicks": 1, "impressions": 1,
    }])
    _write_direct_placements(paths, [
        _direct_placement_row(placement="big.ru", cost_raw=120_000_000,
                               cost_rub=120.0, clicks=50),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A15"})
    assert "a15" in artifacts
    rows = _read_metric(paths, "a15")
    ranking_row = next(r for r in rows if r["finding"] == "placement_ranking")
    assert ranking_row["cost_normalized_rub"] == pytest.approx(100.0)  # 120.0 / 1.2
