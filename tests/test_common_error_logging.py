"""Проверка: SourceUnavailable на сетевом сбое несёт исходный текст исключения.

Раньше сообщение содержало только type(exc).__name__ — сам текст ошибки
(например, детали SSL/DNS-сбоя) терялся и аналитик не мог понять причину
без --debug. Теперь str(exc) идёт в то же сообщение, что и обычный лог.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402


class _RaisingSession:
    """Сессия, у которой .request() всегда роняет сетевое исключение."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        raise self._exc


def test_source_unavailable_preserves_original_exception_text():
    """Текст исходного исключения (не только имя класса) попадает в SourceUnavailable."""
    original = ConnectionError("Имитация сбоя: имя хоста не резолвится")
    session = _RaisingSession(original)

    with pytest.raises(C.SourceUnavailable) as exc_info:
        C.http_request(session, "GET", "http://x", source="t",
                        max_attempts=2, sleeper=lambda _s: None)

    message = str(exc_info.value)
    assert "ConnectionError" in message
    assert "Имитация сбоя: имя хоста не резолвится" in message
    assert session.calls == 2


def test_source_unavailable_message_has_no_debug_only_gate():
    """http_request не принимает debug-флаг: полный текст ошибки всегда в исключении,
    а не только при каком-то отдельном verbose-режиме — вызывающий log() получает
    его безусловно (см. orchestrator.py: extract[...] лог пишется всегда, не по флагу)."""
    original = TimeoutError("превышено время ожидания ответа")
    session = _RaisingSession(original)

    with pytest.raises(C.SourceUnavailable) as exc_info:
        C.http_request(session, "GET", "http://x", source="t",
                        max_attempts=1, sleeper=lambda _s: None)

    assert "превышено время ожидания ответа" in str(exc_info.value)
