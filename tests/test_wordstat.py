"""Тесты src/extract/wordstat.py — topRequests + dynamics (task_id WS-1).

Полная замена прежнего месячного агрегата: старый экстрактор ходил в очередь
отчётов Wordstat (legacy v4 Директа), новый — в Wordstat API
(api.wordstat.yandex.net, topRequests/dynamics). Сценарии:
    1. topRequests: нормальный ответ, пустой ответ (маска без данных).
    2. Фильтр стоп-слов: совпадение по подстроке, отсутствие ложных срабатываний.
    3. core_queries: seed-маска добавляется, даже если не в топе; дедуп работает.
    4. dynamics: полный диапазон в один вызов на фразу, weekly-гранулярность.
    5. HTTP 503 (квота Wordstat): retry с backoff, manifest фиксирует
       wordstat_quota_hit=true/false и wordstat_calls_made.
    6. Регрессия: отсутствие seeds по-прежнему падает SourceUnavailable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402
from src.extract import wordstat  # noqa: E402
from src.extract import wordstat_config as WC  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Тестовые дублёры HTTP (см. tests/test_extract_smoke.py — тот же паттерн) ─
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


class Paths:
    """Мини-дублёр ClientPaths: .raw и .root (для inputs/wordstat_stopwords.yaml)."""

    def __init__(self, raw: Path, root: Path):
        self.raw = raw
        self.root = root


@pytest.fixture
def paths(tmp_path):
    root = tmp_path / "clients" / "test-client"
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    return Paths(root / "data" / "raw", root=root)


def _write_stopwords(paths, entries_yaml: str) -> None:
    (paths.root / "inputs" / "wordstat_stopwords.yaml").write_text(entries_yaml, encoding="utf-8")


NO_SLEEP = lambda _sec: None
ENV = {"WORDSTAT_TOKEN": "fake-ws-token"}

CONFIG = {
    "wordstat_seeds": ["аренда авто"],
    "sources": {"wordstat": {"regions": [213], "devices": ["all"]}},
    "top_n_gap": 2,
    "top_n_seasonality": 2,
    "data_window": {"date_from": "2026-01-01", "date_to": "2026-01-31"},
}

STOPWORDS_YAML = """
entries:
  - phrase: "конкурент-бренд"
    scope: "junk"
    reason: "конкурент"
    added_by: "test"
    added_at: "2026-07-21"
  - phrase: "что такое"
    scope: "general"
    reason: "инфозапрос"
    added_by: "test"
    added_at: "2026-07-21"
