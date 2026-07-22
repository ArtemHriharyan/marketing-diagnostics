"""Тесты 2A-direct-strategy(-fix): BiddingStrategy через campaigns.get + statistics_field_scope.

2A-direct-strategy-fix: "Strategy" не входит в enum верхнеуровневого FieldNames
campaigns.get (боевой error 8000, см. src/extract/direct.py:CAMPAIGNS_FIELD_NAMES_ENUM) —
BiddingStrategy запрашивается отдельным параметром TextCampaignFieldNames и приходит
вложенным в TextCampaign.BiddingStrategy.Search/Network.BiddingStrategyType.
Никаких других вызовов (placements/targeting/ads/keywords) не затрагиваем.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import direct  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, *, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}
        self.content = (text or "").encode("utf-8")

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
    )


def _query_tsv():
    return (
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить окна\tbroad\t2000000\t3\t50\t1\n"
    )


CONFIG_DIRECT = {
    "sources": {"direct": {"enabled": True, "client_login": "test-login"}},
    "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
}
ENV = {"DIRECT_TOKEN": "fake-token"}


def _routes(box, *, campaigns_result):
    def reports(n):
        _m, _u, kwargs = box["session"].calls[-1]
        params = kwargs["json"]["params"]
        rt = params["ReportType"]
        if rt == "CAMPAIGN_PERFORMANCE_REPORT":
            body = _campaign_tsv()
        elif rt == "SEARCH_QUERY_PERFORMANCE_REPORT":
            body = _query_tsv()
        else:
            body = "Placement\tAdNetworkType\tCampaignId\tCost\tClicks\tConversions\n"
        if n == 0:
            return FakeResponse(status_code=202, headers={"retryIn": "0"})
        return FakeResponse(status_code=200, text=body)

    return [
        (_contains("/reports"), reports),
        (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": campaigns_result}})),
        (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": []}})),
        (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
        (_contains("/adextensions"), FakeResponse(json_data={"result": {"AdExtensions": []}})),
        (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": []}})),
        (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": []}})),
        (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": []}})),
    ]


# ── BiddingStrategy запрошен через TextCampaignFieldNames, не FieldNames ────
def test_strategy_requested_in_field_names():
    """"Strategy" не входит в enum FieldNames campaigns.get (error 8000) и не
    запрашивается там; BiddingStrategy запрашивается отдельно."""
    assert "Strategy" not in direct.CAMPAIGN_FIELD_NAMES
    assert "Statistics" in direct.CAMPAIGN_FIELD_NAMES
    assert "Strategy" not in direct.CAMPAIGNS_FIELD_NAMES_ENUM
    assert "BiddingStrategy" in direct.TEXT_CAMPAIGN_FIELD_NAMES


def test_campaigns_get_call_includes_strategy_field(tmp_path):
    """Реальный вызов campaigns.get несёт TextCampaignFieldNames с BiddingStrategy,
    но НЕ несёт "Strategy" в FieldNames."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "Поиск", "State": "ON"},
    ]))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    campaigns_calls = [kwargs for _m, u, kwargs in session.calls if "/campaigns" in u]
    assert campaigns_calls, "не найден ни один вызов campaigns.get"
    for kwargs in campaigns_calls:
        params = kwargs["json"]["params"]
        field_names = params.get("FieldNames", [])
        assert "Strategy" not in field_names
        assert params.get("TextCampaignFieldNames") == ["BiddingStrategy"]


# ── strategy_field_present: факт наличия, не предположение ──────────────────
def test_strategy_field_present_true_when_api_returns_it(tmp_path):
    """Если API вернул TextCampaign.BiddingStrategy хотя бы у одной кампании —
    флаг True, сырые вложенные данные в manifest."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "Поиск", "State": "ON",
         "TextCampaign": {"BiddingStrategy": {
             "Search": {"BiddingStrategyType": "WB_MAXIMUM_CONVERSION_RATE"}}}},
    ]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["strategy_field_present"] is True

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["strategy_field_present"] is True
    assert entry["strategy_field_samples"], "сырые образцы BiddingStrategy должны попасть в manifest"
    assert entry["strategy_field_samples"][0] == {
        "Search": {"BiddingStrategyType": "WB_MAXIMUM_CONVERSION_RATE"}
    }


def test_strategy_field_absent_documented_not_guessed(tmp_path):
    """Если API не вернул TextCampaign.BiddingStrategy — флаг False + явный note,
    без угадывания структуры."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "Поиск", "State": "ON"},
    ]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["strategy_field_present"] is False

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["strategy_field_present"] is False
    assert entry["strategy_field_samples"] == []
    notes = entry.get("notes", [])
    assert any("Strategy" in n for n in notes), f"ожидался note про отсутствие Strategy: {notes}"


