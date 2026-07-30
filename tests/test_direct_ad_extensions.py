"""Тесты FIX-ad-extensions-coverage: adextensions.get запрашивает только
подтверждённые документацией поля, невалидное поле не роняет источник, а
недоступность структурных полей цены/акции/срока/наличия (A24) закрыта явным
caveat в manifest, а не молчаливым отсутствием данных.

Факт по официальной документации (ref-v5/adextensions/get.html): Яндекс.Директ
API v5 отдаёт единственный тип расширения — CALLOUT (уточнение) и только его
текст (CalloutText). Цена/акция/срок/наличие через API недоступны. Это НЕ
пробел выгрузки, а ограничение источника — фиксируется явно.
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


def _routes(box, *, adextensions_response=None):
    """adextensions_response: callable(n)->FakeResponse либо FakeResponse.
    По умолчанию — пустой успешный ответ AdExtensions."""
    if adextensions_response is None:
        adextensions_response = FakeResponse(json_data={"result": {"AdExtensions": []}})

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
        (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": [
            {"Id": 1, "Name": "Поиск", "State": "ON"},
        ]}})),
        (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": []}})),
        (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
        (_contains("/adextensions"), adextensions_response),
        (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": []}})),
        (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": []}})),
        (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": []}})),
    ]


def _run(tmp_path, session):
    paths = Paths(tmp_path / "data" / "raw")
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    return direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP), paths


# ── Константы: только CALLOUT, State невалиден ──────────────────────────────
def test_adextensions_constants_documented():
    """Единственный тип — CALLOUT; State не запрашивается; поля из enum."""
    assert direct.ADEXTENSION_TYPES == ["CALLOUT"]
    assert "State" not in direct.ADEXTENSIONS_FIELD_NAMES
    assert "State" not in direct.ADEXTENSIONS_FIELD_NAMES_ENUM
    assert set(direct.ADEXTENSIONS_FIELD_NAMES) <= direct.ADEXTENSIONS_FIELD_NAMES_ENUM
    assert "CalloutText" != direct.ADEXTENSION_TYPES  # sanity


# ── Реальный вызов adextensions.get: документированные поля, без State ──────
def test_adextensions_get_uses_documented_fields(tmp_path):
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session
    _run(tmp_path, session)

    ae_calls = [kwargs for _m, u, kwargs in session.calls if "/adextensions" in u]
    assert ae_calls, "adextensions.get не был вызван"
    for kwargs in ae_calls:
        params = kwargs["json"]["params"]
        field_names = params.get("FieldNames", [])
        assert "State" not in field_names, "State невалиден для adextensions.get"
        assert all(f in direct.ADEXTENSIONS_FIELD_NAMES_ENUM for f in field_names)
        assert params.get("CalloutFieldNames") == ["CalloutText"]
        assert params.get("SelectionCriteria", {}).get("Types") == ["CALLOUT"]


# ── Невалидное поле фильтруется ДО запроса, источник не падает ──────────────
def test_invalid_adextensions_field_filtered_before_request(tmp_path, monkeypatch):
    monkeypatch.setattr(
        direct, "ADEXTENSIONS_FIELD_NAMES",
        direct.ADEXTENSIONS_FIELD_NAMES + ["State", "NotARealField"],
    )
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session
    result, _ = _run(tmp_path, session)
    assert result is not None  # источник не упал

    ae_calls = [kwargs for _m, u, kwargs in session.calls if "/adextensions" in u]
    assert ae_calls
    for kwargs in ae_calls:
        sent = kwargs["json"]["params"].get("FieldNames", [])
        assert "State" not in sent
        assert "NotARealField" not in sent


# ── Ошибка adextensions.get изолирована: extract целиком не падает ──────────
def test_adextensions_failure_isolated(tmp_path):
    import json as _json
    err_body = _json.dumps({"error": {"error_code": "8000", "error_detail": "bad field"}})
    box = {}
    session = FakeSession(_routes(box, adextensions_response=FakeResponse(
        status_code=200, text=err_body)))
    box["session"] = session
    result, paths = _run(tmp_path, session)

    assert result is not None  # источник Direct не упал из-за расширений
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    notes = entry.get("notes", [])
    assert any("adextensions" in n or "уточнения" in n for n in notes), (
        f"ожидался note о недоступности расширений: {notes}"
    )


# ── A24 закрыт явным caveat, а не молчаливым отсутствием ────────────────────
def test_a24_price_fields_unavailable_documented_not_silent(tmp_path):
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session
    _result, paths = _run(tmp_path, session)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["ad_extensions_price_fields_available"] is False
    assert entry["ad_extensions_types_available"] == ["CALLOUT"]

    caveat = entry["ad_extensions_caveat"]
    assert "A24" in caveat["affected_checks"]
    reason = caveat["reason"]
    # причина названа явно, не пустая строка
    assert "CalloutText" in reason
    assert "A24" in reason
    # A21/A23 не должны быть помечены как затронутые (они не зависят от расширений)
    assert "A21" not in caveat["affected_checks"]
    assert "A23" not in caveat["affected_checks"]


# ── Успешный CALLOUT-ответ выгружается как есть (текст уточнения) ───────────
def test_callout_extension_extracted_when_present(tmp_path):
    box = {}
    session = FakeSession(_routes(box, adextensions_response=FakeResponse(json_data={
        "result": {"AdExtensions": [
            {"Id": 5, "Type": "CALLOUT", "Callout": {"CalloutText": "Доставка 24ч"}},
        ]},
    })))
    box["session"] = session
    _run(tmp_path, session)

    ae_json = tmp_path / "data" / "raw" / "direct" / "ad_texts.json"
    assert ae_json.exists()
    import json as _json
    data = _json.loads(ae_json.read_text("utf-8"))
    assert data["extensions"], "callout-расширение должно попасть в ad_texts.json"
    assert data["extensions"][0]["Callout"]["CalloutText"] == "Доставка 24ч"
