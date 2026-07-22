"""Тесты слоя transform: build_canonical.

Юнит-тесты на чистые функции-правила (дедуп, UTM-порог, разворачивание
фиксов, бренд-классификация и остальные правила преобразований) плюс
один сквозной тест build() на минимальном сырье в формате, который
реально пишут экстракторы (см. tests/test_extract_smoke.py).
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract.metrika_logs import LOOKBACK_SUBDIR, VISIT_FIELDS, VISIT_FIELDS_BASE  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.transform import build_canonical as bc  # noqa: E402


# ═════════════════════════════ dedupe_visits ═════════════════════════════
def test_dedupe_visits_keeps_last_by_dt():
    df = pd.DataFrame([
        {"visit_id": "v1", "dt": datetime(2026, 5, 1, 10, 0), "tag": "first"},
        {"visit_id": "v1", "dt": datetime(2026, 5, 1, 12, 0), "tag": "last"},
        {"visit_id": "v2", "dt": datetime(2026, 5, 2, 9, 0), "tag": "only"},
    ])
    out = bc.dedupe_visits(df)
    assert len(out) == 2
    v1 = out[out["visit_id"] == "v1"].iloc[0]
    assert v1["tag"] == "last"


def test_dedupe_visits_no_duplicates_is_noop():
    df = pd.DataFrame([
        {"visit_id": "v1", "dt": datetime(2026, 5, 1, 10, 0)},
        {"visit_id": "v2", "dt": datetime(2026, 5, 2, 9, 0)},
    ])
    out = bc.dedupe_visits(df)
    assert len(out) == 2


def test_dedupe_visits_empty_df():
    df = pd.DataFrame(columns=["visit_id", "dt"])
    out = bc.dedupe_visits(df)
    assert out.empty


# ═════════════════════════════ apply_utm_threshold ═══════════════════════
def test_utm_threshold_below_25_percent_stays_ad():
    """3 из 20 ad-визитов (15%) без utm < порога 0.25 -> все остаются ad."""
    source_group = pd.Series(["ad"] * 20 + ["organic"] * 5)
    utm = pd.Series((["fb_camp"] * 17 + [""] * 3) + [""] * 5)
    source_final, uncertain, frac = bc.apply_utm_threshold(source_group, utm, 0.25)

    assert frac == pytest.approx(3 / 20)
    assert uncertain is False
    assert (source_final.iloc[:20] == "ad").all()
    # неплатный трафик не трогаем правилом порога вовсе
    assert (source_final.iloc[20:] == "organic").all()


def test_utm_threshold_at_or_above_25_percent_marks_undefined():
    """6 из 20 ad-визитов (30%) без utm >= порога 0.25 -> undefined + флаг."""
    source_group = pd.Series(["ad"] * 20)
    utm = pd.Series(["fb_camp"] * 14 + [""] * 6)
    source_final, uncertain, frac = bc.apply_utm_threshold(source_group, utm, 0.25)

    assert frac == pytest.approx(6 / 20)
    assert uncertain is True
    assert (source_final.iloc[:14] == "ad").all()
    assert (source_final.iloc[14:] == "undefined").all()


def test_utm_threshold_recognizes_ne_opredeleno_token():
    source_group = pd.Series(["ad", "ad", "ad", "ad"])
    utm = pd.Series(["camp", "Не определено", "  ", None])
    source_final, uncertain, frac = bc.apply_utm_threshold(source_group, utm, 0.25)
    assert frac == pytest.approx(3 / 4)
    assert uncertain is True


def test_utm_threshold_no_ad_visits_no_uncertainty():
    source_group = pd.Series(["organic", "direct"])
    utm = pd.Series(["", ""])
    source_final, uncertain, frac = bc.apply_utm_threshold(source_group, utm, 0.25)
    assert frac == 0.0
    assert uncertain is False
    assert list(source_final) == ["organic", "direct"]


# ═════════════════════════════ expand_manual_costs ═══════════════════════
def test_expand_manual_costs_splits_fee_across_days_in_month():
    costs_manual = {"agency_fee_rub_month": 30000, "seo_fee_rub_month": 0, "other": []}
    rows = bc.expand_manual_costs(costs_manual, date(2026, 6, 1), date(2026, 6, 30))
    assert len(rows) == 30  # июнь — 30 дней, один фикс
    assert all(r["source_tag"] == "agency_fee" for r in rows)
    assert all(r["cost_raw"] == pytest.approx(1000.0) for r in rows)  # 30000/30
    assert rows[0]["date"] == date(2026, 6, 1)
    assert rows[0]["campaign_id"] is None


def test_expand_manual_costs_multiple_fees_and_month_boundary():
    costs_manual = {
        "agency_fee_rub_month": 31000,   # июль — 31 день -> 1000/день
        "seo_fee_rub_month": 28000,      # февраль (невисокосный) -> но тут окно июль -> 28000/31
        "other": [{"name": "yandex.biz", "rub_month": 3100, "source_tag": "yandex_business"}],
    }
    rows = bc.expand_manual_costs(costs_manual, date(2026, 7, 1), date(2026, 7, 2))
    assert len(rows) == 6  # 2 дня x 3 фикса
    by_tag = {r["source_tag"]: r for r in rows if r["date"] == date(2026, 7, 1)}
    assert by_tag["agency_fee"]["cost_raw"] == pytest.approx(1000.0)
    assert by_tag["seo_fee"]["cost_raw"] == pytest.approx(28000 / 31)
    assert by_tag["yandex_business"]["cost_raw"] == pytest.approx(100.0)
    assert by_tag["yandex_business"]["campaign_name"] == "yandex.biz"


def test_expand_manual_costs_invalid_source_tag_falls_back_to_other():
    costs_manual = {"other": [{"name": "x", "rub_month": 1000, "source_tag": "garbage"}]}
    rows = bc.expand_manual_costs(costs_manual, date(2026, 6, 1), date(2026, 6, 1))
    assert rows[0]["source_tag"] == "other"


def test_expand_manual_costs_no_fees_returns_empty():
    rows = bc.expand_manual_costs({}, date(2026, 6, 1), date(2026, 6, 30))
    assert rows == []


def test_expand_manual_costs_zero_fees_are_skipped():
    costs_manual = {"agency_fee_rub_month": 0, "seo_fee_rub_month": 0, "other": [{"rub_month": 0}]}
    rows = bc.expand_manual_costs(costs_manual, date(2026, 6, 1), date(2026, 6, 30))
    assert rows == []


# ═════════════════════════════ is_brand_query ════════════════════════════
def test_is_brand_query_case_insensitive_match():
    assert bc.is_brand_query("Купить Погнали аренда", ["погнали"]) is True
    assert bc.is_brand_query("ПОГНАЛИ.RENT отзывы", ["Погнали"]) is True


def test_is_brand_query_no_match():
    assert bc.is_brand_query("аренда авто спб", ["погнали"]) is False


def test_is_brand_query_empty_terms_or_query():
    assert bc.is_brand_query("аренда авто", []) is False
    assert bc.is_brand_query(None, ["погнали"]) is False
    assert bc.is_brand_query("погнали рент", [""]) is False


# ═════════════════════════════ classify_traffic_source ═══════════════════
@pytest.mark.parametrize("raw,expected", [
    ("ad", "ad"),
    ("cpa_network", "ad"),
    ("search_engine", "organic"),
    ("direct", "direct"),
    ("link", "referral"),
    ("recommendation_system", "referral"),
    ("internal", "internal"),
    ("social_network", "social"),
    ("messenger", "messenger"),
    ("email", "other"),
    ("something_unknown", "other"),
    ("", "other"),
    (None, "other"),
])
def test_classify_traffic_source_mapping_table(raw, expected):
    assert bc.classify_traffic_source(raw) == expected


# ═════════════════════════════ map_device ═════════════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("1", "desktop"), ("2", "mobile"), ("3", "tablet"), ("4", "tv"),
    ("99", "desktop"), ("", "desktop"), (None, "desktop"),
])
def test_map_device(raw, expected):
    assert bc.map_device(raw) == expected


# ═════════════════════════════ goal_flags / parse_goal_ids ═══════════════
def test_parse_goal_ids_splits_on_common_delimiters():
    assert bc.parse_goal_ids("1,2; 3|4") == ["1", "2", "3", "4"]
    assert bc.parse_goal_ids("") == []
    assert bc.parse_goal_ids(None) == []


def test_goal_flags_marks_visit_level_achievements_and_counts_submits():
    goals_cfg = {
        "form_open_goal_ids": [10],
        "form_submit_goal_ids": [20],
        "call_click_goal_ids": [30],
        "messenger_goal_ids": [40],
    }
    flags = bc.goal_flags(["10", "20", "20", "99"], goals_cfg)
    assert flags == {
        "form_open": True, "form_submit": True, "call_click": False,
        "messenger_click": False, "form_submit_count": 2,
    }


def test_goal_flags_no_achievements():
    goals_cfg = {"form_open_goal_ids": [10]}
    flags = bc.goal_flags([], goals_cfg)
    assert flags["form_open"] is False
    assert flags["form_submit_count"] == 0


# ═════════════════════════════ normalize_entry_page ═══════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("https://site.ru/Cars/", "/cars"),
    ("https://site.ru/", "/"),
    ("https://site.ru", "/"),
    ("https://site.ru/cars?utm_source=x#frag", "/cars"),
    (None, "/"),
])
def test_normalize_entry_page(raw, expected):
    assert bc.normalize_entry_page(raw) == expected


# ═════════════════════════════ classify_strategy_optimize_for ════════════
@pytest.mark.parametrize("raw,expected", [
    ("HIGHEST_POSITION", "clicks"),
    ("AVERAGE_CPC", "clicks"),
    ("AVERAGE_CPA", "conversions"),
    ("WB_MAXIMUM_CONVERSION_RATE", "conversions"),
    ("SOME_NEW_STRATEGY", "unknown"),
    (None, "unknown"),
])
def test_classify_strategy_optimize_for(raw, expected):
    assert bc.classify_strategy_optimize_for(raw) == expected


def test_extract_bidding_strategy_type_from_nested_search_scope():
    campaign = {"Id": 1, "Name": "x", "TextCampaign": {
        "BiddingStrategy": {"Search": {"BiddingStrategyType": "AVERAGE_CPA"}}}}
    assert bc._extract_bidding_strategy_type(campaign) == "AVERAGE_CPA"


def test_extract_bidding_strategy_type_missing_returns_none():
    assert bc._extract_bidding_strategy_type({"Id": 1, "Name": "x"}) is None


# ═════════════════════════════ crm normalization ══════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("won", "won"), ("lost", "lost"), ("in_progress", "in_progress"),
    ("new", "new"), ("", "unknown"), (None, "unknown"), ("garbage", "unknown"),
])
def test_normalize_crm_status(raw, expected):
    assert bc.normalize_crm_status(raw) == expected


def test_normalize_crm_source_lowercases_and_strips():
    assert bc.normalize_crm_source("  Яндекс.Директ  ") == "яндекс.директ"
    assert bc.normalize_crm_source(None) == "unknown"
    assert bc.normalize_crm_source("") == "unknown"


# ═════════════════════════════ build(): сквозной тест ═════════════════════
class _Paths:
    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.canonical = root / "data" / "canonical"


def _write_metrika_logs_fixture(raw_dir: Path) -> None:
    out_dir = raw_dir / "metrika_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    header = "\t".join(VISIT_FIELDS)

    def row(visit_id, client_id, dt, source, utm_source, goals):
        values = {
            "ym:s:visitID": visit_id,
            "ym:s:clientID": client_id,
            "ym:s:dateTime": dt,
            "ym:s:lastsignTrafficSource": source,
            "ym:s:lastsignUTMSource": utm_source,
            "ym:s:lastsignUTMMedium": "cpc",
            "ym:s:lastsignUTMCampaign": "spring",
            "ym:s:lastSignDirectClickOrder": "",
            "ym:s:deviceCategory": "2",
            "ym:s:startURL": "https://site.ru/Cars/?utm_source=x",
            "ym:s:goalsID": goals,
            "ym:s:referer": "",
            "ym:s:isNewUser": "1",
            "ym:s:pageViews": "3",
            "ym:s:visitDuration": "120",
            # Поля патча — transform их пока не читает, но выгрузка их несёт.
            "ym:s:lastTrafficSource": source,
            "ym:s:browser": "chrome",
            "ym:s:operatingSystem": "android",
            "ym:s:screenWidth": "1080",
            "ym:s:screenHeight": "2400",
            "ym:s:regionCountry": "225",
            "ym:s:regionCity": "213",
        }
        # Недостающие (напр. пробные yclid/gclid) -> пустая ячейка.
        return "\t".join(values.get(f, "") for f in VISIT_FIELDS)

    lines = [header]
    # v1 дублируется дважды -> должна остаться версия с более поздним dateTime.
    lines.append(row("v1", "c1", "2026-06-01 10:00:00", "ad", "", "20"))
    lines.append(row("v1", "c1", "2026-06-01 15:00:00", "ad", "yandex", "20,20"))
    lines.append(row("v2", "c2", "2026-06-02 09:00:00", "search_engine", "", ""))

    text = "\n".join(lines) + "\n"
    with gzip.open(out_dir / "visits_2026-06-01_2026-06-30_part000.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write(text)


def _write_direct_fixture(raw_dir: Path) -> None:
    out_dir = raw_dir / "direct"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Legacy flat file — используется build_costs (CAMPAIGN_PERFORMANCE_REPORT).
    (out_dir / "campaign_performance.tsv").write_text(
        "CampaignId\tCampaignName\tCost\tClicks\tImpressions\tDate\n"
        "1\tПоиск\t5000000\t10\t200\t2026-06-01\n",
        encoding="utf-8",
    )

    # Новый формат: помесячные чанки для build_direct_queries.
    queries_dir = out_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить машину\tbroad\tDESKTOP\t2000000\t3\t50\t1\n",
        encoding="utf-8",
    )

    # Legacy файл (для smoke-тестов экстрактора, не используется transform).
    (out_dir / "search_query_performance.tsv").write_text(
        "Query\tCampaignId\tAdGroupId\tCost\tClicks\tConversions\n"
        "купить машину\t1\t11\t2000000\t3\t1\n",
        encoding="utf-8",
    )

    strategies = [{"Id": 1, "Name": "Поиск", "TextCampaign": {
        "BiddingStrategy": {"Search": {"BiddingStrategyType": "AVERAGE_CPA"}}}}]
    (out_dir / "campaign_strategies.json").write_text(
        json.dumps(strategies, ensure_ascii=False), encoding="utf-8"
    )


def test_build_writes_only_tables_with_raw_source(tmp_path):
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    _write_metrika_logs_fixture(paths.raw)
    _write_direct_fixture(paths.raw)

    manifest_mod.update_source(
        paths.raw, "metrika_logs", date_from="2026-06-01", date_to="2026-06-30",
        rows=3, script_version="test", canonical_tables=["visits"],
    )
    manifest_mod.update_source(
        paths.raw, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=2, script_version="test", canonical_tables=["costs", "direct_queries"],
        extra={"cost_basis": "net_no_vat", "cost_micros_per_rub": 1_000_000},
    )

    config = {
        "goals": {"form_submit_goal_ids": [20]},
        "costs_manual": {"agency_fee_rub_month": 3000},
        "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-02"},
        "brand_terms": [],
    }
    defaults = {"utm_undefined_threshold": 0.25}

    built = bc.build(paths, config, defaults)

    assert set(built) == {"visits", "costs", "direct_queries", "campaign_strategies"}
    assert not (paths.canonical / "seo_queries.parquet").exists()
    assert not (paths.canonical / "crm.parquet").exists()

    visits = pd.read_parquet(paths.canonical / "visits.parquet")
    assert len(visits) == 2  # v1 дедуп в одну строку
    v1 = visits[visits["visit_id"] == "v1"].iloc[0]
    assert v1["form_submit_count"] == 2       # goalsID "20,20" во второй (последней) версии v1
    assert v1["source_group"] == "ad"
    assert v1["utm_source_raw"] == "yandex"    # взята последняя по dateTime версия
    assert v1["entry_page"] == "/cars"
    v2 = visits[visits["visit_id"] == "v2"].iloc[0]
    assert v2["source_group"] == "organic"
    assert v2["is_ad"] == False

    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    # 1 строка Директа (2026-06-01) + 2 дня ручного фикса (agency_fee).
    assert len(costs) == 3
    assert set(costs["source_tag"]) == {"direct", "agency_fee"}
    direct_row = costs[costs["source_tag"] == "direct"].iloc[0]
    assert direct_row["cost_raw"] == pytest.approx(5.0)
    # no finance config in this test -> VAT basis unknown
    assert direct_row["cost_status"] == "vat_basis_unknown"
    assert pd.isna(direct_row["cost_normalized"])

    dq = pd.read_parquet(paths.canonical / "direct_queries.parquet")
    assert dq.iloc[0]["query"] == "купить машину"
    assert dq.iloc[0]["campaign_name"] == "Поиск"
    assert str(dq.iloc[0]["date"]) == "2026-06-01"
    assert dq.iloc[0]["cost_raw"] == 2000000
    # cost_rub — валютная конверсия, считается всегда (не зависит от Q01).
    assert dq.iloc[0]["cost_rub"] == pytest.approx(2.0)
    # cost_normalized — НДС-нормализация; на этом слое ещё null (Q01 не применён).
    assert pd.isna(dq.iloc[0]["cost_normalized"])
    assert dq.iloc[0]["vat_basis_applied"] == False  # noqa: E712 — numpy bool, `is` fails
    assert dq.iloc[0]["conversions_all"] == 1
    assert dq.iloc[0]["match_type"] == "broad"

    cs = pd.read_parquet(paths.canonical / "campaign_strategies.parquet")
    assert cs.iloc[0]["optimize_for"] == "conversions"

    canonical_manifest = json.loads((paths.canonical / "manifest.json").read_text("utf-8"))
    assert set(canonical_manifest["tables"]) == set(built)
    assert canonical_manifest["flags"]["utm_uncertain"] is False


# ═════ direct_queries/campaigns/geo: cost_rub vs cost_normalized (Q01) ═════
# cost_rub — валютная конверсия (cost_raw / 1_000_000), считается всегда,
# независимо от того, получен ли ответ на Q01 (finance.vat_basis_by_source).
# cost_normalized — НДС-нормализация; на слое transform всегда null,
# vat_basis_applied всегда False — их заполняет compute после Q01. Не путать
# с costs.parquet, где cost_normalized/cost_status считаются уже здесь, в
# transform, через _vat_lookup/_apply_vat_to_rows (другая таблица, другой
# контракт) — см. test_costs_vat_* ниже.

def _write_direct_queries_fixture(direct_dir: Path) -> None:
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить машину\tbroad\tDESKTOP\t65630000\t3\t50\t1\n",
        encoding="utf-8",
    )


def test_build_direct_queries_cost_rub_always_computed_cost_normalized_null(tmp_path):
    direct_dir = tmp_path / "direct"
    _write_direct_queries_fixture(direct_dir)

    df = bc.build_direct_queries(direct_dir, None)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["cost_raw"] == 65630000
    assert row["cost_rub"] == pytest.approx(65.63)
    assert pd.isna(row["cost_normalized"])
    assert row["vat_basis_applied"] == False  # noqa: E712


def _write_direct_campaigns_fixture(direct_dir: Path) -> None:
    campaigns_dir = direct_dir / "campaigns"
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    (campaigns_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tDevice\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\tDESKTOP\t65630000\t3\t50\t1\n",
        encoding="utf-8",
    )


def test_build_direct_campaigns_cost_rub_always_computed_cost_normalized_null(tmp_path):
    direct_dir = tmp_path / "direct"
    _write_direct_campaigns_fixture(direct_dir)

    df = bc.build_direct_campaigns(direct_dir, None)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["cost_raw"] == 65630000
    assert row["cost_rub"] == pytest.approx(65.63)
    assert pd.isna(row["cost_normalized"])
    assert row["vat_basis_applied"] == False  # noqa: E712


def _write_direct_geo_fixture(direct_dir: Path) -> None:
    geo_dir = direct_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    (geo_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tLocationOfPresenceId\tLocationOfPresenceName\t"
        "Device\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t213\tМосква\tDESKTOP\t65630000\t3\t50\t1\n",
        encoding="utf-8",
    )


def test_build_direct_geo_cost_rub_always_computed_cost_normalized_null(tmp_path):
    direct_dir = tmp_path / "direct"
    _write_direct_geo_fixture(direct_dir)

    df = bc.build_direct_geo(direct_dir, None)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["cost_raw"] == 65630000
    assert row["cost_rub"] == pytest.approx(65.63)
    assert pd.isna(row["cost_normalized"])
    assert row["vat_basis_applied"] == False  # noqa: E712


def test_direct_queries_parquet_schema_has_cost_rub_and_cost_normalized(tmp_path):
    """Схема parquet: cost_rub float64 (никогда null здесь), cost_normalized

    float64 (всегда null на этом слое), vat_basis_applied bool (всегда False).
    """
    import pyarrow.parquet as pq

    direct_dir = tmp_path / "direct"
    _write_direct_queries_fixture(direct_dir)
    df = bc.build_direct_queries(direct_dir, None)

    out_path = tmp_path / "direct_queries.parquet"
    bc._write_direct_table(df, "direct_queries", out_path)

    schema = pq.read_schema(out_path)
    assert str(schema.field("cost_rub").type) == "double"
    assert str(schema.field("cost_normalized").type) == "double"
    assert str(schema.field("vat_basis_applied").type) == "bool"

    written = pd.read_parquet(out_path)
    assert written.iloc[0]["cost_rub"] == pytest.approx(65.63)
    assert pd.isna(written.iloc[0]["cost_normalized"])
    assert written.iloc[0]["vat_basis_applied"] == False  # noqa: E712


def test_join_goal_convs_invariant_uses_cost_rub_not_cost_normalized(tmp_path):
    """_join_goal_convs проверяет сумму cost_rub (реальная валютная величина);

    cost_normalized всегда null на этом слое, сравнивать его сумму бессмысленно
    и раньше приводило бы к ложному инварианту (0.0 == 0.0 всегда).
    """
    direct_dir = tmp_path / "direct"
    _write_direct_queries_fixture(direct_dir)

    goals_dir = direct_dir / "queries" / "goals" / "goal_10"
    goals_dir.mkdir(parents=True, exist_ok=True)
    (goals_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить машину\tbroad\tDESKTOP\t1\n",
        encoding="utf-8",
    )

    df = bc.build_direct_queries(
        direct_dir, None, macro_goals=[{"id": 10}],
    )
    assert "goal_conv_10" in df.columns
    assert df.iloc[0]["goal_conv_10"] == 1
    # cost_rub не изменился джойном (иначе _join_goal_convs бросил бы ValueError).
    assert df.iloc[0]["cost_rub"] == pytest.approx(65.63)


# ═════════════════ build(): placements/geo-monthly/ad_texts подключены ═════
def test_build_wires_placements_geo_monthly_and_ad_texts(tmp_path):
    """Сквозной build() производит direct_placements.parquet/geo.parquet/

    ad_texts.json+ad_texts_archived.json — не только модули существуют
    изолированно (задача 4X-direct-wiring).
    """
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    direct_dir = paths.raw / "direct"
    direct_dir.mkdir(parents=True, exist_ok=True)

    placements_dir = direct_dir / "placements"
    placements_dir.mkdir(parents=True, exist_ok=True)
    (placements_dir / "placement_performance.tsv").write_text(
        "Placement\tAdNetworkType\tCampaignId\tCost\tClicks\tConversions\n"
        "site.ru\tYANDEX_NETWORK\t1\t65630000\t12\t2\n",
        encoding="utf-8",
    )

    geo_dir = direct_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    (geo_dir / "2026-05.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tLocationOfPresenceId\tLocationOfPresenceName\t"
        "Device\tCost\tClicks\tImpressions\tConversions\n"
        "2026-05-10\t1\tПоиск\t213\tМосква\tDESKTOP\t5000000\t10\t100\t1\n",
        encoding="utf-8",
    )
    (geo_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tLocationOfPresenceId\tLocationOfPresenceName\t"
        "Device\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t213\tМосква\tDESKTOP\t2000000\t4\t40\t0\n",
        encoding="utf-8",
    )

    (direct_dir / "ad_texts.json").write_text(
        json.dumps({"ads": [
            {"Id": 1, "CampaignId": 1, "State": "ACTIVE", "TextAd": {"Title": "A"}},
            {"Id": 2, "CampaignId": 1, "State": "ARCHIVED", "TextAd": {"Title": "B"}},
        ], "extensions": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest_mod.update_source(
        paths.raw, "direct", date_from="2026-05-01", date_to="2026-06-30",
        rows=0, script_version="test", canonical_tables=["costs", "direct_queries"],
    )

    config = {"data_window": {"date_from": "2026-05-01", "date_to": "2026-06-30"}}
    defaults = {"utm_undefined_threshold": 0.25}

    built = bc.build(paths, config, defaults)

    assert "direct_placements" in built
    assert "geo" in built

    dp = pd.read_parquet(paths.canonical / "direct_placements.parquet")
    assert len(dp) == 1
    assert dp.iloc[0]["cost_raw"] == 65630000
    assert dp.iloc[0]["cost_rub"] == pytest.approx(65.63)
    assert pd.isna(dp.iloc[0]["cost_normalized"])

    geo = pd.read_parquet(paths.canonical / "geo.parquet")
    assert len(geo) == 2
    assert set(geo["month"]) == {"2026-05", "2026-06"}
    # Исходные помесячные файлы geo не потеряны/не изменены.
    assert (geo_dir / "2026-05.tsv").exists()
    assert (geo_dir / "2026-06.tsv").exists()

    active_path = paths.canonical / "ad_texts.json"
    archived_path = paths.canonical / "ad_texts_archived.json"
    assert active_path.exists()
    assert archived_path.exists()
    active_payload = json.loads(active_path.read_text(encoding="utf-8"))
    archived_payload = json.loads(archived_path.read_text(encoding="utf-8"))
    assert {a["Id"] for a in active_payload["ads"]} == {1}
    assert {a["Id"] for a in archived_payload["ads"]} == {2}

    canonical_manifest = json.loads((paths.canonical / "manifest.json").read_text("utf-8"))
    assert "direct_placements" in canonical_manifest["tables"]
    assert "geo" in canonical_manifest["tables"]


def test_build_no_ad_texts_source_writes_no_ad_texts_files(tmp_path):
    """Без raw ad_texts.json — canonical ad_texts.json/ad_texts_archived.json не создаются."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    direct_dir = paths.raw / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить машину\tbroad\tDESKTOP\t2000000\t3\t50\t1\n",
        encoding="utf-8",
    )
    manifest_mod.update_source(
        paths.raw, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["direct_queries"],
    )

    bc.build(paths, {"data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}},
              {"utm_undefined_threshold": 0.25})

    assert not (paths.canonical / "ad_texts.json").exists()
    assert not (paths.canonical / "ad_texts_archived.json").exists()


# ═════════════════════ build_visits: склейка base + backfill ═══════════════
_BACKFILL_HEADER = [
    "ym:s:visitID", "ym:s:lastTrafficSource", "ym:s:browser", "ym:s:operatingSystem",
    "ym:s:screenWidth", "ym:s:screenHeight", "ym:s:regionCountry", "ym:s:regionCity",
]


def _write_base_visits(metrika_dir: Path, visits: list[dict]) -> None:
    """Базовый слой visits_*.csv.gz — ТОЛЬКО базовые поля (без полей патча)."""
    metrika_dir.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(VISIT_FIELDS_BASE)]
    for v in visits:
        cells = {f: "" for f in VISIT_FIELDS_BASE}
        cells["ym:s:visitID"] = v["id"]
        cells["ym:s:clientID"] = v.get("cid", "c")
        cells["ym:s:dateTime"] = v["dt"]
        cells["ym:s:lastsignTrafficSource"] = v.get("src", "direct")
        cells["ym:s:deviceCategory"] = v.get("dev", "2")
        cells["ym:s:startURL"] = v.get("url", "https://site.ru/")
        cells["ym:s:goalsID"] = v.get("goals", "")
        cells["ym:s:isNewUser"] = v.get("new", "0")
        cells["ym:s:pageViews"] = v.get("pv", "1")
        cells["ym:s:visitDuration"] = v.get("dur", "10")
        lines.append("\t".join(cells[f] for f in VISIT_FIELDS_BASE))
    with gzip.open(metrika_dir / "visits_2026-06-01_2026-06-30_part000.csv.gz",
                   "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_backfill(metrika_dir: Path, rows: list[dict],
                    fname: str = "visits_backfill_2026-06-01_2026-06-30_part000.csv.gz") -> None:
    """Backfill-слой в подкаталоге backfill/ (visits_backfill_*.csv.gz)."""
    bf_dir = metrika_dir / "backfill"
    bf_dir.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(_BACKFILL_HEADER)]
    for r in rows:
        lines.append("\t".join(str(r.get(h, "")) for h in _BACKFILL_HEADER))
    with gzip.open(bf_dir / fname, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def test_build_visits_base_plus_backfill_integration(tmp_path):
    """3 базовых визита + 3 backfill-строки (с дублем ключа) -> 3 строки, поля заполнены."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [
        {"id": "v1", "dt": "2026-06-01 10:00:00", "src": "ad"},
        {"id": "v2", "dt": "2026-06-02 10:00:00", "src": "direct"},
        {"id": "v3", "dt": "2026-06-03 10:00:00", "src": "search_engine"},  # без backfill
    ])
    _write_backfill(metrika, [
        {"ym:s:visitID": "v1", "ym:s:lastTrafficSource": "ad", "ym:s:browser": "chrome",
         "ym:s:operatingSystem": "android", "ym:s:screenWidth": "360", "ym:s:screenHeight": "780",
         "ym:s:regionCountry": "Russia", "ym:s:regionCity": "Vladivostok"},
        {"ym:s:visitID": "v2", "ym:s:lastTrafficSource": "internal", "ym:s:browser": "safari",
         "ym:s:operatingSystem": "ios", "ym:s:screenWidth": "390", "ym:s:screenHeight": "844",
         "ym:s:regionCountry": "Russia", "ym:s:regionCity": "Moscow"},
        # Дубль ключа v2 -> детерминированно побеждает ПОСЛЕДНЯЯ строка.
        {"ym:s:visitID": "v2", "ym:s:lastTrafficSource": "organic", "ym:s:browser": "firefox",
         "ym:s:operatingSystem": "windows", "ym:s:screenWidth": "1920", "ym:s:screenHeight": "1080",
         "ym:s:regionCountry": "Russia", "ym:s:regionCity": "Kazan"},
    ])

    df, _utm, stats = bc.build_visits(metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    # Итог 3 строки: дубль ключа НЕ размножает, визит без backfill сохранён.
    assert len(df) == 3
    assert set(df["visit_id"]) == {"v1", "v2", "v3"}

    v1 = df[df.visit_id == "v1"].iloc[0]
    assert v1["last_traffic_source_naive"] == "ad"
    assert v1["browser"] == "chrome" and v1["os"] == "android"
    assert v1["screen_width"] == 360 and v1["screen_height"] == 780
    assert v1["screen_resolution"] == "360x780"
    assert v1["region_country"] == "Russia" and v1["region_city"] == "Vladivostok"
    # source_group (last-significant) НЕ трогается наивной моделью.
    assert v1["source_group"] == "ad"

    # v2: детерминированный дедуп -> победила последняя backfill-строка.
    v2 = df[df.visit_id == "v2"].iloc[0]
    assert v2["last_traffic_source_naive"] == "organic"
    assert v2["screen_resolution"] == "1920x1080"
    assert v2["source_group"] == "direct"   # lastsign нетронут

    # v3 без backfill: новые поля null, сам визит на месте.
    v3 = df[df.visit_id == "v3"].iloc[0]
    assert pd.isna(v3["last_traffic_source_naive"])
    assert pd.isna(v3["screen_width"]) and pd.isna(v3["screen_resolution"])
    assert v3["source_group"] == "organic"

    # is_robot присутствует, но НЕ заполнен (API не отдаёт) — нигде не false.
    assert df["is_robot"].isna().all()

    assert stats["backfill_rows"] == 3
    assert stats["backfill_dedup_dropped"] == 1
    assert stats["backfill_matched"] == 2
    assert stats["backfill_unmatched"] == 0
    assert stats["is_robot_available"] is False


def test_build_visits_unmatched_backfill_recorded(tmp_path):
    """Backfill-ключ без базового визита не добавляет строк, но фиксируется в статистике."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [{"id": "v1", "dt": "2026-06-01 10:00:00", "src": "ad"}])
    _write_backfill(metrika, [
        {"ym:s:visitID": "v1", "ym:s:lastTrafficSource": "ad", "ym:s:browser": "chrome"},
        {"ym:s:visitID": "ghost", "ym:s:lastTrafficSource": "direct"},  # нет в базовых
    ])

    df, _utm, stats = bc.build_visits(metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    assert len(df) == 1 and set(df["visit_id"]) == {"v1"}
    assert stats["backfill_matched"] == 1
    assert stats["backfill_unmatched"] == 1


def test_build_visits_without_backfill_keeps_base_null_fields(tmp_path):
    """Нет backfill/ -> новые колонки существуют и null, базовый визит сохранён."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [{"id": "v1", "dt": "2026-06-01 10:00:00", "src": "ad"}])

    df, _utm, stats = bc.build_visits(metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    assert len(df) == 1
    for col in ("last_traffic_source_naive", "browser", "os", "screen_width",
                "screen_height", "screen_resolution", "region_country", "region_city",
                "is_robot"):
        assert col in df.columns
        assert pd.isna(df.iloc[0][col])
    assert stats["backfill_rows"] == 0 and stats["backfill_unmatched"] == 0


def test_build_visits_parquet_dtypes_and_original_columns(tmp_path):
    """Сквозной build(): screen_* — int64 nullable, is_robot — bool nullable, 16 базовых на месте."""
    import pyarrow.parquet as pq

    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    metrika = paths.raw / "metrika_logs"
    _write_base_visits(metrika, [{"id": "v1", "dt": "2026-06-01 10:00:00", "src": "ad"}])
    _write_backfill(metrika, [
        {"ym:s:visitID": "v1", "ym:s:lastTrafficSource": "ad",
         "ym:s:screenWidth": "360", "ym:s:screenHeight": "780"},
    ])
    manifest_mod.update_source(
        paths.raw, "metrika_logs", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["visits"],
    )

    built = bc.build(paths, {"goals": {}}, {"utm_undefined_threshold": 0.25})
    assert "visits" in built

    schema = pq.read_schema(paths.canonical / "visits.parquet")
    assert str(schema.field("screen_width").type) == "int64"
    assert str(schema.field("screen_height").type) == "int64"
    assert str(schema.field("is_robot").type) == "bool"

    visits = pd.read_parquet(paths.canonical / "visits.parquet")
    assert visits.iloc[0]["screen_width"] == 360
    assert visits.iloc[0]["last_traffic_source_naive"] == "ad"
    assert visits["is_robot"].isna().all()

    # Исходные 16 колонок и их наличие не изменены.
    for col in ("visit_id", "client_id", "dt", "date", "device", "source_group",
                "utm_source_raw", "source_final", "is_ad", "entry_page", "form_open",
                "form_submit", "call_click", "messenger_click", "form_submit_count",
                "is_new_user"):
        assert col in visits.columns

    canonical_manifest = json.loads((paths.canonical / "manifest.json").read_text("utf-8"))
    assert canonical_manifest["flags"]["metrika_backfill"]["is_robot_available"] is False


# ═════════════════════ build_visits: lookback (4X-lookback-canonical-flag) ═
def _write_lookback_visits(
    metrika_dir: Path, visits: list[dict],
    fname: str = "visits_lookback_2026-05-01_2026-05-31_part000.csv.gz",
) -> None:
    """Лукбэк-слой lookback/visits_lookback_*.csv.gz — тот же формат, что и база."""
    lookback_dir = metrika_dir / LOOKBACK_SUBDIR
    lookback_dir.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(VISIT_FIELDS_BASE)]
    for v in visits:
        cells = {f: "" for f in VISIT_FIELDS_BASE}
        cells["ym:s:visitID"] = v["id"]
        cells["ym:s:clientID"] = v.get("cid", "c")
        cells["ym:s:dateTime"] = v["dt"]
        cells["ym:s:lastsignTrafficSource"] = v.get("src", "direct")
        cells["ym:s:deviceCategory"] = v.get("dev", "2")
        cells["ym:s:startURL"] = v.get("url", "https://site.ru/")
        cells["ym:s:goalsID"] = v.get("goals", "")
        cells["ym:s:isNewUser"] = v.get("new", "0")
        cells["ym:s:pageViews"] = v.get("pv", "1")
        cells["ym:s:visitDuration"] = v.get("dur", "10")
        lines.append("\t".join(cells[f] for f in VISIT_FIELDS_BASE))
    with gzip.open(lookback_dir / fname, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


_LOOKBACK_CONFIG = {
    "goals": {},
    "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
}
_LOOKBACK_DEFAULTS = {
    "utm_undefined_threshold": 0.25,
    "transform": {"traffic_resolve_lookback_days": 30},
}


def test_build_visits_lookback_rows_tagged_and_used_for_carry_forward(tmp_path):
    """Лукбэк-визит того же clientID ДО основного окна восстанавливает источник
    визита с ambiguous (internal) lastsign в основном окне — то, что раньше
    было архитектурно невозможно (см. заменённый test_lookback_wiring_check.py:
    test_build_visits_does_not_see_lookback_subdir_rows)."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [
        {"id": "v1", "cid": "c1", "dt": "2026-06-05 10:00:00", "src": "internal"},
    ])
    # В пределах lookback_cutoff (2026-06-01 - 30д = 2026-05-02) -> учитывается.
    _write_lookback_visits(metrika, [
        {"id": "vlb", "cid": "c1", "dt": "2026-05-20 09:00:00", "src": "ad"},
    ])

    df, _utm, stats = bc.build_visits(metrika, _LOOKBACK_CONFIG, _LOOKBACK_DEFAULTS)

    assert set(df["visit_id"]) == {"v1", "vlb"}
    by_id = df.set_index("visit_id")
    assert by_id.loc["vlb", "is_lookback_only"] == True  # noqa: E712
    assert by_id.loc["v1", "is_lookback_only"] == False  # noqa: E712

    # Цепочка carry-forward дотянулась через границу окна до лукбэк-визита.
    assert by_id.loc["v1", "source_group_resolved"] == "ad"
    assert by_id.loc["v1", "traffic_source_resolved"] == True  # noqa: E712

    # Лукбэк не входит в знаменатель traffic_source_resolve (только осн. окно).
    assert stats["traffic_source_resolve"]["internal_or_undefined_total"] == 1
    assert stats["traffic_source_resolve"]["unresolved_count"] == 0


