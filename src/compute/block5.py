"""Блок 5 — SEO и спрос.

Проверки:
    5.1 strike zone (позиции 11-20)             [seo_queries]
    5.2 бренд / небренд                          [seo_queries]
    5.3 аномалии CTR                             [seo_queries]
    5.4 Яндекс vs Google (+ручная фиксация)     [seo_queries]
    5.5 спрос Wordstat                           [wordstat]
    5.6 контент без пути к деньгам               [visits, seo_queries]

Контракт:
    Читает   — data/canonical/{seo_queries,wordstat,visits}.parquet,
               config.brand_terms, пороги defaults.
    Пишет    — data/metrics/: strike_zone, brand_nonbrand, ctr_anomalies,
               yandex_vs_google, wordstat_demand, content_no_money_path
               (csv + json).
    БЕЗ LLM.
"""

from __future__ import annotations

from typing import Any


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    raise NotImplementedError
