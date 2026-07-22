"""Разворот wide-таблицы Вебмастера «Популярные запросы» в long-формат.

Контракт:
    Читает — DataFrame в wide-формате ручной/API-выгрузки Вебмастера:
        Query | Url | {YYYY-MM}_shows | {YYYY-MM}_position | {YYYY-MM}_demand |
        {YYYY-MM}_ctr | {YYYY-MM}_clicks
    (см. data-export-spec-v1.md, раздел D, и clients/_template/config.yaml:
    sources.webmaster.manual_export_file).

    Пишет — DataFrame в long-формате: query, url, month, shows, position,
    demand, ctr, clicks — одна строка на (query, url, month).

Правило NaN vs 0 (важно, не путать):
    Отсутствующий месяц для пары (query, url) — пустая ячейка в исходном
    wide-файле (запрос не показывался в этом месяце вовсе) -> NaN. Явный
    0 в ячейке (shows=0, demand=0 и т.п.) — Яндекс подтвердил ноль показов/
    спроса, это ЗНАЧЕНИЕ, а не отсутствие данных -> сохраняется как 0.
    Смешивать эти два случая нельзя: NaN должен уходить в подсчёт охвата/
    полноты данных отдельно от honest zero.

    Функция не агрегирует и не отбрасывает нулевые месяцы — в отличие от
    src.extract.webmaster_manual, который сворачивает wide-файл в одну
    строку на (query, page) для search_queries_popular.json и явно
    пропускает месяцы с shows=0. Здесь месячная гранулярность и различие
    NaN/0 сохраняются — это отдельный контракт для будущей compute-
    классификации is_brand/сезонности (regex по config.brand_terms,
    см. clients/_template/config.yaml).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd

_METRICS = ("shows", "position", "demand", "ctr", "clicks")
_MONTH_COL_RE = re.compile(r"^(\d{4}-\d{2})_(shows|position|demand|ctr|clicks)$")

_DEFAULT_QUERY_COL = "Query"
_DEFAULT_URL_COL = "Url"

LONG_COLUMNS = ["query", "url", "month", *_METRICS]


def detect_months(columns: Iterable[Any]) -> list[str]:
    """Колонки wide-таблицы -> отсортированный список месяцев YYYY-MM."""
    months = {m.group(1) for col in columns if (m := _MONTH_COL_RE.match(str(col)))}
    return sorted(months)


def reshape_popular_queries_wide_to_long(
    df: pd.DataFrame,
    query_col: str = _DEFAULT_QUERY_COL,
    url_col: str = _DEFAULT_URL_COL,
) -> pd.DataFrame:
    """Wide popular-queries -> long (query, url, month, shows, position, demand, ctr, clicks).

    Пустая ячейка метрики за месяц -> NaN. Явный 0 сохраняется как 0 (см.
    докстринг модуля). Колонка месяц-метрика, отсутствующая в df целиком,
    даёт NaN по этой метрике для всех строк того месяца.
    """
    months = detect_months(df.columns)
    if not months:
        return pd.DataFrame(columns=LONG_COLUMNS)

    query_series = (
        df[query_col].reset_index(drop=True)
        if query_col in df.columns
        else pd.Series([pd.NA] * len(df))
    )
    url_series = (
        df[url_col].reset_index(drop=True)
        if url_col in df.columns
        else pd.Series([pd.NA] * len(df))
    )

    frames: list[pd.DataFrame] = []
    for month in months:
        block: dict[str, Any] = {
            "query": query_series,
            "url": url_series,
            "month": month,
        }
        for metric in _METRICS:
            col = f"{month}_{metric}"
            if col in df.columns:
                block[metric] = pd.to_numeric(df[col], errors="coerce").reset_index(drop=True)
            else:
                block[metric] = pd.NA
        frames.append(pd.DataFrame(block))

    return pd.concat(frames, ignore_index=True)[LONG_COLUMNS]
