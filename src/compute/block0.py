"""Блок 0 — гигиена данных и постановка.

Проверки (config/methodology.yaml):
    0.1 переотработка целей          [visits]
    0.2 качество UTM                 [visits]
    0.3 состав целей                 [visits, client_answers]
    0.4 цель оптимизации кампаний    [client_answers]
    0.5 покрытие каналов             [client_answers]
    0.6 двойной счёт расходов        [costs, client_answers]

Контракт:
    Читает   — data/canonical/{visits,costs}.parquet, inputs/client_answers.yaml,
               пороги из config/defaults.yaml (utm_undefined_threshold,
               goal_inflation_warning).
    Пишет    — data/metrics/ артефакты: goal_inflation, utm_quality,
               goal_composition, campaign_objective, channel_coverage,
               cost_double_count (csv + json).
    БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить проверки блока 0 из числа runnable_ids; вернуть имена артефактов. TODO."""
    raise NotImplementedError
