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

import requests  # noqa: E402

from src.extract import _common as C  # noqa: E402
from src.extract import crux  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.pipeline import orchestrator  # noqa: E402


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


# ── Тест 4: отсутствие ключа — понятная ошибка, а не тихий пустой результат ──
def test_crux_missing_api_key_raises_clear_error(paths):
    """Без CRUX_API_KEY в .env extract() падает с внятным SourceUnavailable,
    а не молча пишет пустой crux.json."""
    session = FakeSession([])  # ни одного HTTP-вызова быть не должно

    with pytest.raises(C.SourceUnavailable) as exc_info:
        crux.extract(CONFIG, {}, paths, session=session)

    assert "CRUX_API_KEY" in str(exc_info.value)
    assert session.calls == []
    assert not (paths.raw / "crux" / "crux.json").exists()


# ── Тест 5: ping() — осмысленный результат ──────────────────────────────────
def test_ping_true_with_valid_config_and_key():
    assert crux.ping(CONFIG, ENV) is True


def test_ping_false_without_key():
    assert crux.ping(CONFIG, {}) is False


def test_ping_false_without_origin():
    config_no_origin = {"sources": {"crux": {"enabled": True, "api_key_env": "CRUX_API_KEY"}}}
    assert crux.ping(config_no_origin, ENV) is False


def test_ping_true_via_gsc_site_url_fallback():
    """origin не задан явно, но есть sources.gsc.site_url — ping должен его подхватить."""
    config = {
        "sources": {
            "crux": {"enabled": True, "api_key_env": "CRUX_API_KEY"},
            "gsc": {"site_url": "https://example.com/"},
        },
    }
    assert crux.ping(config, ENV) is True


# ── Тест 6: crux.extract вызывается из основного оркестратора (полный прогон) ──
class _OrchestratorPaths:
    """Минимальный дублёр ClientPaths, достаточный для orchestrator.run_extract."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config_file = root / "config.yaml"
        self.env_file = root / ".env"
        self.raw = root / "data" / "raw"


def test_crux_dispatch_wired_in_orchestrator():
    """Регрессия на карту диспетчеризации: crux не должен молча выпасть из EXTRACTORS."""
    assert orchestrator.EXTRACTORS.get("crux") == ["crux"]
    assert orchestrator._modules_for_source("crux", {"enabled": True}) == ["crux"]


def test_crux_extract_called_from_orchestrator_full_run(tmp_path, monkeypatch):
    """Полный прогон run_extract() с одним включённым источником (crux) и
    замоканным ключом: реальный requests.Session подменяется на уровне
    Session.request, но диспетчеризация, загрузка .env/config.yaml и запись
    manifest — настоящие (как в проде)."""
    root = tmp_path / "client"
    root.mkdir()
    (root / "config.yaml").write_text(
        "sources:\n"
        "  crux:\n"
        "    enabled: true\n"
        "    api_key_env: \"CRUX_API_KEY\"\n"
        "    origin: \"https://example.com\"\n"
        "    key_urls: []\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("CRUX_API_KEY=fake-orchestrator-key\n", encoding="utf-8")

    def fake_request(self, method, url, **kwargs):
        assert "records:queryRecord" in url
        return FakeResponse(json_data={"record": {
            "key": {"origin": "https://example.com"},
            "metrics": {
                "largest_contentful_paint": {"percentiles": {"p75": 2400}},
            },
        }})

    monkeypatch.setattr(requests.Session, "request", fake_request)

    log_lines: list[str] = []
    orchestrator.run_extract(_OrchestratorPaths(root), log_lines.append)

    assert (root / "data" / "raw" / "crux" / "crux.json").exists()

    entry = manifest_mod.load_manifest(root / "data" / "raw")["sources"]["crux"]
    assert entry["cwv_field_data_available"] is True

    summary = "\n".join(log_lines)
    assert "extract[crux]: старт" in summary
    assert "выгружено 1" in summary
    assert "недоступно 0" in summary
