"""Тесты CrUX-экстрактора (Chrome UX Report API).

Три сценария задачи 3C:
  1. данные есть  — origin + key URL -> cwv_field_data_available=True, два record-а
  2. данных нет   — 404 штатный, НЕ ошибка, cwv_field_data_available=False, один вызов
  3. временная API-ошибка — 5xx исчерпывает ретраи -> SourceUnavailable
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402
from src.extract import crux  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Тестовые дублёры ────────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, status_code=200, *, json_data=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = {}
        self.text = ""

    def json(self):
        if self._json is None:
            raise ValueError("нет JSON")
        return self._json


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self._per_route_counts: dict[int, int] = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for idx, (pred, responder) in enumerate(self.routes):
            if pred(method, url):
                n = self._per_route_counts.get(idx, 0)
                self._per_route_counts[idx] = n + 1
                return responder(n) if callable(responder) else responder
        raise AssertionError(f"нет мока для {method} {url}")


def _url_contains(*needles):
    return lambda _method, url: all(n in url for n in needles)


class _Paths:
    def __init__(self, raw: Path):
        self.raw = raw
        self.root = raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return _Paths(tmp_path / "data" / "raw")


# ── Фикстуры данных ─────────────────────────────────────────────────────────
CONFIG = {
    "sources": {"crux": {
        "enabled": True,
        "api_key_env": "CRUX_API_KEY",
        "origin": "https://example.com",
        "key_urls": ["https://example.com/landing"],
    }},
}
ENV = {"CRUX_API_KEY": "fake-key"}


def _ok_response():
    return FakeResponse(json_data={"record": {
        "key": {"origin": "https://example.com"},
        "metrics": {
            "largest_contentful_paint": {"percentiles": {"p75": 2500}},
            "cumulative_layout_shift": {"percentiles": {"p75": "0.08"}},
            "interaction_to_next_paint": {"percentiles": {"p75": 180}},
        },
    }})


# ── Тест 1: данные есть ─────────────────────────────────────────────────────
def test_crux_field_data_present(paths):
    """200 для origin и key URL -> cwv_field_data_available=True, два record-а в файле."""
    session = FakeSession([(_url_contains("records:queryRecord"), _ok_response())])

    result = crux.extract(CONFIG, ENV, paths, session=session)

    assert result["cwv_field_data_available"] is True
    assert result["rows"] == 2

    data = json.loads((paths.raw / "crux" / "crux.json").read_text("utf-8"))
    assert data["cwv_field_data_available"] is True
    assert len(data["records"]) == 2
    origin_rec = data["records"][0]
    assert origin_rec["target_type"] == "origin"
    assert origin_rec["p75"]["largest_contentful_paint"] == 2500

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["crux"]
    assert entry["cwv_field_data_available"] is True
    assert entry["source_mode"] == "api"


# ── Тест 2: данных нет (404 = штатный случай) ───────────────────────────────
def test_crux_no_field_data_is_not_error(paths):
    """404 — НЕ исключение: cwv_field_data_available=False, URL-запросы пропущены."""
    session = FakeSession([
        (_url_contains("records:queryRecord"), FakeResponse(status_code=404)),
    ])

    result = crux.extract(CONFIG, ENV, paths, session=session)

    assert result["cwv_field_data_available"] is False
    # origin пуст -> URL-запросы не делаются -> ровно один HTTP-вызов
    assert len(session.calls) == 1

    data = json.loads((paths.raw / "crux" / "crux.json").read_text("utf-8"))
    assert data["cwv_field_data_available"] is False
    assert data["records"][0]["field_data_available"] is False

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["crux"]
    assert entry["cwv_field_data_available"] is False
    assert any("порог" in n or "лаборатор" in n for n in entry["notes"])


# ── Тест 3: временная API-ошибка (5xx) ──────────────────────────────────────
def test_crux_transient_5xx_raises_source_unavailable(paths, monkeypatch):
    """5xx на всех попытках: ретраи исчерпаны -> SourceUnavailable (не зависание)."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    session = FakeSession([
        (_url_contains("records:queryRecord"), FakeResponse(status_code=503)),
    ])

    with pytest.raises(C.SourceUnavailable):
        crux.extract(CONFIG, ENV, paths, session=session)

    # Все MAX_ATTEMPTS попыток должны быть сделаны перед сдачей
    assert len(session.calls) == C.MAX_ATTEMPTS