def test_build_visits_without_lookback_dir_stays_unresolved(tmp_path):
    """Без lookback/ тот же ambiguous-визит остаётся unresolved (контраст к тесту выше)."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [
        {"id": "v1", "cid": "c1", "dt": "2026-06-05 10:00:00", "src": "internal"},
    ])

    df, _utm, stats = bc.build_visits(metrika, _LOOKBACK_CONFIG, _LOOKBACK_DEFAULTS)

    assert set(df["visit_id"]) == {"v1"}
    row = df.set_index("visit_id").loc["v1"]
    assert row["is_lookback_only"] == False  # noqa: E712
    assert row["traffic_source_resolved"] == False  # noqa: E712
    assert stats["traffic_source_resolve"]["unresolved_count"] == 1


def test_build_visits_lookback_before_cutoff_does_not_resolve(tmp_path):
    """Лукбэк-визит РАНЬШЕ lookback_cutoff не используется как реальный источник."""
    metrika = tmp_path / "data" / "raw" / "metrika_logs"
    _write_base_visits(metrika, [
        {"id": "v1", "cid": "c1", "dt": "2026-06-05 10:00:00", "src": "internal"},
    ])
    # cutoff = 2026-05-02; визит раньше границы -> не учитывается.
    _write_lookback_visits(metrika, [
        {"id": "vlb", "cid": "c1", "dt": "2026-04-15 09:00:00", "src": "ad"},
    ])

    df, _utm, _stats = bc.build_visits(metrika, _LOOKBACK_CONFIG, _LOOKBACK_DEFAULTS)

    row = df.set_index("visit_id").loc["v1"]
    assert row["traffic_source_resolved"] == False  # noqa: E712


def test_build_excludes_lookback_rows_from_visits_parquet(tmp_path):
    """Итоговый visits.parquet (build()) исключает is_lookback_only=true строки —
    компьют-слой их никогда не видит."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    metrika = paths.raw / "metrika_logs"
    _write_base_visits(metrika, [
        {"id": "v1", "cid": "c1", "dt": "2026-06-05 10:00:00", "src": "internal"},
    ])
    _write_lookback_visits(metrika, [
        {"id": "vlb", "cid": "c1", "dt": "2026-05-20 09:00:00", "src": "ad"},
    ])
    manifest_mod.update_source(
        paths.raw, "metrika_logs", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["visits"],
    )

    built = bc.build(paths, _LOOKBACK_CONFIG, _LOOKBACK_DEFAULTS)
    assert "visits" in built

    visits = pd.read_parquet(paths.canonical / "visits.parquet")
    assert set(visits["visit_id"]) == {"v1"}
    assert "is_lookback_only" not in visits.columns
    assert visits.iloc[0]["source_group_resolved"] == "ad"