def test_strategy_field_not_flat_top_level(tmp_path):
    """Плоское верхнеуровневое поле Strategy (устаревшая форма) НЕ распознаётся
    как BiddingStrategy — контракт требует именно вложенный TextCampaign.BiddingStrategy."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "Поиск", "State": "ON",
         "Strategy": {"Search": {"BiddingStrategyType": "WB_MAXIMUM_CONVERSION_RATE"}}},
    ]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["strategy_field_present"] is False


def test_strategy_parses_network_bidding_strategy_type(tmp_path):
    """BiddingStrategyType читается из вложенного TextCampaign.BiddingStrategy.Network,
    не только Search."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "РСЯ", "State": "ON",
         "TextCampaign": {"BiddingStrategy": {
             "Network": {"BiddingStrategyType": "SERVING_OFF"}}}},
    ]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["strategy_field_present"] is True

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["strategy_field_samples"][0] == {
        "Network": {"BiddingStrategyType": "SERVING_OFF"}
    }


# ── field_names_validation: невалидный элемент фильтруется до отправки ─────
def test_invalid_field_name_filtered_before_request(tmp_path, monkeypatch):
    """Невалидное имя поля (не входит в CAMPAIGNS_FIELD_NAMES_ENUM) отфильтровано
    ДО отправки запроса — не попадает в тело FieldNames, источник не падает."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[{"Id": 1, "Name": "Поиск"}]))
    box["session"] = session

    monkeypatch.setattr(
        direct, "CAMPAIGN_FIELD_NAMES",
        direct.CAMPAIGN_FIELD_NAMES + ["NotARealField"],
    )

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result is not None

    campaigns_calls = [kwargs for _m, u, kwargs in session.calls if "/campaigns" in u]
    for kwargs in campaigns_calls:
        assert "NotARealField" not in kwargs["json"]["params"].get("FieldNames", [])


# ── statistics_field_scope: одно из трёх значений, не null по умолчанию ─────
def test_statistics_field_scope_present_and_valid(tmp_path):
    """manifest.statistics_field_scope не null и входит в разрешённый набор значений."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[
        {"Id": 1, "Name": "Поиск", "State": "ON", "Statistics": {"Clicks": 1, "Impressions": 10}},
    ]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["statistics_field_scope"] is not None
    assert result["statistics_field_scope"] in ("rolling_window", "all_time", "unknown")

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["statistics_field_scope"] is not None
    assert entry["statistics_field_scope"] in ("rolling_window", "all_time", "unknown")


def test_statistics_field_scope_unknown_by_default(tmp_path):
    """Без живого сравнения периодов statistics_field_scope честно = unknown (не угадан)."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[{"Id": 1, "Name": "Поиск"}]))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert result["statistics_field_scope"] == direct.STATISTICS_FIELD_SCOPE_UNKNOWN


def test_campaigns_get_never_sends_statistics_crit(tmp_path):
    """campaigns.get не передаёт StatisticsCrit ни в одном вызове (проверка по факту)."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[{"Id": 1, "Name": "Поиск"}]))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    campaigns_calls = [kwargs for _m, u, kwargs in session.calls if "/campaigns" in u]
    assert campaigns_calls
    for kwargs in campaigns_calls:
        assert "StatisticsCrit" not in kwargs["json"]["params"]


# ── прочие вызовы не затронуты ──────────────────────────────────────────────
def test_other_calls_unaffected(tmp_path):
    """placements/targeting/ads/keywords продолжают запрашиваться как раньше."""
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    box = {}
    session = FakeSession(_routes(box, campaigns_result=[{"Id": 1, "Name": "Поиск"}]))
    box["session"] = session

    direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    called_paths = {u for _m, u, _kw in session.calls}
    for expected in ("/adgroups", "/bidmodifiers", "/ads", "/keywords"):
        assert any(expected in u for u in called_paths), f"{expected} не был вызван"
