"""Тесты 4X-lookback-wiring-check.

Две независимые вещи проверяются здесь, без предположений:

1. Видит ли resolve_traffic_source (через build_visits) строки из
   metrika_logs/lookback/, или только основной глоб verhнего уровня
   src_dir. Ответ фиксируется тестом, а не текстом — если поведение когда-
   нибудь изменится (после отдельной задачи над build_canonical.py), тест
   упадёт и потребует явного пересмотра этой записи.

2. force_lookback_backfill (src/extract/metrika_logs.py) — принудительная
   дозаливка LOOKBACK_SUBDIR для уже извлечённого окна, не дожидаясь
   естественного триггера _should_backfill/_already_extracted, и без
   изменения уже существующих canonical-данных.

Использует ту же схему моков HTTP, что и tests/test_metrika_logs_patch.py /
tests/test_metrika_logs_lookback.py — реальная сеть не трогается.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pandas as pd
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
MAIN_PART_TEXT = (
    "ym:s:visitID\tym:s:clientID\tym:s:dateTime\tym:s:lastsignTrafficSource\n"
    "v1\tc1\t2026-06-15 10:00:00\tad\n"
)


def _evaluate_route(get_session):
    return (
        lambda m, u: m == "GET" and u.endswith("/logrequests/evaluate"),
        lambda _n: FakeResponse(json_data={"log_request_evaluation": {"possible": True}}),
    )


def _full_routes(box, *, request_id=900, part_text=MAIN_PART_TEXT):
    def poll_responder(n):
        status = "created" if n == 0 else "processed"
        parts = [] if n == 0 else [{"part_number": 0}]
        return FakeResponse(json_data={"log_request": {
            "request_id": request_id, "status": status, "parts": parts}})

    return [
        _evaluate_route(lambda: box["session"]),
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": request_id, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith(f"/logrequest/{request_id}"), poll_responder),
        (_contains("/part/0/download"), FakeResponse(text=part_text)),
    ]


def _lookback_only_routes(box, *, request_id=950, part_text=None):
    """Мок без /evaluate — force_lookback_backfill не должен его вызывать
    (поля переиспользуются из существующего manifest, не пере-негоциируются)."""
    part_text = part_text or (
        "ym:s:visitID\tym:s:clientID\tym:s:dateTime\tym:s:lastsignTrafficSource\n"
        "vlb\tc1\t2026-05-15 09:00:00\tad\n"
    )

    def poll_responder(_n):
        return FakeResponse(json_data={"log_request": {
            "request_id": request_id, "status": "processed", "parts": [{"part_number": 0}]}})

    return [
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": request_id, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith(f"/logrequest/{request_id}"), poll_responder),
        (_contains("/part/0/download"), FakeResponse(text=part_text)),
    ]


# ── (1) resolve_traffic_source / build_visits видимость lookback/ ─────────
def test_build_visits_does_not_see_lookback_subdir_rows(paths):
    """Явный факт, не предположение: визиты metrika_logs/lookback/ НЕ попадают
    в df, который build_visits передаёт в resolve_traffic_source.

    Это архитектурный пробел build_canonical.py (не исправляется в этой
    задаче — см. её описание и docs/implementation_status.md, задача
    4X-metrika-lookback, пункт (а)). Если это когда-нибудь изменится, тест
    ниже упадёт и потребует пересмотра записи в manifest/докстрингах.
    """
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session
    metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        defaults={"transform": {"traffic_resolve_lookback_days": 30}},
    )

    src_dir = paths.raw / "metrika_logs"
    lookback_files = list((src_dir / metrika_logs.LOOKBACK_SUBDIR).glob("*.csv.gz"))
    assert len(lookback_files) == 1, "sanity: lookback-файл реально должен существовать"

    df, _utm, _stats = bc.build_visits(src_dir, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    # Только основной визит (v1). Лог-визит lookback (vlb/другой clientID/дата
    # мая) сюда не попал бы, даже если бы там был другой visit_id — глоб
    # _read_metrika_logs_rows не спускается в подкаталоги.
    assert set(df["visit_id"]) == {"v1"}
    assert len(df) == 1


def test_read_metrika_logs_rows_globs_top_level_only_by_construction(paths):
    """Прямая проверка механизма (не через build_visits): _read_metrika_logs_rows
    использует raw_dir.glob("visits_*.csv.gz") — нерекурсивный glob, поэтому
    файлы в raw_dir/lookback/ физически не входят в результат."""
    src_dir = paths.raw / "metrika_logs"
    src_dir.mkdir(parents=True)
    with gzip.open(src_dir / "visits_2026-06-01_2026-06-30_part000.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write("ym:s:visitID\nv1\n")

    lookback_dir = src_dir / metrika_logs.LOOKBACK_SUBDIR
    lookback_dir.mkdir(parents=True)
    with gzip.open(lookback_dir / "visits_lookback_2026-05-01_2026-05-31_part000.csv.gz",
                   "wt", encoding="utf-8") as fh:
        fh.write("ym:s:visitID\nvlb\n")

    rows = bc._read_metrika_logs_rows(src_dir)
    ids = {r["ym:s:visitID"] for r in rows}
    assert ids == {"v1"}   # "vlb" (lookback/) отсутствует


# ── (2) force_lookback_backfill ─────────────────────────────────────────────
def test_force_lookback_backfill_requires_no_evaluate_call(paths):
    """Поля переиспользуются из существующего manifest — /logrequests/evaluate
    не вызывается заново (мок его вообще не регистрирует, любой вызов упал бы
    с AssertionError «нет мока»)."""
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session
    metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        defaults={"transform": {"traffic_resolve_lookback_days": 0}},   # без lookback в первом прогоне
    )

    box2 = {}
    session2 = FakeSession(_lookback_only_routes(box2))
    box2["session"] = session2

    result = metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session2, sleeper=NO_SLEEP,
        force_lookback_backfill=True,
        defaults={"transform": {"traffic_resolve_lookback_days": 30}},
    )

    assert result["lookback_requested_days"] == 30
    assert result["lookback_rows"] == 1


def test_force_lookback_backfill_does_not_touch_main_window_files(paths):
    """Принудительный lookback не трогает visits_*.csv.gz и не создаёт backfill/."""
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session
    metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        defaults={"transform": {"traffic_resolve_lookback_days": 0}},
    )

    src_dir = paths.raw / "metrika_logs"
    main_files_before = {p: p.read_bytes() for p in src_dir.glob("visits_*.csv.gz")}
    assert main_files_before   # sanity: основной файл реально есть

    box2 = {}
    session2 = FakeSession(_lookback_only_routes(box2))
    box2["session"] = session2
    metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session2, sleeper=NO_SLEEP,
        force_lookback_backfill=True,
        defaults={"transform": {"traffic_resolve_lookback_days": 30}},
    )

    main_files_after = {p: p.read_bytes() for p in src_dir.glob("visits_*.csv.gz")}
    assert main_files_after == main_files_before
    assert not (src_dir / metrika_logs.BACKFILL_SUBDIR).exists()


def test_force_lookback_backfill_preserves_prior_manifest_fields(paths):
    """update_source перезаписывает запись целиком — force_lookback_backfill
    обязан явно перенести прежние поля (region_field, dropped_fields и т.п.),
    иначе они молча теряются."""
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session
    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
                          defaults={"transform": {"traffic_resolve_lookback_days": 0}})

    before = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert before["region_field"] == "ym:s:regionArea"   # sanity: поле реально было записано

    box2 = {}
    session2 = FakeSession(_lookback_only_routes(box2))
    box2["session"] = session2
    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session2, sleeper=NO_SLEEP,
                          force_lookback_backfill=True,
                          defaults={"transform": {"traffic_resolve_lookback_days": 30}})

    after = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert after["region_field"] == before["region_field"]
    assert after["schema_version"] == before["schema_version"]
    assert after["dropped_fields"] == before["dropped_fields"]
    assert after["lookback_rows"] == 1


def test_force_lookback_backfill_without_prior_extraction_falls_back_to_full_run(paths):
    """Окно ещё не извлекалось -> нечего форсировать, обычная полная выгрузка
    (которая уже включает lookback как часть _run_full)."""
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session

    result = metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
        force_lookback_backfill=True,
        defaults={"transform": {"traffic_resolve_lookback_days": 30}},
    )

    assert result["rows"] == 1
    assert result["lookback_rows"] == 1
    src_dir = paths.raw / "metrika_logs"
    assert list(src_dir.glob("visits_*.csv.gz"))   # основной файл реально создан


def test_force_lookback_backfill_does_not_change_existing_canonical_output(paths):
    """Принудительный lookback не меняет canonical-визиты (build_visits) —
    т.к. lookback/ ей не видна (см. первый блок тестов), результат до/после
    идентичен по составу и содержимому строк."""
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session
    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
                          defaults={"transform": {"traffic_resolve_lookback_days": 0}})

    src_dir = paths.raw / "metrika_logs"
    df_before, _, _ = bc.build_visits(src_dir, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    box2 = {}
    session2 = FakeSession(_lookback_only_routes(box2))
    box2["session"] = session2
    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session2, sleeper=NO_SLEEP,
                          force_lookback_backfill=True,
                          defaults={"transform": {"traffic_resolve_lookback_days": 30}})

    df_after, _, _ = bc.build_visits(src_dir, {"goals": {}}, {"utm_undefined_threshold": 0.25})

    pd.testing.assert_frame_equal(
        df_before.reset_index(drop=True), df_after.reset_index(drop=True),
    )