def test_build_visits_main_rows_unchanged_with_or_without_lookback(tmp_path):
    """Наличие lookback-данных не меняет уже разрешённые визиты основного окна
    (сравнение построчно на одной фикстуре, где нет ambiguous-визитов —
    единственный сценарий, где carry-forward вообще мог бы что-то изменить)."""
    def _make(with_lookback: bool) -> pd.DataFrame:
        metrika = tmp_path / ("with_lb" if with_lookback else "without_lb") / "metrika_logs"
        _write_base_visits(metrika, [
            {"id": "v1", "cid": "c1", "dt": "2026-06-05 10:00:00", "src": "search_engine"},
            {"id": "v2", "cid": "c2", "dt": "2026-06-06 10:00:00", "src": "ad"},
        ])
        if with_lookback:
            _write_lookback_visits(metrika, [
                {"id": "vlb", "cid": "c1", "dt": "2026-05-20 09:00:00", "src": "direct"},
            ])
        df, _utm, _stats = bc.build_visits(metrika, _LOOKBACK_CONFIG, _LOOKBACK_DEFAULTS)
        return df[df["is_lookback_only"] == False].reset_index(drop=True)  # noqa: E712

    df_without = _make(False)
    df_with = _make(True)

    pd.testing.assert_frame_equal(df_without, df_with)


