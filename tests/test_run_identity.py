"""STATE-1: идентичность прогона связывает degradation_report и metrics/.

Проверяется ровно то, ради чего run_id заведён: артефакт, не принадлежащий
текущему прогону, не попадает в кандидаты молча — он получает состояние
(stale либо unregistered) и запись в degradation_report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import candidates, common  # noqa: E402
from src.pipeline import degradation as degradation_mod  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


class _Paths:
    def __init__(self, root: Path):
        self.metrics = root


@pytest.fixture(autouse=True)
def _closed_run_context():
    """Контекст прогона глобален для процесса — не протекать между тестами."""
    common.set_run_context(None, None)
    yield
    common.set_run_context(None, None)


def _candidate_row(check_id: str) -> dict:
    return {
        "check_id": check_id,
        "candidate": True,
        "row_role": "candidate",
        "candidate_reason": "high_cost",
        "context_refs": [],
        "value": 1200.0,
    }


def _run(metrics_dir: Path, run_id: str, check_ids: tuple[str, ...]) -> dict:
    """Прогон: открыть контекст, записать артефакты, собрать кандидатов."""
    metrics_dir.mkdir(parents=True, exist_ok=True)
    manifest_mod.start_metrics_run(metrics_dir, run_id)
    common.set_run_context(run_id, metrics_dir)
    try:
        for check_id in check_ids:
            common.write_metric_artifact(
                metrics_dir, check_id.lower(), [_candidate_row(check_id)]
            )
        candidates.run(_Paths(metrics_dir), {}, set())
    finally:
        common.set_run_context(None, None)
    return json.loads((metrics_dir / "analysis_candidates.json").read_text(encoding="utf-8"))


def _artifacts_in(result: dict) -> set[str]:
    idx = result["columns"].index("artifact")
    return {row[idx] for row in result["rows"]}


# ── run_id: генерация и детерминизм ─────────────────────────────────────────

def test_run_id_is_deterministic_for_identical_input_state():
    """run_id — отпечаток входа, а не времени: иначе побайтовая сверка прогонов
    (test_compute_determinism) краснела бы на каждом артефакте."""
    report = {"available_tables": ["visits"], "runnable_check_ids": ["A01"], "skipped": []}

    assert common.compute_run_id(report) == common.compute_run_id(dict(report))


def test_run_id_changes_when_input_state_changes():
    base = {"available_tables": ["visits"], "runnable_check_ids": ["A01"], "skipped": []}
    changed = {"available_tables": ["visits", "costs"], "runnable_check_ids": ["A01"], "skipped": []}

    assert common.compute_run_id(base) != common.compute_run_id(changed)


def test_run_id_ignores_its_own_field():
    """run_id считается от отчёта без run_id — иначе значение зависело бы от
    того, был ли ключ уже проставлен, и прогон не воспроизводился бы."""
    report = {"available_tables": ["visits"], "runnable_check_ids": ["A01"]}
    run_id = common.compute_run_id(report)

    assert common.compute_run_id({**report, "run_id": run_id}) == run_id


# ── Регистрация артефактов прогоном ─────────────────────────────────────────

def test_write_metric_artifact_registers_artifact_with_run_id(tmp_path):
    result = _run(tmp_path, "run-aaa", ("A01",))

    registry = manifest_mod.load_metrics_manifest(tmp_path)["artifacts"]
    assert registry["a01"]["run_id"] == "run-aaa"
    assert result["run_id"] == "run-aaa"
    assert _artifacts_in(result) == {"a01"}


def test_registration_is_inert_outside_a_run(tmp_path):
    """Вызов блока вне dispatch (юнит-тест, отладка) ничего не регистрирует."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    common.write_metric_artifact(tmp_path, "a01", [_candidate_row("A01")])

    assert manifest_mod.load_metrics_manifest(tmp_path)["artifacts"] == {}


# ── stale: артефакт чужого прогона ──────────────────────────────────────────

def test_artifact_with_foreign_run_id_is_stale_and_excluded(tmp_path):
    """Подменённый run_id -> stale, из кандидатов исключён, назван поимённо.

    Это же и есть тест на откат: если write_metric_artifact перестанет писать
    run_id в реестр, a02 не окажется зарегистрирован текущим прогоном и тест
    покраснеет на строке про включение a02 в кандидаты.
    """
    _run(tmp_path, "run-old", ("A01", "A02"))
    # a01 остаётся от прошлого прогона, a02 переписывается текущим.
    result = _run(tmp_path, "run-new", ("A02",))

    assert _artifacts_in(result) == {"a02"}
    assert result["coverage"]["stale_artifacts"] == [
        {"artifact": "a01", "run_id": "run-old"}
    ]
    assert result["coverage"]["unregistered_artifacts"] == []


def test_stale_artifact_reaches_degradation_report(tmp_path):
    _run(tmp_path, "run-old", ("A01", "A02"))
    _run(tmp_path, "run-new", ("A02",))

    stale, unregistered = common.artifact_states_from_candidates(tmp_path)
    report = degradation_mod.register_artifact_states({}, stale, unregistered)

    assert report["artifact_states"]["stale"] == [
        {"artifact": "a01", "run_id": "run-old"}
    ]


def test_stale_artifact_is_not_deleted(tmp_path):
    """Чужой артефакт исключается из пакета, но остаётся на диске уликой."""
    _run(tmp_path, "run-old", ("A01", "A02"))
    _run(tmp_path, "run-new", ("A02",))

    assert (tmp_path / "a01.json").exists()
    assert (tmp_path / "a01.csv").exists()


# ── unregistered: файл вне реестра входов ───────────────────────────────────

def test_foreign_file_is_unregistered_and_never_a_candidate(tmp_path):
    _run(tmp_path, "run-aaa", ("A01",))
    (tmp_path / "x.json").write_text(
        json.dumps([_candidate_row("X99")], ensure_ascii=False), encoding="utf-8"
    )
    result = _run(tmp_path, "run-aaa", ("A01",))

    assert "x" not in _artifacts_in(result)
    assert result["coverage"]["unregistered_artifacts"] == ["x"]

    stale, unregistered = common.artifact_states_from_candidates(tmp_path)
    report = degradation_mod.register_artifact_states({}, stale, unregistered)
    assert report["artifact_states"]["unregistered"] == ["x"]


def test_run_manifest_is_not_scanned_as_an_input(tmp_path):
    result = _run(tmp_path, "run-aaa", ("A01",))

    assert result["coverage"]["unregistered_artifacts"] == []
    assert "manifest" not in _artifacts_in(result)


def test_without_run_context_every_file_is_an_input(tmp_path):
    """Прогон не открыт — состояние неизвестно; выдумывать stale нельзя."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a01.json").write_text(
        json.dumps([_candidate_row("A01")], ensure_ascii=False), encoding="utf-8"
    )

    result = candidates.build_analysis_candidates(tmp_path)

    assert result["run_id"] is None
    assert _artifacts_in(result) == {"a01"}
    assert result["coverage"]["stale_artifacts"] == []
    assert result["coverage"]["unregistered_artifacts"] == []
