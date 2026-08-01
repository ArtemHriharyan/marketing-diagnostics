"""Компактная сводка конечных расходов по компонентам, каналам и месяцам.

Компоненты ``canonical_costs`` читают фактически уплаченную сумму из canonical
``costs`` без повторного добавления или вычитания НДС. ``monthly_fixed`` уже
задан конечной ежемесячной суммой в клиентском config. Модуль не меняет формулы
A-проверок и не использует LLM.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

from .common import write_json_atomic


_SUPPORTED_SOURCES = {"canonical_costs", "monthly_fixed"}
_CONFIG_PATH_ATTRIBUTES = ("config", "config_path", "client_config")


def _require_text(component: dict[str, Any], field: str) -> str:
    value = component.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"spend_components: поле {field!r} должно быть непустой строкой")
    return value.strip()


def validate_spend_components(value: Any) -> list[dict[str, Any]]:
    """Проверить контракт ``spend_components`` и вернуть нормализованный список."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("spend_components должен быть списком")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    source_tags: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("каждый spend_component должен быть объектом")
        component_id = _require_text(raw, "id")
        if component_id in ids:
            raise ValueError(f"дублирующийся spend_components.id: {component_id}")
        ids.add(component_id)

        source = _require_text(raw, "source")
        if source not in _SUPPORTED_SOURCES:
            raise ValueError(f"неподдерживаемый spend_components.source: {source}")

        component: dict[str, Any] = {
            "id": component_id,
            "channel": _require_text(raw, "channel"),
            "kind": _require_text(raw, "kind"),
            "source": source,
        }
        if source == "canonical_costs":
            source_tag = _require_text(raw, "source_tag")
            if source_tag in source_tags:
                raise ValueError(f"дублирующийся canonical source_tag: {source_tag}")
            source_tags.add(source_tag)
            component["source_tag"] = source_tag
        else:
            amount = raw.get("amount_rub_month")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise ValueError("amount_rub_month должен быть числом")
            amount = float(amount)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("amount_rub_month должен быть конечным неотрицательным числом")
            component["amount_rub_month"] = amount
        normalized.append(component)
    return normalized


def _month_range(config: dict[str, Any]) -> list[str]:
    window = config.get("data_window") or {}
    if window.get("mode") != "explicit":
        return []
    raw_from = window.get("date_from")
    raw_to = window.get("date_to")
    if not isinstance(raw_from, str) or not isinstance(raw_to, str) or raw_to == "today":
        return []
    try:
        start = date.fromisoformat(raw_from)
        end = date.fromisoformat(raw_to)
    except ValueError:
        return []
    if start > end:
        return []

    months: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def _cost_columns(costs_path: Path) -> tuple[str, str]:
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(costs_path)]).fetchall()
    finally:
        con.close()
    names = {row[0] for row in rows}
    if "source_tag" not in names:
        raise ValueError("canonical costs не содержит source_tag")
    month_column = "month" if "month" in names else "date" if "date" in names else ""
    if not month_column:
        raise ValueError("canonical costs не содержит month или date")
    # cost_raw — исходная фактически уплаченная сумма. cost_rub оставлен как
    # совместимый вариант схемы; cost_normalized намеренно не используется.
    amount_column = "cost_raw" if "cost_raw" in names else "cost_rub" if "cost_rub" in names else ""
    if not amount_column:
        raise ValueError("canonical costs не содержит cost_raw или cost_rub")
    return month_column, amount_column


