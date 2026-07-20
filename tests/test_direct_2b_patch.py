"""Тесты 2B-patch: диагностика периода, новые измерения, цели, JOIN в canonical.

Тесты step0 (диагностические), новых измерений, конфига целей и джойна.
Старые smoke-тесты direct не трогаются (test_extract_smoke.py, не в allowed_files).
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import direct  # noqa: E402
from src.extract import _common as C  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.transform import build_canonical as bc  # noqa: E402


# ── Вспомогательные моки ────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, status_code=200, *, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("нет JSON в ответе")
        return self._json


class FakeSession:
    """Отдаёт ответы по совпадению подстроки в URL; запоминает вызовы."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self._per_route_counts = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for idx, (pred, responder) in enumerate(self.routes):
            if pred(method, url):
                n = self._per_route_counts.get(idx, 0)
                self._per_route_counts[idx] = n + 1
                return responder(n) if callable(responder) else responder
        raise AssertionError(f"нет мока для {method} {url}")


def _contains(*needles):
    return lambda method, url: all(n in url for n in needles)


NO_SLEEP = lambda _: None


class Paths:
    def __init__(self, raw: Path):
        self.raw = raw
        self.root = raw.parent.parent


def _campaign_tsv():
    return (
        "Date\tCampaignId\tCampaignName\tDevice\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\tDESKTOP\t5000000\t10\t200\t2\n"
        "2026-06-02\t1\tПоиск\tDESKTOP\t3000000\t7\t150\t1\n"
    )


def _query_tsv():
    return (
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить окна\tbroad\tDESKTOP\t2000000\t3\t50\t1\n"
    )


def _geo_tsv():
    return (
        "Date\tCampaignId\tCampaignName\tLocationOfPresenceId\tLocationOfPresenceName\t"
        "Device\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t213\tМосква\tDESKTOP\t1000000\t5\t100\t0\n"
    )