def test_build_costs_only_from_manual_fixtures_without_direct(tmp_path):
    """SEO-only клиент без Директа: costs строится только из costs_manual."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)

    config = {
        "costs_manual": {"seo_fee_rub_month": 3100},
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-02"},
    }
    defaults = {"utm_undefined_threshold": 0.25}

    built = bc.build(paths, config, defaults)
    assert built == ["costs"]

    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    assert len(costs) == 2
    assert (costs["source_tag"] == "seo_fee").all()
    assert costs.iloc[0]["cost_raw"] == pytest.approx(100.0)  # 3100/31
    assert (costs["cost_status"] == "vat_basis_unknown").all()
    assert costs["cost_normalized"].isna().all()


def test_build_no_raw_and_no_manual_costs_produces_nothing(tmp_path):
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    built = bc.build(paths, {}, {"utm_undefined_threshold": 0.25})
    assert built == []


# ═════════════════════════════ build_crm ═══════════════════════════════════
def test_build_crm_reads_and_normalizes(tmp_path):
    crm_dir = tmp_path / "crm"
    crm_dir.mkdir(parents=True)
    with (crm_dir / "leads.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "lead_date", "source", "lead_kind", "lead_id", "phone_hash",
            "status", "status_raw", "amount_rub", "is_new_client",
        ])
        writer.writeheader()
        writer.writerow({
            "lead_date": "2026-06-05", "source": "  Яндекс  ", "lead_kind": "phone",
            "lead_id": "", "phone_hash": "abc123", "status": "won",
            "status_raw": "успешно", "amount_rub": "15000.5", "is_new_client": "True",
        })
        writer.writerow({
            "lead_date": "2026-06-06", "source": "google", "lead_kind": "id",
            "lead_id": "ORDER-1", "phone_hash": "", "status": "",
            "status_raw": "неизвестный", "amount_rub": "", "is_new_client": "",
        })
        writer.writerow({
            "lead_date": "не дата", "source": "x", "lead_kind": "id",
            "lead_id": "ORDER-2", "phone_hash": "", "status": "lost",
            "status_raw": "отказ", "amount_rub": "100", "is_new_client": "False",
        })

    df = bc.build_crm(crm_dir)
    assert len(df) == 2  # строка с нечитаемой датой отброшена

    won = df[df["status_norm"] == "won"].iloc[0]
    assert won["source_norm"] == "яндекс"
    assert won["amount_rub"] == pytest.approx(15000.5)
    assert won["is_new_client"] is True
    assert won["phone_hash"] == "abc123"

    unknown_status = df[df["source_norm"] == "google"].iloc[0]
    assert unknown_status["status_norm"] == "unknown"
    # amount_rub — колонка смешанного типа (float | None) -> pandas хранит
    # отсутствующее значение как NaN; в parquet write_canonical_table пишет
    # настоящий null (см. test_build_writes_only_tables_with_raw_source).
    assert pd.isna(unknown_status["amount_rub"])
    assert unknown_status["is_new_client"] is None


def test_build_crm_missing_dir_returns_empty(tmp_path):
    df = bc.build_crm(tmp_path / "nope")
    assert df.empty


# ═════════════════════════════ seo_queries builders ════════════════════════
def test_build_seo_queries_gsc_aggregates_devices_and_flags_brand():
    gsc_dir_content = (
        "month,query,page,device,clicks,impressions,ctr,position\n"
        "2026-05-01,аренда погнали,https://site.ru/cars,DESKTOP,5,50,0.1,3.0\n"
        "2026-05-01,аренда погнали,https://site.ru/cars,MOBILE,3,30,0.1,5.0\n"
        "2026-05-01,прокат авто,https://site.ru/cars,DESKTOP,1,10,0.1,8.0\n"
    )

    def _write(tmp_path):
        gsc_dir = tmp_path / "gsc"
        gsc_dir.mkdir(parents=True)
        (gsc_dir / "gsc_2026-05-01.csv").write_text(gsc_dir_content, encoding="utf-8")
        return gsc_dir

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        gsc_dir = _write(Path(td))
        df = bc.build_seo_queries_gsc(gsc_dir, {"brand_terms": ["погнали"]})

    assert set(df["source"]) == {"gsc"}
    assert df["month"].iloc[0] == "2026-05"
    branded = df[df["query"] == "аренда погнали"].iloc[0]
    assert branded["total_clicks"] == 8            # 5 + 3, device-срезы объединены
    assert branded["total_shows"] == 80            # 50 + 30
    assert branded["avg_show_position"] == pytest.approx((3.0 * 50 + 5.0 * 30) / 80)
    assert bool(branded["is_brand"]) is True
    nonbrand = df[df["query"] == "прокат авто"].iloc[0]
    assert bool(nonbrand["is_brand"]) is False


def test_build_seo_queries_webmaster_uses_window_end_month():
    """Старый формат (без page, CTR, DEMAND): month из date_to, page=null, ctr=null."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wm_dir = Path(td) / "webmaster"
        wm_dir.mkdir(parents=True)
        popular = [{
            "query_id": "q1", "query_text": "погнали аренда",
            "indicators": {"TOTAL_SHOWS": 1000, "TOTAL_CLICKS": 50,
                           "AVG_SHOW_POSITION": 3.1, "AVG_CLICK_POSITION": 2.2},
        }]
        (wm_dir / "search_queries_popular.json").write_text(
            json.dumps(popular, ensure_ascii=False), encoding="utf-8"
        )
        entry = {"date_from": "2026-05-01", "date_to": "2026-06-30"}
        df = bc.build_seo_queries_webmaster(wm_dir, entry, {"brand_terms": ["погнали"]})

    assert df.iloc[0]["source"] == "webmaster"
    assert df.iloc[0]["month"] == "2026-06"
    assert df.iloc[0]["avg_show_position"] == pytest.approx(2.2)
    assert bool(df.iloc[0]["is_brand"]) is True
    assert df.iloc[0]["page"] is None
    # Старый формат: CTR и DEMAND отсутствуют → null, не ошибка
    assert df.iloc[0]["ctr"] is None or pd.isna(df.iloc[0]["ctr"])


