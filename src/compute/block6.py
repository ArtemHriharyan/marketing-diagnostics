"""Блок 6 — CRM и воронка продаж.

Проверки:
    6.1 лид -> сделка          [visits, crm]
    6.2 новые / повторные       [crm]
    6.3 скорость обработки       [crm]

Контракт:
    Читает   — data/canonical/{crm,visits}.parquet, пороги defaults.
    Пишет    — data/metrics/: lead_to_deal, new_vs_returning, handling_speed
               (csv + json).
    Деньги — float рубли. БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
