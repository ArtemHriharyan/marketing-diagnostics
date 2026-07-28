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


def _write_config(paths: _Paths, macro_goals: list[dict] | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    config = {
        "sources": {
            "direct": {
                "macro_goals": macro_goals if macro_goals is not None else [],
            },
        },
    }
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


def _base_visit(**overrides) -> dict:
    row = {"device": "desktop", "source_group": "organic", "form_submit": False}
    row.update(overrides)
    return row


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


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