# ═════════════════ Новые поля visits: naive vs lastsign ══════════════════════

def test_last_traffic_source_naive_does_not_affect_source_classification(tmp_path):
    """last_traffic_source_naive НЕ влияет на source_group/source_final (lastsign).

    При расхождении naive и lastsign — source_group определяется только
    ym:s:lastsignTrafficSource; last_traffic_source_naive записывается как есть.
    """
    metrika = tmp_path / "metrika_logs"
    # lastsign = "direct" → source_group = "direct"
    _write_base_visits(metrika, [
        {"id": "v1", "dt": "2026-06-01 10:00:00", "src": "direct"},
    ])
    # naive = "ad" (расходится с lastsign "direct")
    _write_backfill(metrika, [
        {"ym:s:visitID": "v1", "ym:s:lastTrafficSource": "ad",
         "ym:s:browser": "chrome", "ym:s:operatingSystem": "android",
         "ym:s:screenWidth": "360", "ym:s:screenHeight": "780",
         "ym:s:regionCountry": "Russia", "ym:s:regionCity": "Moscow"},
    ])
    df, uncertain, _ = bc.build_visits(metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    v1 = df[df.visit_id == "v1"].iloc[0]
    # lastsign → source_group (naive не меняет)
    assert v1["source_group"] == "direct"
    # naive — отдельное поле; может расходиться с source_group
    assert v1["last_traffic_source_naive"] == "ad"
    # source_final и is_ad определяются только через source_group
    assert v1["source_final"] == "direct"
    assert v1["is_ad"] == False  # noqa: E712 — numpy bool, `is` fails
    assert uncertain is False


def test_dedupe_new_fields_use_last_dt_row(tmp_path):
    """Дедуп по visit_id: browser, os, region_city и прочие новые поля берутся
    из строки с позднейшим dt (то же правило, что и для базовых полей).
    """
    import gzip as _gzip

    metrika = tmp_path / "metrika_logs"
    metrika.mkdir(parents=True, exist_ok=True)

    lines = ["\t".join(VISIT_FIELDS)]
    for dt, browser, city in [
        ("2026-06-01 08:00:00", "chrome",  "Moscow"),  # ранняя строка
        ("2026-06-01 12:00:00", "firefox", "Kazan"),   # поздняя → должна выжить
    ]:
        cells = {f: "" for f in VISIT_FIELDS}
        cells.update({
            "ym:s:visitID": "v1",
            "ym:s:clientID": "c1",
            "ym:s:dateTime": dt,
            "ym:s:lastsignTrafficSource": "direct",
            "ym:s:deviceCategory": "1",
            "ym:s:startURL": "https://site.ru/",
            "ym:s:lastTrafficSource": "direct",
            "ym:s:browser": browser,
            "ym:s:operatingSystem": "windows",
            "ym:s:screenWidth": "1920",
            "ym:s:screenHeight": "1080",
            "ym:s:regionCountry": "Russia",
            "ym:s:regionArea": city,
        })
        lines.append("\t".join(cells[f] for f in VISIT_FIELDS))

    with _gzip.open(metrika / "visits_2026-06-01.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    # 2A-patch: имя поля региона (regionArea вместо regionCity) читается из
    # manifest.region_field, не хардкодится — CSV выше уже написан под regionArea.
    manifest_entry = {"region_field": "ym:s:regionArea", "region_field_verified": True}
    df, _, _ = bc.build_visits(
        metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25}, manifest_entry,
    )

    assert len(df) == 1                        # дедуп: одна строка
    v1 = df.iloc[0]
    assert v1["browser"] == "firefox"          # из строки с позднейшим dt
    assert v1["region_city"] == "Kazan"        # из строки с позднейшим dt
    assert v1["screen_resolution"] == "1920x1080"
    assert v1["os"] == "windows"
    assert v1["region_country"] == "Russia"


def test_region_field_falls_back_to_region_city_when_not_verified(tmp_path):
    """2A-patch: manifest.region_field_verified=false -> extract уже откатился на
    ym:s:regionCity (API отклонил regionArea в этом прогоне) и записал именно это
    имя в manifest.region_field. build_visits обязан прочитать факт из raw CSV
    (колонка ym:s:regionCity реально там), а не молча дать region_city=None.
    """
    import gzip as _gzip

    metrika = tmp_path / "metrika_logs"
    metrika.mkdir(parents=True, exist_ok=True)

    # Откат: в этом прогоне extract запрашивал ym:s:regionCity, а НЕ regionArea
    # (см. metrika_logs._resolve_region_field) — raw CSV реально содержит regionCity,
    # не оба поля сразу.
    fields = [f for f in VISIT_FIELDS if f != "ym:s:regionArea"] + ["ym:s:regionCity"]
    lines = ["\t".join(fields)]
    cells = {f: "" for f in fields}
    cells.update({
        "ym:s:visitID": "v1",
        "ym:s:clientID": "c1",
        "ym:s:dateTime": "2026-06-01 08:00:00",
        "ym:s:lastsignTrafficSource": "direct",
        "ym:s:deviceCategory": "1",
        "ym:s:startURL": "https://site.ru/",
        "ym:s:regionCountry": "Russia",
        "ym:s:regionCity": "Kazan",
    })
    lines.append("\t".join(cells[f] for f in fields))

    with _gzip.open(metrika / "visits_2026-06-01.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    manifest_entry = {
        "region_field": "ym:s:regionCity",
        "region_field_verified": False,
        "region_field_error": "Unknown field in the request: ym:s:regionArea for the source visits",
    }
    df, _, _ = bc.build_visits(
        metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25}, manifest_entry,
    )

    assert len(df) == 1
    assert df.iloc[0]["region_city"] == "Kazan"   # деградация на regionCity, не None


