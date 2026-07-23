"""Реестр методологии: D02/D03 требуют goals отдельно от visits (4I-goals-canonical).

goals — новая каноническая таблица (см. src/transform/build_canonical.build_goals);
без неё D02 (цель = клик, а не отправка) и D03 (смешаны бизнес-цели и
микроконверсии) не могут проверить фактическую конфигурацию целей счётчика.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import degradation, orchestrator  # noqa: E402


def _check(methodology: dict, check_id: str) -> dict:
    by_id = {c["id"]: c for c in methodology["checks"]}
    return by_id[check_id]


def test_d02_d03_requires_include_goals_and_visits():
    methodology = orchestrator.load_methodology()
    for check_id in ("D02", "D03"):
        check = _check(methodology, check_id)
        assert set(check["requires"]) == {"visits", "goals"}


def test_d02_d03_not_runnable_without_goals():
    """Одних visits недостаточно — D02/D03 недоступны без goals."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, available={"visits"})
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert by_id["D02"]["runnable"] is False
    assert by_id["D03"]["runnable"] is False


def test_d02_d03_runnable_with_visits_and_goals():
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(
        methodology, available={"visits", "goals"}
    )
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert by_id["D02"]["runnable"] is True
    assert by_id["D03"]["runnable"] is True


def test_other_block0_requires_unaffected():
    """Точечная правка D02/D03 не задела requires соседних проверок блока 0."""
    methodology = orchestrator.load_methodology()
    assert _check(methodology, "D04")["requires"] == ["visits"]
    assert _check(methodology, "D05")["requires"] == ["visits"]
