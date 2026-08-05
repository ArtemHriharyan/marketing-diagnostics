"""Конфигурационные visit-level воронки по сохранённым целям Метрики."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from . import common


# page_type — производное измерение: тип страницы входа по правилам клиента
# (config.yaml: page_types). Стоит рядом с entry_page, а не вместо него:
# entry_page отвечает на вопрос «какая страница», page_type — «какой класс
# страниц», и только второй вопрос имеет ответ при 28 тыс. сырых URL.
_PAGE_TYPE_DIMENSION = "page_type"
_ENTRY_PAGE_DIMENSION = "entry_page"

_SEGMENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("month", "date"),
    ("channel", "source_group"),
    ("device", "device"),
    (_PAGE_TYPE_DIMENSION, _PAGE_TYPE_DIMENSION),
    (_ENTRY_PAGE_DIMENSION, _ENTRY_PAGE_DIMENSION),
)

# Значение page_type для URL, не попавшего ни под одно правило клиента.
# Это НЕ пропуск: строка "other" присутствует в разрезе всегда, её доля —
# прямой измеритель полноты правил (высокая доля = правила неполны).
_PAGE_TYPE_OTHER = "other"

# PERF-1A. Значение разреза, не прошедшее отбор, не считается отдельной строкой:
# расчёт значения с единичными визитами не окупается, а объём артефакта при
# этом ломает байт-кап стадии analyze. Все непрошедшие значения схлопываются в
# одну строку, чтобы сумма по разрезу сходилась с итогом.
# SEG-FIX: порог отбора здесь — segment_compute_min_visits (порог РАСЧЁТА),
# а не min_sample_visits (порог достаточности выборки). Достаточность выборки
# проверяется позже, там, где сегмент становится кандидатом в находку.
_OTHER_VALUE = "__other__"

# Величины схлопнутой строки, которые нельзя получить сложением непрошедших
# значений (доли, а не счётчики). Пишем null, сумму не подставляем.
_NON_ADDITIVE_TRANSITION_KEY = "transition_rate"
_NON_ADDITIVE_FIRST_TO_LAST_KEY = "conversion_rate"

_DEFAULTS_FILE = Path(__file__).resolve().parents[2] / "config" / "defaults.yaml"

# PERF-1B. Артефакт слоя metrics: пишется один раз в `run()`, читается
# потребителями (block3 C06, seasonality, acquisition_economics) вместо
# повторного расчёта.
_ARTIFACT_NAME = "funnels.json"


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


def _normalise_page_types(config: dict[str, Any]) -> list[tuple[str, list[re.Pattern[str]]]]:
    """Правила page_type из конфига клиента: [(id, [скомпилированные regex])].

    Список УПОРЯДОЧЕН, выигрывает первое совпадение — порядок значим и живёт
    в конфиге клиента, а не в коде (принцип 1). Записи без id или без единого
    валидного шаблона пропускаются: битое правило не должно ронять расчёт
    (принцип 4), оно просто не участвует в классификации.
    """
    raw = config.get("page_types")
    if not isinstance(raw, list):
        return []

    rules: list[tuple[str, list[re.Pattern[str]]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        page_type = item.get("id")
        patterns = item.get("match")
        if not isinstance(page_type, str) or not page_type:
            continue
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list):
            continue
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                continue
            try:
                compiled.append(re.compile(pattern))
            except re.error:
                continue
        if compiled:
            rules.append((page_type, compiled))
    return rules


def _classify_page_type(
    value: Any, rules: list[tuple[str, list[re.Pattern[str]]]]
) -> str | None:
    """Тип страницы входа по правилам клиента; None — самой страницы нет.

    Непопавший под правила URL получает "other", а не пропуск: разрез обязан
    покрывать все визиты с известной страницей входа.
    """
    if value is None or value != value:  # None и NaN — «страницы входа нет»
        return None
    text = str(value)
    for page_type, patterns in rules:
        if any(pattern.search(text) for pattern in patterns):
            return page_type
    return _PAGE_TYPE_OTHER


def _month(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m")
    text = str(value)
    return text[:7] if len(text) >= 7 else text


def _table_columns(con: Any, table: str) -> set[str]:
    return {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}


def _load_visit_data(
    paths: Any, page_type_rules: list[tuple[str, list[re.Pattern[str]]]] | None = None
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, int]]]:
    con = common.open_duckdb(paths)
    try:
        visit_columns = _table_columns(con, "visits")
        if "visit_id" not in visit_columns:
            return {}, {}
        selected = [column for _, column in _SEGMENT_COLUMNS if column in visit_columns]
        select_sql = ", ".join(["visit_id", *(f'"{column}"' for column in selected)])
        visit_rows = con.execute(f"SELECT {select_sql} FROM visits").fetchall()
        # page_type считается из entry_page по правилам конфига — отдельной
        # колонки в canonical нет и не должно быть (это разрез слоя compute).
        # Без правил в config.yaml разреза page_type у клиента просто нет:
        # классифицировать URL в коде нечем (принцип 1).
        rules = page_type_rules or []
        derive_page_type = bool(rules) and (
            _ENTRY_PAGE_DIMENSION in selected and _PAGE_TYPE_DIMENSION not in selected
        )
        visits: dict[str, dict[str, Any]] = {}
        for row in visit_rows:
            visit_id = str(row[0])
            values = dict(zip(selected, row[1:]))
            values["date"] = _month(values.get("date"))
            if derive_page_type:
                values[_PAGE_TYPE_DIMENSION] = _classify_page_type(
                    values.get(_ENTRY_PAGE_DIMENSION), rules
                )
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


def _load_defaults(defaults: dict[str, Any] | None) -> dict[str, Any]:
    """Пороги отбора сегментов: из dispatch, иначе прямо из config/defaults.yaml.

    В обычном прогоне расчёт один (PERF-1B), но запасной путь `load_funnels`
    вызывает `compute_funnels` без defaults — политика разреза при этом обязана
    совпасть с той, что применил бы dispatch.
    """
    if isinstance(defaults, dict) and defaults:
        return defaults
    if not _DEFAULTS_FILE.exists():
        return {}
    with _DEFAULTS_FILE.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    return loaded if isinstance(loaded, dict) else {}


def _segment_policy(defaults: dict[str, Any] | None) -> dict[str, Any]:
    """Правило отбора значений разреза (config/defaults.yaml, принцип 1).

    `min_visits` — порог РАСЧЁТА (`segment_compute_min_visits`), не порог
    достаточности выборки (`min_sample_visits`): см. комментарий в
    config/defaults.yaml. Старый ключ `segment_min_visits` не читается.
    """
    values = _load_defaults(defaults)
    dimensions = values.get("segment_filtered_dimensions")
    return {
        "min_visits": int(values.get("segment_compute_min_visits", 30)),
        "max_values": int(values.get("segment_max_values", 2000)),
        "coverage_target": float(values.get("segment_coverage_target", 0.80)),
        "entry_page_top_per_type": int(
            values.get("segment_entry_page_top_per_type", 1500)
        ),
        "dimensions": sorted(
            str(item) for item in dimensions if isinstance(item, (str, int))
        ) if isinstance(dimensions, list) else [],
    }


def _segment_counts(
    visits: dict[str, dict[str, Any]], visit_ids: set[str], column: str
) -> dict[Any, int]:
    """Визиты на каждое значение разреза одним проходом (вход для отбора)."""
    counts: dict[Any, int] = defaultdict(int)
    for visit_id in visit_ids:
        value = visits[visit_id].get(column)
        if value is None or value != value:  # None и NaN — «значения нет»
            continue
        counts[value] += 1
    return dict(counts)


def _select_segment_values(
    dimension: str,
    counts: dict[Any, int],
    policy: dict[str, Any],
    groups: dict[Any, str] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Отобрать значения разреза до входа в цикл; вернуть отбор и его покрытие.

    Берутся все значения от `segment_compute_min_visits` визитов и выше, по
    убыванию визитов, пока не исчерпан `segment_max_values`. Значения ниже
    порога расчёта схлопываются в `__other__`.

    `segment_coverage_target` — ЦЕЛЬ, а не правило остановки: раньше отбор
    прекращался при её достижении, и разрез с одной доминирующей страницей
    (pognali.rent: "/" даёт 61% визитов) сводился к двум строкам. Теперь
    достижение цели только фиксируется в манифесте (`coverage_target_met`).

    `groups` — принадлежность значения к производному классу (для entry_page —
    page_type). При заданном groups внутри каждого класса остаются только
    топ-`segment_entry_page_top_per_type` значений по визитам, чтобы один класс
    страниц не выбрал весь бюджет строк и не вытеснил остальные.

    Правило применяется только к разрезам из `segment_filtered_dimensions`.
    """
    visits_total = sum(counts.values())
    rule_applied = dimension in policy["dimensions"]

    if not rule_applied:
        selected = list(counts)
        visits_selected = visits_total
        values_below_threshold = 0
        values_dropped_by_group_cap = 0
    else:
        ordered = [
            value for value in sorted(counts, key=lambda item: (-counts[item], str(item)))
            if counts[value] >= policy["min_visits"]
        ]
        values_below_threshold = len(counts) - len(ordered)
        if groups:
            per_type_cap = policy["entry_page_top_per_type"]
            taken_per_group: dict[str, int] = defaultdict(int)
            capped: list[Any] = []
            for value in ordered:
                group = groups.get(value, _PAGE_TYPE_OTHER)
                if taken_per_group[group] >= per_type_cap:
                    continue
                taken_per_group[group] += 1
                capped.append(value)
            values_dropped_by_group_cap = len(ordered) - len(capped)
            ordered = capped
        else:
            values_dropped_by_group_cap = 0
        selected = ordered[: policy["max_values"]]
        visits_selected = sum(counts[value] for value in selected)

    coverage_ratio = round(visits_selected / visits_total, 4) if visits_total else None
    return sorted(selected, key=str), {
        "dimension": dimension,
        "rule_applied": rule_applied,
        "values_total": len(counts),
        "values_selected": len(selected),
        "values_collapsed": len(counts) - len(selected),
        "values_below_compute_threshold": values_below_threshold,
        "values_dropped_by_group_cap": values_dropped_by_group_cap,
        "visits_total": visits_total,
        "visits_selected": visits_selected,
        "visits_collapsed": visits_total - visits_selected,
        "coverage_ratio": coverage_ratio,
        "coverage_target_met": (
            coverage_ratio >= policy["coverage_target"] if coverage_ratio is not None else None
        ),
    }


