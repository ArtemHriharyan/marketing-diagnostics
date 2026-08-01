"""Универсальная экономика привлечения по CRM-записям и этапам воронки.

Модуль использует только настроенные клиентом модели, итоговые компоненты
``cost_summary.json`` и детерминированные canonical/funnel-артефакты. Он не
делает предположений о долях, каналах, семантике CRM или ID целей.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

import yaml

from . import common


_SUPPORTED_MODES = {"crm_attributed", "crm_share_estimate", "tracked_funnel"}
_SUPPORTED_RECORD_UNITS = {"paid_booking", "lead", "opportunity", "unknown"}
_CONFIG_PATH_ATTRIBUTES = ("config_file", "config", "config_path", "client_config")
_RECORD_UNIT_NAMES = {
    "paid_booking": "оплаченная бронь",
    "lead": "обращение",
    "opportunity": "потенциальная сделка",
}
_RESULT_UNITS = {
    "paid_booking": "rub_per_paid_booking",
    "lead": "rub_per_lead",
    "opportunity": "rub_per_opportunity",
}


def _require_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} должен быть непустой строкой")
    return value.strip()


def _normalise_traffic_filter(value: Any, model_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"acquisition_models.{model_id}.traffic_filter должен быть объектом")
    if set(value) == {"field", "value"}:
        field = _require_text(value.get("field"), f"acquisition_models.{model_id}.traffic_filter.field")
        filter_value = value.get("value")
    elif len(value) == 1:
        field, filter_value = next(iter(value.items()))
        field = _require_text(field, f"acquisition_models.{model_id}.traffic_filter field")
    else:
        raise ValueError(
            f"acquisition_models.{model_id}.traffic_filter должен содержать field/value или одно поле"
        )
    if filter_value is None or isinstance(filter_value, (dict, list)):
        raise ValueError(f"acquisition_models.{model_id}.traffic_filter.value должен быть скаляром")
    return {"field": field, "value": filter_value}


def validate_acquisition_models(value: Any) -> list[dict[str, Any]]:
    """Проверить и нормализовать клиентские модели экономики привлечения."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("acquisition_models должен быть списком")

    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("каждая acquisition_model должна быть объектом")
        model_id = _require_text(raw.get("id"), "acquisition_models.id")
        if model_id in seen_ids:
            raise ValueError(f"дублирующийся acquisition_models.id: {model_id}")
        seen_ids.add(model_id)
        mode = _require_text(raw.get("mode"), f"acquisition_models.{model_id}.mode")
        if mode not in _SUPPORTED_MODES:
            raise ValueError(f"неподдерживаемый acquisition_models.mode: {mode}")
        components = raw.get("spend_components")
        if not isinstance(components, list) or not components:
            raise ValueError(f"acquisition_models.{model_id}.spend_components должен быть непустым списком")
        component_ids = [
            _require_text(item, f"acquisition_models.{model_id}.spend_components")
            for item in components
        ]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError(f"acquisition_models.{model_id}.spend_components содержит дубли")

        model: dict[str, Any] = {
            "id": model_id,
            "mode": mode,
            "spend_components": component_ids,
        }
        channel = raw.get("channel")
        if channel is not None:
            model["channel"] = _require_text(channel, f"acquisition_models.{model_id}.channel")

        if mode == "crm_attributed":
            model["crm_source"] = _require_text(
                raw.get("crm_source"), f"acquisition_models.{model_id}.crm_source"
            ).lower()
        elif mode == "crm_share_estimate":
            share = raw.get("crm_share")
            if isinstance(share, bool) or not isinstance(share, (int, float)):
                raise ValueError(f"acquisition_models.{model_id}.crm_share должен быть числом")
            share = float(share)
            if not math.isfinite(share) or not 0 < share <= 1:
                raise ValueError(f"acquisition_models.{model_id}.crm_share должен быть в интервале (0, 1]")
            model["crm_share"] = share
        else:
            model["funnel"] = _require_text(
                raw.get("funnel"), f"acquisition_models.{model_id}.funnel"
            )
            model["stage"] = _require_text(
                raw.get("stage"), f"acquisition_models.{model_id}.stage"
            )
            model["traffic_filter"] = _normalise_traffic_filter(
                raw.get("traffic_filter"), model_id
            )
        result.append(model)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    return loaded if isinstance(loaded, dict) else {}


