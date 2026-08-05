"""ROBUST-1: intake печатает таблицу зависимостей и помечает отсутствующие.

Отсутствие зависимости на intake — предупреждение, а не остановка стадии
(принцип 4): затронутые блоки уйдут в деградацию на своём этапе.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import degradation as degradation_mod  # noqa: E402
from src.pipeline import orchestrator  # noqa: E402


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, msg: str = "") -> None:
        self.messages.append(msg)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


_FAKE_DEPS = [
    {"module": "json", "package": "stdlib", "affects": ("compute",)},
    {"module": "no_such_pkg_robust1", "package": "no-such",
     "affects": ("compute: block1", "analyze")},
]


def test_intake_prints_dependency_table_and_marks_missing(monkeypatch):
    monkeypatch.setattr(
        degradation_mod, "DEPENDENCIES", tuple(_FAKE_DEPS), raising=False
    )
    log = _Log()

    rows = orchestrator._log_dependency_table(log)

    assert [row["present"] for row in rows] == [True, False]
    # Таблица: заголовок + строка на каждую зависимость.
    assert "зависимость" in log.text and "затронуто" in log.text
    assert "json" in log.text
    assert "no_such_pkg_robust1" in log.text
    # Отсутствующая помечена и названа вместе с затронутыми блоками.
    assert "ПРЕДУПРЕЖДЕНИЕ: нет зависимости no_such_pkg_robust1" in log.text
    assert "compute: block1" in log.text
    assert "не остановка" in log.text


def test_intake_does_not_fail_on_missing_dependency(monkeypatch, tmp_path):
    """Отсутствие зависимости не роняет intake: стадия возвращает True."""
    monkeypatch.setattr(
        degradation_mod, "DEPENDENCIES", tuple(_FAKE_DEPS), raising=False
    )
    client_root = tmp_path / "clients" / "acme"
    client_root.mkdir(parents=True)
    (client_root / "config.yaml").write_text(
        "client:\n  name: acme\nsources:\n  metrika:\n    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator, "CLIENTS_DIR", tmp_path / "clients")

    paths = orchestrator.ClientPaths("acme")
    log = _Log()

    assert orchestrator.run_intake(paths, log) is True
    assert "no_such_pkg_robust1" in log.text


def test_dependency_table_covers_declared_requirements():
    """Карта зависимостей покрывает пакеты requirements.txt (кроме pytest)."""
    declared = set()
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = line.split(">=")[0].split("==")[0].strip()
        declared.add(name.lower())
    declared.discard("pytest")  # тестовый инструмент, не стадия пайплайна

    mapped = {str(dep["package"]).lower() for dep in degradation_mod.DEPENDENCIES}
    assert declared <= mapped, f"не покрыты: {sorted(declared - mapped)}"
