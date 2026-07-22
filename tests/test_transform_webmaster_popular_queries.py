"""Тесты слоя transform: разворот wide popular-queries Вебмастера в long.

См. src/transform/webmaster_popular_queries.py — контракт NaN vs 0 и
помесячная гранулярность (в отличие от src.extract.webmaster_manual,
который агрегирует всё окно в одну строку на query×page).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.transform import webmaster_popular_queries as wpq  # noqa: E402


# ═════════════════════════════ detect_months ═════════════════════════════
def test_detect_months_sorted_and_deduped():
    columns = [
        "Query", "Url",
        "2026-02_shows", "2026-02_position", "2026-02_demand", "2026-02_ctr", "2026-02_clicks",
        "2026-01_shows", "2026-01_position",
    ]
    assert wpq.detect_months(columns) == ["2026-01", "2026-02"]


def test_detect_months_empty_when_no_month_columns():
    assert wpq.detect_months(["Query", "Url"]) == []


# ═══════════════════════ reshape_popular_queries_wide_to_long ═════════════
def test_reshape_basic_two_months_two_queries():
    df = pd.DataFrame([
        {
            "Query": "аренда авто", "Url": "/rent",
            "2026-01_shows": 100, "2026-01_position": 3.5, "2026-01_demand": 500,
            "2026-01_ctr": 0.12, "2026-01_clicks": 12,
            "2026-02_shows": 80, "2026-02_position": 4.0, "2026-02_demand": 450,
            "2026-02_ctr": 0.1, "2026-02_clicks": 8,
        },
        {
            "Query": "прокат машин", "Url": "/rent2",
            "2026-01_shows": 50, "2026-01_position": 5.0, "2026-01_demand": 200,
            "2026-01_ctr": 0.08, "2026-01_clicks": 4,
            "2026-02_shows": 60, "2026-02_position": 4.5, "2026-02_demand": 210,
            "2026-02_ctr": 0.09, "2026-02_clicks": 5,
        },
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)

    assert list(out.columns) == ["query", "url", "month", "shows", "position", "demand", "ctr", "clicks"]
    assert len(out) == 4  # 2 запроса x 2 месяца

    row = out[(out["query"] == "аренда авто") & (out["month"] == "2026-01")].iloc[0]
    assert row["shows"] == 100
    assert row["position"] == 3.5
    assert row["demand"] == 500
    assert row["clicks"] == 12
    assert row["url"] == "/rent"


def test_reshape_missing_month_cell_is_nan_not_zero():
    """Пустая ячейка (запрос не показывался в этом месяце) -> NaN, не 0."""
    df = pd.DataFrame([
        {
            "Query": "аренда авто", "Url": "/rent",
            "2026-01_shows": 100, "2026-01_position": 3.5, "2026-01_demand": 500,
            "2026-01_ctr": 0.12, "2026-01_clicks": 12,
            "2026-02_shows": "", "2026-02_position": "", "2026-02_demand": "",
            "2026-02_ctr": "", "2026-02_clicks": "",
        },
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)

    feb = out[out["month"] == "2026-02"].iloc[0]
    for metric in ("shows", "position", "demand", "ctr", "clicks"):
        assert pd.isna(feb[metric]), f"{metric} должен быть NaN при пропущенном месяце"

    jan = out[out["month"] == "2026-01"].iloc[0]
    assert jan["shows"] == 100
    assert not pd.isna(jan["shows"])


def test_reshape_explicit_zero_is_preserved_not_nan():
    """Явный 0 (Яндекс подтвердил ноль) должен остаться 0, не превращаться в NaN."""
    df = pd.DataFrame([
        {
            "Query": "редкий запрос", "Url": "/rare",
            "2026-01_shows": 0, "2026-01_position": None, "2026-01_demand": 0,
            "2026-01_ctr": 0, "2026-01_clicks": 0,
        },
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)
    row = out.iloc[0]

    assert row["shows"] == 0
    assert not pd.isna(row["shows"])
    assert row["demand"] == 0
    assert not pd.isna(row["demand"])
    assert row["clicks"] == 0
    assert not pd.isna(row["clicks"])
    # position действительно отсутствует (None в источнике) -> NaN допустим только для неё
    assert pd.isna(row["position"])


def test_reshape_zero_and_nan_do_not_collide_across_rows():
    """Один запрос с honest zero, другой с пропуском в том же месяце — не должны совпасть."""
    df = pd.DataFrame([
        {"Query": "q_zero", "Url": "/a", "2026-01_shows": 0, "2026-01_demand": 0},
        {"Query": "q_missing", "Url": "/b", "2026-01_shows": "", "2026-01_demand": ""},
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)

    zero_row = out[out["query"] == "q_zero"].iloc[0]
    missing_row = out[out["query"] == "q_missing"].iloc[0]

    assert zero_row["shows"] == 0 and not pd.isna(zero_row["shows"])
    assert pd.isna(missing_row["shows"])


def test_reshape_missing_metric_column_entirely_gives_nan():
    """Если для месяца вообще нет колонки метрики (напр. demand), она -> NaN, а не 0."""
    df = pd.DataFrame([
        {"Query": "q1", "Url": "/a", "2026-01_shows": 10, "2026-01_clicks": 1},
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)
    row = out.iloc[0]
    assert row["shows"] == 10
    assert pd.isna(row["demand"])
    assert pd.isna(row["position"])
    assert pd.isna(row["ctr"])


def test_reshape_no_month_columns_returns_empty_with_expected_schema():
    df = pd.DataFrame([{"Query": "q1", "Url": "/a"}])
    out = wpq.reshape_popular_queries_wide_to_long(df)
    assert out.empty
    assert list(out.columns) == ["query", "url", "month", "shows", "position", "demand", "ctr", "clicks"]


def test_reshape_custom_column_map_for_query_and_url():
    df = pd.DataFrame([
        {"query_text": "q1", "page_url": "/a", "2026-01_shows": 5},
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df, query_col="query_text", url_col="page_url")
    assert out.iloc[0]["query"] == "q1"
    assert out.iloc[0]["url"] == "/a"


def test_reshape_row_order_stable_by_month_then_input_order():
    df = pd.DataFrame([
        {"Query": "q1", "Url": "/a", "2026-01_shows": 1, "2026-02_shows": 2},
        {"Query": "q2", "Url": "/b", "2026-01_shows": 3, "2026-02_shows": 4},
    ])
    out = wpq.reshape_popular_queries_wide_to_long(df)
    months_seen = out["month"].tolist()
    assert months_seen == ["2026-01", "2026-01", "2026-02", "2026-02"]


# ═══════════════════════════ brand_terms config (smoke) ═══════════════════
def test_template_config_has_brand_terms_field():
    import yaml

    config_path = REPO_ROOT / "clients" / "_template" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "brand_terms" in config
    assert config["brand_terms"] == []


def test_empty_brand_terms_does_not_break_reshape():
    """brand_terms пустой по умолчанию не участвует в reshape (is_brand — в compute),
    но конфиг с пустым списком не должен ничего ломать в пайплайне."""
    df = pd.DataFrame([{"Query": "q1", "Url": "/a", "2026-01_shows": 5}])
    out = wpq.reshape_popular_queries_wide_to_long(df)
    assert not out.empty
