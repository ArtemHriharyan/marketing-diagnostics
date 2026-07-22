"""run_extract: лог SourceUnavailable не смешивает внутренний код оркестратора
с текстом исходной ошибки в одной фразе.

Раньше сообщение выглядело как:
    "... — сеть недоступна ...: ConnectionError: ... (код 3)"
где "код 3" стоит вплотную к тексту сетевой/SSL-ошибки и может быть прочитано
как её код, хотя это EXIT_SOURCE_UNAVAILABLE — внутренний код оркестратора,
не связанный с текстом исключения (см. src/extract/_common.py).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as extract_common  # noqa: E402
from src.pipeline import orchestrator  # noqa: E402


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, msg: str = "") -> None:
        self.messages.append(msg)


def _install_fake_extractor(monkeypatch, mod_name: str, exc: Exception):
    """Подменить src.extract.<mod_name> на фейковый модуль, чей extract() роняет exc."""
    fake = types.ModuleType(f"src.extract.{mod_name}")

    def extract(config, env, paths, **kwargs):
        raise exc

    fake.extract = extract
    monkeypatch.setitem(sys.modules, f"src.extract.{mod_name}", fake)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = orchestrator.ClientPaths("_template")
    monkeypatch.setattr(p, "raw", tmp_path / "raw")
    monkeypatch.setattr(p, "logs", tmp_path / "logs")
    return p


def _run_extract_with_single_source(monkeypatch, paths, source: str, mod_name: str, exc: Exception):
    _install_fake_extractor(monkeypatch, mod_name, exc)
    config = {"sources": {source: {"enabled": True}}}
    monkeypatch.setattr(orchestrator, "load_client_config", lambda _paths: config)
    monkeypatch.setattr(orchestrator, "EXTRACTORS", {**orchestrator.EXTRACTORS, source: [mod_name]})

    log = _Log()
    orchestrator.run_extract(paths, log)
    return log.messages


def test_network_error_log_separates_internal_code_from_exception_text(monkeypatch, paths):
    """Сетевой сбой: "код оркестратора" явно подписан отдельно от текста ошибки."""
    exc = extract_common.SourceUnavailable(
        "crux",
        "сеть недоступна после 3 попыток: ConnectionError: имя хоста не резолвится",
    )
    messages = _run_extract_with_single_source(monkeypatch, paths, "crux", "crux", exc)

    unavailable_lines = [m for m in messages if "ИСТОЧНИК НЕДОСТУПЕН" in m]
    assert len(unavailable_lines) == 1
    line = unavailable_lines[0]

    # Текст исходной ошибки присутствует как есть.
    assert "сеть недоступна после 3 попыток: ConnectionError: имя хоста не резолвится" in line
    # Внутренний код оркестратора явно подписан, а не голое число рядом с текстом ошибки.
    assert "внутренний код оркестратора" in line
    assert "3" in line
    # Формулировка не оставляет "(код 3)" вплотную к тексту ошибки без пояснения.
    assert "(код 3)" not in line


def test_auth_error_log_also_separates_internal_code(monkeypatch, paths):
    """AuthError (частный случай SourceUnavailable) — тот же формат лога."""
    exc = extract_common.AuthError("crux", extract_common.auth_dead_message("crux"))
    messages = _run_extract_with_single_source(monkeypatch, paths, "crux", "crux", exc)

    unavailable_lines = [m for m in messages if "ИСТОЧНИК НЕДОСТУПЕН" in m]
    assert len(unavailable_lines) == 1
    line = unavailable_lines[0]
    assert "внутренний код оркестратора 3" in line
    assert "(код 3)" not in line
