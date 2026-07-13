"""Блок 3 — деньги, приоритеты, честность.

Проверки:
    3.1 денежная рамка          [visits, costs] (агрегирует итоги 1.1 + 2.1)
    3.2 приоритизация находок   [] (агрегирует все находки блоков)
    3.3 границы честности       [degradation_report]

Контракт:
    Читает   — артефакты предыдущих блоков из data/metrics/,
               data/metrics/degradation_report.json, config.costs_manual,
               inputs/client_answers.yaml (avg_check, margin).
    Пишет    — data/metrics/: money_frame, prioritization, honesty_bounds
               (csv + json). honesty_bounds строится из degradation_report и
               уходит в отчёт как раздел «Что не удалось проверить».
    БЕЗ LLM (приоритизация — по детерминированным правилам, не по модели).
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