def _segment_members(
    visits: dict[str, dict[str, Any]],
    visit_ids: set[str],
    column: str,
    selected: list[Any],
) -> tuple[dict[Any, set[str]], set[str]]:
    """Визиты отобранных значений и общий остаток для строки "__other__"."""
    members: dict[Any, set[str]] = {value: set() for value in selected}
    other: set[str] = set()
    for visit_id in visit_ids:
        value = visits[visit_id].get(column)
        if value is None or value != value:
            continue
        if value in members:
            members[value].add(visit_id)
        else:
            other.add(visit_id)
    return members, other


def _blank_non_additive(slice_result: dict[str, Any]) -> dict[str, Any]:
    """Обнулить неаддитивные доли схлопнутой строки: null вместо суммы."""
    for transition in slice_result["transitions"]:
        transition[_NON_ADDITIVE_TRANSITION_KEY] = None
    slice_result["first_to_last"][_NON_ADDITIVE_FIRST_TO_LAST_KEY] = None
    return slice_result


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


def compute_funnels(paths: Any, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Посчитать все корректно настроенные воронки, не записывая артефакт."""
    client_config = _load_config(paths)
    funnel_configs = _normalise_funnels(client_config)
    if not funnel_configs:
        return {"status": "unavailable", "reason": "funnels не настроены", "funnels": []}

    canonical = common.load_canonical(paths)
    if "visits" not in canonical or "visit_goals" not in canonical:
        return {
            "status": "unavailable",
            "reason": "для воронок нужны canonical visits и visit_goals",
            "funnels": [],
        }

    page_type_rules = _normalise_page_types(client_config)
    visits, goals = _load_visit_data(paths, page_type_rules)
    goal_times = _load_goal_times(paths)
    all_visit_ids = set(visits)

    # Отбор значений разреза не зависит от воронки — считается один раз.
    policy = _segment_policy(defaults)
    segment_plan: list[tuple[str, list[Any], dict[Any, set[str]], set[str], dict[str, Any]]] = []
    for dimension, column in _SEGMENT_COLUMNS:
        counts = _segment_counts(visits, all_visit_ids, column)
        if dimension == _PAGE_TYPE_DIMENSION and page_type_rules:
            # Строка "other" присутствует в разрезе всегда, даже нулевая: её
            # доля — измеритель полноты правил клиента, и отсутствие строки
            # нельзя отличить от «правила не проверялись».
            counts.setdefault(_PAGE_TYPE_OTHER, 0)
        groups = None
        if dimension == _ENTRY_PAGE_DIMENSION and page_type_rules:
            # entry_page — drill-down внутри page_type: бюджет строк делится
            # между типами страниц, а не достаётся самому крупному из них.
            groups = {
                value: _classify_page_type(value, page_type_rules) or _PAGE_TYPE_OTHER
                for value in counts
            }
        selected, coverage = _select_segment_values(dimension, counts, policy, groups)
        members, other_ids = _segment_members(visits, all_visit_ids, column, selected)
        segment_plan.append((dimension, selected, members, other_ids, coverage))
    segment_coverage = [coverage for *_, coverage in segment_plan]

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
        for dimension, values, members, other_ids, coverage in segment_plan:
            for value in values:
                segments.append({
                    "dimension": dimension,
                    "value": value,
                    **_funnel_slice(members[value], stages, goals, goal_times),
                })
            if other_ids:
                segments.append({
                    "dimension": dimension,
                    "value": _OTHER_VALUE,
                    "collapsed_values": coverage["values_collapsed"],
                    "collapsed_visits": coverage["visits_collapsed"],
                    **_blank_non_additive(
                        _funnel_slice(other_ids, stages, goals, goal_times)
                    ),
                })

        results.append({
            "funnel_id": funnel_config["funnel_id"],
            "stage_definitions": stages,
            **overall,
            "repeat_achievements": repeat_by_stage,
            "segment_coverage": [dict(record) for record in segment_coverage],
            "segments": segments,
        })
    return {
        "status": "ok",
        "funnels": results,
        # Оговорка о покрытии разреза: сколько значений было, сколько отобрано,
        # какая доля визитов покрыта отобранными. Должна доехать до отчёта.
        "segment_selection": {
            "policy": policy,
            "dimensions": [dict(record) for record in segment_coverage],
        },
    }


def load_funnels(paths: Any, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Вернуть воронки прогона: артефакт `funnels.json`, иначе расчёт.

    Единственная точка входа для потребителей внутри compute (block3 C06,
    seasonality). Воронки считает `run()` — он идёт в dispatch раньше своих
    потребителей, поэтому в обычном прогоне здесь только чтение готового
    артефакта своего же слоя, без второго и третьего `compute_funnels`
    (PERF-1B: три вызова были идентичны по параметрам, ≈4.1 с чистого дубля).

    Расчёт остаётся запасным путём для случаев, когда артефакта нет: блок
    `funnels` упал или модуль вызван вне dispatch (тесты, отладка). Принцип 4 —
    отсутствие артефакта не роняет потребителя.
    """
    artifact = Path(paths.metrics) / _ARTIFACT_NAME
    try:
        loaded = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = None
    if isinstance(loaded, dict) and isinstance(loaded.get("status"), str):
        return loaded
    return compute_funnels(paths, defaults)


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать компактный funnels.json; runnable_ids здесь не нужны."""
    del runnable_ids
    common.write_json_atomic(
        Path(paths.metrics) / _ARTIFACT_NAME, compute_funnels(paths, defaults)
    )
    return ["funnels"]
