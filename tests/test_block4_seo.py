"""Тесты блока 4 compute (задачи 5bA/5bB/5bC): S01-S27 (SEO и органический спрос).

По каждой проверке — минимум один сценарий. Обязательные сценарии из промта
задачи 5bA: device есть -> device-разрез считается (S08/S09); device="unknown"
-> исключается только из device-специфичных находок, но участвует в остальных
агрегатах (не выбрасывается целиком). confidence_cap из degradation_report
применяется. Задача 5bC добавляет S21-S27 и агрегат SEO confidence_cap в
metrics_summary (src.compute.common.build_metrics_summary).

Задача FIX-block4-seo-wordstat-consumption (после FIX-wordstat-canonical):
S07/S26 без canonical["wordstat"] по-прежнему unavailable, но с ним считают
реальный кластер-спрос-vs-карта-страниц (см. _write_wordstat/_base_wordstat_row
ниже); S06 поднимает confidence до MED, когда Wordstat реально подтверждает
или опровергает сезонное объяснение аномалии показов.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import yaml

from src.compute import block4_seo  # noqa: E402
from src.compute import common as compute_common  # noqa: E402


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


def _write_site_pages(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "site_pages.parquet")


def _write_link_graph(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "site_link_graph.parquet")


def _write_wordstat(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "wordstat.parquet")


def _base_wordstat_row(**overrides) -> dict:
    row = {
        "phrase": "phrase", "normalized_phrase": "phrase",
        "date": "2026-01-01", "month": "2026-01",
        "count": 0, "share": None, "purpose": "gap",
        "seed_mask": "phrase", "scope": "gap-specific",
        "top_requests_count": None,
    }
    row.update(overrides)
    return row


def _write_crux_raw(paths: _Paths, data: dict) -> None:
    out_dir = paths.raw / "crux"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "crux.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_input_yaml(paths: _Paths, name: str, data: dict) -> None:
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


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


def _base_site_page(**overrides) -> dict:
    row = {
        "url": "https://example.com/page", "http_status": 200, "redirect_chain": "[]",
        "final_url": "https://example.com/page", "canonical_url": "https://example.com/page",
        "robots_directive": "", "in_sitemap": True, "title": "Заголовок",
        "description": "Описание", "h1": "H1", "crawled_at": "2026-07-20T00:00:00Z",
        "js_content_diff": None,
    }
    row.update(overrides)
    return row


ALL_S01_10 = {f"S{i:02d}" for i in range(1, 11)}
ALL_S11_20 = {f"S{i:02d}" for i in range(11, 21)}
ALL_S21_27 = {f"S{i:02d}" for i in range(21, 28)}


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


def test_s06_confidence_rises_to_med_when_seasonality_confirmed(tmp_path):
    """Wordstat повторяет тот же spike в том же месяце -> сезонность

    подтверждена, confidence реально поднимается выше жёсткого LOW."""
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q", page="/p", month=m, total_shows=s, total_clicks=10)
        for m, s in [("2026-01", 100), ("2026-02", 100), ("2026-03", 100), ("2026-04", 300)]
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="q", normalized_phrase="q", month=m, date=f"{m}-01",
                            count=c, purpose="seasonality", scope="gap-specific")
        for m, c in [("2026-01", 100), ("2026-02", 100), ("2026-03", 100), ("2026-04", 300)]
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S06"})

    assert "s06" in artifacts
    rows = _read_metric(paths, "s06")
    reconciliation = next(r for r in rows if r["finding"] == "seasonality_reconciliation")
    assert reconciliation["wordstat_available"] is True
    assert reconciliation["confidence"] == "MED"
    assert reconciliation["verdict"] == "seasonality_explains_anomaly"
    month_entry = next(m for m in reconciliation["months"] if m["month"] == "2026-04")
    assert month_entry["seasonality_confirmed"] is True


def test_s06_confidence_rises_to_med_when_seasonality_not_confirmed(tmp_path):
    """Wordstat плоский в месяце SEO-аномалии -> сезонность НЕ подтверждена,

    но confidence всё равно MED (реальная сверка состоялась, а не гипотеза)."""
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q", page="/p", month=m, total_shows=s, total_clicks=10)
        for m, s in [("2026-01", 100), ("2026-02", 100), ("2026-03", 100), ("2026-04", 300)]
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="q", normalized_phrase="q", month=m, date=f"{m}-01",
                            count=100, purpose="seasonality", scope="gap-specific")
        for m in ("2026-01", "2026-02", "2026-03", "2026-04")
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S06"})

    rows = _read_metric(paths, "s06")
    reconciliation = next(r for r in rows if r["finding"] == "seasonality_reconciliation")
    assert reconciliation["confidence"] == "MED"
    assert reconciliation["verdict"] == "anomaly_not_fully_explained_by_seasonality"
    month_entry = next(m for m in reconciliation["months"] if m["month"] == "2026-04")
    assert month_entry["seasonality_confirmed"] is False


# ── S07 — коммерческий спрос без посадочной (FIX-s07-site-pages-join:
# has_matching_query — старая логика по seo_queries.query; has_matching_page —
# новая, через canonical["site_pages"] title/h1/url-путь) ──────────────────
def test_s07_always_unavailable_wordstat_missing(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(total_shows=100, total_clicks=10)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    assert "s07" in artifacts
    rows = _read_metric(paths, "s07")
    assert rows[0]["status"] == "unavailable"
    assert "wordstat" in rows[0]["reason"]


def test_s07_unavailable_without_site_pages(tmp_path):
    """wordstat доступен, но site_pages нет — unavailable с caveat "карта

    страниц", НЕ тихий fallback на старую query-only логику.
    """
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="аренда авто", page="/catalog/", total_shows=100, total_clicks=10),
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="прокат авто спб", normalized_phrase="прокат авто спб",
                            count=30),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    assert "s07" in artifacts
    rows = _read_metric(paths, "s07")
    assert rows[0]["status"] == "unavailable"
    assert "карт" in rows[0]["reason"]
    assert "site_pages" in rows[0]["reason"]


def test_s07_reports_gap_candidates_without_query_or_page_match(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="аренда авто", page="/catalog/", total_shows=100, total_clicks=10),
    ])
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/catalog/", title="Каталог авто", h1="Каталог"),
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="аренда авто", normalized_phrase="аренда авто",
                            count=15, date="2026-01-01"),
        _base_wordstat_row(phrase="аренда авто", normalized_phrase="аренда авто",
                            count=10, date="2026-01-08"),
        _base_wordstat_row(phrase="прокат авто спб", normalized_phrase="прокат авто спб",
                            count=30),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    assert "s07" in artifacts
    rows = _read_metric(paths, "s07")
    summary = rows[0]
    assert summary["finding"] == "summary"
    assert "status" not in summary
    assert summary["clusters_evaluated"] == 2
    assert summary["query_gap_candidate_count"] == 1
    assert summary["gap_candidate_count"] == 1
    assert summary["confidence"] == "MED"
    candidate = next(
        r for r in rows if r.get("finding") == "commercial_demand_without_landing_page"
    )
    assert candidate["normalized_phrase"] == "прокат авто спб"
    assert candidate["demand_total"] == 30
    assert candidate["has_matching_query"] is False
    assert candidate["has_matching_page"] is False
    assert candidate["confidence"] == "MED"


def test_s07_page_match_without_query_match_is_not_a_finding(tmp_path):
    """Ключевое отличие от старой реализации: кластер без query, но с

    реальной релевантной страницей в site_pages (title/h1 покрывают все
    слова фразы) — НЕ находка (страница есть, просто не ранжируется).
    """
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="аренда авто", page="/catalog/", total_shows=100, total_clicks=10),
    ])
    _write_site_pages(paths, [
        _base_site_page(
            url="https://example.com/rental/", title="Прокат авто в Спб", h1="Прокат авто СПб",
        ),
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="прокат авто спб", normalized_phrase="прокат авто спб",
                            count=30),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    assert "s07" in artifacts
    rows = _read_metric(paths, "s07")
    summary = rows[0]
    assert summary["clusters_evaluated"] == 1
    assert summary["query_gap_candidate_count"] == 1
    assert summary["gap_candidate_count"] == 0
    assert not any(r.get("finding") == "commercial_demand_without_landing_page" for r in rows)


def test_s07_below_min_demand_threshold_not_a_candidate(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(query="unrelated", total_shows=100, total_clicks=10)])
    _write_site_pages(paths, [_base_site_page(url="https://example.com/other/")])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="редкая фраза", normalized_phrase="редкая фраза", count=5),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S07"})

    rows = _read_metric(paths, "s07")
    summary = rows[0]
    assert summary["clusters_evaluated"] == 0
    assert summary["gap_candidate_count"] == 0


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


# ── S11 — важные страницы закрыты robots/noindex ────────────────────────────
def test_s11_flags_robots_blocked_important_page(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/promo", robots_directive="noindex",
                         in_sitemap=True),
    ])
    _write_seo_queries(paths, [_base_seo_row(page="/promo", total_shows=30, total_clicks=1)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S11"})

    assert "s11" in artifacts
    rows = _read_metric(paths, "s11")
    candidate = next(r for r in rows if r["finding"] == "robots_blocks_important_page")
    assert candidate["page"] == "https://example.com/promo"
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["candidate_count"] == 1
    assert summary["crawled_url_count"] == 1
    assert "экстраполир" in summary["crawl_coverage_caveat"].lower()


def test_s11_unavailable_without_site_pages(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(total_shows=30)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S11"})

    assert "s11" in artifacts
    rows = _read_metric(paths, "s11")
    assert rows[0]["status"] == "unavailable"


# ── S12 — canonical указывает на неверную страницу ──────────────────────────
def test_s12_flags_canonical_pointing_elsewhere(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/dup", canonical_url="https://example.com/main"),
    ])
    _write_seo_queries(paths, [_base_seo_row(page="/dup", total_shows=25, total_clicks=2)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S12"})

    assert "s12" in artifacts
    rows = _read_metric(paths, "s12")
    candidate = next(r for r in rows if r["finding"] == "canonical_points_elsewhere")
    assert candidate["page"] == "https://example.com/dup"
    assert candidate["total_shows"] == 25


# ── S13 — sitemap неполный/устаревший/с ошибочными URL ──────────────────────
def test_s13_flags_missing_from_sitemap_and_broken_in_sitemap(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/a", in_sitemap=False),
        _base_site_page(url="https://example.com/b", in_sitemap=True, http_status=404),
    ])
    _write_seo_queries(paths, [_base_seo_row(page="/a", total_shows=25, total_clicks=1)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S13"})

    assert "s13" in artifacts
    rows = _read_metric(paths, "s13")
    missing = next(r for r in rows if r["finding"] == "traffic_page_missing_from_sitemap")
    assert missing["page"] == "https://example.com/a"
    broken = next(r for r in rows if r["finding"] == "sitemap_contains_broken_url")
    assert broken["page"] == "https://example.com/b"


# ── S14 — органический трафик ведёт на 404/удалённые страницы ─────────────
def test_s14_flags_organic_traffic_to_broken_page(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [_base_site_page(url="https://example.com/gone", http_status=404)])
    _write_seo_queries(paths, [_base_seo_row(page="/gone", total_shows=10, total_clicks=3)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S14"})

    assert "s14" in artifacts
    rows = _read_metric(paths, "s14")
    candidate = next(r for r in rows if r["finding"] == "organic_traffic_to_broken_page")
    assert candidate["http_status"] == 404


# ── S15 — цепочки и массовые редиректы размывают сигнал ─────────────────────
def test_s15_flags_excessive_redirect_chain(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(
            url="https://example.com/old",
            redirect_chain=json.dumps(["https://example.com/mid1", "https://example.com/mid2"]),
            final_url="https://example.com/new",
        ),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S15"})

    assert "s15" in artifacts
    rows = _read_metric(paths, "s15")
    candidate = next(r for r in rows if r["finding"] == "redirect_chain")
    assert candidate["redirect_hops"] == 2
    assert candidate["excessive_redirect_chain"] is True


# ── S16 — индекс раздут дублями/параметрами/тонкими страницами ─────────────
def test_s16_flags_duplicate_cluster(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/product", canonical_url="https://example.com/product"),
        _base_site_page(url="https://example.com/product-copy1", canonical_url="https://example.com/product"),
        _base_site_page(url="https://example.com/product-copy2", canonical_url="https://example.com/product"),
    ])
    _write_seo_queries(paths, [_base_seo_row(page="/product", total_shows=5, total_clicks=1)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S16"})

    assert "s16" in artifacts
    rows = _read_metric(paths, "s16")
    cluster = next(r for r in rows if r["finding"] == "duplicate_cluster")
    assert cluster["canonical_target"] == "/product"
    assert cluster["duplicate_source_count"] == 2


# ── S17 — title/description/H1 отсутствуют/дублируются ─────────────────────
def test_s17_flags_missing_metadata_and_duplicate_title(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/a", title="", description="", h1=""),
        _base_site_page(url="https://example.com/b", title="Одинаковый заголовок"),
        _base_site_page(url="https://example.com/c", title="Одинаковый заголовок"),
    ])
    _write_seo_queries(paths, [
        _base_seo_row(page="/a", total_shows=25, total_clicks=1),
        _base_seo_row(page="/b", total_shows=25, total_clicks=1),
        _base_seo_row(page="/c", total_shows=25, total_clicks=1),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S17"})

    assert "s17" in artifacts
    rows = _read_metric(paths, "s17")
    missing = next(r for r in rows if r["finding"] == "missing_metadata")
    assert missing["page"] == "https://example.com/a"
    assert set(missing["missing_fields"]) == {"title", "description", "h1"}
    duplicate = next(r for r in rows if r["finding"] == "duplicate_title")
    assert set(duplicate["pages"]) == {"/b", "/c"}


# ── S18 — важные страницы имеют мало внутренних ссылок/сироты ──────────────
def test_s18_flags_orphan_and_low_inlink_pages(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(url="https://example.com/orphan"),
        _base_site_page(url="https://example.com/weak"),
        _base_site_page(url="https://example.com/strong"),
    ])
    _write_link_graph(paths, [
        {"from_url": "https://example.com/", "to_url": "https://example.com/weak", "depth_from_home": 1},
        {"from_url": "https://example.com/", "to_url": "https://example.com/strong", "depth_from_home": 1},
        {"from_url": "https://example.com/other", "to_url": "https://example.com/strong", "depth_from_home": 2},
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S18"})

    assert "s18" in artifacts
    rows = _read_metric(paths, "s18")
    orphan = next(r for r in rows if r.get("page") == "/orphan")
    assert orphan["finding"] == "orphan_page"
    weak = next(r for r in rows if r.get("page") == "/weak")
    assert weak["finding"] == "low_inlink_page"
    assert not any(r.get("page") == "/strong" for r in rows if r["finding"] != "summary")


def test_s18_unavailable_without_link_graph(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [_base_site_page()])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S18"})

    assert "s18" in artifacts
    rows = _read_metric(paths, "s18")
    assert rows[0]["status"] == "unavailable"


# ── S19 — архитектура требует слишком много кликов до коммерции ────────────
def test_s19_flags_pages_beyond_depth_threshold(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [_base_site_page(url="https://example.com/deep")])
    _write_link_graph(paths, [
        {"from_url": "https://example.com/p1", "to_url": "https://example.com/p2", "depth_from_home": 1},
        {"from_url": "https://example.com/p2", "to_url": "https://example.com/p3", "depth_from_home": 2},
        {"from_url": "https://example.com/p3", "to_url": "https://example.com/p4", "depth_from_home": 3},
        {"from_url": "https://example.com/p4", "to_url": "https://example.com/deep", "depth_from_home": 4},
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S19"})

    assert "s19" in artifacts
    rows = _read_metric(paths, "s19")
    deep = next(r for r in rows if r.get("finding") == "page_too_deep")
    assert deep["page"] == "/deep"
    assert deep["depth_from_home"] == 4
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["commercial_classification_available"] is False


# ── S20 — мобильная производительность и CWV ────────────────────────────────
def test_s20_crux_field_data_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_crux_raw(paths, {
        "cwv_field_data_available": True,
        "records": [{
            "target_type": "origin", "target": "https://example.com",
            "field_data_available": True,
            "p75": {"largest_contentful_paint": 5000},
        }],
    })
    _write_seo_queries(paths, [
        _base_seo_row(device="mobile", total_shows=40, total_clicks=2, avg_show_position=8.0),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S20"})

    assert "s20" in artifacts
    rows = _read_metric(paths, "s20")
    field_row = next(r for r in rows if r["finding"] == "field_cwv")
    assert field_row["largest_contentful_paint_rating"] == "poor"
    assert field_row["confidence"] == "MED"


def test_s20_crux_empty_falls_back_to_manual_lab_med(tmp_path):
    """CrUX отсутствует -> ручной лабораторный замер, confidence MED (задача 5bB, промт)."""
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(device="mobile", total_shows=40, total_clicks=2)])
    _write_input_yaml(paths, "manual_cwv", {
        "meta": {"tested_at": "2026-07-20", "device": "mobile"},
        "patterns": [{"url": "/", "lcp_ms": 6000, "cls": 0.3, "inp_ms": 100}],
    })

    artifacts = block4_seo.run(paths, DEFAULTS, {"S20"})

    assert "s20" in artifacts
    rows = _read_metric(paths, "s20")
    manual_row = next(r for r in rows if r["finding"] == "manual_lab_cwv")
    assert manual_row["device"] == "mobile"
    assert manual_row["lcp_rating"] == "poor"
    assert manual_row["confidence"] == "MED"


def test_s20_neither_source_available_is_low_confidence(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(device="mobile", total_shows=40, total_clicks=2)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S20"})

    assert "s20" in artifacts
    rows = _read_metric(paths, "s20")
    unavailable = next(r for r in rows if r["finding"] == "cwv_unavailable")
    assert unavailable["confidence"] == "LOW"


def test_run_ignores_s11_20_when_sources_missing(tmp_path):
    paths = _Paths(tmp_path)
    artifacts = block4_seo.run(paths, DEFAULTS, ALL_S11_20)
    assert artifacts == []


# ── S21 — Яндекс и Google показывают противоположную картину ───────────────
def test_s21_flags_cross_system_divergence(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q1", page="/p", source="gsc",
                       total_shows=30, total_clicks=9, avg_show_position=3.0),
        _base_seo_row(query="q1", page="/p", source="webmaster",
                       total_shows=30, total_clicks=1, avg_show_position=15.0),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S21"})

    assert "s21" in artifacts
    rows = _read_metric(paths, "s21")
    candidate = next(r for r in rows if r["finding"] == "cross_system_divergence")
    assert candidate["cross_system_divergent"] is True
    assert candidate["position_gap"] == 12.0
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["pages_compared"] == 1
    assert summary["divergent_page_count"] == 1


def test_s21_ignores_pages_present_in_only_one_system(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="q1", page="/p", source="gsc", total_shows=30, total_clicks=3),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S21"})

    assert "s21" in artifacts
    rows = _read_metric(paths, "s21")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["pages_compared"] == 0


# ── S22 — контент получает органику, не переводит в коммерческий раздел ────
def test_s22_flags_dead_end_organic_page(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="what is x", page="/blog/x", total_shows=30, total_clicks=5),
    ])
    _write_visits(paths, [
        _base_visit(entry_page="/blog/x", form_submit=False) for _ in range(25)
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S22"})

    assert "s22" in artifacts
    rows = _read_metric(paths, "s22")
    candidate = next(r for r in rows if r["finding"] == "organic_page_without_conversion_path")
    assert candidate["no_conversion_path"] is True
    assert candidate["page_classification_available"] is False
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["dead_end_page_count"] == 1
    assert summary["dead_end_click_share"] == 1.0


# ── S23 — органические посадочные конвертируют хуже сопоставимых страниц ───
def test_s23_flags_organic_underperforming_other_traffic(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(page="/x", total_shows=30, total_clicks=3)])
    visits = (
        [_base_visit(source_group="organic", entry_page="/x", form_submit=False) for _ in range(25)]
        + [_base_visit(source_group="paid", entry_page="/x", form_submit=(i < 20)) for i in range(25)]
    )
    _write_visits(paths, visits)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S23"})

    assert "s23" in artifacts
    rows = _read_metric(paths, "s23")
    candidate = next(r for r in rows if r["finding"] == "organic_underperforms_other_traffic")
    assert candidate["organic_engagement_rate"] == 0.0
    assert candidate["other_traffic_engagement_rate"] == 0.8
    assert candidate["organic_significantly_worse"] is True


# ── S24 — высококонверсионные SEO-страницы теряют видимость ────────────────
def test_s24_flags_high_value_page_losing_visibility(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="a", page="/blog/a", month="2026-01",
                       total_shows=100, total_clicks=20),
        _base_seo_row(query="a", page="/blog/a", month="2026-02",
                       total_shows=100, total_clicks=5),
    ])
    _write_visits(paths, [
        _base_visit(entry_page="/blog/a", form_submit=(i < 5)) for i in range(25)
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S24"})

    assert "s24" in artifacts
    rows = _read_metric(paths, "s24")
    candidate = next(r for r in rows if r["finding"] == "high_value_page_losing_visibility")
    assert candidate["page_declining"] is True
    assert candidate["high_value_page"] is True
    assert candidate["losing_visibility_candidate"] is True
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["losing_visibility_candidates"] == 1


# ── S23/S24 device-разрез (задача 5bC, промт: "используют device так же,
# как в 5bA") ────────────────────────────────────────────────────────────────
def test_s23_device_split_reports_by_device_and_excludes_nothing_from_overall(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(page="/x", total_shows=30, total_clicks=3)])
    visits = (
        [_base_visit(source_group="organic", device="desktop", entry_page="/x", form_submit=False)
         for _ in range(25)]
        + [_base_visit(source_group="paid", device="desktop", entry_page="/x", form_submit=(i < 20))
           for i in range(25)]
        + [_base_visit(source_group="organic", device="mobile", entry_page="/x", form_submit=(i < 20))
           for i in range(25)]
        + [_base_visit(source_group="paid", device="mobile", entry_page="/x", form_submit=(i < 20))
           for i in range(25)]
    )
    _write_visits(paths, visits)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S23"})

    assert "s23" in artifacts
    rows = _read_metric(paths, "s23")
    by_device = [r for r in rows if r["finding"] == "organic_underperforms_other_traffic_by_device"]
    devices_seen = {r["device"] for r in by_device}
    assert devices_seen == {"desktop", "mobile"}
    desktop_row = next(r for r in by_device if r["device"] == "desktop")
    mobile_row = next(r for r in by_device if r["device"] == "mobile")
    assert desktop_row["organic_significantly_worse"] is True
    assert mobile_row["organic_significantly_worse"] is False

    # overall (device-агностический) агрегат по-прежнему считает ВСЕ визиты страницы.
    overall = next(r for r in rows if r["finding"] == "organic_underperforms_other_traffic")
    assert overall["organic_visits"] == 50
    assert overall["other_traffic_visits"] == 50


def test_s24_device_split_flags_high_value_page_losing_visibility_by_device(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="a", page="/blog/a", month="2026-01", device="desktop",
                       total_shows=100, total_clicks=20),
        _base_seo_row(query="a", page="/blog/a", month="2026-02", device="desktop",
                       total_shows=100, total_clicks=5),
    ])
    _write_visits(paths, [
        _base_visit(entry_page="/blog/a", device="desktop", form_submit=(i < 5)) for i in range(25)
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S24"})

    assert "s24" in artifacts
    rows = _read_metric(paths, "s24")
    by_device = [r for r in rows if r["finding"] == "high_value_page_losing_visibility_by_device"]
    assert len(by_device) == 1
    assert by_device[0]["device"] == "desktop"
    assert by_device[0]["losing_visibility_candidate"] is True
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["device_losing_visibility_candidates"] == 1


def test_s24_device_split_excludes_unknown_device_but_keeps_overall(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="a", page="/blog/a", month="2026-01", device="unknown",
                       total_shows=100, total_clicks=20),
        _base_seo_row(query="a", page="/blog/a", month="2026-02", device="unknown",
                       total_shows=100, total_clicks=5),
    ])
    _write_visits(paths, [
        _base_visit(entry_page="/blog/a", form_submit=(i < 5)) for i in range(25)
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S24"})

    assert "s24" in artifacts
    rows = _read_metric(paths, "s24")
    by_device = [r for r in rows if r["finding"] == "high_value_page_losing_visibility_by_device"]
    assert by_device == []
    overall = next(r for r in rows if r["finding"] == "high_value_page_losing_visibility")
    assert overall["page_declining"] is True


# ── S25 — сниппет не использует структурированные данные/элементы выдачи ───
def test_s25_flags_snippet_gap_candidate_on_page1(tmp_path):
    paths = _Paths(tmp_path)
    rows_in = [
        _base_seo_row(query=f"q{i}", page=f"/p{i}", total_shows=100, total_clicks=10,
                       avg_show_position=7.0)
        for i in range(4)
    ]
    rows_in.append(
        _base_seo_row(query="q_outlier", page="/p_outlier", total_shows=100, total_clicks=2,
                       avg_show_position=7.0)
    )
    _write_seo_queries(paths, rows_in)

    artifacts = block4_seo.run(paths, DEFAULTS, {"S25"})

    assert "s25" in artifacts
    rows = _read_metric(paths, "s25")
    outlier = next(r for r in rows if r.get("query") == "q_outlier")
    assert outlier["snippet_gap_candidate"] is True
    assert outlier["structured_data_field_available"] is False
    assert outlier["manual_serp_check_required"] is True
    normal = next(r for r in rows if r.get("query") == "q0")
    assert normal["snippet_gap_candidate"] is False


# ── S26 — геоспрос не покрыт отдельными релевантными страницами ────────────
def test_s26_always_unavailable_wordstat_missing(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(total_shows=100, total_clicks=10)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S26"})

    assert "s26" in artifacts
    rows = _read_metric(paths, "s26")
    assert rows[0]["status"] == "unavailable"
    assert "wordstat" in rows[0]["reason"]
    assert "ядро не посчитано" in rows[0]["reason"]


def test_s26_reports_geo_candidates_when_wordstat_available(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [
        _base_seo_row(query="аренда авто", page="/catalog/", total_shows=100, total_clicks=10),
    ])
    _write_wordstat(paths, [
        _base_wordstat_row(phrase="аренда авто", normalized_phrase="аренда авто", count=25),
        _base_wordstat_row(phrase="прокат авто спб", normalized_phrase="прокат авто спб", count=30),
    ])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S26"})

    assert "s26" in artifacts
    rows = _read_metric(paths, "s26")
    summary = rows[0]
    assert "status" not in summary
    assert summary["geo_dimension_available"] is False
    assert summary["gap_candidate_count"] == 1
    candidate = next(r for r in rows if r.get("finding") == "geo_demand_without_landing_page")
    assert candidate["normalized_phrase"] == "прокат авто спб"
    assert candidate["geo_dimension_available"] is False
    assert candidate["confidence"] == "MED"


# ── S27 — JS-контент или ссылки недоступны поисковому роботу ───────────────
def test_s27_flags_js_rendering_gap_candidate(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [
        _base_site_page(
            url="https://example.com/app",
            js_content_diff=json.dumps({
                "raw_link_count": 2, "rendered_link_count": 10,
                "links_only_in_rendered": ["https://example.com/app/a", "https://example.com/app/b"],
                "text_changed": True,
            }),
        ),
    ])
    _write_seo_queries(paths, [_base_seo_row(page="/app", total_shows=30, total_clicks=2)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S27"})

    assert "s27" in artifacts
    rows = _read_metric(paths, "s27")
    candidate = next(r for r in rows if r["finding"] == "js_rendering_gap_candidate")
    assert candidate["js_rendering_gap_candidate"] is True
    assert candidate["links_only_in_rendered_count"] == 2
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["js_rendering_gap_candidates"] == 1


def test_s27_unavailable_without_site_pages(tmp_path):
    paths = _Paths(tmp_path)
    _write_seo_queries(paths, [_base_seo_row(total_shows=30)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S27"})

    assert "s27" in artifacts
    rows = _read_metric(paths, "s27")
    assert rows[0]["status"] == "unavailable"
    assert "ядро не посчитано" in rows[0]["reason"]


def test_s27_unavailable_when_js_diff_never_populated(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [_base_site_page(js_content_diff=None)])
    _write_seo_queries(paths, [_base_seo_row(total_shows=30)])

    artifacts = block4_seo.run(paths, DEFAULTS, {"S27"})

    assert "s27" in artifacts
    rows = _read_metric(paths, "s27")
    assert rows[0]["status"] == "unavailable"
    assert "ядро не посчитано" in rows[0]["reason"]


def test_run_ignores_s21_27_when_seo_queries_missing(tmp_path):
    paths = _Paths(tmp_path)
    artifacts = block4_seo.run(paths, DEFAULTS, ALL_S21_27)
    assert artifacts == []


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


# ── metrics_summary: агрегат confidence_cap блока 4 (SEO), задача 5bC ──────
def test_metrics_summary_seo_confidence_cap_counts_med_checks():
    degradation_report = {
        "checks": [
            {"check_id": "S01", "runnable": True, "confidence_cap": "HIGH"},
            {"check_id": "S07", "runnable": True, "confidence_cap": "MED"},
            {"check_id": "S26", "runnable": True, "confidence_cap": "MED"},
            {"check_id": "S27", "runnable": False, "confidence_cap": "MED"},
            {"check_id": "A01", "runnable": True, "confidence_cap": "MED"},
        ],
    }
    dispatch_result = {"artifacts": [], "block_status": {}}

    summary = compute_common.build_metrics_summary(degradation_report, dispatch_result)

    # Только runnable S-проверки: S01, S07, S26 (S27 не runnable, A01 не блок 4).
    assert summary["seo_confidence_cap"] == {
        "runnable_count": 3,
        "med_cap_count": 2,
        "med_cap_share": round(2 / 3, 4),
    }


def test_metrics_summary_seo_confidence_cap_handles_no_checks():
    summary = compute_common.build_metrics_summary({}, {"artifacts": [], "block_status": {}})
    assert summary["seo_confidence_cap"] == {
        "runnable_count": 0,
        "med_cap_count": 0,
        "med_cap_share": None,
    }