def test_region_field_defaults_to_region_city_without_manifest_entry(tmp_path):
    """Manifest без записи region_field (выгрузка до 2A-patch, manifest_metrika_entry=None
    или запись без этого ключа) -> тоже откат на исторически известное ym:s:regionCity,
    а не пустая колонка."""
    import gzip as _gzip

    metrika = tmp_path / "metrika_logs"
    metrika.mkdir(parents=True, exist_ok=True)

    # Выгрузка до 2A-patch: raw CSV написан старым extract под regionCity.
    fields = [f for f in VISIT_FIELDS if f != "ym:s:regionArea"] + ["ym:s:regionCity"]
    lines = ["\t".join(fields)]
    cells = {f: "" for f in fields}
    cells.update({
        "ym:s:visitID": "v1",
        "ym:s:clientID": "c1",
        "ym:s:dateTime": "2026-06-01 08:00:00",
        "ym:s:lastsignTrafficSource": "direct",
        "ym:s:deviceCategory": "1",
        "ym:s:startURL": "https://site.ru/",
        "ym:s:regionCountry": "Russia",
        "ym:s:regionCity": "Kazan",
    })
    lines.append("\t".join(cells[f] for f in fields))

    with _gzip.open(metrika / "visits_2026-06-01.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    df, _, _ = bc.build_visits(metrika, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    assert len(df) == 1
    assert df.iloc[0]["region_city"] == "Kazan"


# ═════════════════════════ costs: VAT-нормализация ═══════════════════════════

def test_vat_lookup_maps_sources_correctly():
    vat_basis = [
        {"source": "direct", "vat_included": False, "evidence": "счёт"},
        {"source": "agency_fee", "vat_included": True, "evidence": "договор"},
        {"source": "seo_fee", "vat_included": None},
    ]
    lk = bc._vat_lookup(vat_basis)
    assert lk["direct"] is False
    assert lk["agency_fee"] is True
    assert lk["seo_fee"] is None
    assert "other" not in lk


def test_vat_lookup_empty_returns_empty_dict():
    assert bc._vat_lookup([]) == {}
    assert bc._vat_lookup(None) == {}


def test_costs_vat_net_status(tmp_path):
    """vat_included=false → cost_normalized=cost_raw, cost_status='net'."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    config = {
        "costs_manual": {"seo_fee_rub_month": 3100},
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-01"},
        "finance": {"vat_basis_by_source": [
            {"source": "seo_fee", "vat_included": False},
        ]},
    }
    bc.build(paths, config, {"utm_undefined_threshold": 0.25})
    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    row = costs.iloc[0]
    assert row["cost_status"] == "net"
    assert row["cost_normalized"] == pytest.approx(row["cost_raw"])


def test_costs_vat_gross_status(tmp_path):
    """vat_included=true → cost_normalized=cost_raw/1.2, cost_status='gross'."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    config = {
        "costs_manual": {"agency_fee_rub_month": 12000},
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-01"},
        "finance": {"vat_basis_by_source": [
            {"source": "agency_fee", "vat_included": True},
        ]},
    }
    bc.build(paths, config, {"utm_undefined_threshold": 0.25})
    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    row = costs.iloc[0]
    assert row["cost_status"] == "gross"
    assert row["cost_normalized"] == pytest.approx(row["cost_raw"] / 1.2)


