"""Конфигурационные visit-level воронки по сохранённым целям Метрики."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from . import common


_SEGMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("month", "date"),
    ("channel", "source_group"),
    ("device", "device"),
    ("entry_page", "entry_page"),
)


def _load_config(paths: Any) -> dict[str, Any]:
    config_file = Path(paths.config_file)
    if not config_file.exists():
        return {}
    with config_file.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    return loaded if isinstance(loaded, dict) else {}


def _normalise_funnels(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_funnels = config.get("funnels")
    if not isinstance(raw_funnels, dict):
        return []

    result: list[dict[str, Any]] = []
    for funnel_id, raw_stages in raw_funnels.items():
        if not isinstance(funnel_id, str) or not funnel_id or not isinstance(raw_stages, list):
            continue
        stages: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for raw_stage in raw_stages:
            if not isinstance(raw_stage, dict):
                continue
            stage = raw_stage.get("stage")
            goal_ids = raw_stage.get("goal_ids")
            if not isinstance(stage, str) or not stage or stage in seen_names:
                continue
            if not isinstance(goal_ids, list):
                continue
            normalised_ids = list(dict.fromkeys(str(goal_id) for goal_id in goal_ids if goal_id is not None))
            if not normalised_ids:
                continue
            seen_names.add(stage)
            stages.append({"stage": stage, "goal_ids": normalised_ids})
        if stages:
            result.append({"funnel_id": funnel_id, "stages": stages})
    return result


def _month(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m")
    text = str(value)
    return text[:7] if len(text) >= 7 else text


def _table_columns(con: Any, table: str) -> set[str]:
    return {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}


def _load_visit_data(paths: Any) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, int]]]:
    con = common.open_duckdb(paths)
    try:
        visit_columns = _table_columns(con, "visits")
        if "visit_id" not in visit_columns:
            return {}, {}
        selected = [column for _, column in _SEGMENT_COLUMNS if column in visit_columns]
        select_sql = ", ".join(["visit_id", *(f'"{column}"' for column in selected)])
        visit_rows = con.execute(f"SELECT {select_sql} FROM visits").fetchall()
        visits: dict[str, dict[str, Any]] = {}
        for row in visit_rows:
            visit_id = str(row[0])
            values = dict(zip(selected, row[1:]))
            values["date"] = _month(values.get("date"))
            visits.setdefault(visit_id, values)

        goal_columns = _table_columns(con, "visit_goals")
        if not {"visit_id", "goal_id", "achievement_count"}.issubset(goal_columns):
            return visits, {}
        goal_rows = con.execute(
            "SELECT visit_id, goal_id, achievement_count FROM visit_goals"
        ).fetchall()
    finally:
        con.close()

    goals: dict[str, dict[str, int]] = defaultdict(dict)
    for visit_id, goal_id, achievement_count in goal_rows:
        key = str(visit_id)
        goal_key = str(goal_id)
        count = max(int(achievement_count or 0), 0)
        goals[key][goal_key] = goals[key].get(goal_key, 0) + count
    return visits, dict(goals)


def _load_goal_times(paths: Any) -> dict[str, dict[str, str]]:
    canonical = common.load_canonical(paths)
    if "goal_achievements" not in canonical:
        return {}

    con = common.open_duckdb(paths)
    try:
        columns = _table_columns(con, "goal_achievements")
        time_column = next(
            (
                candidate
                for candidate in (
                    "goal_datetime", "achievement_time", "event_time", "datetime", "date_time"
                )
                if candidate in columns
            ),
            None,
        )
        if time_column is None or not {"visit_id", "goal_id"}.issubset(columns):
            return {}
        rows = con.execute(
            f'SELECT visit_id, goal_id, "{time_column}" FROM goal_achievements '
            f'WHERE "{time_column}" IS NOT NULL'
        ).fetchall()
    finally:
        con.close()

    result: dict[str, dict[str, str]] = defaultdict(dict)
    for visit_id, goal_id, event_time in rows:
        visit_key = str(visit_id)
        goal_key = str(goal_id)
        time_key = event_time.isoformat() if hasattr(event_time, "isoformat") else str(event_time)
        current = result[visit_key].get(goal_key)
        if current is None or time_key < current:
            result[visit_key][goal_key] = time_key
    return dict(result)


def _stage_visit_ids(
    visit_ids: set[str], stages: list[dict[str, Any]], goals: dict[str, dict[str, int]]
) -> list[set[str]]:
    return [
        {
            visit_id
            for visit_id in visit_ids
            if any(goals.get(visit_id, {}).get(goal_id, 0) > 0 for goal_id in stage["goal_ids"])
        }
        for stage in stages
    ]


def _funnel_slice(
    visit_ids: set[str],
    stages: list[dict[str, Any]],
    goals: dict[str, dict[str, int]],
    goal_times: dict[str, dict[str, str]],
) -> dict[str, Any]:
    reached = _stage_visit_ids(visit_ids, stages, goals)
    stage_rows = [
        {"stage": stage["stage"], "visits": len(stage_visits)}
        for stage, stage_visits in zip(stages, reached)
    ]
    transitions: list[dict[str, Any]] = []
    for previous, following, previous_ids, following_ids in zip(
        stages, stages[1:], reached, reached[1:]
    ):
        continued = previous_ids & following_ids
        ordered_visits = 0
        out_of_order_visits = 0
        for visit_id in continued:
            previous_times = [
                goal_times.get(visit_id, {}).get(goal_id) for goal_id in previous["goal_ids"]
            ]
            following_times = [
                goal_times.get(visit_id, {}).get(goal_id) for goal_id in following["goal_ids"]
            ]
            previous_times = [value for value in previous_times if value is not None]
            following_times = [value for value in following_times if value is not None]
            if not previous_times or not following_times:
                continue
            if min(previous_times) <= min(following_times):
                ordered_visits += 1
            else:
                out_of_order_visits += 1
        transitions.append({
            "from_stage": previous["stage"],
            "to_stage": following["stage"],
            "continued_visits": len(continued),
            "transition_rate": round(len(continued) / len(previous_ids), 4) if previous_ids else None,
            "previous_without_next_visits": len(previous_ids - following_ids),
            "later_without_previous_visits": len(following_ids - previous_ids),
            "later_without_previous_interpretation": "tracking_anomaly_or_alternative_scenario",
            "event_order": {
                "status": "available" if ordered_visits + out_of_order_visits else "unavailable",
                "visits_with_timestamps": ordered_visits + out_of_order_visits,
                "ordered_visits": ordered_visits,
                "out_of_order_visits": out_of_order_visits,
            },
        })

    first_ids = reached[0]
    last_ids = reached[-1]
    completed = first_ids & last_ids
    return {
        "stages": stage_rows,
        "transitions": transitions,
        "first_to_last": {
            "from_stage": stages[0]["stage"],
            "to_stage": stages[-1]["stage"],
            "completed_visits": len(completed),
            "conversion_rate": round(len(completed) / len(first_ids), 4) if first_ids else None,
            "first_without_last_visits": len(first_ids - last_ids),
            "later_without_first_visits": len(last_ids - first_ids),
        },
    }


def compute_funnels(paths: Any) -> dict[str, Any]:
    """Посчитать все корректно настроенные воронки, не записывая артефакт."""
    funnel_configs = _normalise_funnels(_load_config(paths))
    if not funnel_configs:
        return {"status": "unavailable", "reason": "funnels не настроены", "funnels": []}

    canonical = common.load_canonical(paths)
    if "visits" not in canonical or "visit_goals" not in canonical:
        return {
            "status": "unavailable",
            "reason": "для воронок нужны canonical visits и visit_goals",
            "funnels": [],
        }

    visits, goals = _load_visit_data(paths)
    goal_times = _load_goal_times(paths)
    all_visit_ids = set(visits)
    results: list[dict[str, Any]] = []
    for funnel_config in funnel_configs:
        stages = funnel_config["stages"]
        overall = _funnel_slice(all_visit_ids, stages, goals, goal_times)
        repeat_by_stage: list[dict[str, Any]] = []
        for stage in stages:
            counts = [
                sum(goals.get(visit_id, {}).get(goal_id, 0) for goal_id in stage["goal_ids"])
                for visit_id in all_visit_ids
            ]
            repeat_by_stage.append({
                "stage": stage["stage"],
                "visits_with_repeats": sum(count > 1 for count in counts),
                "extra_achievements": sum(max(count - 1, 0) for count in counts),
            })

        segments: list[dict[str, Any]] = []
        for dimension, column in _SEGMENT_COLUMNS:
            values = sorted(
                {visits[visit_id].get(column) for visit_id in all_visit_ids}
                - {None},
                key=str,
            )
            for value in values:
                segment_visits = {
                    visit_id for visit_id in all_visit_ids if visits[visit_id].get(column) == value
                }
                segments.append({
                    "dimension": dimension,
                    "value": value,
                    **_funnel_slice(segment_visits, stages, goals, goal_times),
                })

        results.append({
            "funnel_id": funnel_config["funnel_id"],
            "stage_definitions": stages,
            **overall,
            "repeat_achievements": repeat_by_stage,
            "segments": segments,
        })
    return {"status": "ok", "funnels": results}


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать компактный funnels.json; параметры dispatch здесь не нужны."""
    del defaults, runnable_ids
    common.write_json_atomic(Path(paths.metrics) / "funnels.json", compute_funnels(paths))
    return ["funnels"]
