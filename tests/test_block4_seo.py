"""Тесты блока 4 compute (задача 5bA): S01-S10 (SEO и органический спрос).

По каждой проверке — минимум один сценарий. Обязательные сценарии из промта
задачи: device есть -> device-разрез считается (S08/S09); device="unknown" ->
исключается только из device-специфичных находок, но участвует в остальных
агрегатах (не выбрасывается целиком). Плюс: S07 всегда unavailable (wordstat
структурно недоступен), confidence_cap из degradation_report применяется.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from src.compute import block4_seo  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_block0.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


DEFAULTS = {
    "min_sample_visits": 500,
}


def _write_seo_queries(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "seo_queries.parquet")


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visits.parquet")


def _write_degradation(paths: _Paths, checks: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps({"checks": checks}), encoding="utf-8"
    )


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _base_seo_row(**overrides) -> dict:
    row = {
        "query": "query", "page": "/page", "source": "gsc", "month": "2026-01",
        "device": "desktop", "total_shows": 0, "total_clicks": 0,
        "avg_show_position": 5.0, "is_brand": False,
        "source_mode": "api", "completeness": "verified",
        "ctr": None, "demand": None,
    }
    row.update(overrides)
    return row


def _base_visit(**overrides) -> dict:
    row = {
        "device": "desktop", "source_group": "organic", "entry_page": "/page",
        "form_open": False, "form_submit": False,
        "call_click": False, "messenger_click": False,
    }
    row.update(overrides)
    return row


ALL_S01_10 = {f"S{i:02d}" for i in range(1, 11)}


# ── S01 — брендовый/небрендовый органический трафик смешан ─────────────────
def test_s01_flags_brand_heavy_mix(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="acme", is_brand=True, total_shows=80, total_clicks=10),
        _base_seo_row(query="widget", is_brand=False, total_shows=40, total_clicks=5),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S01"})

    assert "s01" in artifacts
    rows = _read_metric(paths, "s01")
    summary = next(r for r in rows if r["finding"] == "brand_nonbrand_mix")
    assert summary["total_shows"] == 120
    assert summary["brand_share_of_shows"] == 0.6667
    assert summary["organic_demand_mix_brand_heavy"] is True


def test_s01_below_volume_threshold_not_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="acme", is_brand=True, total_shows=5, total_clicks=1),
    ])

    block4_seo.run(paths, DEFAULTS, {"S01"})
    rows = _read_metric(paths, "s01")
    summary = next(r for r in rows if r["finding"] == "brand_nonbrand_mix")
    assert summary["organic_demand_mix_brand_heavy"] is False


# ── S02 — позиции 4-10 недополучают клики ────────────────────────────────────
def test_s02_surfaces_nonbrand_position_4_10(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="buy widget", page="/widget", is_brand=False,
                       total_shows=30, total_clicks=2, avg_show_position=6.0),
        _base_seo_row(query="acme widget", page="/brand", is_brand=True,
                       total_shows=30, total_clicks=2, avg_show_position=6.0),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S02"})

    assert "s02" in artifacts
    rows = _read_metric(paths, "s02")
    opportunities = [r for r in rows if r["finding"] == "position_4_10_opportunity"]
    assert len(opportunities) == 1
    assert opportunities[0]["query"] == "buy widget"


# ── S03 — strike zone 11-20 (легаси 5.1) ────────────────────────────────────
def test_s03_surfaces_strike_zone(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="widget repair", page="/repair", is_brand=False,
                       total_shows=25, total_clicks=1, avg_show_position=15.0),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S03"})

    assert "s03" in artifacts
    rows = _read_metric(paths, "s03")
    strike = next(r for r in rows if r["finding"] == "strike_zone_11_20")
    assert strike["query"] == "widget repair"
    assert strike["avg_position"] == 15.0


# ── S04 — CTR аномально низкий для текущей позиции ──────────────────────────
def test_s04_flags_ctr_anomaly_vs_bucket_median(tmp_path):
    paths = _Paths(tmp_path)
    rows_in = [
        _base_seo_row(query=f"q{i}", page=f"/p{i}", is_brand=False,
                       total_shows=100, total_clicks=10, avg_show_position=7.0)
        for i in range(4)
    ]
    rows_in.append(
        _base_seo_row(query="q_outlier", page="/p_outlier", is_brand=False,
                       total_shows=100, total_clicks=2, avg_show_position=7.0)
    )
    _write_seo_queries(paths, rows_in)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S04"})

    assert "s04" in artifacts
    rows = _read_metric(paths, "s04")
    outlier = next(r for r in rows if r.get("query") == "q_outlier")
    assert outlier["ctr_anomalously_low"] is True
    normal = next(r for r in rows if r.get("query") == "q0")
    assert normal["ctr_anomalously_low"] is False


# ── S05 — отдельные страницы теряют клики и позиции ─────────────────────────
def test_s05_flags_declining_page_and_insufficient_history(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="a", page="/blog/a", month="2026-01",
                       total_shows=100, total_clicks=20),
        _base_seo_row(query="a", page="/blog/a", month="2026-02",
                       total_shows=100, total_clicks=5),
        _base_seo_row(query="b", page="/blog/b", month="2026-01",
                       total_shows=100, total_clicks=20),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S05"})

    assert "s05" in artifacts
    rows = _read_metric(paths, "s05")
    declining = next(r for r in rows if r.get("page") == "/blog/a")
    assert declining["page_declining"] is True
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["pages_with_insufficient_month_history"] == 1


# ── S06 — сезонность vs SEO (легаси 5.5) ────────────────────────────────────
def test_s06_reports_trend_and_wordstat_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q", page="/p", month=m, total_shows=s, total_clicks=10)
        for m, s in [("2026-01", 100), ("2026-02", 100), ("2026-03", 100), ("2026-04", 300)]
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S06"})

    assert "s06" in artifacts
    rows = _read_metric(paths, "s06")
    anomaly = next(r for r in rows if r["finding"] == "monthly_shows_anomaly")
    assert anomaly["anomaly_type"] == "spike"
    reconciliation = next(r for r in rows if r["finding"] == "seasonality_reconciliation")
    assert reconciliation["wordstat_available"] is False
    assert reconciliation["confidence"] == "LOW"


# ── S07 — коммерческий спрос без посадочной: всегда unavailable ────────────
def test_s07_always_unavailable_wordstat_missing(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(total_shows=100, total_clicks=10)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    assert "s07" in artifacts
    rows = _read_metric(paths, "s07")
    assert rows[0]["status"] == "unavailable"
    assert "wordstat" in rows[0]["reason"]


# ── S08 — страница не соответствует намерению запроса (device-разрез) ──────
def test_s08_device_split_excludes_unknown_but_keeps_overall(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q1", page="/landing", device="desktop", total_shows=25, total_clicks=2),
        _base_seo_row(query="q2", page="/landing", device="mobile", total_shows=25, total_clicks=2),
        _base_seo_row(query="q3", page="/landing", device="unknown", total_shows=25, total_clicks=2),
    ])
    visits = (
        [_base_visit(device="desktop", entry_page="/landing", form_submit=False) for _ in range(25)]
        + [_base_visit(device="mobile", entry_page="/landing", form_submit=True) for _ in range(25)]
    )
    _write_visits(paths, visits)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S08"})

    assert "s08" in artifacts
    rows = _read_metric(paths, "s08")
    by_device = [r for r in rows if r["finding"] == "page_intent_mismatch_by_device"]
    devices_seen = {r["device"] for r in by_device}
    assert devices_seen == {"desktop", "mobile"}
    desktop_row = next(r for r in by_device if r["device"] == "desktop")
    mobile_row = next(r for r in by_device if r["device"] == "mobile")
    assert desktop_row["intent_mismatch_candidate"] is True
    assert mobile_row["intent_mismatch_candidate"] is False

    overall = next(r for r in rows if r["finding"] == "page_intent_mismatch_overall")
    # unknown-device seo-строка всё равно участвует в overall total_shows (75),
    # а вовлечённость overall считается по ВСЕМ визитам страницы (50 визитов,
    # 25 из них с form_submit) -> zero_engagement_share = 0.5, ниже порога 0.9.
    assert overall["total_shows"] == 75
    assert overall["zero_engagement_share"] == 0.5
    assert overall["intent_mismatch_candidate"] is False


# ── S09 — несколько страниц конкурируют по одному кластеру ──────────────────
def test_s09_overall_includes_unknown_device_but_by_device_excludes_it(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="shoes", page="/a", device="desktop", total_shows=15, total_clicks=2),
        _base_seo_row(query="shoes", page="/b", device="unknown", total_shows=10, total_clicks=1),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S09"})

    assert "s09" in artifacts
    rows = _read_metric(paths, "s09")
    overall = next(r for r in rows if r["finding"] == "query_page_overlap_overall")
    assert overall["competing_page_count"] == 2
    assert overall["total_shows"] == 25

    by_device = [r for r in rows if r["finding"] == "query_page_overlap_by_device"]
    assert by_device == []  # только 1 не-unknown устройство несёт данные -> нет пересечения


# ── S10 — по запросу ранжируется не та страница ─────────────────────────────
def test_s10_flags_better_converting_alternative_page(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="boots", page="/old", total_shows=15, total_clicks=1, avg_show_position=3.0),
        _base_seo_row(query="boots", page="/new", total_shows=15, total_clicks=1, avg_show_position=8.0),
    ])
    visits = (
        [_base_visit(entry_page="/old", form_submit=(i < 3)) for i in range(30)]
        + [_base_visit(entry_page="/new", form_submit=(i < 20)) for i in range(30)]
    )
    _write_visits(paths, visits)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S10"})

    assert "s10" in artifacts
    rows = _read_metric(paths, "s10")
    candidate = next(r for r in rows if r["finding"] == "wrong_page_ranking_candidate")
    assert candidate["ranking_leader_page"] == "/old"
    assert candidate["better_converting_page"] == "/new"
    assert candidate["target_url_from_config"] is False


def test_s10_runs_without_visits_and_reports_unavailable_context(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="boots", page="/old", total_shows=15, total_clicks=1, avg_show_position=3.0),
        _base_seo_row(query="boots", page="/new", total_shows=15, total_clicks=1, avg_show_position=8.0),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S10"})

    assert "s10" in artifacts
    rows = _read_metric(paths, "s10")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["visits_available"] is False
    assert summary["wrong_page_ranking_candidates"] == 0


# ── Диспетчер: confidence_cap из degradation_report ─────────────────────────
def test_confidence_cap_from_degradation_report_applied(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query=f"q{i}", page=f"/p{i}", device="desktop",
                       total_shows=25, total_clicks=2)
        for i in range(3)
    ])
    visits = [_base_visit(device="desktop", entry_page="/p0", form_submit=True)
              for _ in range(600)]
    _write_visits(paths, visits)
    _write_degradation(paths, [{"check_id": "S08", "confidence_cap": "MED"}])

    block4_seo.run(paths, DEFAULTS, {"S08"})

    rows = _read_metric(paths, "s08")
    overall = next(r for r in rows if r["finding"] == "page_intent_mismatch_overall"
                   and r["page"] == "/p0")
    # Без capping выборка 600 >= 500 дала бы HIGH — degradation_report прижимает к MED.
    assert overall["confidence"] == "MED"


def test_run_ignores_s01_10_when_seo_queries_missing(tmp_path):
    paths = _Paths(tmp_path)
    artifacts = block4_seo.run(paths, DEFAULTS, ALL_S01_10)
    assert artifacts == []