"""

TOP_REQUESTS_RESPONSE = {
    "topRequests": [
        {"phrase": "аренда авто без водителя", "count": 100},
        {"phrase": "аренда авто конкурент-бренд", "count": 90},   # junk
        {"phrase": "что такое аренда авто", "count": 80},          # general
        {"phrase": "аренда авто владивосток", "count": 70},
    ]
}

DYNAMICS_RESPONSE = {
    "dynamics": [
        {"date": "2026-01-05", "count": 10, "share": 0.01},
        {"date": "2026-01-12", "count": 12, "share": 0.012},
    ]
}


def _routes(top_requests_resp, dynamics_resp):
    return [
        (_contains("/v1/topRequests"), lambda _n: FakeResponse(json_data=top_requests_resp)),
        (_contains("/v1/dynamics"), lambda _n: FakeResponse(json_data=dynamics_resp)),
    ]


# ── 1 + 3. topRequests нормальный ответ, core_queries: seed + дедуп ─────────
def test_extract_builds_core_queries_and_weekly_with_dedup(paths):
    _write_stopwords(paths, STOPWORDS_YAML)
    session = FakeSession(_routes(TOP_REQUESTS_RESPONSE, DYNAMICS_RESPONSE))

    result = wordstat.extract(CONFIG, ENV, paths, session=session, sleeper=NO_SLEEP)

    core = pd.read_parquet(paths.raw / "wordstat" / "wordstat_core_queries.parquet")
    weekly = pd.read_parquet(paths.raw / "wordstat" / "wordstat_weekly.parquet")

    phrases = set(core["phrase"])
    # seed-маска добавлена, хотя её нет среди topRequests-элементов.
    assert "аренда авто" in phrases
    seed_row = core[core["phrase"] == "аренда авто"].iloc[0]
    assert list(seed_row["purpose"]) == ["seasonality"]
    assert pd.isna(seed_row["top_requests_count"])

    # junk вырезан отовсюду.
    assert "аренда авто конкурент-бренд" not in phrases
    # general вырезан из gap, но остался в seasonality.
    general_row = core[core["phrase"] == "что такое аренда авто"].iloc[0]
    assert list(general_row["purpose"]) == ["seasonality"]
    assert general_row["scope"] == "general"

    # Фраза, попавшая и в gap, и в seasonality — дедуп в одну запись с обоими purpose.
    both_row = core[core["phrase"] == "аренда авто без водителя"].iloc[0]
    assert list(both_row["purpose"]) == ["gap", "seasonality"]
    assert both_row["scope"] == "gap-specific"

    assert result["target_queries"] == len(core) == 4
    # 4 уникальные фразы x 2 недельные точки каждая.
    assert len(weekly) == 8
    assert set(weekly["date"]) == {"2026-01-05", "2026-01-12"}

    raw_dump = paths.raw / "wordstat" / "topRequests_raw" / "аренда_авто.json"
    assert json.loads(raw_dump.read_text("utf-8")) == TOP_REQUESTS_RESPONSE

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["wordstat"]
    assert entry["canonical_tables"] == ["wordstat"]
    assert entry["wordstat_quota_hit"] is False
    assert entry["wordstat_calls_made"] == 1 + 4  # 1 topRequests + 4 dynamics (по фразе)
    assert entry["wordstat_stopwords_empty"] is False


# ── 1. topRequests пустой ответ (маска без данных) ──────────────────────────
def test_extract_handles_empty_top_requests(paths):
    _write_stopwords(paths, STOPWORDS_YAML)
    config = {**CONFIG, "wordstat_seeds": ["маска без данных"]}
    session = FakeSession(_routes({"topRequests": []}, DYNAMICS_RESPONSE))

    result = wordstat.extract(config, ENV, paths, session=session, sleeper=NO_SLEEP)

    # Только сама seed-маска, форсированно добавленная в seasonality.
    assert result["target_queries"] == 1
    core = pd.read_parquet(paths.raw / "wordstat" / "wordstat_core_queries.parquet")
    assert list(core["phrase"]) == ["маска без данных"]


# ── 2. Фильтр стоп-слов: подстрока + отсутствие ложных срабатываний ────────
def test_merge_candidates_stopword_substring_and_no_false_positive():
    entries = [
        {"phrase": "конкурент-бренд", "scope": "junk", "reason": "т"},
        {"phrase": "что такое", "scope": "general", "reason": "т"},
    ]
    items = [
        {"phrase": "аренда авто конкурент-бренд владивосток", "count": 50},  # junk по подстроке
        {"phrase": "аренда авто владивосток", "count": 40},                   # не содержит стоп-фраз
    ]
    target: dict = {}
    wordstat._merge_gap_candidates(target, "аренда авто", items, top_n=5, stopword_entries=entries)
    phrases = {v["phrase"] for v in target.values()}
    assert "аренда авто конкурент-бренд владивосток" not in phrases  # отфильтровано
    assert "аренда авто владивосток" in phrases  # ложного срабатывания нет


# ── 4. dynamics: один вызов на фразу, полный диапазон, weekly ──────────────
def test_dynamics_called_once_per_phrase_with_full_range(paths):
    _write_stopwords(paths, STOPWORDS_YAML)
    session = FakeSession(_routes(TOP_REQUESTS_RESPONSE, DYNAMICS_RESPONSE))

    wordstat.extract(CONFIG, ENV, paths, session=session, sleeper=NO_SLEEP)

    dyn_calls = [c for c in session.calls if "/v1/dynamics" in c[1]]
    # 4 уникальные фразы -> ровно 4 вызова dynamics, ни один не по неделям в цикле.
    assert len(dyn_calls) == 4
    for call in dyn_calls:
        body = call[2]["json"]
        assert body["period"] == "weekly"
        assert body["fromDate"] == "2026-01-01"
        assert body["toDate"] == "2026-01-31"


# ── 5. HTTP 503 (квота): retry с backoff, manifest фиксирует wordstat_quota_hit ─
def test_quota_503_retries_and_flags_manifest(paths):
    _write_stopwords(paths, STOPWORDS_YAML)

    def top_requests_responder(n):
        if n == 0:
            return FakeResponse(status_code=503)
        return FakeResponse(json_data=TOP_REQUESTS_RESPONSE)

    routes = [
        (_contains("/v1/topRequests"), top_requests_responder),
        (_contains("/v1/dynamics"), lambda _n: FakeResponse(json_data=DYNAMICS_RESPONSE)),
    ]
    session = FakeSession(routes)

    result = wordstat.extract(CONFIG, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["target_queries"] == 4
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["wordstat"]
    assert entry["wordstat_quota_hit"] is True
    # 1 неудачная (503, не в счётчике calls_made) + 1 успешная topRequests + 4 dynamics.
    assert entry["wordstat_calls_made"] == 1 + 4


def test_quota_503_exhausted_raises(paths):
    _write_stopwords(paths, STOPWORDS_YAML)
    routes = [(_contains("/v1/topRequests"), lambda _n: FakeResponse(status_code=503))]
    session = FakeSession(routes)

    with pytest.raises(C.SourceUnavailable):
        wordstat.extract(CONFIG, ENV, paths, session=session, sleeper=NO_SLEEP)


# ── 6. Регрессия: без seeds — SourceUnavailable (поведение не изменилось) ──
def test_extract_no_seeds_raises(paths):
    with pytest.raises(C.SourceUnavailable):
        wordstat.extract({"wordstat_seeds": []}, ENV, paths,
                          session=FakeSession([]), sleeper=NO_SLEEP)
