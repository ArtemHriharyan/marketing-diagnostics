"""Блок 4 — атрибуция.

Проверки:
    4.1 last-significant атрибуция   [visits]
    4.2 недооценённые каналы          [visits]

Контракт:
    Читает   — data/canonical/visits.parquet (цепочки источников по визитам),
               пороги defaults.
    Пишет    — data/metrics/: lastsign_attribution, undervalued_channels
               (csv + json).
    БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
