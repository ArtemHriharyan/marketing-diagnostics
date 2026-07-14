"""Выделенные тесты карты деградации (task 1B).

Сценарии:
1. Недоступный источник -> runnable=False, reason_if_not_runnable не None.
2. type downgrade: истинное условие -> type_downgraded; ложное -> type_default.
3. Один manual-источник в requires -> confidence_cap=MED.
4. Все requires из api-источников -> confidence_cap=HIGH.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.degradation import evaluate_check, table_source_modes  # noqa: E402


def _modes(config=None):
    return table_source_modes(config)


# ── 1. Недоступный источник ─────────────────────────────────────────────────

def test_unavailable_source_not_runnable():
    """Проверка, у которой requires недоступен, получает runnable=False и reason."""
    check = {
        "id": "X01",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(check, available=set(), source_modes=_modes())
    assert result["runnable"] is False
    assert result["reason_if_not_runnable"] is not None
    assert len(result["reason_if_not_runnable"]) > 0


def test_available_source_is_runnable():
    """Все requires доступны -> runnable=True и reason=None."""
    check = {
        "id": "X02",
        "requires": ["visits"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(check, available={"visits"}, source_modes=_modes())
    assert result["runnable"] is True
    assert result["reason_if_not_runnable"] is None


# ── 2. type downgrade ────────────────────────────────────────────────────────

def test_type_downgrade_applies_when_condition_true():
    """Условие type_downgrade_if истинно -> type_effective = type_downgraded."""
    check = {
        "id": "X03",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": "some_flag == false",
        "type_downgraded": "B",
    }
    # some_flag отсутствует в flags -> false -> "== false" истинно.
    result = evaluate_check(
        check, available={"costs"}, source_modes=_modes(), flags={}
    )
    assert result["type_effective"] == "B"


def test_type_downgrade_skipped_when_condition_false():
    """Условие type_downgrade_if ложно -> type_effective = type_default."""
    check = {
        "id": "X04",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": "some_flag == true",
        "type_downgraded": "B",
    }
    # some_flag отсутствует -> false -> "== true" ложно.
    result = evaluate_check(
        check, available={"costs"}, source_modes=_modes(), flags={}
    )
    assert result["type_effective"] == "A"


# ── 3. Один manual-источник в requires -> MED ────────────────────────────────

def test_one_manual_required_caps_confidence_at_med():
    """Хотя бы один requires — manual -> confidence_cap=MED."""
    # client_answers входит в _MANUAL_TABLES -> всегда mode=manual.
    check = {
        "id": "X05",
        "requires": ["costs", "client_answers"],
        "type_default": "A+Q",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(
        check,
        available={"costs", "client_answers"},
        source_modes=_modes(),
        manual_cap="MED",
    )
    assert result["confidence_cap"] == "MED"
    assert result["source_modes"]["client_answers"] == "manual"
    assert result["source_modes"]["costs"] == "api"


# ── 4. Все requires из api-источников -> HIGH ────────────────────────────────

def test_all_api_required_keeps_confidence_high():
    """Все requires — api-источники -> confidence_cap=HIGH."""
    check = {
        "id": "X06",
        "requires": ["visits", "costs"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(
        check,
        available={"visits", "costs"},
        source_modes=_modes(),
        manual_cap="MED",
    )
    assert result["confidence_cap"] == "HIGH"
    assert result["source_modes"]["visits"] == "api"
    assert result["source_modes"]["costs"] == "api"
