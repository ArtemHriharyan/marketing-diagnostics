"""Тесты 4X-metrika-lookback: расширение окна Logs API назад для carry-forward.

Контекст: T02/T03 (см. marketing-diagnostics-methodology-v2.md §5,
tests/test_transform_visits_traffic_resolve.py) требует истории clientID ДО
data_window.date_from, чтобы восстановить internal/undefined источник. Этот
патч заставляет extract запрашивать ещё
config.transform.traffic_resolve_lookback_days (config/defaults.yaml, default
30) дней раньше — только как контекст, не для метрик.

Использует ту же схему моков HTTP (FakeSession/FakeResponse), что и
tests/test_metrika_logs_patch.py / tests/test_extract_smoke.py — реальная
сеть не трогается.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import metrika_logs  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.transform import build_canonical as bc  # noqa: E402


# ── Тестовые дублёры HTTP (см. test_metrika_logs_patch.py) ─────────────────
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
    def __init__(self, raw: Path, root: Path | None = None):
        self.raw = raw
        self.root = root if root is not None else raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "data" / "raw", root=tmp_path)


CONFIG_METRIKA = {
    "sources": {"metrika": {"enabled": True, "counter_id": 12345}},
    "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
}
ENV = {"METRIKA_TOKEN": "fake-metrika"}
NO_SLEEP = lambda _sec: None


def _evaluate_route(get_session):
    """Мок logrequests/evaluate: всегда ok (в этих тестах негоциация не тестируется)."""
    def responder(_n):
        return FakeResponse(json_data={"log_request_evaluation": {"possible": True}})
    return (lambda m, u: m == "GET" and u.endswith("/logrequests/evaluate"), responder)


def _create_route(request_id):
    return (
        lambda m, u: m == "POST" and u.endswith("/logrequests"),
        FakeResponse(json_data={"log_request": {"request_id": request_id, "status": "created"}}),
    )


def _poll_route(request_id):
    """Готово с первого опроса (ready сразу) — независимо от того, сколько раз вызван."""
    def responder(_n):
        return FakeResponse(json_data={"log_request": {
            "request_id": request_id, "status": "processed", "parts": [{"part_number": 0}]}})
    return (lambda m, u: m == "GET" and u.endswith(f"/logrequest/{request_id}"), responder)


def _download_route(part_text):
    return (_contains("/part/0/download"), FakeResponse(text=part_text))


def _routes(box, *, request_id=700, part_text=None):
    part_text = part_text or (
        "ym:s:visitID\tym:s:clientID\tym:s:dateTime\tym:s:lastsignTrafficSource\n"
        "v1\tc1\t2026-06-15 10:00:00\tad\n"
    )
    return [
        _evaluate_route(lambda: box["session"]),
        _create_route(request_id),
        _poll_route(request_id),
        _download_route(part_text),
    ]


# ── Запрос уходит с расширенной датой ───────────────────────────────────────
def test_lookback_request_extends_start_date_backward(paths):
    """При выгрузке основного окна extract доп. запрашивает lookback-чанк
    ДО data_window.date_from (по умолчанию 30 дней, config/defaults.yaml)."""
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session

    result = metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        defaults={"transform": {"traffic_resolve_lookback_days": 30}},
    )

    create_calls = [c for c in session.calls if c[0] == "POST" and c[1].endswith("/logrequests")]
    requested_windows = {(c[2]["params"]["date1"], c[2]["params"]["date2"]) for c in create_calls}

    assert ("2026-06-01", "2026-06-30") in requested_windows   # основное окно
    assert ("2026-05-02", "2026-05-31") in requested_windows   # lookback: 30 дней до date_from

    assert result["lookback_requested_days"] == 30
    assert result["lookback_date_from_requested"] == "2026-05-02"
    assert result["lookback_date_to"] == "2026-05-31"
    assert result["lookback_rows"] == 1


def test_lookback_days_default_from_config_defaults_yaml(paths):
    """Без defaults (None) используется дефолт 30 (config/defaults.yaml)."""
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session

    result = metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["lookback_requested_days"] == 30
    assert result["lookback_date_from_requested"] == "2026-05-02"


def test_lookback_zero_disables_extra_fetch(paths):
    """lookback_days=0 -> ни одного лишнего запроса, нулевая статистика."""
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session

    result = metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        defaults={"transform": {"traffic_resolve_lookback_days": 0}},
    )

    create_calls = [c for c in session.calls if c[0] == "POST" and c[1].endswith("/logrequests")]
    assert len(create_calls) == 1   # только основное окно
    assert result["lookback_requested_days"] == 0
    assert result["lookback_rows"] == 0
    assert result["lookback_effective_date_from"] is None


# ── Раздельное расположение сырья (эквивалент is_lookback_only на raw-слое) ──
def test_lookback_rows_written_to_separate_subdir_not_top_level(paths):
    """Lookback-визиты лежат в metrika_logs/lookback/, не смешиваясь с visits_*
    верхнего уровня (build_visits их не увидит — см. следующий тест)."""
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session

    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    src_dir = paths.raw / "metrika_logs"
    top_level_files = sorted(p.name for p in src_dir.glob("visits_*.csv.gz"))
    lookback_files = sorted(p.name for p in (src_dir / metrika_logs.LOOKBACK_SUBDIR).glob("*.csv.gz"))

    assert all(not name.startswith("visits_lookback_") for name in top_level_files)
    assert len(lookback_files) == 1
    assert lookback_files[0].startswith("visits_lookback_")


def test_lookback_manifest_records_actual_depth_and_dir(paths):
    box = {}
    session = FakeSession(_routes(box))
    box["session"] = session

    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["lookback_requested_days"] == 30
    assert entry["lookback_rows"] == 1
    assert entry["lookback_effective_date_from"] == "2026-05-02"
    assert entry["lookback_days_covered"] == 30


def test_lookback_partial_history_reports_covered_depth_honestly(tmp_path):
    """Счётчик не имеет истории так далеко назад: старые чанки возвращают 0
    строк -> lookback_days_covered отражает ТОЛЬКО реально покрытые дни, а не
    запрошенные (см. docstring _fetch_lookback: "не предполагать полноту").

    lookback_days=90 от 2026-07-01 -> чанки апрель/май/июнь (в хронологическом
    порядке, см. C.month_chunks). Мок отдаёт пустые апрель/май (до начала
    истории счётчика) и непустой июнь — фактическая глубина должна быть
    ровно 30 дней (2026-07-01 minus 2026-06-01), не 90.
    """
    from datetime import date

    chunk_texts = [
        "ym:s:visitID\n",                                    # апрель — пусто
        "ym:s:visitID\n",                                    # май — пусто
        "ym:s:visitID\tym:s:clientID\nv1\tc1\n",              # июнь — есть данные
    ]
    create_calls = {"n": 0}
    download_calls = {"n": -1}

    def create_responder(_n):
        create_calls["n"] += 1
        return FakeResponse(json_data={
            "log_request": {"request_id": 800 + create_calls["n"], "status": "created"}
        })

    def poll_responder(_n):
        return FakeResponse(json_data={
            "log_request": {"status": "processed", "parts": [{"part_number": 0}]}
        })

    def download_responder(_n):
        download_calls["n"] += 1
        return FakeResponse(text=chunk_texts[download_calls["n"]])

    routes = [
        (lambda m, u: m == "POST" and u.endswith("/logrequests"), create_responder),
        (lambda m, u: m == "GET" and "/logrequest/" in u and "part" not in u, poll_responder),
        (_contains("/part/0/download"), download_responder),
    ]
    session = FakeSession(routes)

    result = metrika_logs._fetch_lookback(
        session, 12345, {}, tmp_path, date(2026, 7, 1),
        ["ym:s:visitID", "ym:s:clientID"], 90, sleeper=NO_SLEEP, log=lambda _msg: None,
    )

    assert result["lookback_requested_days"] == 90
    assert result["lookback_effective_date_from"] == "2026-06-01"
    assert result["lookback_days_covered"] == 30   # реально покрыт только июнь, не все 90
    assert result["lookback_rows"] == 1


# ── Lookback-визиты не попадают в основные agg-метрики (build_visits) ──────
def test_lookback_visits_excluded_from_build_visits_aggregation(paths, tmp_path):
    """build_visits (canonical transform, не изменялся этой задачей) не видит
    metrika_logs/lookback/ — визиты оттуда не попадают ни в одну метрику."""
    box = {}
    session = FakeSession(_routes(
        box, part_text=(
            "ym:s:visitID\tym:s:clientID\tym:s:dateTime\tym:s:lastsignTrafficSource\n"
            "v1\tc1\t2026-06-15 10:00:00\tad\n"
        ),
    ))
    box["session"] = session

    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    src_dir = paths.raw / "metrika_logs"
    # Убедимся, что lookback-файл реально существует и НЕ пуст (иначе тест
    # ничего не проверяет).
    lookback_files = list((src_dir / metrika_logs.LOOKBACK_SUBDIR).glob("*.csv.gz"))
    assert len(lookback_files) == 1

    df, _utm, _stats = bc.build_visits(src_dir, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    # Ровно 1 визит (из основного окна) — визит(ы) lookback/ не подмешались.
    assert len(df) == 1
    assert set(df["visit_id"]) == {"v1"}