def _direct_routes(box, *, campaign_tsv=None):
    """HTTP-моки для тестов 2B-patch (упрощённый набор)."""
    campaign_tsv = campaign_tsv or _campaign_tsv()

    def reports(n):
        _m, _u, kwargs = box["session"].calls[-1]
        params = kwargs["json"]["params"]
        rt = params["ReportType"]
        report_name = params.get("ReportName", "")
        if rt == "CAMPAIGN_PERFORMANCE_REPORT":
            body = campaign_tsv
        elif rt == "SEARCH_QUERY_PERFORMANCE_REPORT":
            body = _query_tsv()
        elif rt == "CUSTOM_REPORT" and report_name.startswith("geo_performance"):
            body = _geo_tsv()
        else:
            body = "Placement\tAdNetworkType\tCampaignId\tCost\tClicks\tConversions\n"
        if n == 0:
            return FakeResponse(status_code=202, headers={"retryIn": "0"})
        return FakeResponse(status_code=200, text=body)

    return [
        (_contains("/reports"), reports),
        (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": [
            {"Id": 1, "Name": "Поиск", "TextCampaign": {
                "BiddingStrategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}}}
        ]}})),
        (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": []}})),
        (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
        (_contains("/adextensions"), FakeResponse(json_data={"result": {"AdExtensions": []}})),
        (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": []}})),
        (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": []}})),
        (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": []}})),
    ]


CONFIG_DIRECT = {
    "sources": {"direct": {"enabled": True, "client_login": "test-login"}},
    "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
}
ENV = {"DIRECT_TOKEN": "fake-token"}


# ── step0: логирование периода ───────────────────────────────────────────────
def test_step0_period_logged(tmp_path):
    """Каждый чанк каждого отчёта логирует {date_from, date_to, rows} в manifest."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    # Одно окно = 1 чанк (июнь 2026) → 1 запись в period_logs.
    assert "campaign_period_logs" in entry
    assert "query_period_logs" in entry
    assert "geo_period_logs" in entry
    cl = entry["campaign_period_logs"]
    assert len(cl) == 1
    assert cl[0]["date_from"] == "2026-06-01"
    assert cl[0]["date_to"] == "2026-06-30"
    assert cl[0]["rows"] == 2  # две строки в _campaign_tsv()
    ql = entry["query_period_logs"]
    assert ql[0]["rows"] == 1


# ── cost_conversion: Cost хранится как int микрорублей ──────────────────────
def test_cost_conversion(tmp_path):
    """raw TSV Cost хранится как строка; _parse_cost делит в одном месте."""
    cost_raw, cost_normalized = bc._parse_cost("2000000")
    assert cost_raw == 2000000
    assert cost_normalized == pytest.approx(2.0)

    # Двойного деления нет: ещё один вызов не делит снова.
    cost_raw2, cost_normalized2 = bc._parse_cost(str(cost_raw))
    assert cost_raw2 == 2000000
    assert cost_normalized2 == pytest.approx(2.0)


# ── query_report_dimensions: новые измерения в QUERY ────────────────────────
def test_query_report_dimensions(tmp_path):
    """build_direct_queries читает Date, MatchType, Device, Impressions из queries/."""
    direct_dir = tmp_path / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-15\t10\tТест\t20\tаренда авто\texact\tMOBILE\t3000000\t5\t80\t2\n",
        encoding="utf-8",
    )
    df = bc.build_direct_queries(direct_dir, None)
    assert not df.empty
    row = df.iloc[0]
    assert str(row["date"]) == "2026-06-15"
    assert row["match_type"] == "exact"
    assert row["device"] == "MOBILE"
    assert row["impressions"] == 80
    assert row["cost_raw"] == 3000000
    assert row["cost_normalized"] == pytest.approx(3.0)
    assert row["conversions_all"] == 2
    assert "ctr" not in df.columns
    assert "cpa" not in df.columns
    assert "avg_cpc" not in df.columns


# ── geo_report_schema: GEO пишется в geo/ и содержит нужные поля ───────────
def test_geo_report_schema(tmp_path):
    """build_direct_geo читает LocationOfPresenceId/Name из geo/."""
    direct_dir = tmp_path / "direct"
    geo_dir = direct_dir / "geo"
    geo_dir.mkdir(parents=True, exist_ok=True)
    (geo_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tLocationOfPresenceId\t"
        "LocationOfPresenceName\tDevice\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t213\tМосква\tDESKTOP\t1000000\t5\t100\t0\n",
        encoding="utf-8",
    )
    df = bc.build_direct_geo(direct_dir, None)
    assert not df.empty
    row = df.iloc[0]
    assert row["location_of_presence_id"] == "213"
    assert row["location_of_presence_name"] == "Москва"
    assert row["cost_raw"] == 1000000
    assert row["cost_normalized"] == pytest.approx(1.0)
    assert "ctr" not in df.columns


# ── config_attribution_required: ошибка при macro_goals без attribution_type ─
def test_config_attribution_required(tmp_path):
    """attribution_type пуст + macro_goals не пуст → ошибка конфига (не молча)."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    cfg = {
        "sources": {"direct": {
            "enabled": True,
            "client_login": "cl",
            "attribution_type": "",          # пусто — должно вызвать ошибку
            "macro_goals": [{"id": 12345678, "name": "Форма"}],
        }},
        "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
    }
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    with pytest.raises(C.SourceUnavailable, match="attribution_type"):
        direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP)


# ── goals_empty: нет целей → manifest.macro_goals_configured=False ───────────
def test_goals_empty(tmp_path):
    """macro_goals=[] → macro_goals_configured=False в manifest, goal_conv нет."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    cfg = {
        "sources": {"direct": {
            "enabled": True, "client_login": "cl",
            "attribution_type": "", "macro_goals": [],
        }},
        "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
    }
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    result = direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["macro_goals_configured"] is False

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["macro_goals_configured"] is False
    assert "caveat" in entry  # текст про отсутствие CPA


# ── goals_multiple: 2 цели → 2 раздельных колонки и 2 раздельных запроса ───
def test_goals_multiple(tmp_path):
    """2 цели в macro_goals → goal_conv_<id1> и goal_conv_<id2> раздельно."""
    direct_dir = tmp_path / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tзапрос\tbroad\tDESKTOP\t1000000\t2\t30\t0\n",
        encoding="utf-8",
    )
    # Папки для каждой цели
    goal_a_dir = queries_dir / "goals" / "goal_111"
    goal_b_dir = queries_dir / "goals" / "goal_222"
    goal_a_dir.mkdir(parents=True, exist_ok=True)
    goal_b_dir.mkdir(parents=True, exist_ok=True)
    (goal_a_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tзапрос\tbroad\tDESKTOP\t3\n",
        encoding="utf-8",
    )
    (goal_b_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tзапрос\tbroad\tDESKTOP\t1\n",
        encoding="utf-8",
    )

    macro_goals = [{"id": 111, "name": "Цель А"}, {"id": 222, "name": "Цель Б"}]
    df = bc.build_direct_queries(direct_dir, None, macro_goals=macro_goals)

    assert "goal_conv_111" in df.columns
    assert "goal_conv_222" in df.columns
    assert int(df.iloc[0]["goal_conv_111"]) == 3
    assert int(df.iloc[0]["goal_conv_222"]) == 1
    # Конверсии по целям НЕ суммируются в одной колонке.
    assert "goal_conv_total" not in df.columns


# ── join_missing_row_is_zero: отсутствующая строка в goal → 0 ───────────────
def test_join_missing_row_is_zero(tmp_path):
    """Если в goal-отчёте нет строки для комбинации ключа → goal_conv = 0 (не null)."""
    direct_dir = tmp_path / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tТест\t11\tзапрос А\tbroad\tDESKTOP\t1000000\t1\t10\t0\n"
        "2026-06-01\t1\tТест\t11\tзапрос Б\tbroad\tDESKTOP\t500000\t1\t5\t0\n",
        encoding="utf-8",
    )
    goal_dir = queries_dir / "goals" / "goal_999"
    goal_dir.mkdir(parents=True, exist_ok=True)
    # Goal-отчёт есть только для "запрос А", "запрос Б" отсутствует.
    (goal_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\tConversions\n"
        "2026-06-01\t1\tТест\t11\tзапрос А\tbroad\tDESKTOP\t5\n",
        encoding="utf-8",
    )

    df = bc.build_direct_queries(direct_dir, None, macro_goals=[{"id": 999, "name": "Цель"}])
    assert "goal_conv_999" in df.columns
    row_a = df[df["query"] == "запрос А"].iloc[0]
    row_b = df[df["query"] == "запрос Б"].iloc[0]
    assert int(row_a["goal_conv_999"]) == 5
    assert int(row_b["goal_conv_999"]) == 0    # не null!
    assert row_b["goal_conv_999"] is not None  # явно не null


# ── join_key_not_unique_fails: неуникальный ключ → ошибка ───────────────────
def test_join_key_not_unique_fails(tmp_path):
    """Если ключ в base_df не уникален → _join_goal_convs поднимает ValueError."""
    base_df = pd.DataFrame([
        {"date": date(2026, 6, 1), "campaign_id": "1", "campaign_name": "T",
         "ad_group_id": "11", "query": "dup", "match_type": "broad", "device": "D",
         "cost_raw": 1000000, "cost_normalized": 1.0, "clicks": 1, "impressions": 10,
         "conversions_all": 0},
        {"date": date(2026, 6, 1), "campaign_id": "1", "campaign_name": "T",
         "ad_group_id": "11", "query": "dup", "match_type": "broad", "device": "D",
         "cost_raw": 1000000, "cost_normalized": 1.0, "clicks": 2, "impressions": 20,
         "conversions_all": 0},
    ])
    key_cols = ["date", "campaign_id", "campaign_name", "ad_group_id",
                "query", "match_type", "device"]
    goals_base_dir = tmp_path / "goals"
    goals_base_dir.mkdir()

    with pytest.raises(ValueError, match="не уникален"):
        bc._join_goal_convs(base_df, goals_base_dir, ["999"], key_cols)


# ── join_cost_preserved: сумма cost_normalized не меняется после джойна ─────
def test_join_cost_preserved(tmp_path):
    """После LEFT JOIN суммарный cost_normalized не должен измениться."""
    base_df = pd.DataFrame([
        {"date": date(2026, 6, 1), "campaign_id": "1", "campaign_name": "Т",
         "ad_group_id": "11", "query": "q1", "match_type": "broad", "device": "D",
         "cost_raw": 5000000, "cost_normalized": 5.0, "clicks": 10,
         "impressions": 100, "conversions_all": 0},
        {"date": date(2026, 6, 2), "campaign_id": "1", "campaign_name": "Т",
         "ad_group_id": "11", "query": "q2", "match_type": "broad", "device": "D",
         "cost_raw": 3000000, "cost_normalized": 3.0, "clicks": 7,
         "impressions": 70, "conversions_all": 0},
    ])
    key_cols = ["date", "campaign_id", "campaign_name", "ad_group_id",
                "query", "match_type", "device"]
    goals_dir = tmp_path / "goals" / "goal_100"
    goals_dir.mkdir(parents=True)
    (goals_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\tConversions\n"
        "2026-06-01\t1\tТ\t11\tq1\tbroad\tD\t2\n",
        encoding="utf-8",
    )

    result = bc._join_goal_convs(base_df, tmp_path / "goals", ["100"], key_cols)
    assert result["cost_normalized"].sum() == pytest.approx(8.0)  # 5.0 + 3.0


# ── no_derived_columns: производные метрики не создаются в canonical ─────────
def test_no_derived_columns(tmp_path):
    """build_direct_queries не создаёт cpa, cr, ctr, avg_cpc, cost_per_conversion."""
    direct_dir = tmp_path / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tТ\t11\tq\tbroad\tD\t1000000\t5\t50\t1\n",
        encoding="utf-8",
    )
    df = bc.build_direct_queries(direct_dir, None)
    forbidden = {"cpa", "cr", "ctr", "avg_cpc", "cost_per_conversion"}
    assert forbidden.isdisjoint(df.columns), (
        f"Запрещённые производные колонки в direct_queries: {forbidden & set(df.columns)}"
    )

    # Аналогично для direct_campaigns.
    campaign_dir = direct_dir / "campaigns"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tDevice\tCost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tТ\tDESKTOP\t1000000\t5\t50\t1\n",
        encoding="utf-8",
    )
    dc_df = bc.build_direct_campaigns(direct_dir, None)
    assert forbidden.isdisjoint(dc_df.columns)


# ── ШАГ 0: окно запросов 180 дней ───────────────────────────────────────────

def test_window_truncation_queries(tmp_path):
    """Запрошено 12 мес → window_effective_from не раньше today-180; window_truncated=True;
    caveat_type=source_window_limit в manifest.
    """
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    # Окно: 12 месяцев назад → требует обрезки для SEARCH_QUERY
    today = date(2026, 7, 20)
    date_from = date(2025, 7, 20)   # 365 дней — больше лимита 180
    date_to = date(2026, 6, 30)
    cfg = {
        "sources": {"direct": {"enabled": True, "client_login": "cl"}},
        "data_window": {"date_from": str(date_from), "date_to": str(date_to)},
    }

    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP, today=today)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    qw = entry.get("window_infos", {}).get("queries", {})
    assert qw.get("window_truncated") is True, "window_truncated должен быть True"
    assert qw.get("window_requested_from") == str(date_from)

    # effective_from не раньше today - 180 дней
    limit_from = today - timedelta(days=direct.REPORT_WINDOW_LIMIT_DAYS["SEARCH_QUERY_PERFORMANCE_REPORT"])
    from datetime import datetime
    eff_from = datetime.strptime(qw["window_effective_from"], "%Y-%m-%d").date()
    assert eff_from >= limit_from, (
        f"effective_from {eff_from} раньше минимально допустимого {limit_from}"
    )

    # caveat_type = "source_window_limit" — методологическое ограничение, не проблема данных
    caveat = entry.get("query_window_caveat", {})
    assert caveat.get("caveat_type") == "source_window_limit", (
        f"Ожидался caveat_type=source_window_limit, получен: {caveat}"
    )


def test_window_no_limit_campaigns(tmp_path):
    """CAMPAIGN_PERFORMANCE_REPORT не имеет лимита → window_truncated=False при любом окне."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    today = date(2026, 7, 20)
    cfg = {
        "sources": {"direct": {"enabled": True, "client_login": "cl"}},
        "data_window": {"date_from": "2025-07-01", "date_to": "2026-06-30"},  # 12 мес
    }

    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP, today=today)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    cw = entry.get("window_infos", {}).get("campaigns", {})
    assert cw.get("window_truncated") is False, (
        f"Кампании не должны обрезаться: {cw}"
    )
    # Campaigns: effective_from совпадает с requested_from
    assert cw.get("window_effective_from") == cw.get("window_requested_from")


def test_partial_failure_isolated(tmp_path):
    """Падение SEARCH_QUERY_PERFORMANCE_REPORT не роняет campaigns;
    report_status.campaigns=ok, report_status.queries=failed.
    """
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    def routes_with_failing_queries(box):
        def reports(n):
            _m, _u, kwargs = box["session"].calls[-1]
            params = kwargs["json"]["params"]
            rt = params["ReportType"]
            report_name = params.get("ReportName", "")
            if rt == "SEARCH_QUERY_PERFORMANCE_REPORT":
                # Симулируем недоступность: ошибка API для запросов
                return FakeResponse(
                    status_code=400,
                    text='{"error":{"error_code":"58","error_string":"unavailable",'
                         '"error_detail":"report not available"}}',
                )
            if rt == "CAMPAIGN_PERFORMANCE_REPORT":
                body = _campaign_tsv()
            elif rt == "CUSTOM_REPORT" and report_name.startswith("geo_performance"):
                body = _geo_tsv()
            else:
                body = "Placement\tAdNetworkType\tCampaignId\tCost\tClicks\tConversions\n"
            if n == 0:
                return FakeResponse(status_code=202, headers={"retryIn": "0"})
            return FakeResponse(status_code=200, text=body)

        return [
            (_contains("/reports"), reports),
            (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": []}})),
            (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": []}})),
            (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
            (_contains("/adextensions"), FakeResponse(json_data={"result": {"AdExtensions": []}})),
            (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": []}})),
            (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": []}})),
            (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": []}})),
        ]

    cfg = {
        "sources": {"direct": {"enabled": True, "client_login": "cl"}},
        "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
    }
    box = {}
    session = FakeSession(routes_with_failing_queries(box))
    box["session"] = session

    # Не должно падать, даже если queries недоступны
    result = direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result is not None

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    rs = entry.get("report_status", {})
    assert rs.get("campaigns") == "ok", f"campaigns должен быть ok: {rs}"
    assert rs.get("queries") == "failed", f"queries должен быть failed: {rs}"


def test_error_encoding(tmp_path):
    """Ошибка API с кириллицей в error_detail читается корректно (UTF-8)."""
    # Симулируем ответ сервера с кириллицей в теле ошибки.
    error_json = '{"error":{"error_code":"513","error_string":"Ошибка","error_detail":"Логин не подключён"}}'
    error_bytes = error_json.encode("utf-8")

    class FakeRespWithContent:
        status_code = 200
        headers = {}
        content = error_bytes
        text = error_bytes.decode("latin-1")  # Намеренно неправильная кодировка в .text

        def json(self):
            return json.loads(self.text)  # Это вернёт мусор для кириллицы

    resp = FakeRespWithContent()
    err = direct._api_error(resp)
    assert err is not None, "Должен вернуть блок error"
    # Кириллица должна быть корректной из content (не из .text)
    detail = err.get("error_detail", "")
    assert "Логин" in detail, (
        f"error_detail должен содержать корректную кириллицу, получено: {detail!r}"
    )
    assert "не подключён" in detail


def test_geo_unavailable_documented(tmp_path):
    """Если GEO_PERFORMANCE_REPORT недоступен: geo_report_available=False в manifest,
    пайплайн не падает, причина записана в geo_caveat.
    """
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    def routes_with_failing_geo(box):
        def reports(n):
            _m, _u, kwargs = box["session"].calls[-1]
            params = kwargs["json"]["params"]
            rt = params["ReportType"]
            report_name = params.get("ReportName", "")
            if rt == "CUSTOM_REPORT" and report_name.startswith("geo_performance"):
                return FakeResponse(
                    status_code=400,
                    text='{"error":{"error_code":"58","error_string":"unavailable",'
                         '"error_detail":"geo report not available"}}',
                )
            if rt == "CAMPAIGN_PERFORMANCE_REPORT":
                body = _campaign_tsv()
            else:
                body = (
                    "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\t"
                    "Cost\tClicks\tImpressions\tConversions\n"
                    "2026-06-01\t1\tП\t11\tq\tbroad\t1000000\t1\t10\t0\n"
                )
            if n == 0:
                return FakeResponse(status_code=202, headers={"retryIn": "0"})
            return FakeResponse(status_code=200, text=body)

        return [
            (_contains("/reports"), reports),
            (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": []}})),
            (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": []}})),
            (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
            (_contains("/adextensions"), FakeResponse(json_data={"result": {"AdExtensions": []}})),
            (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": []}})),
            (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": []}})),
            (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": []}})),
        ]

    cfg = {
        "sources": {"direct": {"enabled": True, "client_login": "cl"}},
        "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
    }
    box = {}
    session = FakeSession(routes_with_failing_geo(box))
    box["session"] = session

    # Пайплайн не должен падать при недоступности geo
    result = direct.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result is not None

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry.get("geo_report_available") is False, (
        "geo_report_available должен быть False в manifest"
    )
    geo_caveat = entry.get("geo_caveat", {})
    assert geo_caveat.get("geo_report_available") is False
    assert geo_caveat.get("reason"), "Причина недоступности geo должна быть записана"
    # report_status.geo = failed
    assert entry.get("report_status", {}).get("geo") == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 2B-patch-2: QUERY_FIELDS без Device, geo через CUSTOM_REPORT, CampaignIds
# в adgroups/ads/keywords.get, feeds.get без Ids -> graceful empty.
# ═══════════════════════════════════════════════════════════════════════════

def test_query_report_no_device_field():
    """QUERY_FIELDS/QUERY_FIELDS_GOAL не содержат Device (API error 4000)."""
    assert "Device" not in direct.QUERY_FIELDS
    assert "Device" not in direct.QUERY_FIELDS_GOAL


def test_geo_uses_custom_report(tmp_path):
    """Вызов гео-отчёта идёт с ReportType=CUSTOM_REPORT, а не GEO_PERFORMANCE_REPORT."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    geo_calls = [
        kwargs["json"]["params"] for _m, _u, kwargs in session.calls
        if "/reports" in _u and kwargs["json"]["params"].get("ReportName", "").startswith("geo_performance")
    ]
    assert geo_calls, "не найден ни один запрос гео-отчёта"
    for params in geo_calls:
        assert params["ReportType"] == "CUSTOM_REPORT"


def test_geo_report_available_true_on_success(tmp_path):
    """При успешном ответе CUSTOM_REPORT для гео geo_report_available=True в manifest."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry.get("geo_report_available") is True
    assert entry.get("report_status", {}).get("geo") == "ok"


def test_targeting_requires_campaign_ids(tmp_path):
    """adgroups.get вызывается с непустым SelectionCriteria.CampaignIds."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    adgroups_calls = [kwargs for _m, u, kwargs in session.calls if "/adgroups" in u]
    assert adgroups_calls, "не найден ни один вызов adgroups.get"
    for kwargs in adgroups_calls:
        criteria = kwargs["json"]["params"].get("SelectionCriteria", {})
        assert criteria.get("CampaignIds"), (
            f"adgroups.get должен получать непустой CampaignIds: {criteria}"
        )


def test_ad_texts_requires_campaign_ids(tmp_path):
    """ads.get вызывается с непустым SelectionCriteria.CampaignIds."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    ads_calls = [kwargs for _m, u, kwargs in session.calls if "/ads" in u]
    assert ads_calls, "не найден ни один вызов ads.get"
    for kwargs in ads_calls:
        criteria = kwargs["json"]["params"].get("SelectionCriteria", {})
        assert criteria.get("CampaignIds"), (
            f"ads.get должен получать непустой CampaignIds: {criteria}"
        )


def test_keywords_requires_campaign_ids(tmp_path):
    """keywords.get вызывается с непустым SelectionCriteria.CampaignIds."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    keywords_calls = [kwargs for _m, u, kwargs in session.calls if "/keywords" in u]
    assert keywords_calls, "не найден ни один вызов keywords.get"
    for kwargs in keywords_calls:
        criteria = kwargs["json"]["params"].get("SelectionCriteria", {})
        assert criteria.get("CampaignIds"), (
            f"keywords.get должен получать непустой CampaignIds: {criteria}"
        )


def test_feed_missing_ids_graceful(tmp_path):
    """feeds.get не вызывается без Ids; feed_used=False, явный note, пайплайн не падает."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["feed_used"] is False

    feeds_calls = [kwargs for _m, u, kwargs in session.calls if "/feeds" in u]
    assert not feeds_calls, "feeds.get не должен вызываться без известных Ids"

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry.get("feed_used") is False
    notes = entry.get("notes", [])
    assert any("feeds.get требует Ids" in n for n in notes), (
        f"ожидался явный note про ограничение feeds.get: {notes}"
    )
