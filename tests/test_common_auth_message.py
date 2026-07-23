"""Различение 401 (UNAUTHENTICATED) и 403 (PERMISSION_DENIED) в сообщениях об
ошибке авторизации.

Раньше оба статуса маппились в одну и ту же фразу "токен ... мёртв, обнови в
.env" — для 403 это вводило в заблуждение: ключ валиден, но не хватает прав
(роль/биллинг), замена ключа не поможет. Теперь auth_dead_message() и, через
неё, http_request()/ensure_ok() различают статусы и дают разные, корректно
направляющие сообщения.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.headers: dict[str, str] = {}


class _FixedStatusSession:
    """Сессия, у которой .request() всегда возвращает фиксированный статус."""

    def __init__(self, status_code: int):
        self._status_code = status_code
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return _FakeResponse(self._status_code)


# ── auth_dead_message() напрямую ────────────────────────────────────────────
def test_auth_dead_message_401_points_to_env_replacement():
    message = C.auth_dead_message("wordstat", 401)
    assert "мёртв" in message
    assert ".env" in message


def test_auth_dead_message_403_points_to_permissions_not_env():
    message = C.auth_dead_message("wordstat", 403)
    assert "403" in message
    assert "прав" in message
    assert "мёртв" not in message


def test_auth_dead_message_default_status_matches_401_wording():
    """Вызовы без status (напр. get_token, gsc_api._mint_access_token) не
    затронуты: поведение по умолчанию не меняется."""
    assert C.auth_dead_message("direct") == C.auth_dead_message("direct", 401)


# ── http_request(): статус доходит до сообщения ─────────────────────────────
def test_http_request_401_raises_dead_token_message():
    session = _FixedStatusSession(401)
    with pytest.raises(C.AuthError) as exc:
        C.http_request(session, "GET", "http://x", source="wordstat",
                        sleeper=lambda _s: None)
    assert "мёртв" in str(exc.value)
    assert session.calls == 1  # 401 не ретраится


def test_http_request_403_raises_permission_message():
    session = _FixedStatusSession(403)
    with pytest.raises(C.AuthError) as exc:
        C.http_request(session, "GET", "http://x", source="wordstat",
                        sleeper=lambda _s: None)
    message = str(exc.value)
    assert "403" in message
    assert "прав" in message
    assert "мёртв" not in message
    assert session.calls == 1  # 403 не ретраится


# ── ensure_ok(): та же дифференциация после ретраев ──────────────────────────
def test_ensure_ok_401_raises_dead_token_message():
    with pytest.raises(C.AuthError) as exc:
        C.ensure_ok(_FakeResponse(401), "gsc")
    assert "мёртв" in str(exc.value)


def test_ensure_ok_403_raises_permission_message():
    with pytest.raises(C.AuthError) as exc:
        C.ensure_ok(_FakeResponse(403), "gsc")
    message = str(exc.value)
    assert "403" in message
    assert "прав" in message
    assert "мёртв" not in message
