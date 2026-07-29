"""FIX-report-wiring: run_report реально вызывает build_report.build().

До этой задачи run_report после гейта печатал строку-заглушку и не вызывал
src.report.build_report.build() — diagnostic_report.md никогда не создавался
(см. docs/implementation_status.md, AUDIT-report-wiring). Проверяется точка
подключения: build() вызывается с реальными путями клиента после прохождения
гейта, а ошибка build() не превращается в "успех без файла" (пробрасывается).

Гейт (approved пуст -> report запрещён) уже покрыт
tests/test_orchestrator_analyze_gate.py — здесь не дублируется, кроме
одного регрессионного теста на стыке с реальным build_report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import orchestrator  # noqa: E402
from src.report import build_report  # noqa: E402


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, msg: str = "") -> None:
        self.messages.append(msg)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = orchestrator.ClientPaths("_template")
    monkeypatch.setattr(p, "findings_approved", tmp_path / "findings" / "approved")
    monkeypatch.setattr(p, "report", tmp_path / "report")
    monkeypatch.setattr(p, "metrics", tmp_path / "data" / "metrics")
    monkeypatch.setattr(
        orchestrator, "load_client_config", lambda _paths: {"client": {"name": "т"}}
    )
    return p


def _write_approved_finding(paths: orchestrator.ClientPaths) -> None:
    paths.findings_approved.mkdir(parents=True, exist_ok=True)
    finding = {
        "check_id": "A01",
        "name": "тестовая находка",
        "status": "подтверждена",
        "confidence": "HIGH",
    }
    (paths.findings_approved / "F-A-01.yaml").write_text(
        yaml.safe_dump(finding, allow_unicode=True), encoding="utf-8"
    )


# ── report реально собирается через build_report.build ─────────────────────
def test_run_report_builds_report_when_approved_present(paths):
    _write_approved_finding(paths)

    log = _Log()
    ok = orchestrator.run_report(paths, log)

    assert ok is True
    report_file = paths.report / build_report.REPORT_FILENAME
    assert report_file.exists()
    assert "A01" in report_file.read_text(encoding="utf-8")

    agenda_file = paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME
    assert agenda_file.exists()

    appendix_dir = paths.report / build_report.APPENDIX_TABLES_DIRNAME
    assert (appendix_dir / build_report.SKIPPED_CHECKS_CSV).exists()

    assert str(report_file) in log.text


# ── регрессия: гейт по-прежнему блокирует пустой approved/ ─────────────────
def test_run_report_still_gated_when_approved_empty(paths):
    log = _Log()
    ok = orchestrator.run_report(paths, log)

    assert ok is False
    assert "ГЕЙТ" in log.text
    assert not (paths.report / build_report.REPORT_FILENAME).exists()


# ── ошибка build() не превращается в "успех без файла" ─────────────────────
def test_run_report_propagates_build_error(monkeypatch, paths):
    _write_approved_finding(paths)

    def _boom(_paths, _config, _defaults):
        raise RuntimeError("сборка сломалась")

    monkeypatch.setattr(build_report, "build", _boom)

    log = _Log()
    with pytest.raises(RuntimeError, match="сборка сломалась"):
        orchestrator.run_report(paths, log)

    assert "ОШИБКА" in log.text
