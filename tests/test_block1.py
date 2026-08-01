"""Тесты блока 1 compute (задача 5D): A01–A11 (экономика платной рекламы).

По каждой проверке — как минимум один сценарий находки, плюс отдельно:
CPA (A05), кампании с расходом и 0 чистых конверсий (A04), статзначимость
(A01, paid_vs_site_gap), неизвестная НДС-база (cost_normalized IS NULL —
деградация, не подмена cost_raw) и партия запросов match_type=NONE, которая
не должна искажать разрез "по фразе" (A09/A11).
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
import yaml

from src.compute import block1  # noqa: E402
from src.compute.candidates import build_analysis_candidates  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_block0.py)."""

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


def _write_config(
    paths: _Paths,
    macro_goals: list[dict] | None = None,
    client_geo: str | None = None,
    brand_terms: list[str] | None = None,
) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    config = {
        "sources": {
            "direct": {
                "macro_goals": macro_goals if macro_goals is not None else [],
            },
        },
    }
    if client_geo is not None:
        config["client"] = {"geo": client_geo}
    if brand_terms is not None:
        config["brand_terms"] = brand_terms
    paths.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")


def _write_dated_parquet(path: Path, rows: list[dict], date_field: str = "date") -> None:
    """Явный pyarrow date32 для date_field (см. tests/test_block0.py)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if date_field in df.columns:
        idx = table.schema.get_field_index(date_field)
        date_array = pa.array(list(df[date_field]), type=pa.date32())
        table = table.set_column(idx, pa.field(date_field, pa.date32()), date_array)
    pq.write_table(table, path)


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visits.parquet")


def _write_costs(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "costs.parquet", rows)


def _write_campaign_strategies(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "campaign_strategies.parquet")


def _write_direct_campaigns(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "direct_campaigns.parquet", rows)


def _write_direct_queries(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "direct_queries.parquet", rows)


def _write_direct_geo(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "direct_geo.parquet")


def _write_direct_placements(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "direct_placements.parquet")


def _write_ad_texts(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "ad_texts.parquet")


def _write_seo_queries(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "seo_queries.parquet")


def _base_visit(**overrides) -> dict:
    row = {"device": "desktop", "source_group": "organic", "form_submit": False}
    row.update(overrides)
    return row


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        assert row["candidate"] is (row["row_role"] == "candidate")
        assert row["candidate_reason"]
        assert isinstance(row["context_refs"], list)
        assert row["row_ref"].startswith(f"{name}:")
    return rows


def test_a_candidate_contract_coverage_is_complete(tmp_path):
    metrics_dir = tmp_path / "metrics"
    block1._write_metric_artifact(metrics_dir, "a24", [
        {"check_id": "A24", "finding": "manual_verification_required",
         "reason": "нужна ручная сверка", "confidence": "LOW"},
        {"check_id": "A24", "finding": "manual_check_candidate",
         "ad_id": "1", "confidence": "LOW"},
    ], confidence_cap="LOW")

    result = build_analysis_candidates(metrics_dir)

    assert result["coverage"]["contract_coverage"] == 1.0
    assert result["coverage"]["candidate_rows_declared"] == 1
    assert result["coverage"]["context_refs_resolved"] == 1


def _write_degradation(paths: _Paths, checks: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps({"checks": checks}), encoding="utf-8"
    )


# ── A01 — paid_vs_site_gap (статзначимость) + campaign_strategy_mismatch ───

def test_a01_paid_vs_site_gap_significant(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = (
        [_base_visit(source_group="organic", form_submit=True) for _ in range(400)]
        + [_base_visit(source_group="organic", form_submit=False) for _ in range(600)]
        + [_base_visit(source_group="ad", form_submit=True) for _ in range(50)]
        + [_base_visit(source_group="ad", form_submit=False) for _ in range(550)]
    )
    _write_visits(paths, visits)

    artifacts = block1.run(paths, DEFAULTS, {"A01"})
    assert "a01" in artifacts

    rows = _read_metric(paths, "a01")
    gap_row = next(r for r in rows if r["finding"] == "paid_vs_site_gap")
    assert gap_row["ad_visits"] == 600
    assert gap_row["gap_pp"] > 0.03
    assert gap_row["paid_underperforms"] is True
    assert gap_row["p_value"] < 0.05


def test_a01_paid_vs_site_gap_not_significant_small_sample(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = (
        [_base_visit(source_group="organic", form_submit=True) for _ in range(40)]
        + [_base_visit(source_group="organic", form_submit=False) for _ in range(60)]
        + [_base_visit(source_group="ad", form_submit=True) for _ in range(2)]
        + [_base_visit(source_group="ad", form_submit=False) for _ in range(8)]
    )
    _write_visits(paths, visits)

    block1.run(paths, DEFAULTS, {"A01"})
    rows = _read_metric(paths, "a01")
    gap_row = next(r for r in rows if r["finding"] == "paid_vs_site_gap")
    # Мало данных (ad_visits < min_sample_visits) -> не объявляем находкой.
    assert gap_row["paid_underperforms"] is False
    assert gap_row["confidence"] == "MED"


def test_a01_campaign_strategy_mismatch_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit(form_submit=True) for _ in range(5)])
    _write_campaign_strategies(paths, [
        {"campaign_id": "1", "campaign_name": "clicks-campaign", "strategy_type": "AVERAGE_CPC",
         "optimize_for": "clicks"},
        {"campaign_id": "2", "campaign_name": "cpa-campaign", "strategy_type": "AVERAGE_CPA",
         "optimize_for": "conversions"},
    ])

    block1.run(paths, DEFAULTS, {"A01"})
    rows = _read_metric(paths, "a01")
    mismatches = [r for r in rows if r["finding"] == "campaign_strategy_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["campaign_id"] == "1"
    assert mismatches[0]["suspect_wrong_objective"] is True


# ── A02 — "максимум кликов" при стабильной конверсионной цели ──────────────

def test_a02_clicks_strategy_with_stable_goal(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit()])
    _write_campaign_strategies(paths, [
        {"campaign_id": "1", "campaign_name": "clicks-campaign", "strategy_type": "AVERAGE_CPC",
         "optimize_for": "clicks"},
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 1, 1), "campaign_id": "1", "campaign_name": "clicks-campaign",
         "device": "desktop", "cost_raw": 1_000_000, "cost_rub": 1.0,
         "cost_normalized": 1.0, "vat_basis_applied": True,
         "clicks": 10, "impressions": 100, "conversions_all": 20,
         f"goal_conv_{MACRO_GOAL_ID}": 8},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A02"})
    assert "a02" in artifacts
    rows = _read_metric(paths, "a02")
    assert rows[0]["net_conversions"] == 8
    assert rows[0]["clicks_strategy_despite_stable_goal"] is True


def test_a02_unavailable_without_macro_goals(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[])
    _write_visits(paths, [_base_visit()])
    _write_campaign_strategies(paths, [
        {"campaign_id": "1", "campaign_name": "c1", "strategy_type": "AVERAGE_CPC",
         "optimize_for": "clicks"},
    ])

    block1.run(paths, DEFAULTS, {"A02"})
    rows = _read_metric(paths, "a02")
    assert rows[0]["status"] == "unavailable"


# ── A03 — сверка автостратегии с D01/D03 ────────────────────────────────────

def test_a03_unavailable_without_block0_artifacts(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit()])

    block1.run(paths, DEFAULTS, {"A03"})
    rows = _read_metric(paths, "a03")
    assert rows[0]["status"] == "unavailable"


def test_a03_flags_auto_strategy_at_risk_from_d01_overtrigger(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit()])
    _write_campaign_strategies(paths, [
        {"campaign_id": "1", "campaign_name": "auto", "strategy_type": "AVERAGE_CPA",
         "optimize_for": "conversions"},
    ])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "d01.json").write_text(
        json.dumps([{"goal_group": "form_submit", "overtrigger": True}]), encoding="utf-8"
    )
    (paths.metrics / "d03.json").write_text(
        json.dumps([{"finding": "goal_mix_summary", "has_overlap": False,
                      "has_uncategorized": False}]),
        encoding="utf-8",
    )

    block1.run(paths, DEFAULTS, {"A03"})
    rows = _read_metric(paths, "a03")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["d01_has_overtrigger"] is True
    assert summary["auto_strategy_at_risk"] is True
    campaign_row = next(r for r in rows if r["finding"] == "auto_strategy_campaign")
    assert campaign_row["at_risk_of_contaminated_signal"] is True


# ── A04 — расход есть, чистых конверсий нет ─────────────────────────────────

def test_a04_zero_conversion_campaign_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit()])
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "burns-budget", "cost_raw": 5000.0, "cost_normalized": 5000.0,
         "cost_status": "net", "clicks": 100, "impressions": 1000},
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "2",
         "campaign_name": "converts-fine", "cost_raw": 3000.0, "cost_normalized": 3000.0,
         "cost_status": "net", "clicks": 80, "impressions": 800},
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 1, 1), "campaign_id": "1", "campaign_name": "burns-budget",
         "device": "desktop", "cost_raw": 5_000_000_000, "cost_rub": 5000.0,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 100, "impressions": 1000, "conversions_all": 0,
         f"goal_conv_{MACRO_GOAL_ID}": 0},
        {"date": date(2026, 1, 1), "campaign_id": "2", "campaign_name": "converts-fine",
         "device": "desktop", "cost_raw": 3_000_000_000, "cost_rub": 3000.0,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 80, "impressions": 800, "conversions_all": 10,
         f"goal_conv_{MACRO_GOAL_ID}": 6},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A04"})
    assert "a04" in artifacts
    rows = _read_metric(paths, "a04")
    burns = next(r for r in rows if r["campaign_id"] == "1")
    converts = next(r for r in rows if r["campaign_id"] == "2")
    assert burns["net_conversions"] == 0
    assert burns["zero_conversion_campaign"] is True
    assert converts["zero_conversion_campaign"] is False


def test_a04_unknown_vat_base_degrades_not_substitutes(tmp_path):
    """cost_normalized IS NULL (vat_basis_unknown) -> сумма null, а не cost_raw."""
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit()])
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "unknown-vat", "cost_raw": 4000.0, "cost_normalized": None,
         "cost_status": "vat_basis_unknown", "clicks": 50, "impressions": 500},
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 1, 1), "campaign_id": "1", "campaign_name": "unknown-vat",
         "device": "desktop", "cost_raw": 4_000_000_000, "cost_rub": 4000.0,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 50, "impressions": 500, "conversions_all": 0,
         f"goal_conv_{MACRO_GOAL_ID}": 0},
    ])

    block1.run(paths, DEFAULTS, {"A04"})
    rows = _read_metric(paths, "a04")
    assert rows[0]["cost_normalized_rub"] is None
    # cost is null -> zero_conversion_campaign не может утверждаться (spend неизвестен).
    assert rows[0]["zero_conversion_campaign"] is False


# ── A05 — CPA устойчиво хуже сопоставимых кампаний ──────────────────────────

def test_a05_cpa_outlier_detected(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit()])
    campaigns = [("1", 1000.0, 10), ("2", 1100.0, 11), ("3", 900.0, 9), ("4", 9000.0, 6)]
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": cid,
         "campaign_name": f"c{cid}", "cost_raw": cost, "cost_normalized": cost,
         "cost_status": "net", "clicks": 50, "impressions": 500}
        for cid, cost, _conv in campaigns
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 1, 1), "campaign_id": cid, "campaign_name": f"c{cid}",
         "device": "desktop", "cost_raw": int(cost * 1_000_000), "cost_rub": cost,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 50, "impressions": 500, "conversions_all": conv,
         f"goal_conv_{MACRO_GOAL_ID}": conv}
        for cid, cost, conv in campaigns
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A05"})
    assert "a05" in artifacts
    rows = _read_metric(paths, "a05")
    outlier = next(r for r in rows if r["campaign_id"] == "4")
    normal = next(r for r in rows if r["campaign_id"] == "1")
    assert outlier["cpa_persistently_worse"] is True
    assert normal["cpa_persistently_worse"] is False


# ── A06 — бюджет распределён не по эффективности ────────────────────────────

def test_a06_budget_misallocation_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit()])
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "big-weak",
         "campaign_name": "big-weak", "cost_raw": 9000.0, "cost_normalized": 9000.0,
         "cost_status": "net", "clicks": 200, "impressions": 2000},
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "small-strong",
         "campaign_name": "small-strong", "cost_raw": 1000.0, "cost_normalized": 1000.0,
         "cost_status": "net", "clicks": 50, "impressions": 500},
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 1, 1), "campaign_id": "big-weak", "campaign_name": "big-weak",
         "device": "desktop", "cost_raw": 9_000_000_000, "cost_rub": 9000.0,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 200, "impressions": 2000, "conversions_all": 2,
         f"goal_conv_{MACRO_GOAL_ID}": 1},
        {"date": date(2026, 1, 1), "campaign_id": "small-strong", "campaign_name": "small-strong",
         "device": "desktop", "cost_raw": 1_000_000_000, "cost_rub": 1000.0,
         "cost_normalized": None, "vat_basis_applied": False,
         "clicks": 50, "impressions": 500, "conversions_all": 20,
         f"goal_conv_{MACRO_GOAL_ID}": 19},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A06"})
    assert "a06" in artifacts
    rows = _read_metric(paths, "a06")
    big_weak = next(r for r in rows if r["campaign_id"] == "big-weak")
    small_strong = next(r for r in rows if r["campaign_id"] == "small-strong")
    assert big_weak["budget_misallocated"] is True
    assert small_strong["budget_misallocated"] is False


# ── A07 — всегда unavailable (нет LostImpressionShare в canonical) ─────────

def test_a07_always_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": 10, "impressions": 100},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A07"})
    assert "a07" in artifacts
    rows = _read_metric(paths, "a07")
    assert rows[0]["status"] == "unavailable"


# ── A08 — раздробленная структура кампаний ──────────────────────────────────

def test_a08_fragmented_structure_detected(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit()])
    rows_in = []
    # Одна крупная кампания (95% бюджета) + 5 мелких (по 1% каждая).
    rows_in.append({"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "big",
                     "campaign_name": "big", "cost_raw": 9500.0, "cost_normalized": 9500.0,
                     "cost_status": "net", "clicks": 500, "impressions": 5000})
    for i in range(5):
        rows_in.append({"date": date(2026, 1, 1), "source_tag": "direct",
                         "campaign_id": f"small{i}", "campaign_name": f"small{i}",
                         "cost_raw": 100.0, "cost_normalized": 100.0, "cost_status": "net",
                         "clicks": 5, "impressions": 50})
    _write_costs(paths, rows_in)

    artifacts = block1.run(paths, DEFAULTS, {"A08"})
    assert "a08" in artifacts
    rows = _read_metric(paths, "a08")
    assert rows[0]["total_campaigns"] == 6
    assert rows[0]["fragmented_structure"] is True


def test_a08_insufficient_campaigns_for_check(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit()])
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": 5, "impressions": 50},
    ])

    block1.run(paths, DEFAULTS, {"A08"})
    rows = _read_metric(paths, "a08")
    assert rows[0]["insufficient_campaigns_for_check"] is True


# ── A09 — нецелевые запросы (расход без чистых конверсий по фразе) ─────────

def _direct_query_row(**overrides) -> dict:
    row = {
        "date": date(2026, 1, 1), "campaign_id": "1", "campaign_name": "c1",
        "ad_group_id": "1", "query": "аренда авто", "match_type": "KEYWORD",
        "device": "desktop", "cost_raw": 1_000_000, "cost_rub": 1.0,
        "cost_normalized": 1.0, "vat_basis_applied": True,
        "clicks": 10, "impressions": 100, "conversions_all": 0,
        f"goal_conv_{MACRO_GOAL_ID}": 0,
    }
    row.update(overrides)
    return row


def test_a09_flags_query_with_no_net_conversions(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_direct_queries(paths, [
        _direct_query_row(query="дешёвый прокат", match_type="KEYWORD",
                           cost_normalized=500.0, **{f"goal_conv_{MACRO_GOAL_ID}": 0}),
        _direct_query_row(query="аренда авто спб", match_type="SYNONYM",
                           cost_normalized=300.0, **{f"goal_conv_{MACRO_GOAL_ID}": 3}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A09"})
    assert "a09" in artifacts
    rows = _read_metric(paths, "a09")
    no_conv = next(r for r in rows if r["query"] == "дешёвый прокат")
    converts = next(r for r in rows if r["query"] == "аренда авто спб")
    assert no_conv["no_net_conversions"] is True
    assert converts["no_net_conversions"] is False


def test_a09_none_match_type_does_not_skew_phrase_aggregate(tmp_path):
    """match_type=NONE не попадает в разрез "по фразе" и не искажает его."""
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_direct_queries(paths, [
        _direct_query_row(query="аренда авто", match_type="KEYWORD",
                           cost_normalized=200.0, **{f"goal_conv_{MACRO_GOAL_ID}": 5}),
    ] + [
        _direct_query_row(query="аренда авто", match_type="NONE",
                           cost_normalized=50.0, **{f"goal_conv_{MACRO_GOAL_ID}": 0})
        for _ in range(20)
    ])

    block1.run(paths, DEFAULTS, {"A09"})
    rows = _read_metric(paths, "a09")
    phrase_rows = [r for r in rows if r["finding"] == "query_spend_vs_conversions"]
    # Ровно одна строка "по фразе" для KEYWORD — NONE не создал вторую/не склеился.
    assert len(phrase_rows) == 1
    assert phrase_rows[0]["match_type"] == "KEYWORD"
    assert phrase_rows[0]["no_net_conversions"] is False

    none_summary = next(r for r in rows if r["finding"] == "outside_named_phrases")
    assert none_summary["row_count"] == 20
    assert none_summary["cost_normalized_rub"] == 1000.0


def test_a09_unavailable_without_macro_goals(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[])
    _write_direct_queries(paths, [_direct_query_row()])

    block1.run(paths, DEFAULTS, {"A09"})
    rows = _read_metric(paths, "a09")
    assert rows[0]["status"] == "unavailable"


def test_a09_unknown_vat_base_degrades_not_substitutes(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_direct_queries(paths, [
        _direct_query_row(query="без ндс-базы", match_type="KEYWORD", cost_normalized=None),
    ])

    block1.run(paths, DEFAULTS, {"A09"})
    rows = _read_metric(paths, "a09")
    phrase_row = next(r for r in rows if r["finding"] == "query_spend_vs_conversions")
    assert phrase_row["cost_normalized_rub"] is None
    assert phrase_row["no_net_conversions"] is False


# ── A10 — не хватает минус-слов (повтор по месяцам) ─────────────────────────

def test_a10_recurring_zero_conversion_query_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    rows_in = []
    for month_day in (date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)):
        rows_in.append(_direct_query_row(
            date=month_day, query="мусорный запрос", match_type="KEYWORD",
            cost_normalized=100.0, **{f"goal_conv_{MACRO_GOAL_ID}": 0},
        ))
    rows_in.append(_direct_query_row(
        date=date(2026, 1, 15), query="хороший запрос", match_type="KEYWORD",
        cost_normalized=100.0, **{f"goal_conv_{MACRO_GOAL_ID}": 5},
    ))
    _write_direct_queries(paths, rows_in)

    artifacts = block1.run(paths, DEFAULTS, {"A10"})
    assert "a10" in artifacts
    rows = _read_metric(paths, "a10")
    junk = next(r for r in rows if r["query"] == "мусорный запрос")
    assert junk["recurring_months_count"] == 3
    assert junk["missing_negative_keyword_candidate"] is True
    assert all(r["query"] != "хороший запрос" for r in rows)


# ── A11 — автотаргетинг/широкие соответствия размывают семантику ───────────

def test_a11_none_match_type_worse_cpa_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit(form_submit=True) for _ in range(5)]
                  + [_base_visit() for _ in range(15)])
    _write_direct_queries(paths, [
        _direct_query_row(query="q1", match_type="KEYWORD", cost_normalized=1000.0,
                           **{f"goal_conv_{MACRO_GOAL_ID}": 10}),
        _direct_query_row(query="q2", match_type="NONE", cost_normalized=900.0,
                           **{f"goal_conv_{MACRO_GOAL_ID}": 0}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A11"})
    assert "a11" in artifacts
    rows = _read_metric(paths, "a11")
    none_row = next(r for r in rows if r["match_type"] == "NONE")
    keyword_row = next(r for r in rows if r["match_type"] == "KEYWORD")
    assert none_row["match_type_dilutes_semantics"] is True
    assert keyword_row["match_type_dilutes_semantics"] is False
    assert none_row["site_form_submit_rate_context"] == 0.25


# ── confidence_cap — compute капает вниз, никогда не поднимает ─────────────

def test_confidence_is_capped_to_degradation_report_ceiling(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_visits(paths, [_base_visit(form_submit=True) for _ in range(600)]
                  + [_base_visit() for _ in range(400)])
    _write_degradation(paths, [{"check_id": "A01", "confidence_cap": "LOW"}])

    block1.run(paths, DEFAULTS, {"A01"})
    rows = _read_metric(paths, "a01")
    gap_row = next(r for r in rows if r["finding"] == "paid_vs_site_gap")
    assert gap_row["confidence"] == "LOW"


# ═════════════════════════ A12–A26 (задача 5E) ═════════════════════════════

def _direct_geo_row(**overrides) -> dict:
    row = {
        "date": date(2026, 1, 1), "month": "2026-01",
        "campaign_id": "1", "campaign_name": "c1",
        "location_of_presence_id": "1", "location_of_presence_name": "Москва",
        "device": "desktop", "cost_raw": 1_000_000, "cost_rub": 1.0,
        "cost_normalized": 1.0, "vat_basis_applied": True,
        "clicks": 10, "impressions": 100, "conversions_all": 0,
        f"goal_conv_{MACRO_GOAL_ID}": 0,
    }
    row.update(overrides)
    return row


def _direct_campaign_row(**overrides) -> dict:
    row = {
        "date": date(2026, 1, 5), "campaign_id": "1", "campaign_name": "c1",
        "device": "desktop", "cost_raw": 1_000_000, "cost_rub": 1.0,
        "cost_normalized": 1.0, "vat_basis_applied": True,
        "clicks": 10, "impressions": 100, "conversions_all": 0,
        f"goal_conv_{MACRO_GOAL_ID}": 0,
    }
    row.update(overrides)
    return row


def _direct_placement_row(**overrides) -> dict:
    row = {
        "placement": "site.ru", "ad_network_type": "YANDEX_SEARCH_PARTNER",
        "campaign_id": "1", "cost_raw": 1_000_000, "cost_rub": 1.0,
        "cost_normalized": 1.0, "vat_basis_applied": True,
        "clicks": 10, "conversions_all": 0,
    }
    row.update(overrides)
    return row


def _write_min_costs(paths: _Paths) -> None:
    """Минимальная строка costs.parquet — только чтобы удовлетворить грубый

    gate диспетчера (requires: costs), сами данные проверкой не читаются.
    """
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": 10, "impressions": 100},
    ])


def _write_min_visits(paths: _Paths) -> None:
    """Минимальная строка visits.parquet — только чтобы удовлетворить грубый

    gate диспетчера (requires: visits) там, где сама проверка visits не читает.
    """
    _write_visits(paths, [_base_visit()])


def _ad_text_row(**overrides) -> dict:
    row = {
        "ad_id": "1", "campaign_id": "1", "ad_group_id": "1", "type": "TEXT_AD",
        "state": "ON", "status": "ACCEPTED", "title": "Заголовок", "title2": None,
        "text": "Описание объявления", "href": "https://example.com/",
        "display_url_path": "",
    }
    row.update(overrides)
    return row


# ── A12 — гео (обязательная последовательность: CPA нецелевого vs целевого) ─

def test_a12_off_target_region_flagged_and_incomplete_rows_excluded(tmp_path):
    """Заодно покрывает "неполные targeting fields": строка с пустым

    location_of_presence_name не должна попадать ни в summary, ни в детали,
    не должна ронять проверку.
    """
    paths = _Paths(tmp_path)
    _write_config(
        paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}], client_geo="Москва",
    )
    _write_min_costs(paths)
    _write_min_visits(paths)
    _write_direct_geo(paths, [
        _direct_geo_row(location_of_presence_name=None, cost_normalized=500.0, clicks=50,
                         **{f"goal_conv_{MACRO_GOAL_ID}": 3}),
        _direct_geo_row(location_of_presence_name="Москва", cost_normalized=1000.0, clicks=100,
                         **{f"goal_conv_{MACRO_GOAL_ID}": 20}),
        _direct_geo_row(location_of_presence_name="Владивосток", cost_normalized=900.0, clicks=90,
                         **{f"goal_conv_{MACRO_GOAL_ID}": 6}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A12"})
    assert "a12" in artifacts
    rows = _read_metric(paths, "a12")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["target_regions_matched"] == ["Москва"]
    assert summary["target_region_net_conversions"] == 20
    vladivostok = next(r for r in rows if r.get("location_of_presence_name") == "Владивосток")
    assert vladivostok["off_target_geo_worse"] is True


def test_a12_unavailable_without_target_region(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_min_costs(paths)
    _write_min_visits(paths)
    _write_direct_geo(paths, [_direct_geo_row()])

    block1.run(paths, DEFAULTS, {"A12"})
    rows = _read_metric(paths, "a12")
    assert rows[0]["status"] == "unavailable"


def test_a12_confidence_capped_by_degradation_report(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(
        paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}], client_geo="Москва",
    )
    _write_min_costs(paths)
    _write_min_visits(paths)
    _write_direct_geo(paths, [
        _direct_geo_row(location_of_presence_name="Москва", cost_normalized=1000.0, clicks=100,
                         **{f"goal_conv_{MACRO_GOAL_ID}": 20}),
    ])
    _write_degradation(paths, [{"check_id": "A12", "confidence_cap": "LOW"}])

    block1.run(paths, DEFAULTS, {"A12"})
    rows = _read_metric(paths, "a12")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["confidence"] == "LOW"


# ── A13 — день недели/час показа ────────────────────────────────────────────

def test_a13_weekday_outlier_flagged_and_hour_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_min_costs(paths)
    _write_min_visits(paths)
    rows_in = []
    for day in (5, 12, 19, 26):
        rows_in.append(_direct_campaign_row(
            date=date(2026, 1, day), cost_normalized=100.0,
            **{f"goal_conv_{MACRO_GOAL_ID}": 10},
        ))
    for day in (7, 14, 21, 28):
        rows_in.append(_direct_campaign_row(
            date=date(2026, 1, day), cost_normalized=100.0,
            **{f"goal_conv_{MACRO_GOAL_ID}": 2},
        ))
    _write_direct_campaigns(paths, rows_in)

    artifacts = block1.run(paths, DEFAULTS, {"A13"})
    assert "a13" in artifacts
    rows = _read_metric(paths, "a13")
    hour_row = next(r for r in rows if r["finding"] == "hour_of_day_unavailable")
    assert hour_row["confidence"] == "LOW"
    weekday_rows = [r for r in rows if r["finding"] == "weekday_economics"]
    worse = next(r for r in weekday_rows if r["weekday_persistently_worse"])
    better = next(r for r in weekday_rows if not r["weekday_persistently_worse"])
    assert worse["cpa_rub"] > better["cpa_rub"]


def test_a13_unavailable_without_direct_campaigns(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_min_costs(paths)
    _write_min_visits(paths)

    block1.run(paths, DEFAULTS, {"A13"})
    rows = _read_metric(paths, "a13")
    assert rows[0]["status"] == "unavailable"


# ── A14 — устройства ─────────────────────────────────────────────────────────

def test_a14_device_cr_and_cpa(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_min_costs(paths)
    visits = (
        [_base_visit(source_group="ad", device="desktop", form_submit=True) for _ in range(200)]
        + [_base_visit(source_group="ad", device="desktop", form_submit=False) for _ in range(300)]
        + [_base_visit(source_group="ad", device="mobile", form_submit=True) for _ in range(50)]
        + [_base_visit(source_group="ad", device="mobile", form_submit=False) for _ in range(450)]
    )
    _write_visits(paths, visits)
    _write_direct_campaigns(paths, [
        _direct_campaign_row(campaign_id="1", device="desktop", cost_normalized=1000.0,
                              **{f"goal_conv_{MACRO_GOAL_ID}": 20}),
        _direct_campaign_row(campaign_id="2", device="mobile", cost_normalized=1000.0,
                              **{f"goal_conv_{MACRO_GOAL_ID}": 5}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A14"})
    assert "a14" in artifacts
    rows = _read_metric(paths, "a14")
    cr_rows = [r for r in rows if r["finding"] == "cr_by_device"]
    desktop_cr = next(r for r in cr_rows if r["device"] == "desktop")
    mobile_cr = next(r for r in cr_rows if r["device"] == "mobile")
    assert desktop_cr["device_cr_worse_than_overall"] is False
    assert mobile_cr["device_cr_worse_than_overall"] is True
    cpa_rows = [r for r in rows if r["finding"] == "cpa_by_device"]
    mobile_cpa = next(r for r in cpa_rows if r["device"] == "mobile")
    assert mobile_cpa["device_cpa_persistently_worse"] is True


def test_a14_cpa_by_device_unavailable_without_macro_goals(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[])
    _write_min_costs(paths)
    _write_visits(paths, [_base_visit(source_group="ad")])

    block1.run(paths, DEFAULTS, {"A14"})
    rows = _read_metric(paths, "a14")
    assert any(r["finding"] == "cpa_by_device_unavailable" for r in rows)


# ── A15 — площадки РСЯ ───────────────────────────────────────────────────────

def test_a15_placement_ranking_and_net_conversions_unavailable_marker(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_min_costs(paths)
    _write_min_visits(paths)
    _write_direct_placements(paths, [
        _direct_placement_row(placement="big-spender.ru", cost_normalized=900.0, clicks=200),
        _direct_placement_row(placement="tiny.ru", cost_normalized=5.0, clicks=2),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A15"})
    assert "a15" in artifacts
    rows = _read_metric(paths, "a15")
    marker = next(r for r in rows if r["finding"] == "net_conversions_unavailable")
    assert marker["confidence"] == "LOW"
    ranked = [r for r in rows if r["finding"] == "placement_ranking"]
    assert all(r["placement"] != "tiny.ru" for r in ranked)
    big = next(r for r in ranked if r["placement"] == "big-spender.ru")
    assert big["notable_spend_share"] is True


# ── A16 — ретаргетинг: всегда unavailable (см. докстринг модуля) ───────────

def test_a16_always_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": 10, "impressions": 100},
    ])
    _write_visits(paths, [_base_visit()])

    artifacts = block1.run(paths, DEFAULTS, {"A16"})
    assert "a16" in artifacts
    rows = _read_metric(paths, "a16")
    assert rows[0]["status"] == "unavailable"


# ── A17 — брендовая каннибализация ──────────────────────────────────────────

def test_a17_brand_cannibalization_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(
        paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}],
        brand_terms=["покажи бренд"],
    )
    _write_direct_queries(paths, [
        _direct_query_row(query="покажи бренд аренда", match_type="KEYWORD",
                           cost_normalized=500.0, clicks=50,
                           **{f"goal_conv_{MACRO_GOAL_ID}": 10}),
    ])
    _write_seo_queries(paths, [
        {"query": "покажи бренд аренда", "page": "/", "source": "gsc", "month": "2026-01",
         "device": "unknown", "total_shows": 1000, "total_clicks": 400,
         "avg_show_position": 1.5, "is_brand": True, "source_mode": "api",
         "completeness": "verified", "ctr": None, "demand": None},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A17"})
    assert "a17" in artifacts
    rows = _read_metric(paths, "a17")
    detail = next(r for r in rows if r["finding"] == "brand_query_paid_vs_organic")
    assert detail["organic_already_visible"] is True
    assert detail["possible_cannibalization"] is True
    assert any(r["finding"] == "competitor_ads_not_checked" for r in rows)


def test_a17_unavailable_without_brand_terms(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_direct_queries(paths, [_direct_query_row()])

    block1.run(paths, DEFAULTS, {"A17"})
    rows = _read_metric(paths, "a17")
    assert rows[0]["status"] == "unavailable"


# ── A18 — кампании конкурируют за одинаковый спрос ──────────────────────────

def test_a18_competing_campaigns_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_direct_queries(paths, [
        _direct_query_row(query="аренда авто", campaign_id="1", clicks=20, cost_normalized=200.0),
        _direct_query_row(query="аренда авто", campaign_id="2", clicks=15, cost_normalized=150.0),
        _direct_query_row(query="уникальный запрос", campaign_id="1", clicks=5, cost_normalized=50.0),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A18"})
    assert "a18" in artifacts
    rows = _read_metric(paths, "a18")
    competing = next(r for r in rows if r["query"] == "аренда авто")
    assert competing["campaign_count"] == 2
    assert all(r["query"] != "уникальный запрос" for r in rows)


# ── A19 — CPC аномально высок ────────────────────────────────────────────────

def test_a19_cpc_outlier_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_min_costs(paths)
    _write_direct_queries(paths, [
        _direct_query_row(query="q1", match_type="KEYWORD", cost_normalized=500.0, clicks=25),
        _direct_query_row(query="q2", match_type="KEYWORD", cost_normalized=100.0, clicks=25),
        _direct_query_row(query="q3", match_type="SYNONYM", cost_normalized=3000.0, clicks=30),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A19"})
    assert "a19" in artifacts
    rows = _read_metric(paths, "a19")
    q3 = next(r for r in rows if r["query"] == "q3")
    q2 = next(r for r in rows if r["query"] == "q2")
    assert q3["cpc_anomalously_high"] is True
    assert q2["cpc_anomalously_high"] is False


# ── A20 — низкий CTR у релевантных показов ──────────────────────────────────

def test_a20_low_ctr_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_direct_queries(paths, [
        _direct_query_row(query="q1", match_type="KEYWORD", clicks=50, impressions=500),
        _direct_query_row(query="q2", match_type="KEYWORD", clicks=45, impressions=500),
        _direct_query_row(query="q3", match_type="SYNONYM", clicks=5, impressions=500),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A20"})
    assert "a20" in artifacts
    rows = _read_metric(paths, "a20")
    q3 = next(r for r in rows if r["query"] == "q3")
    q1 = next(r for r in rows if r["query"] == "q1")
    assert q3["anomalously_low_ctr"] is True
    assert q1["anomalously_low_ctr"] is False


# ── A21 — высокий CTR + низкая конверсия ────────────────────────────────────

def test_a21_high_ctr_low_conversion_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_visits(paths, [_base_visit(form_submit=True) for _ in range(10)]
                  + [_base_visit() for _ in range(90)])
    _write_direct_queries(paths, [
        _direct_query_row(query="q1", match_type="KEYWORD", clicks=10, impressions=1000,
                           **{f"goal_conv_{MACRO_GOAL_ID}": 0}),
        _direct_query_row(query="q2", match_type="KEYWORD", clicks=90, impressions=1000,
                           **{f"goal_conv_{MACRO_GOAL_ID}": 5}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A21"})
    assert "a21" in artifacts
    rows = _read_metric(paths, "a21")
    q2 = next(r for r in rows if r["query"] == "q2")
    q1 = next(r for r in rows if r["query"] == "q1")
    assert q2["high_ctr"] is True
    assert q2["high_ctr_low_conversion"] is False
    assert q1["high_ctr"] is False


# ── A22 — запрос vs текст объявления (ad_texts БЕЗ archived-файла) ──────────

def test_a22_query_ad_keyword_mismatch_and_no_archived_file_needed(tmp_path):
    """Явно проверяет требование задачи: A20–A24 не читают

    ad_texts_archived.parquet — файл вообще отсутствует, проверка не падает.
    """
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_min_visits(paths)
    _write_ad_texts(paths, [
        _ad_text_row(campaign_id="1", ad_group_id="1", title="Прокат велосипедов",
                     text="Аренда велосипедов в городе"),
    ])
    _write_direct_queries(paths, [
        _direct_query_row(campaign_id="1", ad_group_id="1", query="ремонт холодильников",
                           match_type="KEYWORD", clicks=50),
    ])
    assert not (paths.canonical / "ad_texts_archived.parquet").exists()

    artifacts = block1.run(paths, DEFAULTS, {"A22"})
    assert "a22" in artifacts
    rows = _read_metric(paths, "a22")
    assert rows[0]["top_query"] == "ремонт холодильников"
    assert rows[0]["query_ad_keyword_mismatch"] is True


def test_a22_unavailable_without_ad_texts(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_min_visits(paths)
    _write_direct_queries(paths, [_direct_query_row()])

    block1.run(paths, DEFAULTS, {"A22"})
    rows = _read_metric(paths, "a22")
    assert rows[0]["status"] == "unavailable"


# ── A23 — конкретный спрос ведён на слишком общую страницу ─────────────────

def test_a23_generic_landing_underperforms(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = (
        [_base_visit(source_group="ad", entry_page="/", form_submit=True) for _ in range(30)]
        + [_base_visit(source_group="ad", entry_page="/", form_submit=False) for _ in range(570)]
        + [_base_visit(source_group="ad", entry_page="/uslugi/remont-holodilnikov", form_submit=True)
           for _ in range(150)]
        + [_base_visit(source_group="ad", entry_page="/uslugi/remont-holodilnikov", form_submit=False)
           for _ in range(450)]
    )
    _write_visits(paths, visits)

    artifacts = block1.run(paths, DEFAULTS, {"A23"})
    assert "a23" in artifacts
    rows = _read_metric(paths, "a23")
    assert rows[0]["generic_landing_underperforms"] is True
    assert rows[0]["confidence"] == "HIGH"


# ── A24 — устаревшая цена/акция (только кандидаты для ручной проверки) ─────

def test_a24_manual_check_candidates_detected(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_direct_queries(paths, [_direct_query_row()])
    _write_ad_texts(paths, [
        _ad_text_row(ad_id="1", title="Скидка 20%", text="Только сегодня"),
        _ad_text_row(ad_id="2", title="Надёжный сервис", text="Работаем без выходных"),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A24"})
    assert "a24" in artifacts
    rows = _read_metric(paths, "a24")
    candidates = [r for r in rows if r["finding"] == "manual_check_candidate"]
    assert len(candidates) == 1
    assert candidates[0]["ad_id"] == "1"
    assert candidates[0]["has_price_pattern"] or candidates[0]["has_promo_word"]


# ── A25 — товарный фид: всегда unavailable (продукт-фид нет в canonical) ───

def test_a25_always_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_costs(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": 10, "impressions": 100},
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A25"})
    assert "a25" in artifacts
    rows = _read_metric(paths, "a25")
    assert rows[0]["status"] == "unavailable"


# ── A26 — оценка без учёта лага/сезонности/малого объёма ────────────────────

def test_a26_insufficient_sample_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": MACRO_GOAL_ID, "name": "Заявка"}])
    _write_min_costs(paths)
    _write_min_visits(paths)
    _write_direct_campaigns(paths, [
        _direct_campaign_row(campaign_id="new", date=date(2026, 1, 5), cost_normalized=100.0,
                              **{f"goal_conv_{MACRO_GOAL_ID}": 1}),
        _direct_campaign_row(campaign_id="established", date=date(2026, 1, 5), cost_normalized=100.0,
                              **{f"goal_conv_{MACRO_GOAL_ID}": 10}),
        _direct_campaign_row(campaign_id="established", date=date(2026, 2, 5), cost_normalized=100.0,
                              **{f"goal_conv_{MACRO_GOAL_ID}": 10}),
    ])

    artifacts = block1.run(paths, DEFAULTS, {"A26"})
    assert "a26" in artifacts
    rows = _read_metric(paths, "a26")
    new_campaign = next(r for r in rows if r.get("campaign_id") == "new")
    established = next(r for r in rows if r.get("campaign_id") == "established")
    assert new_campaign["insufficient_sample_for_judgment"] is True
    assert established["insufficient_sample_for_judgment"] is False
    assert any(r["finding"] == "wordstat_seasonality_unavailable" for r in rows)
