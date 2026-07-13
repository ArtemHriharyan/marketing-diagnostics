"""Блок 1 — форма и конверсия.

Проверки:
    1.1 доходимость формы open->submit по сегментам  [visits]
    1.2 разрыв платный vs сайт                        [visits]
    1.3 качественные причины отвала                   [webvisor_findings]
    1.4 куда уходят бросившие                          [visits]
    1.5 скорость/техника (замеры вручную)             [client_answers]

Контракт:
    Читает   — data/canonical/visits.parquet, inputs/webvisor_findings.yaml,
               inputs/client_answers.yaml, пороги defaults (min_sample_visits,
               significance_alpha).
    Пишет    — data/metrics/: form_funnel, paid_vs_site_gap, dropoff_reasons,
               abandoners_paths, tech_speed (csv + json).
    БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
