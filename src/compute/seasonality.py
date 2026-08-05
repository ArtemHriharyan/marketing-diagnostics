"""Компактная бизнес-сезонность по доступным месячным рядам.

Артефакт не содержит абсолютных значений и сырых фраз Wordstat: наружу
выходят только индексы относительно медианы, пики/провалы, YoY и конфликты
направлений. Каждый источник деградирует независимо от остальных.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from . import common, funnels


_PEAK_INDEX = 200.0
_TROUGH_INDEX = 50.0
_CRM_TABLE_PRIORITY = ("crm_records", "crm_deals", "crm_bookings", "crm", "bookings", "orders")
_MONTH_COLUMNS = ("lead_date", "month", "created_at", "created_date", "date", "paid_at", "closed_at")
_REVENUE_COLUMNS = ("amount_rub", "revenue", "revenue_rub", "amount", "paid_amount", "payment_amount")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _month(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m")
    text = str(value)
    return text[:7] if len(text) >= 7 else None


def _previous_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-", 1))
    if number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{number - 1:02d}"


def _previous_year(month: str) -> str:
    year, number = (int(part) for part in month.split("-", 1))
    return f"{year - 1:04d}-{number:02d}"


def _direction(current: float, previous: float) -> str:
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "stable"


def _unavailable(reason: str, **context: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, **context}


def _indexed_series(values: dict[str, float], **context: Any) -> dict[str, Any]:
    clean = {
        month: float(value)
        for month, value in values.items()
        if _month(month) == month and value is not None and float(value) >= 0
    }
    positive = [value for value in clean.values() if value > 0]
    if not positive:
        return _unavailable("ряд не содержит положительных месячных значений", **context)

    baseline = float(median(positive))
    rows: list[dict[str, Any]] = []
    for month in sorted(clean):
        value = clean[month]
        index = round(value / baseline * 100, 1)
        previous_month = _previous_month(month)
        previous_year = _previous_year(month)
        row: dict[str, Any] = {"month": month, "index": index}
        if previous_month in clean:
            row["mom_direction"] = _direction(value, clean[previous_month])
        if previous_year in clean and clean[previous_year] > 0:
            row["yoy_index"] = round(value / clean[previous_year] * 100, 1)
            row["yoy_direction"] = _direction(value, clean[previous_year])
        rows.append(row)

    return {
        "status": "ok",
        **context,
        "months": rows,
        "peaks": [
            {"month": row["month"], "index": row["index"]}
            for row in rows
            if row["index"] >= _PEAK_INDEX
        ],
        "troughs": [
            {"month": row["month"], "index": row["index"]}
            for row in rows
            if row["index"] <= _TROUGH_INDEX
        ],
    }


def _table_columns(con: Any, table: str) -> set[str]:
    quoted = _quote_identifier(table)
    return {str(row[0]) for row in con.execute(f"DESCRIBE {quoted}").fetchall()}


def _month_expression(column: str) -> str:
    return f"SUBSTR(CAST({_quote_identifier(column)} AS VARCHAR), 1, 7)"


def _aggregate(con: Any, table: str, month_column: str, value_sql: str, where: str = "") -> dict[str, float]:
    quoted_table = _quote_identifier(table)
    month_sql = _month_expression(month_column)
    rows = con.execute(
        f"SELECT {month_sql} AS month, {value_sql} AS value "
        f"FROM {quoted_table} {where} GROUP BY month ORDER BY month"
    ).fetchall()
    return {
        month: float(value or 0)
        for raw_month, value in rows
        if (month := _month(raw_month)) is not None
    }


def _wordstat_series(con: Any, canonical: dict[str, Path]) -> dict[str, Any]:
    if "wordstat" not in canonical:
        return _unavailable("canonical wordstat отсутствует")
    columns = _table_columns(con, "wordstat")
    if not {"month", "count", "purpose"}.issubset(columns):
        return _unavailable("в canonical wordstat нет month/count/purpose")
    values = _aggregate(
        con, "wordstat", "month", "SUM(count)", "WHERE purpose LIKE '%seasonality%'"
    )
    return _indexed_series(values)


def _visits_series(con: Any, canonical: dict[str, Path]) -> dict[str, Any]:
    if "visits" not in canonical:
        return _unavailable("canonical visits отсутствует")
    columns = _table_columns(con, "visits")
    month_column = next((column for column in ("month", "date") if column in columns), None)
    if month_column is None:
        return _unavailable("в canonical visits нет month/date")
    value_sql = "COUNT(DISTINCT visit_id)" if "visit_id" in columns else "COUNT(*)"
    return _indexed_series(_aggregate(con, "visits", month_column, value_sql))


def _funnel_series(paths: Any) -> dict[str, Any]:
    # PERF-1B: воронки уже посчитаны блоком funnels — читаем его артефакт.
    result = funnels.load_funnels(paths)
    if result.get("status") != "ok" or not result.get("funnels"):
        return _unavailable(str(result.get("reason") or "выбранная воронка недоступна"))

    selected = result["funnels"][0]
    stage_definitions = selected.get("stage_definitions") or []
    if not stage_definitions:
        return _unavailable("у выбранной воронки нет итогового этапа")
    final_stage = stage_definitions[-1]["stage"]
    values: dict[str, float] = {}
    for segment in selected.get("segments") or []:
        if segment.get("dimension") != "month":
            continue
        month = _month(segment.get("value"))
        stage = next(
            (item for item in segment.get("stages") or [] if item.get("stage") == final_stage),
            None,
        )
        if month is not None and stage is not None:
            values[month] = float(stage.get("visits") or 0)
    return _indexed_series(values, funnel_id=selected.get("funnel_id"), stage=final_stage)


def _crm_table(canonical: dict[str, Path]) -> str | None:
    names = set(canonical)
    for candidate in _CRM_TABLE_PRIORITY:
        if candidate in names:
            return candidate
    return next((name for name in sorted(names) if name.startswith("crm_")), None)


def _crm_series(con: Any, canonical: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any]]:
    table = _crm_table(canonical)
    if table is None:
        reason = "canonical CRM отсутствует"
        return _unavailable(reason), _unavailable(reason)
    columns = _table_columns(con, table)
    month_column = next((column for column in _MONTH_COLUMNS if column in columns), None)
    if month_column is None:
        reason = "в canonical CRM нет поля месяца/даты"
        return _unavailable(reason), _unavailable(reason)

    records = _indexed_series(_aggregate(con, table, month_column, "COUNT(*)"))
    revenue_column = next((column for column in _REVENUE_COLUMNS if column in columns), None)
    if revenue_column is None:
        revenue = _unavailable("в canonical CRM нет поля выручки")
    else:
        revenue = _indexed_series(
            _aggregate(con, table, month_column, f"SUM(COALESCE({_quote_identifier(revenue_column)}, 0))")
        )
    return records, revenue


def _direction_conflicts(series: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for source, payload in series.items():
        if payload.get("status") != "ok":
            continue
        for row in payload.get("months") or []:
            for comparison, field in (("yoy", "yoy_direction"), ("mom", "mom_direction")):
                direction = row.get(field)
                if direction in {"up", "down", "stable"}:
                    by_key.setdefault((row["month"], comparison), {})[source] = direction

    conflicts: list[dict[str, Any]] = []
    for (month, comparison), directions in sorted(by_key.items()):
        material = set(directions.values())
        if {"up", "down"}.issubset(material):
            conflicts.append({
                "month": month,
                "comparison": comparison,
                "directions": directions,
            })
    return conflicts


def _monthly_index_matrix(series: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for source, payload in series.items():
        if payload.get("status") != "ok":
            continue
        for row in payload.get("months") or []:
            matrix.setdefault(row["month"], {"month": row["month"]})[f"{source}_index"] = row["index"]
    return [matrix[month] for month in sorted(matrix)]


def compute_seasonality(paths: Any) -> dict[str, Any]:
    """Собрать компактную сезонность без записи артефакта."""
    canonical = common.load_canonical(paths)
    con = common.open_duckdb(paths)
    try:
        crm_records, crm_revenue = _crm_series(con, canonical)
        series = {
            "wordstat_demand": _wordstat_series(con, canonical),
            "visits": _visits_series(con, canonical),
            "funnel_final_stage": _funnel_series(paths),
            "crm_records": crm_records,
            "crm_revenue": crm_revenue,
        }
    finally:
        con.close()

    available = sum(payload.get("status") == "ok" for payload in series.values())
    status = "ok" if available == len(series) else "partial" if available else "unavailable"
    return {
        "status": status,
        "index_base": "median_positive_months_100",
        "peak_index_threshold": _PEAK_INDEX,
        "trough_index_threshold": _TROUGH_INDEX,
        "series": series,
        "monthly_indices": _monthly_index_matrix(series),
        "direction_conflicts": _direction_conflicts(series),
    }


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать независимый compact seasonality.json."""
    del defaults, runnable_ids
    common.write_json_atomic(Path(paths.metrics) / "seasonality.json", compute_seasonality(paths))
    return ["seasonality"]
