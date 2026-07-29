"""Задача 6D: подключение src.analyze.draft_findings в run_analyze без
ослабления гейта перед report.

src.analyze.draft_findings.draft() мокается целиком (monkeypatch на модуль) —
реального вызова LLM здесь не нужно, проверяется только точка подключения в
оркестраторе: что она вызывает draft() с нужными аргументами, чистит свой
слой перед перезаписью (идемпотентность) и не трогает findings/approved/ и
сам гейт report.

Сценарии:
1. run_analyze создаёт черновики (делегирует draft_findings.draft).
2. report запрещён, пока findings/approved/ пуст, даже после analyze.
3. run_analyze выводит инструкцию о ручной проверке аналитиком.
4. rejected/ не считается approved (гейт report смотрит только на approved/).
5. Повторный запуск run_analyze идемпотентен — не оставляет файлы
   предыдущего (более многочисленного) прогона.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analyze import draft_findings  # noqa: E402
from src.pipeline import orchestrator  # noqa: E402


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
    monkeypatch.setattr(p, "findings_draft", tmp_path / "findings" / "draft")
    monkeypatch.setattr(p, "findings_approved", tmp_path / "findings" / "approved")
    monkeypatch.setattr(p, "report", tmp_path / "report")
    monkeypatch.setattr(p, "metrics", tmp_path / "data" / "metrics")
    monkeypatch.setattr(p, "canonical", tmp_path / "data" / "canonical")
    monkeypatch.setattr(p, "inputs", tmp_path / "inputs")
    monkeypatch.setattr(orchestrator, "load_client_config", lambda _paths: {"client": {"name": "т"}})
    monkeypatch.setattr(orchestrator, "load_methodology", lambda: {"checks": []})
    return p


def _fake_draft(names: list[str]):
    """draft_findings.draft заглушка: пишет пустые файлы с заданными именами."""

    def _draft(paths_arg, config, methodology, *, client=None):
        paths_arg.findings_draft.mkdir(parents=True, exist_ok=True)
        for name in names:
            (paths_arg.findings_draft / name).write_text("stub", encoding="utf-8")
        return names

    return _draft


# ── 1. run_analyze создаёт черновики через draft_findings.draft ────────────
def test_run_analyze_creates_draft_via_draft_findings(monkeypatch, paths):
    calls: list[tuple] = []

    def _draft(paths_arg, config, methodology, *, client=None):
        calls.append((paths_arg, config, methodology))
        paths_arg.findings_draft.mkdir(parents=True, exist_ok=True)
        (paths_arg.findings_draft / draft_findings.INPUT_PACK_ARTIFACT_NAME).write_text(
            "{}", encoding="utf-8"
        )
        (paths_arg.findings_draft / "F-A-01.yaml").write_text("stub", encoding="utf-8")
        return [draft_findings.INPUT_PACK_ARTIFACT_NAME, "F-A-01.yaml"]

    monkeypatch.setattr(draft_findings, "draft", _draft)

    log = _Log()
    orchestrator.run_analyze(paths, log)

    assert len(calls) == 1
    assert (paths.findings_draft / "F-A-01.yaml").exists()
    assert "F-A-01.yaml" in log.text


# ── 2. report остаётся запрещён, пока approved пуст, даже после analyze ────
def test_report_still_gated_after_analyze_runs(monkeypatch, paths):
    monkeypatch.setattr(draft_findings, "draft", _fake_draft(["F-A-01.yaml"]))

    log = _Log()
    orchestrator.run_analyze(paths, log)

    assert not paths.findings_approved.exists() or not any(
        paths.findings_approved.glob("*.yaml")
    )

    report_log = _Log()
    ok = orchestrator.run_report(paths, report_log)

    assert ok is False
    assert "ГЕЙТ" in report_log.text


# ── 3. Инструкция о ручной проверке ──────────────────────────────────────────
def test_run_analyze_prints_manual_review_instruction(monkeypatch, paths):
    monkeypatch.setattr(draft_findings, "draft", _fake_draft(["F-A-01.yaml"]))

    log = _Log()
    orchestrator.run_analyze(paths, log)

    assert "ручную проверку" in log.text or "ручн" in log.text.lower()
    assert str(paths.findings_draft) in log.text
    assert str(paths.findings_approved) in log.text
    assert "--stage report" in log.text


# ── 4. rejected не считается approved ───────────────────────────────────────
def test_rejected_findings_do_not_count_as_approved(monkeypatch, paths):
    def _draft_with_rejected(paths_arg, config, methodology, *, client=None):
        paths_arg.findings_draft.mkdir(parents=True, exist_ok=True)
        rejected_dir = paths_arg.findings_draft / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "R-01.yaml").write_text("reasons: [x]", encoding="utf-8")
        return []

    monkeypatch.setattr(draft_findings, "draft", _draft_with_rejected)

    log = _Log()
    orchestrator.run_analyze(paths, log)

    assert (paths.findings_draft / "rejected" / "R-01.yaml").exists()
    assert orchestrator.approved_findings_present(paths) is False

    report_log = _Log()
    ok = orchestrator.run_report(paths, report_log)
    assert ok is False


# ── 5. Повторный запуск идемпотентен ────────────────────────────────────────
def test_run_analyze_is_idempotent_across_reruns(monkeypatch, paths):
    monkeypatch.setattr(
        draft_findings, "draft", _fake_draft(["F-A-01.yaml", "F-A-02.yaml", "F-A-03.yaml"])
    )
    orchestrator.run_analyze(paths, _Log())
    assert sorted(p.name for p in paths.findings_draft.glob("F-*.yaml")) == [
        "F-A-01.yaml", "F-A-02.yaml", "F-A-03.yaml",
    ]

    # Второй прогон возвращает МЕНЬШЕ находок — старые файлы не должны остаться.
    monkeypatch.setattr(draft_findings, "draft", _fake_draft(["F-A-01.yaml"]))
    orchestrator.run_analyze(paths, _Log())

    remaining = sorted(p.name for p in paths.findings_draft.glob("F-*.yaml"))
    assert remaining == ["F-A-01.yaml"]
