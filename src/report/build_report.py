"""Сборка итогового отчёта из утверждённых находок.

Контракт:
    Читает   — findings/approved/*.yaml (только утверждённые аналитиком),
               data/metrics/degradation_report.json, config.yaml (ниша, гео —
               для формулировок), config/defaults.yaml (currency_round для вывода).
    Пишет    — report/ (итоговый документ + приложения). Раздел
               «Что не удалось проверить» берётся из degradation_report.skipped
               в неизменном виде.
    Форматирование — здесь и только здесь: рубли округляются (currency_round),
               доли превращаются в проценты. БЕЗ LLM (текст уже утверждён).
    Гейт     — вызывается оркестратором лишь при непустом findings/approved/.
"""

from __future__ import annotations

from typing import Any


def build(paths: Any, config: dict[str, Any], defaults: dict[str, Any]) -> str:
    """Собрать отчёт в report/; вернуть путь к главному файлу. TODO."""
    raise NotImplementedError
