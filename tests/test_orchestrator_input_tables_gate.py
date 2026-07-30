"""FIX-input-tables-manifest-gate: manifest["input_tables"] реально заполняется.

До этой задачи manifest["input_tables"] нигде не писался — run_extract не
вызывал ничего, что заполняет это поле, поэтому
degradation.available_tables_from_manifest никогда не видел client_answers
как доступный источник, и D06/D07/T06 (``requires: [..., client_answers]``)
были structurally недостижимы (см. docs/implementation_status.md,
AUDIT-input-tables-blast-radius) вне зависимости от того, заполнена ли анкета
клиента на самом деле.

Расширенная версия задачи (см. docs/implementation_status.md) добавляет
manual_form_tests в ту же карту (``orchestrator.INPUT_TABLE_FILES``) и меняет
requires C03/C08/C11/C17/C23 в config/methodology.yaml с site_crawl на
manual_form_tests (см. AUDIT-c-checks-required-source-mismatch) — эти пять
проверок были structurally недостижимы по СВОЕЙ, отдельной причине
(несовпадение имени канонической таблицы site_crawl.py:CANONICAL_TABLES=
["pages"] vs site_pages), а не только из-за незаполнения input_tables.

Тест идёт через реальный orchestrator (run_extract -> run_compute ->
degradation_report.json), а не через прямой вызов degradation-функций с
сфабрикованным manifest — тот же урок, что и с VAT-багом: юнит-тест на
функцию маскирует такой баг (available_tables_from_manifest сама по себе
всегда была корректна), интеграционный — ловит разрыв в стыке.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.pipeline import orchestrator  # noqa: E402


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, msg: str = "") -> None:
        self.messages.append(msg)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


_FILLED_CLIENT_ANSWERS = {
    "meta": {"date": "01.07.2026", "respondent": "Алексей", "role": "Владелец"},
    "business": {"avg_check_rub": 10000, "margin_share": 0.25},
}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = orchestrator.ClientPaths("_template")
    monkeypatch.setattr(p, "raw", tmp_path / "raw")
    monkeypatch.setattr(p, "inputs", tmp_path / "inputs")
    monkeypatch.setattr(p, "metrics", tmp_path / "metrics")
    monkeypatch.setattr(p, "logs", tmp_path / "logs")
    # Нет заявленных источников -> run_extract ничего не диспетчеризует,
    # кроме заполнения manifest["input_tables"], которое и проверяется.
    monkeypatch.setattr(
        orchestrator, "load_client_config", lambda _paths: {"sources": {}}
    )
    return p


def _seed_costs_available(paths: orchestrator.ClientPaths) -> None:
    """Сымитировать уже выгруженный ранее источник costs (как реальный direct-экстрактор).

    Пишет через тот же manifest_mod.update_source, которым пользуются
    настоящие экстракторы src/extract/ — не сфабрикованный dict в обход
    manifest-модуля.
    """
    manifest_mod.update_source(
        paths.raw,
        "direct",
        date_from="2026-01-01",
        date_to="2026-06-30",
        rows=5,
        script_version="test",
        canonical_tables=["costs"],
    )


def _run_extract_and_compute(paths: orchestrator.ClientPaths) -> dict:
    import json

    log = _Log()
    orchestrator.run_extract(paths, log)
    orchestrator.run_compute(paths, log)
    report_path = paths.metrics / "degradation_report.json"
    with report_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ── Клиент без заполненной анкеты: гейт остаётся закрыт, но по честной причине ──
def test_gate_stays_closed_without_client_answers(paths):
    _seed_costs_available(paths)

    report = _run_extract_and_compute(paths)

    manifest = manifest_mod.load_manifest(paths.raw)
    assert manifest.get("input_tables") == []

    runnable = set(report["runnable_check_ids"])
    assert "T06" not in runnable
    assert "D06" not in runnable
    assert "D07" not in runnable

    skipped_by_id = {s["id"]: s for s in report["skipped"]}
    assert "client_answers" in skipped_by_id["T06"]["missing"]


# ── Клиент с заполненной анкетой: D06/D07/T06 становятся runnable ──────────────
def test_gate_opens_when_client_answers_filled(paths):
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "client_answers.yaml").write_text(
        yaml.safe_dump(_FILLED_CLIENT_ANSWERS, allow_unicode=True), encoding="utf-8"
    )
    _seed_costs_available(paths)

    report = _run_extract_and_compute(paths)

    manifest = manifest_mod.load_manifest(paths.raw)
    assert manifest.get("input_tables") == ["client_answers"]

    runnable = set(report["runnable_check_ids"])
    assert "T06" in runnable
    assert "D06" in runnable
    assert "D07" in runnable


_FILLED_MANUAL_FORM_TESTS = {
    "meta": {"tested_at": "2026-07-20", "tester": "Аналитик"},
    "patterns": [{"step": "форма", "issue": "маска не даёт ввести телефон"}],
}


# ── Клиент без заполненных ручных тестов форм: C03/C08/C11/C17/C23 закрыты ──
def test_gate_stays_closed_without_manual_form_tests(paths):
    report = _run_extract_and_compute(paths)

    manifest = manifest_mod.load_manifest(paths.raw)
    assert manifest.get("input_tables") == []

    runnable = set(report["runnable_check_ids"])
    for check_id in ("C03", "C08", "C11", "C17", "C23"):
        assert check_id not in runnable

    skipped_by_id = {s["id"]: s for s in report["skipped"]}
    assert "manual_form_tests" in skipped_by_id["C03"]["missing"]


# ── Клиент с заполненными ручными тестами форм, БЕЗ site_crawl: гейт открыт ──
def test_gate_opens_when_manual_form_tests_filled_without_site_crawl(paths):
    """Ключевой сценарий расширенной версии задачи: C03/C08/C11/C17/C23

    становятся runnable от inputs/manual_form_tests.yaml независимо от того,
    выполнялся ли обход сайта — site_crawl вообще не задействован в этом
    тесте (никакой source с canonical_tables=["site_pages"]/["pages"] не
    заведён), что и подтверждает независимость от site_crawl.
    """
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "manual_form_tests.yaml").write_text(
        yaml.safe_dump(_FILLED_MANUAL_FORM_TESTS, allow_unicode=True), encoding="utf-8"
    )

    report = _run_extract_and_compute(paths)

    manifest = manifest_mod.load_manifest(paths.raw)
    assert manifest.get("input_tables") == ["manual_form_tests"]

    runnable = set(report["runnable_check_ids"])
    for check_id in ("C03", "C08", "C11", "C17", "C23"):
        assert check_id in runnable