def test_costs_vat_basis_unknown_when_source_not_in_config(tmp_path):
    """Источник не указан в vat_basis_by_source → normalized=null, status=vat_basis_unknown."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    config = {
        "costs_manual": {"seo_fee_rub_month": 3100},
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-01"},
        "finance": {"vat_basis_by_source": []},
    }
    bc.build(paths, config, {"utm_undefined_threshold": 0.25})
    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    assert (costs["cost_status"] == "vat_basis_unknown").all()
    assert costs["cost_normalized"].isna().all()


def test_costs_vat_basis_unknown_when_no_finance_config(tmp_path):
    """Нет секции finance → status=vat_basis_unknown для всех строк (не 'молча как есть')."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    config = {
        "costs_manual": {"agency_fee_rub_month": 10000},
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-01"},
    }
    bc.build(paths, config, {"utm_undefined_threshold": 0.25})
    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    assert (costs["cost_status"] == "vat_basis_unknown").all()
    assert costs["cost_normalized"].isna().all()


def test_costs_vat_mixed_sources(tmp_path):
    """Фиксы (manual costs): два источника с разными базами НДС в одной таблице."""
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    config = {
        "costs_manual": {
            "agency_fee_rub_month": 12000,   # будет gross: 12000/31 / 1.2
            "seo_fee_rub_month": 9300,       # будет net: 9300/31
        },
        "data_window": {"date_from": "2026-07-01", "date_to": "2026-07-01"},
        "finance": {"vat_basis_by_source": [
            {"source": "agency_fee", "vat_included": True},
            {"source": "seo_fee", "vat_included": False},
        ]},
    }
    bc.build(paths, config, {"utm_undefined_threshold": 0.25})
    costs = pd.read_parquet(paths.canonical / "costs.parquet")
    assert len(costs) == 2

    agency = costs[costs["source_tag"] == "agency_fee"].iloc[0]
    assert agency["cost_status"] == "gross"
    assert agency["cost_normalized"] == pytest.approx(agency["cost_raw"] / 1.2)

    seo = costs[costs["source_tag"] == "seo_fee"].iloc[0]
    assert seo["cost_status"] == "net"
    assert seo["cost_normalized"] == pytest.approx(seo["cost_raw"])


# ═════════════════════════════ normalize_url / dedupe_site_* ═════════════════

@pytest.mark.parametrize("raw,expected", [
    ("https://site.ru/cars/", "https://site.ru/cars"),
    ("https://site.ru/", "https://site.ru/"),
    ("https://SITE.RU/cars/", "https://site.ru/cars"),
    ("/cars/", "/cars"),
    ("/", "/"),
    (None, None),
    ("", ""),
])
def test_normalize_url(raw, expected):
    assert bc.normalize_url(raw) == expected


def test_dedupe_site_pages_normalizes_and_deduplicates():
    """Дубль URL с trailing-slash нормализуется и дедуплицируется; первая строка побеждает."""
    df = pd.DataFrame([
        {
            "url": "https://site.ru/cars/",
            "http_status": 200, "redirect_chain": "[]", "final_url": None,
            "canonical_url": None, "robots_directive": None, "in_sitemap": True,
            "title": "Cars", "description": None, "h1": None,
            "crawled_at": "2026-06-01T00:00:00Z", "js_content_diff": None,
        },
        {
            "url": "https://site.ru/cars",  # то же что первая после нормализации
            "http_status": 301, "redirect_chain": "[]", "final_url": None,
            "canonical_url": None, "robots_directive": None, "in_sitemap": False,
            "title": "Cars dup", "description": None, "h1": None,
            "crawled_at": "2026-06-01T00:00:01Z", "js_content_diff": None,
        },
        {
            "url": "https://site.ru/about",  # уникальный
            "http_status": 200, "redirect_chain": "[]", "final_url": None,
            "canonical_url": None, "robots_directive": None, "in_sitemap": True,
            "title": "About", "description": None, "h1": None,
            "crawled_at": "2026-06-01T00:00:02Z", "js_content_diff": None,
        },
    ])
    out = bc.dedupe_site_pages(df)
    assert len(out) == 2
    assert out.iloc[0]["url"] == "https://site.ru/cars"  # нормализован, первый сохранён
    assert out.iloc[0]["http_status"] == 200             # первая строка победила
    assert "https://site.ru/about" in out["url"].values


def test_dedupe_site_link_graph_normalizes_and_deduplicates():
    df = pd.DataFrame([
        {"from_url": "https://site.ru/", "to_url": "https://site.ru/cars/", "depth_from_home": 1},
        {"from_url": "https://site.ru/", "to_url": "https://site.ru/cars", "depth_from_home": 1},
        {"from_url": "https://site.ru/", "to_url": "https://site.ru/about", "depth_from_home": 1},
    ])
    out = bc.dedupe_site_link_graph(df)
    assert len(out) == 2
    to_urls = set(out["to_url"])
    assert "https://site.ru/cars" in to_urls
    assert "https://site.ru/about" in to_urls


# ═════════════════════════════ seo_queries: source_mode и completeness ════════

def test_build_seo_queries_gsc_manual_unverified(tmp_path):
    """manifest_gsc_entry с source_mode=manual -> все строки получают manual/unverified."""
    gsc_dir = tmp_path / "gsc"
    gsc_dir.mkdir()
    (gsc_dir / "gsc_2026-05.csv").write_text(
        "month,query,page,device,clicks,impressions,ctr,position\n"
        "2026-05-01,аренда авто,https://site.ru/cars,DESKTOP,5,50,0.1,3.0\n",
        encoding="utf-8",
    )
    entry = {"source_mode": "manual", "completeness": "unverified"}
    df = bc.build_seo_queries_gsc(gsc_dir, {"brand_terms": []}, entry)
    assert len(df) == 1
    assert df.iloc[0]["source_mode"] == "manual"
    assert df.iloc[0]["completeness"] == "unverified"


def test_build_seo_queries_gsc_defaults_to_api_verified(tmp_path):
    """Без manifest_gsc_entry -> source_mode=api, completeness=verified."""
    gsc_dir = tmp_path / "gsc"
    gsc_dir.mkdir()
    (gsc_dir / "gsc_2026-05.csv").write_text(
        "month,query,page,device,clicks,impressions,ctr,position\n"
        "2026-05-01,прокат,https://site.ru/,MOBILE,2,20,0.1,5.0\n",
        encoding="utf-8",
    )
    df = bc.build_seo_queries_gsc(gsc_dir, {"brand_terms": []})
    assert df.iloc[0]["source_mode"] == "api"
    assert df.iloc[0]["completeness"] == "verified"


