"""Тесты 2A-patch: состав полей Logs API визитов приведён к реальному API.

Контекст (см. src/extract/metrika_logs.py, SCHEMA_VERSION="visits-v4"):
    - убраны ym:s:isRobot, ym:s:screenResolution, ym:s:lastSignGCLID,
      ym:s:lastSignhasGCLID (не существуют в API либо 100% пустые);
    - добавлены ym:s:goalsDateTime, ym:s:goalsSerialNumber (параллельные
      массивы к ym:s:goalsID — D01/D09), ym:s:from (T01/T03), ym:s:bounce и
      ym:s:endURL (C06/C07/C12);
    - уточнение после боевого прогона: ym:s:isRobotPro тоже отклонён API на
      доступном тарифе — детекция бота через Logs API невозможна ПОСТОЯННО,
      поле убрано из кандидатов насовсем (никакой негоциации/ретраев вокруг
      него), manifest.bot_detection_available всегда False;
    - ym:s:regionCity заменяется попыткой ym:s:regionArea, проверяемой
      отдельным logrequests/evaluate на каждый прогон (_resolve_region_field);
      отказ API -> откат на regionCity, текст ошибки — в manifest.

Использует ту же схему моков HTTP (FakeSession/FakeResponse), что и
tests/test_extract_smoke.py — реальная сеть не трогается.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import metrika_logs  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Тестовые дублёры HTTP (те же, что в test_extract_smoke.py) ─────────────
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


def _evaluate_route(get_session, bad=frozenset()):
    """Мок logrequests/evaluate: 400 «Unknown field» если в составе есть bad-поле."""
    def responder(_n):
        fields = get_session().calls[-1][2]["params"]["fields"].split(",")
        offending = [f for f in fields if f in bad]
        if offending:
            msg = f"Unknown field in the request: {offending[0]} for the source visits"
            return FakeResponse(status_code=400, json_data={
                "errors": [{"error_type": "invalid_parameter", "message": msg}],
                "code": 400, "message": msg})
        return FakeResponse(json_data={"log_request_evaluation": {"possible": True}})
    return (lambda m, u: m == "GET" and u.endswith("/logrequests/evaluate"), responder)


def _full_routes(box, *, bad=frozenset(), request_id=900, part_text=None):
    part_text = part_text or "ym:s:visitID\nv1\n"

    def poll_responder(n):
        status = "created" if n == 0 else "processed"
        parts = [] if n == 0 else [{"part_number": 0}]
        return FakeResponse(json_data={"log_request": {
            "request_id": request_id, "status": status, "parts": parts}})

    return [
        _evaluate_route(lambda: box["session"], bad=bad),
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": request_id, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith(f"/logrequest/{request_id}"), poll_responder),
        (_contains("/part/0/download"), FakeResponse(text=part_text)),
    ]


# ── Поля: убранные / новые ──────────────────────────────────────────────────
def test_removed_fields_not_in_candidates():
    """isRobot/isRobotPro, screenResolution, GCLID, hasGCLID нигде не запрашиваются.

    isRobotPro убран насовсем (не только из VISIT_FIELDS/PATCH_CANDIDATE_FIELDS,
    но и нигде в коде — см. test_bot_detection_never_negotiated ниже).
    """
    removed = {
        "ym:s:isRobot", "ym:s:isRobotPro", "ym:s:screenResolution",
        "ym:s:lastSignGCLID", "ym:s:lastSignhasGCLID",
    }
    assert not (removed & set(metrika_logs.VISIT_FIELDS))
    assert not (removed & set(metrika_logs.PATCH_CANDIDATE_FIELDS))
    assert not hasattr(metrika_logs, "ROBOT_PRO_FIELD")


def test_new_fields_present_in_candidates():
    """Новые поля патча запрашиваются (VISIT_FIELDS) как кандидаты негоциации."""
    new_fields = {
        "ym:s:goalsDateTime", "ym:s:goalsSerialNumber",
        "ym:s:from", "ym:s:bounce", "ym:s:endURL",
    }
    assert new_fields <= set(metrika_logs.VISIT_FIELDS)
    assert new_fields <= set(metrika_logs.PATCH_CANDIDATE_FIELDS)
    # Не в базе — идут через негоциацию, не безусловны.
    assert not (new_fields & set(metrika_logs.VISIT_FIELDS_BASE))


def test_screen_width_height_kept_not_resolution():
    """screenWidth/Height остаются (замена screenResolution, не сам screenResolution)."""
    assert "ym:s:screenWidth" in metrika_logs.VISIT_FIELDS
    assert "ym:s:screenHeight" in metrika_logs.VISIT_FIELDS
    assert "ym:s:screenResolution" not in metrika_logs.VISIT_FIELDS


# ── Детекция бота: постоянное ограничение, без негоциации/ретраев ─────────
def test_bot_detection_always_permanently_false():
    assert metrika_logs.BOT_DETECTION_AVAILABLE is False


def test_bot_detection_never_negotiated(paths):
    """isRobotPro никогда не отправляется в API — ни разу, ни как ретрай.

    Постоянное ограничение (не тарифная деградация): manifest.bot_detection_available
    жёстко False, без единой попытки согласовать поле через evaluate.
    """
    box = {}
    session = FakeSession(_full_routes(box))
    box["session"] = session

    result = metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    for _method, url, kwargs in session.calls:
        fields_param = kwargs.get("params", {}).get("fields", "")
        assert "isRobot" not in fields_param

    assert result["bot_detection_available"] is False
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["bot_detection_available"] is False


# ── Регион визита: regionArea с откатом на regionCity ──────────────────────
def test_region_area_accepted_marks_verified_true(paths):
    """API принимает regionArea -> используется regionArea, verified=true, без ошибки."""
    box = {}
    session = FakeSession(_full_routes(box))   # bad пуст -> всё принято
    box["session"] = session

    result = metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["region_field"] == "ym:s:regionArea"
    assert result["region_field_verified"] is True
    assert result["region_field_error"] is None
    assert "ym:s:regionArea" in result["available_fields"]
    assert "ym:s:regionCity" not in result["available_fields"]

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["region_field"] == "ym:s:regionArea"
    assert entry["region_field_verified"] is True


def test_region_area_rejected_falls_back_to_region_city(paths):
    """API отклоняет regionArea («Unknown field») -> откат на regionCity,
    verified=false, фактический текст ошибки API сохранён в manifest, а
    остальные поля патча остаются доступны (не всё согласование падает)."""
    box = {}
    session = FakeSession(_full_routes(box, bad={"ym:s:regionArea"}))
    box["session"] = session

    result = metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["region_field"] == "ym:s:regionCity"
    assert result["region_field_verified"] is False
    assert result["region_field_error"] is not None
    assert "Unknown field" in result["region_field_error"]
    assert "ym:s:regionCity" in result["available_fields"]
    assert "ym:s:regionArea" not in result["available_fields"]
    # Остальные поля патча не пострадали от отказа региона.
    assert "ym:s:goalsDateTime" in result["available_fields"]
    assert "ym:s:bounce" in result["available_fields"]

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["region_field"] == "ym:s:regionCity"
    assert entry["region_field_verified"] is False
    assert "Unknown field" in entry["region_field_error"]


# ── D11: постоянное ограничение зафиксировано в методологии ───────────────
def test_d11_marked_permanent_low_in_methodology():
    methodology_path = REPO_ROOT / "config" / "methodology.yaml"
    data = yaml.safe_load(methodology_path.read_text(encoding="utf-8"))
    d11 = next(c for c in data["checks"] if c["id"] == "D11")

    assert d11["type_downgraded"] == "permanent_LOW"
    assert d11["downgrade_reason"]
    # permanent_LOW — постоянное ограничение, хардкод, не условие по manifest-флагу
    # (см. CLAUDE.md, раздел «Схема ID проверок»): type_downgrade_if остаётся null.
    assert d11["type_downgrade_if"] is None


# ── goalsDateTime / goalsSerialNumber параллельны goalsID ──────────────────
def test_goal_array_fields_round_trip_unmodified(paths):
    """Массивы goalsID/goalsDateTime/goalsSerialNumber одинаковой длины (запятая —
    разделитель API) проходят в raw csv.gz БАЙТ-В-БАЙТ, без парсинга/усечения
    на уровне extract (парсинг — забота transform)."""
    fields = metrika_logs.VISIT_FIELDS
    values = {
        "ym:s:visitID": "v1",
        "ym:s:goalsID": "10,10,20",
        "ym:s:goalsDateTime": "2026-06-01 10:00:00,2026-06-01 10:00:01,2026-06-02 09:00:00",
        "ym:s:goalsSerialNumber": "1,2,1",
    }
    header = "\t".join(fields)
    row = "\t".join(values.get(f, "x") for f in fields)
    part_text = header + "\n" + row + "\n"

    box = {}
    session = FakeSession(_full_routes(box, part_text=part_text))
    box["session"] = session

    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    src_dir = paths.raw / "metrika_logs"
    gz_files = sorted(src_dir.glob("*.csv.gz"))
    with gzip.open(gz_files[0], "rt", encoding="utf-8") as fh:
        written = fh.read()
    assert written == part_text   # extract не трогает содержимое строк

    written_row = written.splitlines()[1].split("\t")
    cells = dict(zip(fields, written_row))
    goal_ids = cells["ym:s:goalsID"].split(",")
    goal_dts = cells["ym:s:goalsDateTime"].split(",")
    goal_sns = cells["ym:s:goalsSerialNumber"].split(",")
    assert len(goal_ids) == len(goal_dts) == len(goal_sns) == 3


# ── Backfill новых полей 2A-patch поверх уже пропатченного visits-v2 ───────
def test_backfill_triggered_for_previously_v2_patched_window(paths):
    """Окно уже выгружено под предыдущим патчем (schema_version=visits-v2,
    patch_date непустой) -> НЕ считается «уже выгружено» под visits-v3:
    _should_backfill запускает ещё одну довыгрузку новых полей 2A-patch.
    """
    src = paths.raw / "metrika_logs"
    src.mkdir(parents=True)
    old_file = src / "visits_2026-06-01_2026-06-30_part000.csv.gz"
    with gzip.open(old_file, "wt", encoding="utf-8") as fh:
        fh.write("ym:s:visitID\tym:s:browser\nv1\tchrome\n")
    old_bytes = old_file.read_bytes()
    manifest_mod.update_source(
        paths.raw, "metrika_logs",
        date_from="2026-06-01", date_to="2026-06-30", rows=1,
        script_version="0.3.1", canonical_tables=["visits"],
        extra={"schema_version": "visits-v2", "patch_date": "2026-07-13",
               "patch_fields": ["ym:s:browser"]},
    )

    box = {}
    part_text = "ym:s:visitID\tym:s:bounce\tym:s:endURL\nv1\t0\thttps://site.ru/done\n"
    session = FakeSession(_full_routes(box, request_id=901, part_text=part_text))
    box["session"] = session

    result = metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["patch_backfill"] is True
    # Старый файл слоя raw не тронут — неизменность слоя.
    assert old_file.read_bytes() == old_bytes
    backfill_files = sorted((src / "backfill").glob("visits_backfill_*.csv.gz"))
    assert len(backfill_files) == 1

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["schema_version"] == metrika_logs.SCHEMA_VERSION   # visits-v3 теперь
    assert entry["patch_date"] == metrika_logs.PATCH_DATE
    assert "ym:s:bounce" in entry["patch_fields"]
    assert "ym:s:endURL" in entry["patch_fields"]


def test_already_extracted_with_current_schema_skips_reextraction(paths):
    """Окно уже выгружено С ТЕКУЩЕЙ схемой (visits-v3) -> не выгружаем повторно."""
    src = paths.raw / "metrika_logs"
    src.mkdir(parents=True)
    current_file = src / "visits_2026-06-01_2026-06-30_part000.csv.gz"
    with gzip.open(current_file, "wt", encoding="utf-8") as fh:
        fh.write("ym:s:visitID\nv1\n")
    manifest_mod.update_source(
        paths.raw, "metrika_logs",
        date_from="2026-06-01", date_to="2026-06-30", rows=1,
        script_version=metrika_logs.SCRIPT_VERSION, canonical_tables=["visits"],
        extra={"schema_version": metrika_logs.SCHEMA_VERSION,
               "patch_date": metrika_logs.PATCH_DATE, "patch_fields": []},
    )

    session = FakeSession([])   # никаких HTTP-вызовов не ожидается

    metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert session.calls == []   # пропущено, ни одного запроса к API
