"""Тесты блока 2 compute (задача 5F): T01–T10 (трафик, каналы, атрибуция).

По каждой проверке — минимум один сценарий. Обязательные сценарии из промта
задачи: последовательность ad->direct (T02, наивная vs corrected), несколько
cookie одного client (T07), аномальный источник (T09), спам-реферал и
нормальный реферал (T10, разграничение позитив/негатив).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from src.compute import block2  # noqa: E402


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
}


def _write_config(
    paths: _Paths,
    macro_goals: list[dict] | None = None,
    brand_terms: list[str] | None = None,
) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    config: dict = {
        "sources": {"direct": {"macro_goals": macro_goals if macro_goals is not None else []}},
    }
    if brand_terms is not None:
        config["brand_terms"] = brand_terms
    paths.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")


def _write_dated_parquet(path: Path, rows: list[dict], date_field: str = "date") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if date_field in df.columns:
        idx = table.schema.get_field_index(date_field)
        date_array = pa.array(list(df[date_field]), type=pa.date32())
        table = table.set_column(idx, pa.field(date_field, pa.date32()), date_array)
    pq.write_table(table, path)


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "visits.parquet", rows)


def _write_costs(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "costs.parquet", rows)


def _write_direct_campaigns(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "direct_campaigns.parquet", rows)


def _write_direct_queries(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "direct_queries.parquet")


def _write_seo_queries(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "seo_queries.parquet")


def _write_client_answers(paths: _Paths, data: dict) -> None:
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "client_answers.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _base_visit(**overrides) -> dict:
    row = {
        "visit_id": "v1",
        "client_id": "c1",
        "dt": datetime(2026, 6, 1, 10, 0, 0),
        "date": date(2026, 6, 1),
        "device": "desktop",
        "source_group": "organic",
        "source_final": "organic",
        "utm_source_raw": "",
        "entry_page": "/",
        "form_open": False,
        "form_submit": False,
        "call_click": False,
        "messenger_click": False,
        "call_click_count": 0,
        "messenger_click_count": 0,
        "is_new_user": False,
        "last_traffic_source_naive": None,
        "last_sign_traffic_source_raw": None,
        "source_group_resolved": "organic",
        "traffic_source_resolved": True,
    }
    row.update(overrides)
    return row


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ── T01 — внешние ссылки не размечены UTM ────────────────────────────────────

def test_t01_flags_low_utm_tagging_in_referral_group(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = (
        [_base_visit(visit_id=f"r{i}", source_group="referral", source_final="referral",
                      utm_source_raw="") for i in range(40)]
        + [_base_visit(visit_id=f"o{i}") for i in range(10)]
    )
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T01"})
    rows = _read_metric(paths, "t01")
    referral_row = next(r for r in rows if r.get("finding") == "by_source_group"
                         and r["source_group"] == "referral")
    assert referral_row["untagged_share"] == 1.0
    assert referral_row["likely_untagged_external_traffic"] is True


def test_t01_flags_non_standardized_utm_variants(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(visit_id="a1", source_group="ad", source_final="ad", utm_source_raw="vk"),
        _base_visit(visit_id="a2", source_group="ad", source_final="ad", utm_source_raw="VK"),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T01"})
    rows = _read_metric(paths, "t01")
    variant_row = next(r for r in rows if r.get("finding") == "non_standardized_utm_source")
    assert variant_row["variant_count"] == 2


# ── T02 — наивная модель vs corrected lastsign ───────────────────────────────

def test_t02_ad_to_direct_sequence_flags_mismatch(tmp_path):
    """Наивная модель говорит 'ad', carry-forward-corrected модель — 'direct'."""
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(
            visit_id="v1", last_traffic_source_naive="ad",
            source_group_resolved="direct", last_sign_traffic_source_raw="direct",
        ),
        _base_visit(
            visit_id="v2", last_traffic_source_naive="direct",
            source_group_resolved="direct", last_sign_traffic_source_raw="direct",
        ),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T02"})
    rows = _read_metric(paths, "t02")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["naive_available_visits"] == 2
    assert summary["mismatch_count"] == 1

    confusion = next(r for r in rows if r["finding"] == "confusion_matrix")
    assert confusion["naive_group"] == "ad"
    assert confusion["corrected_group"] == "direct"
    assert confusion["visit_count"] == 1

    ad_channel = next(
        r for r in rows if r["finding"] == "channel_naive_vs_corrected" and r["channel"] == "ad"
    )
    assert ad_channel["naive_count"] == 1
    assert ad_channel["corrected_count"] == 0
    assert ad_channel["direction"] == "overstated"


def test_t02_unavailable_when_resolved_column_missing(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "visit_id": "v1", "last_traffic_source_naive": "ad",
    }]).to_parquet(paths.canonical / "visits.parquet")

    block2.run(paths, DEFAULTS, {"T02"})
    rows = _read_metric(paths, "t02")
    assert rows[0]["status"] == "unavailable"


# ── T03 — self-referral / разрыв сессии ─────────────────────────────────────

def test_t03_counts_resolved_session_breaks(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(visit_id="v1", last_sign_traffic_source_raw="ad",
                    source_group_resolved="ad", traffic_source_resolved=True),
        _base_visit(visit_id="v2", last_sign_traffic_source_raw="internal",
                    source_group_resolved="ad", traffic_source_resolved=True),
        _base_visit(visit_id="v3", last_sign_traffic_source_raw="undefined",
                    source_group_resolved="other", traffic_source_resolved=False),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T03"})
    rows = _read_metric(paths, "t03")
    summary = next(r for r in rows if r["finding"] == "session_break_summary")
    assert summary["session_break_visits"] == 2
    assert summary["session_break_resolved"] == 1
    assert summary["session_break_unresolved"] == 1
    assert summary["domain_level_detection_available"] is False


# ── T04 — каналы сравниваются по разным моделям атрибуции ──────────────────

def test_t04_flags_diverging_attribution_models(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, macro_goals=[{"id": 999, "name": "lead"}])
    visits = (
        [_base_visit(visit_id=f"ad{i}", source_group="ad", source_final="ad",
                      form_submit=(i < 5)) for i in range(10)]
    )
    _write_visits(paths, visits)
    _write_costs(paths, [
        {"date": date(2026, 6, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 1000.0, "cost_normalized": 1000.0,
         "cost_status": "ok", "clicks": 10, "impressions": 100},
    ])
    _write_direct_campaigns(paths, [
        {"date": date(2026, 6, 1), "campaign_id": "1", "campaign_name": "c1",
         "device": "desktop", "cost_raw": 1000, "cost_rub": 1000.0,
         "cost_normalized": 1000.0, "vat_basis_applied": True,
         "clicks": 10, "impressions": 100, "conversions_all": 20,
         "goal_conv_999": 20},
    ])

    block2.run(paths, DEFAULTS, {"T04"})
    rows = _read_metric(paths, "t04")
    recon = next(r for r in rows if r["finding"] == "conversion_model_reconciliation")
    assert recon["metrika_ad_conversions"] == 5
    assert recon["direct_conversions"] == 20
    assert recon["attribution_models_diverge"] is True


# ── T05 — брендовый и небрендовый спрос смешаны ─────────────────────────────

def test_t05_flags_brand_heavy_seo_mix(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths, brand_terms=["acme"])
    _write_seo_queries(paths, (
        [{"query": "acme shop", "page": "/", "source": "gsc", "month": "2026-06",
          "device": "desktop", "total_shows": 100, "total_clicks": 10,
          "avg_show_position": 1.0, "is_brand": True, "source_mode": "api",
          "completeness": "verified", "ctr": None, "demand": None} for _ in range(1)]
        + [{"query": "buy shoes", "page": "/", "source": "gsc", "month": "2026-06",
            "device": "desktop", "total_shows": 20, "total_clicks": 1,
            "avg_show_position": 5.0, "is_brand": False, "source_mode": "api",
            "completeness": "verified", "ctr": None, "demand": None}]
    ))

    block2.run(paths, DEFAULTS, {"T05"})
    rows = _read_metric(paths, "t05")
    seo_mix = next(r for r in rows if r["finding"] == "seo_brand_mix")
    assert seo_mix["total_shows"] == 120
    assert seo_mix["demand_mix_brand_heavy"] is True


# ── T06 — офлайн-каналы невидимы ────────────────────────────────────────────

def test_t06_flags_coverage_gap_from_client_answers(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    _write_client_answers(paths, {
        "business": {"offline_lead_channels": ["звонки", "WhatsApp"]},
        "directories": {
            "yandex_maps": {"exists": True, "stats_available": False},
            "gis2": {"exists": False, "stats_available": None},
            "calltracking": {"exists": None, "stats_available": None},
        },
    })

    block2.run(paths, DEFAULTS, {"T06"})
    rows = _read_metric(paths, "t06")
    assert rows[0]["coverage_gap"] is True
    assert "yandex_maps" in rows[0]["invisible_directories"]
    assert "calltracking" in rows[0]["directories_not_answered"]


# ── T07 — cookie-визит трактуется как клиент ────────────────────────────────

def test_t07_multiple_cookies_of_one_client_counted_honestly(tmp_path):
    """Два разных client_id (cookie) — проверка не подменяет их одним "клиентом"."""
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(visit_id="v1", client_id="cookie-1", is_new_user=True),
        _base_visit(visit_id="v2", client_id="cookie-2", is_new_user=True),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T07"})
    rows = _read_metric(paths, "t07")
    row = rows[0]
    assert row["distinct_client_ids"] == 2
    assert row["single_visit_client_ids"] == 2
    assert row["repeat_visit_client_ids"] == 0
    assert row["cookie_is_not_customer_proxy"] is True


def test_t07_repeat_visits_same_cookie_counted_as_returning(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(visit_id="v1", client_id="cookie-1", is_new_user=True),
        _base_visit(visit_id="v2", client_id="cookie-1", is_new_user=False),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T07"})
    rows = _read_metric(paths, "t07")
    row = rows[0]
    assert row["distinct_client_ids"] == 1
    assert row["repeat_visit_client_ids"] == 1
    assert row["new_visits"] == 1
    assert row["returning_visits"] == 1


# ── T08 — зависимость от одного канала или кампании ─────────────────────────

def test_t08_flags_channel_concentration_risk(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = (
        [_base_visit(visit_id=f"ad{i}", source_group="ad", source_final="ad")
         for i in range(40)]
        + [_base_visit(visit_id=f"org{i}") for i in range(5)]
    )
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T08"})
    rows = _read_metric(paths, "t08")
    summary = next(r for r in rows if r["finding"] == "channel_concentration_summary")
    assert summary["dominant_channel"] == "ad"
    assert summary["channel_concentration_risk"] is True


# ── T09 — аномалия канала ───────────────────────────────────────────────────

def test_t09_flags_anomalous_source_spike(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = []
    for day in range(1, 9):
        count = 30 if day == 8 else 5  # день 8 — резкий всплеск (x6 от медианы)
        for i in range(count):
            visits.append(_base_visit(
                visit_id=f"d{day}-{i}", source_group="referral", source_final="referral",
                date=date(2026, 6, day), dt=datetime(2026, 6, day, 10, 0, 0),
            ))
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T09"})
    rows = _read_metric(paths, "t09")
    anomalies = [r for r in rows if r.get("finding") == "channel_anomaly"]
    assert len(anomalies) == 1
    assert anomalies[0]["channel"] == "referral"
    assert anomalies[0]["anomaly_type"] == "spike"
    assert anomalies[0]["date"] == "2026-06-08"


def test_t09_no_anomaly_for_stable_source(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = []
    for day in range(1, 9):
        for i in range(10):
            visits.append(_base_visit(
                visit_id=f"d{day}-{i}", date=date(2026, 6, day),
                dt=datetime(2026, 6, day, 10, 0, 0),
            ))
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T09"})
    rows = _read_metric(paths, "t09")
    anomalies = [r for r in rows if r.get("finding") == "channel_anomaly"]
    assert anomalies == []


# ── T10 — реферальный спам, боты или технические домены ─────────────────────

def test_t10_flags_spam_referral_recurring_zero_engagement(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(
            visit_id=f"spam{i}", client_id="bot-1", source_group="referral",
            source_final="referral", entry_page="/landing",
        )
        for i in range(6)
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T10"})
    rows = _read_metric(paths, "t10")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["spam_candidate_client_ids"] == 1
    assert summary["spam_candidate_visits"] == 6

    candidate = next(r for r in rows if r.get("finding") == "spam_candidate")
    assert candidate["client_id"] == "bot-1"
    assert candidate["zero_engagement"] is True


def test_t10_does_not_flag_normal_referral(tmp_path):
    paths = _Paths(tmp_path)
    _write_config(paths)
    visits = [
        _base_visit(visit_id="v1", client_id="human-1", source_group="referral",
                     source_final="referral", form_submit=True),
        _base_visit(visit_id="v2", client_id="human-2", source_group="referral",
                     source_final="referral"),
    ]
    _write_visits(paths, visits)

    block2.run(paths, DEFAULTS, {"T10"})
    rows = _read_metric(paths, "t10")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["spam_candidate_client_ids"] == 0
    assert not any(r.get("finding") == "spam_candidate" for r in rows)