def _canonical_monthly(costs_path: Path) -> dict[tuple[str, str], float]:
    month_column, amount_column = _cost_columns(costs_path)
    month_sql = (
        'substr(CAST("month" AS VARCHAR), 1, 7)'
        if month_column == "month"
        else 'strftime(CAST("date" AS DATE), \'%Y-%m\')'
    )
    query = f"""
        SELECT {month_sql} AS month, CAST(source_tag AS VARCHAR),
               SUM(CAST(\"{amount_column}\" AS DOUBLE)) AS amount_rub
        FROM read_parquet(?)
        WHERE \"{amount_column}\" IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute(query, [str(costs_path)]).fetchall()
    finally:
        con.close()
    return {(str(month), str(tag)): float(amount) for month, tag, amount in rows}


def build_cost_summary(
    config: dict[str, Any] | None,
    costs_path: Path | None,
) -> dict[str, Any]:
    """Собрать компактную gross-сводку; отсутствие canonical явно деградирует."""
    config = config or {}
    components = validate_spend_components(config.get("spend_components"))
    configured_months = _month_range(config)
    canonical_components = [c for c in components if c["source"] == "canonical_costs"]
    limitations: list[dict[str, Any]] = []
    canonical: dict[tuple[str, str], float] = {}

    if canonical_components:
        if costs_path is None or not Path(costs_path).exists():
            limitations.append(
                {
                    "code": "canonical_costs_unavailable",
                    "component_ids": [c["id"] for c in canonical_components],
                }
            )
        else:
            canonical = _canonical_monthly(Path(costs_path))

    canonical_months = {month for month, _ in canonical}
    months = sorted(set(configured_months) | canonical_months)
    component_rows: list[list[Any]] = []
    for component in components:
        if component["source"] == "monthly_fixed":
            for month in months:
                component_rows.append(
                    [month, component["id"], component["channel"], component["kind"], component["amount_rub_month"]]
                )
            continue

        source_tag = component["source_tag"]
        matched = False
        for month in months:
            key = (month, source_tag)
            if key not in canonical:
                continue
            matched = True
            component_rows.append(
                [month, component["id"], component["channel"], component["kind"], canonical[key]]
            )
        if costs_path is not None and Path(costs_path).exists() and not matched:
            limitations.append(
                {
                    "code": "canonical_source_tag_not_found",
                    "component_id": component["id"],
                    "source_tag": source_tag,
                }
            )

    component_rows.sort(key=lambda row: (row[0], row[1]))
    component_totals: dict[tuple[str, str, str], float] = {}
    channel_totals: dict[str, float] = {}
    monthly_totals: dict[str, float] = {}
    for month, component_id, channel, kind, amount in component_rows:
        component_key = (component_id, channel, kind)
        component_totals[component_key] = component_totals.get(component_key, 0.0) + amount
        channel_totals[channel] = channel_totals.get(channel, 0.0) + amount
        monthly_totals[month] = monthly_totals.get(month, 0.0) + amount

    return {
        "money_basis": "gross_final_rub",
        "component_columns": ["month", "component_id", "channel", "kind", "amount_rub"],
        "component_rows": component_rows,
        "component_total_columns": ["component_id", "channel", "kind", "amount_rub"],
        "component_total_rows": [
            [component_id, channel, kind, amount]
            for (component_id, channel, kind), amount in sorted(component_totals.items())
        ],
        "channel_total_columns": ["channel", "amount_rub"],
        "channel_total_rows": [[channel, amount] for channel, amount in sorted(channel_totals.items())],
        "monthly_total_columns": ["month", "amount_rub"],
        "monthly_total_rows": [[month, amount] for month, amount in sorted(monthly_totals.items())],
        "total_rub": float(sum(channel_totals.values())),
        "limitations": limitations,
    }


def _load_config(paths: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    for attribute in _CONFIG_PATH_ATTRIBUTES:
        candidate = getattr(paths, attribute, None)
        if candidate and Path(candidate).is_file():
            with Path(candidate).open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return defaults if isinstance(defaults, dict) else {}


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать ``cost_summary.json`` независимо от runnable D/A/T/C/S."""
    del runnable_ids
    config = _load_config(paths, defaults)
    costs_path = Path(paths.canonical) / "costs.parquet"
    summary = build_cost_summary(config, costs_path if costs_path.exists() else None)
    write_json_atomic(Path(paths.metrics) / "cost_summary.json", summary)
    return ["cost_summary.json"]