def _load_config(paths: Any, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    for attribute in _CONFIG_PATH_ATTRIBUTES:
        candidate = getattr(paths, attribute, None)
        if candidate and Path(candidate).is_file():
            with Path(candidate).open(encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            return loaded if isinstance(loaded, dict) else {}
    return defaults if isinstance(defaults, dict) else {}


def _table_columns(con: Any, table: str) -> set[str]:
    try:
        return {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}
    except Exception:  # noqa: BLE001 - отсутствие optional canonical таблицы
        return set()


def _crm_summary(paths: Any, record_unit: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "record_unit": record_unit,
        "record_count": None,
        "total_revenue_rub": None,
        "average_revenue_rub": None,
        "median_revenue_rub": None,
        "unique_customers": None,
        "average_revenue_per_customer_rub": None,
        "limitations": [],
    }
    if record_unit == "unknown":
        summary["status"] = "unavailable"
        summary["limitations"].append({"code": "crm_record_unit_unknown"})
        return summary

    canonical = common.load_canonical(paths)
    if "crm" not in canonical:
        summary["status"] = "unavailable"
        summary["limitations"].append({"code": "crm_unavailable"})
        return summary

    con = common.open_duckdb(paths)
    try:
        columns = _table_columns(con, "crm")
        count = int(con.execute("SELECT COUNT(*) FROM crm").fetchone()[0])
        summary["record_count"] = count
        if record_unit != "paid_booking":
            summary["status"] = "ok"
            return summary

        if "amount_rub" not in columns:
            summary["status"] = "partial"
            summary["limitations"].append({"code": "crm_amount_unavailable"})
            return summary
        amounts = [
            float(row[0])
            for row in con.execute("SELECT amount_rub FROM crm WHERE amount_rub IS NOT NULL").fetchall()
        ]
        if len(amounts) != count:
            summary["status"] = "partial"
            summary["limitations"].append(
                {"code": "crm_amount_incomplete", "known_records": len(amounts), "record_count": count}
            )
            return summary
        summary["total_revenue_rub"] = float(sum(amounts))
        summary["average_revenue_rub"] = float(mean(amounts)) if amounts else None
        summary["median_revenue_rub"] = float(median(amounts)) if amounts else None

        if "phone_hash" in columns:
            customer_count, rows_with_customer = con.execute(
                "SELECT COUNT(DISTINCT phone_hash), COUNT(phone_hash) FROM crm"
            ).fetchone()
            if int(rows_with_customer) == count and count:
                summary["unique_customers"] = int(customer_count)
                if customer_count:
                    summary["average_revenue_per_customer_rub"] = float(sum(amounts) / customer_count)
        summary["status"] = "ok"
        return summary
    finally:
        con.close()


def _cost_components(config: dict[str, Any], cost_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    configured = {
        item.get("id"): item
        for item in (config.get("spend_components") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    columns = cost_summary.get("component_total_columns") or []
    rows = cost_summary.get("component_total_rows") or []
    if not isinstance(columns, list) or "component_id" not in columns or "amount_rub" not in columns:
        return {}
    id_index = columns.index("component_id")
    amount_index = columns.index("amount_rub")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(id_index, amount_index):
            continue
        component_id = str(row[id_index])
        configured_item = configured.get(component_id) or {}
        result[component_id] = {
            "id": component_id,
            "channel": configured_item.get("channel"),
            "kind": configured_item.get("kind"),
            "amount_rub": float(row[amount_index]),
        }
    return result


def _funnel_stage_visits(
    funnel_summary: dict[str, Any], funnel_id: str, stage_name: str
) -> int | None:
    for funnel in funnel_summary.get("funnels") or []:
        if not isinstance(funnel, dict) or funnel.get("funnel_id") != funnel_id:
            continue
        for stage in funnel.get("stages") or []:
            if isinstance(stage, dict) and stage.get("stage") == stage_name:
                visits = stage.get("visits")
                return int(visits) if isinstance(visits, int) and not isinstance(visits, bool) else None
    return None


def _stage_goal_ids(config: dict[str, Any], funnel_id: str, stage_name: str) -> list[str]:
    funnels = config.get("funnels") or {}
    stages = funnels.get(funnel_id) if isinstance(funnels, dict) else None
    if not isinstance(stages, list):
        return []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("stage") == stage_name:
            goal_ids = stage.get("goal_ids")
            if isinstance(goal_ids, list):
                return [str(goal_id) for goal_id in goal_ids if goal_id is not None]
    return []


def _filtered_stage_visits(
    paths: Any,
    config: dict[str, Any],
    funnel_id: str,
    stage_name: str,
    traffic_filter: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    goal_ids = _stage_goal_ids(config, funnel_id, stage_name)
    if not goal_ids:
        return None, {"code": "funnel_stage_not_configured", "funnel": funnel_id, "stage": stage_name}
    canonical = common.load_canonical(paths)
    if "visits" not in canonical or "visit_goals" not in canonical:
        return None, {"code": "tracked_funnel_canonical_unavailable"}

    con = common.open_duckdb(paths)
    try:
        visit_columns = _table_columns(con, "visits")
        field = traffic_filter["field"]
        if field not in visit_columns:
            return None, {"code": "traffic_filter_field_unavailable", "field": field}
        quoted_field = '"' + field.replace('"', '""') + '"'
        placeholders = ", ".join("?" for _ in goal_ids)
        query = f"""
            SELECT COUNT(DISTINCT g.visit_id)
            FROM visit_goals AS g
            JOIN visits AS v ON v.visit_id = g.visit_id
            WHERE CAST(g.goal_id AS VARCHAR) IN ({placeholders})
              AND g.achievement_count > 0
              AND v.{quoted_field} = ?
        """
        value = con.execute(query, [*goal_ids, traffic_filter["value"]]).fetchone()[0]
        return int(value), None
    finally:
        con.close()


def _crm_attributed_count(paths: Any, source: str) -> tuple[int | None, dict[str, Any] | None]:
    canonical = common.load_canonical(paths)
    if "crm" not in canonical:
        return None, {"code": "crm_unavailable"}
    con = common.open_duckdb(paths)
    try:
        if "source_norm" not in _table_columns(con, "crm"):
            return None, {"code": "crm_source_unavailable"}
        count = con.execute(
            "SELECT COUNT(*) FROM crm WHERE lower(CAST(source_norm AS VARCHAR)) = ?",
            [source.lower()],
        ).fetchone()[0]
        return int(count), None
    finally:
        con.close()


def _result_name(mode: str, record_unit: str, stage: str | None = None) -> str:
    if mode == "crm_share_estimate" and record_unit == "paid_booking":
        return "оценочная стоимость сайт-брони"
    if mode == "crm_attributed" and record_unit == "paid_booking":
        return "стоимость оплаченной брони"
    if mode == "tracked_funnel":
        if stage == "form_submit":
            return "стоимость отслеженной отправки формы"
        return f"стоимость отслеженного этапа «{stage}»"
    return f"стоимость на CRM-запись: {_RECORD_UNIT_NAMES.get(record_unit, record_unit)}"


def compute_acquisition_economics(
    paths: Any,
    config: dict[str, Any] | None = None,
    cost_summary: dict[str, Any] | None = None,
    funnel_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Посчитать все модели, явно сохраняя допущения и причины недоступности."""
    config = config if isinstance(config, dict) else _load_config(paths)
    crm_config = config.get("crm_csv") if isinstance(config.get("crm_csv"), dict) else {}
    record_unit = crm_config.get("record_unit", "unknown")
    if record_unit not in _SUPPORTED_RECORD_UNITS:
        raise ValueError(f"неподдерживаемый crm_csv.record_unit: {record_unit}")
    models = validate_acquisition_models(config.get("acquisition_models"))
    metrics_dir = Path(paths.metrics)
    cost_summary = cost_summary if isinstance(cost_summary, dict) else _read_json(
        metrics_dir / "cost_summary.json"
    )
    funnel_summary = funnel_summary if isinstance(funnel_summary, dict) else _read_json(
        metrics_dir / "funnels.json"
    )
    components = _cost_components(config, cost_summary)
    crm = _crm_summary(paths, record_unit)

    results: list[dict[str, Any]] = []
    for model in models:
        requested_ids = model["spend_components"]
        selected = [components[item] for item in requested_ids if item in components]
        missing = [item for item in requested_ids if item not in components]
        limitations: list[dict[str, Any]] = []
        if missing:
            limitations.append({"code": "spend_component_unavailable", "component_ids": missing})
        numerator = float(sum(item["amount_rub"] for item in selected))
        denominator: float | int | None = None
        mode = model["mode"]
        basis = "actual" if mode == "crm_attributed" else "estimate" if mode == "crm_share_estimate" else "tracked_proxy"
        if mode == "crm_attributed":
            denominator_method: dict[str, Any] = {
                "type": "crm_attributed",
                "record_unit": record_unit,
                "source_norm": model["crm_source"],
            }
        elif mode == "crm_share_estimate":
            denominator_method = {
                "type": "crm_share_estimate",
                "record_unit": record_unit,
                "crm_records": crm.get("record_count"),
                "crm_share": model["crm_share"],
            }
        else:
            denominator_method = {
                "type": "tracked_funnel_unique_visits",
                "funnel": model["funnel"],
                "stage": model["stage"],
                "traffic_filter": model.get("traffic_filter"),
            }

        if record_unit == "unknown" and mode != "tracked_funnel":
            limitations.append({"code": "crm_record_unit_unknown"})
        elif crm.get("status") == "unavailable" and mode != "tracked_funnel":
            limitations.extend(crm.get("limitations") or [])
        elif mode == "crm_attributed":
            if crm_config.get("attribution_reliable") is not True:
                limitations.append({"code": "crm_attribution_unreliable"})
            else:
                denominator, issue = _crm_attributed_count(paths, model["crm_source"])
                if issue:
                    limitations.append(issue)
        elif mode == "crm_share_estimate":
            crm_count = crm.get("record_count")
            if isinstance(crm_count, int):
                denominator = crm_count * model["crm_share"]
        else:
            traffic_filter = model.get("traffic_filter")
            if model.get("channel") and traffic_filter is None:
                limitations.append({"code": "channel_tracked_filter_required", "channel": model["channel"]})
            elif traffic_filter is None:
                denominator = _funnel_stage_visits(
                    funnel_summary, model["funnel"], model["stage"]
                )
                if denominator is None:
                    limitations.append(
                        {"code": "funnel_stage_unavailable", "funnel": model["funnel"], "stage": model["stage"]}
                    )
            else:
                denominator, issue = _filtered_stage_visits(
                    paths, config, model["funnel"], model["stage"], traffic_filter
                )
                if issue:
                    limitations.append(issue)

        if denominator is not None and denominator <= 0:
            limitations.append({"code": "zero_denominator"})
        available = not limitations and denominator is not None and denominator > 0
        result_value = numerator / denominator if available else None
        result_unit = (
            "rub_per_tracked_conversion"
            if mode == "tracked_funnel"
            else _RESULT_UNITS.get(record_unit)
        )
        results.append({
            "id": model["id"],
            "mode": mode,
            "status": "ok" if available else "unavailable",
            "basis": basis,
            "result_name": _result_name(mode, record_unit, model.get("stage")),
            "numerator": {
                "money_basis": "gross_final_rub",
                "amount_rub": numerator,
                "components": selected,
            },
            "denominator": {"value": denominator, "method": denominator_method},
            "formula": "gross_spend_rub / denominator_records_or_visits",
            "value_rub": result_value,
            "unit": result_unit,
            "assumptions": (
                [{"code": "client_configured_crm_share", "value": model["crm_share"]}]
                if mode == "crm_share_estimate"
                else []
            ),
            "limitations": limitations,
        })

    return {
        "money_basis": "gross_final_rub",
        "crm": crm,
        "models": results,
    }


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать ``acquisition_economics.json`` независимо от ID проверок."""
    del runnable_ids
    config = _load_config(paths, defaults)
    result = compute_acquisition_economics(paths, config=config)
    common.write_json_atomic(Path(paths.metrics) / "acquisition_economics.json", result)
    return ["acquisition_economics.json"]