def test_build_seo_queries_webmaster_manual_unverified():
    """Вебмастер manual: source_mode/completeness передаются через manifest_webmaster_entry."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wm_dir = Path(td) / "webmaster"
        wm_dir.mkdir()
        popular = [{"query_text": "погнали аренда",
                    "indicators": {"TOTAL_SHOWS": 500, "TOTAL_CLICKS": 25,
                                   "AVG_SHOW_POSITION": 3.0}}]
        (wm_dir / "search_queries_popular.json").write_text(
            json.dumps(popular, ensure_ascii=False), encoding="utf-8"
        )
        entry = {"date_from": "2026-05-01", "date_to": "2026-06-30",
                 "source_mode": "manual", "completeness": "unverified"}
        df = bc.build_seo_queries_webmaster(wm_dir, entry, {"brand_terms": []})

    assert df.iloc[0]["source_mode"] == "manual"
    assert df.iloc[0]["completeness"] == "unverified"


def test_build_seo_queries_gsc_month_without_device_not_dropped(tmp_path):
    """Месяц без device-колонки (или device='unknown') не удаляется из seo_queries.

    device не входит в каноническую схему и не используется в группировке,
    поэтому отсутствие разбивки по устройствам не приводит к потере строк.
    """
    gsc_dir = tmp_path / "gsc"
    gsc_dir.mkdir()
    # CSV без колонки device — ровно как после ручного экспорта без фильтра устройства
    (gsc_dir / "gsc_2026-05.csv").write_text(
        "month,query,page,clicks,impressions,ctr,position\n"
        "2026-05-01,аренда авто,https://site.ru/cars,5,50,0.1,3.0\n"
        "2026-05-01,прокат авто,https://site.ru/promo,2,20,0.1,7.0\n",
        encoding="utf-8",
    )
    df = bc.build_seo_queries_gsc(gsc_dir, {"brand_terms": []})
    assert len(df) == 2
    assert set(df["month"]) == {"2026-05"}
    assert set(df["source"]) == {"gsc"}


# ══════════════════ seo_queries: новые поля page / ctr / demand ═══════════════

def test_build_seo_queries_webmaster_wide_format_page_ctr_demand():
    """Новый формат (с page, CTR, DEMAND): поля заполнены, DEMAND=null при отсутствии."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wm_dir = Path(td) / "webmaster"
        wm_dir.mkdir()
        popular = [
            {
                "query_text": "аренда авто",
                "page": "/catalog/",
                "indicators": {
                    "TOTAL_SHOWS": 1234,
                    "TOTAL_CLICKS": 56,
                    "AVG_SHOW_POSITION": 9.8,
                    "CTR": 0.045,
                    "DEMAND": 1535,
                },
            },
            {
                "query_text": "прокат машины",
                "page": "/promo/",
                "indicators": {
                    "TOTAL_SHOWS": 500,
                    "TOTAL_CLICKS": 20,
                    "AVG_SHOW_POSITION": 5.0,
                    # Нет CTR и DEMAND — должны быть null
                },
            },
        ]
        (wm_dir / "search_queries_popular.json").write_text(
            json.dumps(popular, ensure_ascii=False), encoding="utf-8"
        )
        entry = {"date_from": "2026-05-01", "date_to": "2026-06-30"}
        df = bc.build_seo_queries_webmaster(wm_dir, entry, {"brand_terms": []})

    assert "ctr" in df.columns and "demand" in df.columns

    row1 = df[df["query"] == "аренда авто"].iloc[0]
    assert row1["page"] == "/catalog/"
    assert row1["source"] == "webmaster"
    assert row1["ctr"] == pytest.approx(0.045)
    assert row1["demand"] == 1535
    assert row1["total_shows"] == 1234
    assert row1["total_clicks"] == 56
    assert row1["avg_show_position"] == pytest.approx(9.8)

    # CTR/DEMAND отсутствуют → null, не ошибка
    row2 = df[df["query"] == "прокат машины"].iloc[0]
    assert row2["page"] == "/promo/"
    assert row2["ctr"] is None or pd.isna(row2["ctr"])
    assert row2["demand"] is None or pd.isna(row2["demand"])


def test_build_seo_queries_webmaster_and_gsc_same_query_page_are_two_rows(tmp_path):
    """Один запрос с одной страницей из webmaster и gsc — две строки, не дедуп."""
    gsc_dir = tmp_path / "gsc"
    gsc_dir.mkdir()
    (gsc_dir / "gsc_2026-05.csv").write_text(
        "month,query,page,device,clicks,impressions,ctr,position\n"
        "2026-05-01,аренда авто,/catalog/,DESKTOP,5,50,0.1,3.0\n",
        encoding="utf-8",
    )

    wm_dir = tmp_path / "webmaster"
    wm_dir.mkdir()
    popular = [{"query_text": "аренда авто", "page": "/catalog/",
                "indicators": {"TOTAL_SHOWS": 1000, "TOTAL_CLICKS": 40,
                               "AVG_SHOW_POSITION": 3.5}}]
    (wm_dir / "search_queries_popular.json").write_text(
        json.dumps(popular, ensure_ascii=False), encoding="utf-8"
    )

    gsc_df = bc.build_seo_queries_gsc(gsc_dir, {"brand_terms": []})
    wm_df = bc.build_seo_queries_webmaster(wm_dir, {"date_to": "2026-06-30"}, {"brand_terms": []})

    combined = pd.concat([gsc_df, wm_df], ignore_index=True)
    # Один запрос/страница из двух источников = 2 строки, не одна
    assert len(combined) == 2
    assert set(combined["source"]) == {"gsc", "webmaster"}
    # После дедупа по (query, page, source) — всё равно 2 строки
    deduped = combined.drop_duplicates(subset=["query", "page", "source"], keep="first")
    assert len(deduped) == 2


def test_seo_queries_dedup_removes_duplicate_within_source():
    """Дубль (query, page, source) внутри одного источника → одна строка после дедупа."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wm_dir = Path(td) / "webmaster"
        wm_dir.mkdir()
        # Два идентичных (query, page) в одном JSON
        popular = [
            {"query_text": "аренда авто", "page": "/catalog/",
             "indicators": {"TOTAL_SHOWS": 1000, "TOTAL_CLICKS": 40, "AVG_SHOW_POSITION": 3.5}},
            {"query_text": "аренда авто", "page": "/catalog/",
             "indicators": {"TOTAL_SHOWS": 999, "TOTAL_CLICKS": 39, "AVG_SHOW_POSITION": 3.4}},
        ]
        (wm_dir / "search_queries_popular.json").write_text(
            json.dumps(popular, ensure_ascii=False), encoding="utf-8"
        )
        df_raw = bc.build_seo_queries_webmaster(
            wm_dir, {"date_to": "2026-06-30"}, {"brand_terms": []}
        )

    # До дедупа — 2 строки из JSON
    assert len(df_raw) == 2
    # После дедупа по (query, page, source) — одна строка; первая победила
    deduped = df_raw.drop_duplicates(subset=["query", "page", "source"], keep="first")
    assert len(deduped) == 1
    assert deduped.iloc[0]["total_shows"] == 1000  # первая строка


def test_seo_queries_build_deduplicates_via_orchestrator(tmp_path):
    """Сквозной тест: build() дедуплицирует дубли webmaster по (query, page, source)."""
    import pyarrow.parquet as pq

    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)

    wm_dir = paths.raw / "webmaster"
    wm_dir.mkdir()
    popular = [
        {"query_text": "аренда авто", "page": "/catalog/",
         "indicators": {"TOTAL_SHOWS": 1000, "TOTAL_CLICKS": 40, "AVG_SHOW_POSITION": 3.5,
                        "CTR": 0.04, "DEMAND": 500}},
        {"query_text": "аренда авто", "page": "/catalog/",  # дубль
         "indicators": {"TOTAL_SHOWS": 999, "TOTAL_CLICKS": 39, "AVG_SHOW_POSITION": 3.4}},
        {"query_text": "прокат авто", "page": "/promo/",
         "indicators": {"TOTAL_SHOWS": 200, "TOTAL_CLICKS": 10, "AVG_SHOW_POSITION": 7.0}},
    ]
    (wm_dir / "search_queries_popular.json").write_text(
        json.dumps(popular, ensure_ascii=False), encoding="utf-8"
    )

    manifest_mod.update_source(
        paths.raw, "webmaster", date_from="2026-05-01", date_to="2026-06-30",
        rows=3, script_version="test", canonical_tables=["seo_queries"],
    )

    built = bc.build(paths, {"brand_terms": []}, {"utm_undefined_threshold": 0.25})
    assert "seo_queries" in built

    seo = pd.read_parquet(paths.canonical / "seo_queries.parquet")
    # 3 JSON-строки, но дубль (аренда авто, /catalog/, webmaster) сжат в одну
    assert len(seo) == 2
    # Проверяем новые колонки в parquet
    schema = pq.read_schema(paths.canonical / "seo_queries.parquet")
    assert "ctr" in schema.names
    assert "demand" in schema.names
    assert "source" in schema.names
    assert "total_shows" in schema.names
    assert "total_clicks" in schema.names
    assert "avg_show_position" in schema.names

    row = seo[seo["query"] == "аренда авто"].iloc[0]
    assert row["source"] == "webmaster"
    assert row["page"] == "/catalog/"
    assert row["ctr"] == pytest.approx(0.04)
    assert row["demand"] == 500
    # Строка без CTR/DEMAND → null в parquet
    row2 = seo[seo["query"] == "прокат авто"].iloc[0]
    assert pd.isna(row2["ctr"])
    assert pd.isna(row2["demand"])
