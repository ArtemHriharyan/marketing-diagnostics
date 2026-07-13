"""Блок 2 — платный трафик и CPA.

Проверки:
    2.1 полный CPA платного лида                       [visits, costs] (+crm)
    2.2 CPA по кампаниям                                [visits, costs]
    2.3 качество поисковых запросов                     [direct_queries]
    2.4 соответствие запрос->объявление->посадочная    [direct_queries, visits]
    2.5 брендовая каннибализация                        [direct_queries, seo_queries]

Контракт:
    Читает   — data/canonical/{visits,costs,direct_queries,seo_queries}.parquet,
               config.brand_terms, пороги defaults.
    Пишет    — data/metrics/: cpa_summary, cpa_by_campaign, query_quality,
               query_ad_landing_match, brand_cannibalization (csv + json).
    Деньги — float рубли, округление на выводе. БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
