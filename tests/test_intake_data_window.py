"""Тесты валидации data_window в intake.

Покрывают: mode explicit (валидные/невалидные date_from/date_to), date_to="today"
(partial-флаг), вычисление compare_window, обратную совместимость с полем months,
mode: months_back.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.orchestrator import _resolve_data_window
from src.pipeline import manifest as manifest_mod


# Фиксируем «сегодня» во всех тестах для детерминизма.
_TODAY = date(2026, 7, 15)


class _Log:
    """Минимальный логгер для перехвата сообщений intake."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, msg: str = "") -> None:
        self.messages.append(msg)


# ── mode: explicit — валидные случаи ──────────────────────────────────────

def test_explicit_valid_window():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"}
    primary, compare, partial, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert primary == {"date_from": "2025-07-01", "date_to": "2026-06-30"}
    assert compare is None
    assert partial is False


def test_explicit_date_to_today():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2026-01-01", "date_to": "today"}
    primary, _, partial, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert primary == {"date_from": "2026-01-01", "date_to": "2026-07-15"}
    assert partial is True


def test_explicit_date_to_today_case_insensitive():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2026-01-01", "date_to": "TODAY"}
    _, _, partial, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert partial is True


# ── mode: explicit — невалидные случаи ────────────────────────────────────

def test_explicit_date_from_not_first_of_month():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-07", "date_to": "2026-06-30"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors, "должна быть ошибка"
    assert "первым числом месяца" in errors[0]
    assert "2025-07-07" in errors[0]


def test_explicit_date_to_not_last_day():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-27"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors, "должна быть ошибка"
    assert "последним днём" in errors[0]
    assert "2026-06-27" in errors[0]


def test_explicit_invalid_date_from_format():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "not-a-date", "date_to": "2026-06-30"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors
    assert "date_from" in errors[0]


def test_explicit_invalid_date_to_format():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01", "date_to": "bad"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors
    assert "date_to" in errors[0]


def test_explicit_missing_date_from():
    log = _Log()
    dw = {"mode": "explicit", "date_to": "2026-06-30"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors
    assert "date_from" in errors[0]


def test_explicit_missing_date_to():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01"}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors
    assert "date_to" in errors[0]


# ── compare_previous_period ────────────────────────────────────────────────

def test_compare_window_year_over_year():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"}
    compare_cfg = {"enabled": True, "offset_months": 12}
    primary, compare, _, errors = _resolve_data_window(dw, compare_cfg, log, _TODAY)
    assert errors == []
    assert primary == {"date_from": "2025-07-01", "date_to": "2026-06-30"}
    assert compare == {"date_from": "2024-07-01", "date_to": "2025-06-30"}


def test_compare_window_disabled_returns_none():
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"}
    compare_cfg = {"enabled": False, "offset_months": 12}
    _, compare, _, _ = _resolve_data_window(dw, compare_cfg, log, _TODAY)
    assert compare is None


def test_compare_window_with_today():
    """date_to="today" + compare: compare.date_to = today - offset."""
    log = _Log()
    dw = {"mode": "explicit", "date_from": "2026-01-01", "date_to": "today"}
    compare_cfg = {"enabled": True, "offset_months": 12}
    _, compare, partial, errors = _resolve_data_window(dw, compare_cfg, log, _TODAY)
    assert errors == []
    assert partial is True
    # today=2026-07-15 → compare shifts 12 months back
    assert compare == {"date_from": "2025-01-01", "date_to": "2025-07-15"}


# ── Обратная совместимость: старый формат data_window.months ──────────────

def test_legacy_months_no_crash():
    """Старое поле months не роняет intake, предупреждение логируется."""
    log = _Log()
    dw = {"months": 12}
    _, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert any("устаревший" in m for m in log.messages)


def test_legacy_months_references_claude_md():
    log = _Log()
    _resolve_data_window({"months": 12}, None, log, _TODAY)
    assert any("CLAUDE.md" in m for m in log.messages)


def test_legacy_months_computes_correct_window():
    """months=12 при today=2026-07-15 → окно июль 2025 – июнь 2026."""
    log = _Log()
    primary, _, _, _ = _resolve_data_window({"months": 12}, None, log, _TODAY)
    assert primary["date_from"] == "2025-07-01"
    assert primary["date_to"] == "2026-06-30"


def test_legacy_months_6_computes_correct_window():
    """months=6 при today=2026-07-15 → окно январь 2026 – июнь 2026."""
    log = _Log()
    primary, _, _, _ = _resolve_data_window({"months": 6}, None, log, _TODAY)
    assert primary["date_from"] == "2026-01-01"
    assert primary["date_to"] == "2026-06-30"


# ── mode: months_back ─────────────────────────────────────────────────────

def test_months_back_mode_12():
    log = _Log()
    dw = {"mode": "months_back", "months_back": 12}
    primary, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert primary["date_from"] == "2025-07-01"
    assert primary["date_to"] == "2026-06-30"


def test_months_back_mode_6():
    log = _Log()
    dw = {"mode": "months_back", "months_back": 6}
    primary, _, _, errors = _resolve_data_window(dw, None, log, _TODAY)
    assert errors == []
    assert primary["date_from"] == "2026-01-01"
    assert primary["date_to"] == "2026-06-30"


# ── Отсутствие data_window ─────────────────────────────────────────────────

def test_no_data_window_is_ok():
    log = _Log()
    primary, compare, partial, errors = _resolve_data_window(None, None, log, _TODAY)
    assert errors == []
    assert primary is None
    assert partial is False


# ── Запись в manifest.json ─────────────────────────────────────────────────

def test_manifest_written_with_primary_window(tmp_path):
    """update_global записывает primary_window на верхний уровень манифеста."""
    manifest_mod.update_global(
        tmp_path,
        primary_window={"date_from": "2025-07-01", "date_to": "2026-06-30"},
    )
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["primary_window"] == {"date_from": "2025-07-01", "date_to": "2026-06-30"}
    assert "sources" in data


def test_manifest_written_with_partial_flag(tmp_path):
    manifest_mod.update_global(tmp_path, current_month_is_partial=True)
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["current_month_is_partial"] is True


def test_manifest_update_global_does_not_overwrite_sources(tmp_path):
    """Существующие sources не затираются при update_global."""
    manifest_mod.update_source(
        tmp_path,
        "metrika_logs",
        date_from="2025-07-01",
        date_to="2026-06-30",
        rows=100,
        script_version="1.0",
        canonical_tables=["visits"],
    )
    manifest_mod.update_global(tmp_path, primary_window={"date_from": "2025-07-01", "date_to": "2026-06-30"})
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "metrika_logs" in data["sources"]
    assert data["primary_window"]["date_from"] == "2025-07-01"
