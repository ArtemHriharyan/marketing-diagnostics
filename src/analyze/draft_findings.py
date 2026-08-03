"""Генерация черновиков находок из метрик и качественных входов.

Контракт:
    Читает   — analysis_candidates.json и компактные metrics-артефакты,
               degradation_report.json, заполненные inputs/*.yaml,
               config/methodology.yaml (названия проверок для привязки находок).
    Пишет    — findings/draft/*.yaml. Каждая находка: id проверки, формулировка
               (русский), опора на числа, уровень уверенности
               (client-HIGH / MED / …), рекомендация.
    LLM      — ДА, только здесь. Модель формулирует текст находки поверх уже
               посчитанных детерминированных чисел; сами числа не выдумываются.
    Гейт     — вывод идёт в draft/, НЕ в approved/. Перенос — ручной, аналитиком.

    ПОТОЛКИ (обязательны к соблюдению; LLM их только понижает, не повышает):
      * degradation_report.checks[*].confidence_cap — верхняя граница уверенности
        находки по этой проверке (MED, если задействован manual-источник). Это
        второй потолок поверх исходного из compute: итоговая уверенность =
        min(compute-уверенность, confidence_cap).
      * degradation_report.checks[*].type_effective — тип находки (A|B|Q) уже
        после пост-хок понижения; analyze берёт его как есть, не «повышает».

Задача 6A дала детерминированную оболочку (без вызова API Anthropic):
    build_input_pack()   — собирает всё, что модель получит на вход: P06-кандидаты,
                            компактный контекст, inputs, деградацию (оба потолка
                            уверенности), контекст клиента, реестр check_id.
    build_system_prompt() — текст системного промта с запретами модели (см.
                            docstring функции).

Задача 6B подключает сам вызов модели (единственное место в пайплайне,
где это разрешено — принцип 3 CLAUDE.md):
    _call_llm()           — один структурированный вызов (text.format)
                            поверх input_pack; предсказуемый токен-бюджет
                            (LLM_MAX_TOKENS), без повторной генерации после
                            валидного ответа (ретраи — только транспортные,
                            через timeout/max_retries самого SDK).
    draft()               — собирает пакет, пишет его как аудиторский артефакт
                            (INPUT_PACK_ARTIFACT_NAME), вызывает модель и
                            записывает прошедшие schemas.validate_finding
                            находки как findings/draft/F-<блок>-<nn>.yaml
                            (не больше schemas.MAX_FINDINGS_PER_RUN).

    Модель, ключ API и base URL берутся из project env,
    а НЕ из clients/<name>/.env — секреты клиента (принцип 6 CLAUDE.md)
    относятся к источникам данных, а не к самому пайплайну.

    Глубокая программная проверка evidence находок (что цифры в evidence
    реально соответствуют metrics/degradation) — отдельная задача 6C; здесь
    только структурная валидация схемы (schemas.validate_finding).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from ..pipeline import orchestrator as orchestrator_mod
from . import schemas
from . import validate_findings as validate_findings_mod

# Служебные артефакты data/metrics/, которые не являются результатом
# конкретной проверки D/A/T/C/S и не входят в пакет "metrics" как есть —
# degradation разбирается отдельно (см. _load_degradation_report), summary
# не несёт бизнес-чисел (см. src/compute/common.py: build_metrics_summary)
# и в input pack не нужен.
_NON_FINDING_STATUSES = frozenset({"unavailable", "unavailable_for_cause"})
_DIAGNOSTIC_CONTEXT_MARKERS = frozenset({"channel_anomaly_context"})
_COMPACT_CONTEXT_ARTIFACTS = (
    "funnels", "cost_summary", "acquisition_economics", "seasonality",
)
_FUNNEL_SUMMARY_KEYS = ("totals", "transitions", "gaps", "anomalies")
_FUNNEL_ID_KEYS = ("id", "funnel_id", "name", "period")

# Ключи coverage, которые описывают качество сканирования артефактов самим
# пайплайном (QA), а не evidence для находок — в отправляемый пакет не идут.
_COVERAGE_TELEMETRY_KEYS = frozenset({"artifacts"})

# inputs/*.yaml, которые являются входом стадии extract, а не доказательной
# базой находки: стоп-слова Wordstat нужны при сборе частотностей, но модель
# по ним ничего не формулирует.
_NON_EVIDENCE_INPUTS = frozenset({"wordstat_stopwords"})

INPUT_PACK_ARTIFACT_NAME = "_analyze_input_pack.json"

# Дефолты на случай, если ключей нет в config/defaults.yaml
# (analyze_input_pack_byte_cap / analyze_input_pack_warn_bytes) — обоснование
# величин см. в комментарии к этим ключам в config/defaults.yaml.
INPUT_PACK_BYTE_CAP = 150_000
INPUT_PACK_WARN_BYTES = 120_000

# Последний эшелон byte-cap (см. _truncate_candidate_groups): сколько кандидатов
# группы уходит в модель поимённо и по какому полю выбирается top-N. Значения
# переопределяются ключами config/defaults.yaml (analyze_candidate_top_n /
# analyze_candidate_impact_keys); константы — только фолбэк, как у byte-cap.
CANDIDATE_TOP_N = 25
CANDIDATE_IMPACT_KEYS = (
    "payload.money_amount_rub",
    "money_amount_rub",
    "payload.loss_rub",
    "payload.cost_rub",
    "payload.gap_visits",
    "payload.visits",
    "payload.clicks",
    "payload.shows",
)

_LOGGER = logging.getLogger(__name__)


# ── Сбор входного пакета ────────────────────────────────────────────────────
def _load_json_artifact(paths: Any, stem: str) -> Any:
    """Безопасно прочитать один явно разрешённый компактный metrics-артефакт."""
    path = Path(paths.metrics) / f"{stem}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_source_metrics(paths: Any, check_ids: set[str]) -> dict[str, Any]:
    """Прочитать после LLM только check-файлы, нужные для evidence-сверки."""
    result: dict[str, Any] = {}
    for check_id in sorted(check_ids):
        if not schemas.is_valid_check_id_format(check_id):
            continue
        payload = _load_json_artifact(paths, check_id.lower())
        if payload is not None:
            result[check_id.lower()] = payload
    return result


def _prune_empty(value: Any) -> Any:
    """Удалить только незаполненные качественные inputs, сохранив 0 и false."""
    if isinstance(value, dict):
        result = {key: _prune_empty(item) for key, item in value.items()}
        return {key: item for key, item in result.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        result = [_prune_empty(item) for item in value]
        return [item for item in result if item not in (None, "", [], {})]
    return value


def _row_dict(columns: list[str], row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if isinstance(row, list):
        return {
            column: row[index] if index < len(row) else None
            for index, column in enumerate(columns)
        }
    return {}


def _candidate_rows(payload: Any) -> tuple[list[str], list[Any], list[int]]:
    """Вернуть columnar rows P06 и индексы строк-кандидатов."""
    if not isinstance(payload, dict):
        return [], [], []
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        return [], [], []
    if not isinstance(rows, list):
        return columns, [], []
    indexes: list[int] = []
    for index, row in enumerate(rows):
        decoded = _row_dict(columns, row)
        if decoded.get("row_role") == "candidate" or decoded.get("candidate") is True:
            indexes.append(index)
    return columns, rows, indexes


def _context_ref_tokens(value: Any) -> set[str]:
    """Нормализовать явные ссылки P06 без привязки к порядку строк."""
    if isinstance(value, list):
        tokens: set[str] = set()
        for item in value:
            tokens.update(_context_ref_tokens(item))
        return tokens
    if isinstance(value, dict):
        tokens = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        }
        for key in ("row_ref", "ref", "id"):
            if value.get(key) not in (None, ""):
                tokens.add(str(value[key]))
        artifact = value.get("artifact") or value.get("source_artifact")
        row_ref = value.get("row_ref") or value.get("ref")
        if artifact not in (None, "") and row_ref not in (None, ""):
            tokens.add(f"{artifact}:{row_ref}")
        return tokens
    if value not in (None, ""):
        return {str(value)}
    return set()


def _row_ref_tokens(row: dict[str, Any], index: int) -> set[str]:
    tokens = {str(index)}
    for key in ("row_ref", "ref", "id"):
        if row.get(key) not in (None, ""):
            tokens.add(str(row[key]))
    artifact = row.get("artifact") or row.get("source_artifact")
    row_ref = row.get("row_ref") or row.get("ref")
    if artifact not in (None, "") and row_ref not in (None, ""):
        tokens.add(f"{artifact}:{row_ref}")
    return tokens


def _flatten_candidate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Развернуть вложенные поля кандидата в стабильные dotted columns."""
    result: dict[str, Any] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                visit(value[key], f"{path}.{key}" if path else str(key))
            return
        result[path] = value

    visit(row, "")
    return result


def _drop_null_values(mapping: dict[str, Any]) -> dict[str, Any]:
    """Убрать ключи со значением null. Пустая строка, 0 и false — значимые.

    Union-схема analysis_candidates объединяет поля всех типов проверок, из-за
    чего каждая строка несёт сотни ключей со значением null, не несущих данных.
    Отсутствие ключа и null здесь равнозначны по смыслу, поэтому убираем ключ.
    """
    return {key: value for key, value in mapping.items() if value is not None}


def _group_candidate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Сгруппировать кандидатов по check_id+candidate_reason без потери сегментов.

    `common` отдаётся без null-ключей (см. _drop_null_values); из `segments`
    убираются колонки, пустые во ВСЕХ строках группы. Ни одно значимое
    (не-null) значение при этом не теряется.
    """
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.get("check_id"), row.get("candidate_reason"))
        grouped.setdefault(key, []).append(row)

    columns = ["check_id", "candidate_reason", "candidate_count", "common", "segments"]
    packed_rows: list[list[Any]] = []
    for (check_id, reason), group in grouped.items():
        flattened = []
        for row in group:
            item = _flatten_candidate_row(row)
            item.pop("check_id", None)
            item.pop("candidate_reason", None)
            flattened.append(item)
        all_columns = sorted({key for row in flattened for key in row})
        common: dict[str, Any] = {}
        segment_columns: list[str] = []
        for column in all_columns:
            values = [row.get(column) for row in flattened]
            fingerprints = {
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for value in values
            }
            if len(fingerprints) == 1:
                common[column] = values[0]
            elif any(value is not None for value in values):
                segment_columns.append(column)
        common = _drop_null_values(common)
        segments = {
            "columns": segment_columns,
            "rows": [
                [row.get(column) for column in segment_columns]
                for row in flattened
            ] if segment_columns else [],
        }
        packed_rows.append([check_id, reason, len(group), common, segments])
    return {"columns": columns, "rows": packed_rows}


def _project_analysis_candidates(
    payload: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Сгруппировать всех доступных кандидатов и отделить необязательный контекст."""
    if not isinstance(payload, dict):
        return {"columns": [], "rows": [], "coverage": {}}, [], 0
    columns, rows, indexes = _candidate_rows(payload)
    if not columns or not isinstance(rows, list):
        return {"columns": [], "rows": [], "coverage": payload.get("coverage") or {}}, [], 0

    candidate_refs: set[str] = set()
    for index in indexes:
        candidate_refs.update(
            _context_ref_tokens(_row_dict(columns, rows[index]).get("context_refs"))
        )
    candidate_rows: list[dict[str, Any]] = []
    context_rows: list[Any] = []
    excluded: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        decoded = _row_dict(columns, row)
        if index in indexes:
            if _is_unavailable_row(decoded):
                excluded.append({
                    **_candidate_ref(decoded, index), "reason": "unavailable_row",
                })
            else:
                candidate_rows.append(decoded)
        elif candidate_refs.intersection(_row_ref_tokens(decoded, index)):
            if not _is_unavailable_row(decoded):
                context_rows.append(_drop_null_values(decoded))

    result = _group_candidate_rows(candidate_rows)
    result["coverage"] = payload.get("coverage") or {}
    if context_rows:
        # Union-массив columns здесь не нужен: строк контекста мало, а полей в
        # union-схеме сотни, поэтому построчный словарь непустых полей заметно
        # компактнее columnar-представления с null-дырами.
        result["context"] = {"rows": context_rows}
    return result, excluded, len(indexes)


def _is_unavailable_row(row: dict[str, Any]) -> bool:
    return (
        row.get("status") in _NON_FINDING_STATUSES
        or row.get("finding") in _DIAGNOSTIC_CONTEXT_MARKERS
    )


def _candidate_ref(row: dict[str, Any], index: int) -> dict[str, Any]:
    result = {"row_index": index}
    for key in ("check_id", "artifact", "source_artifact", "row_ref"):
        if row.get(key) not in (None, ""):
            result[key] = row[key]
    return result


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _set_pack_size(pack: dict[str, Any], system_prompt: str) -> int:
    """Записать устойчивые размеры тела запроса и финального audit-артефакта."""
    audit = pack.setdefault("audit", {})
    for _ in range(8):
        input_size = _json_size(pack)
        final_size = _json_size({**pack, "system_prompt": system_prompt})
        if (
            audit.get("input_pack_bytes") == input_size
            and audit.get("final_serialized_bytes") == final_size
        ):
            break
        audit["input_pack_bytes"] = input_size
        audit["final_serialized_bytes"] = final_size
    return _json_size({**pack, "system_prompt": system_prompt})


def _refresh_coverage(pack: dict[str, Any], *, detected: int) -> None:
    """Пересчитать coverage по ФАКТИЧЕСКОМУ состоянию pack (после обрезки).

    Вызывается после `_truncate_candidate_groups` (см. `_apply_byte_cap`):
    `candidate_count` группы — исходный размер (до обрезки, не меняется),
    а реально отправленные поимённо кандидаты и свёрнутые в tail_aggregate —
    читаются из текущего segments каждой группы. Это та же арифметика, что и
    в `audit.truncated_candidate_groups` (обе секции читают один и тот же
    tail_aggregate), поэтому расхождения между ними невозможны.
    """
    coverage = pack["coverage"]
    candidates = pack.get("analysis_candidates") or {}
    columns = candidates.get("columns") or []
    rows = candidates.get("rows") or []
    check_index = columns.index("check_id") if "check_id" in columns else -1
    count_index = columns.index("candidate_count") if "candidate_count" in columns else -1
    segments_index = columns.index("segments") if "segments" in columns else -1

    available_total = 0
    aggregated_total = 0
    for row in rows:
        if not isinstance(row, list):
            continue
        if count_index >= 0 and len(row) > count_index:
            available_total += int(row[count_index] or 0)
        if segments_index >= 0 and len(row) > segments_index:
            segments = row[segments_index] if isinstance(row[segments_index], dict) else {}
            tail = segments.get("tail_aggregate") or {}
            aggregated_total += int(tail.get("truncated_candidates") or 0)

    coverage["candidates_detected"] = detected
    coverage["candidates_included"] = available_total - aggregated_total
    coverage["candidates_excluded"] = detected - available_total
    coverage["candidates_omitted"] = aggregated_total
    if aggregated_total:
        coverage["candidates_aggregated"] = aggregated_total
    else:
        coverage.pop("candidates_aggregated", None)
    coverage["candidate_groups"] = len(rows)
    check_ids = [
        row[check_index]
        for row in rows
        if isinstance(row, list) and check_index >= 0 and len(row) > check_index and row[check_index]
    ]
    coverage["included_check_ids"] = sorted(set(check_ids))
    coverage["included_blocks"] = sorted({check_id[0] for check_id in check_ids if check_id})


def _numeric(value: Any) -> float | None:
    """Число для ранжирования влияния. bool — не метрика, строки не приводим."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _group_impact_criterion(
    segments: dict[str, Any], impact_keys: list[str]
) -> tuple[str | None, int]:
    """Первое поле из списка влияния, реально различающее кандидатов группы."""
    columns = segments.get("columns") or []
    rows = segments.get("rows") or []
    for key in impact_keys:
        if key not in columns:
            continue
        index = columns.index(key)
        if any(
            len(row) > index and _numeric(row[index]) is not None
            for row in rows
        ):
            return key, index
    return None, -1


def _truncate_group(
    packed_row: list[Any], top_n: int, impact_keys: list[str]
) -> dict[str, Any] | None:
    """Оставить top-N кандидатов группы по влиянию, хвост свернуть в агрегат.

    Возвращает запись для audit либо None, если группа уже укладывается в N.
    Порядок полностью детерминирован: ранжирование по убыванию метрики влияния
    с тай-брейком по исходному индексу строки, а сами оставленные строки
    возвращаются в исходном порядке.
    """
    segments = packed_row[4] if isinstance(packed_row[4], dict) else {}
    rows = segments.get("rows") or []
    if len(rows) <= top_n:
        return None
    criterion, index = _group_impact_criterion(segments, impact_keys)
    if index >= 0:
        def rank(position: int) -> tuple[int, float, int]:
            value = _numeric(rows[position][index]) if len(rows[position]) > index else None
            return (0 if value is not None else 1, -(value or 0.0), position)

        order = sorted(range(len(rows)), key=rank)
    else:
        # Ни одного числового поля влияния: детерминированный фолбэк — исходный
        # порядок строк группы (он уже детерминирован в _group_candidate_rows).
        criterion = "row_order"
        order = list(range(len(rows)))
    kept = sorted(order[:top_n])
    dropped = sorted(order[top_n:])

    aggregate: dict[str, Any] = {
        "truncated_candidates": len(dropped),
        "criterion": criterion,
        "note": "список кандидатов группы неполный: показаны только top-N по влиянию",
    }
    if index >= 0:
        values = [
            value
            for position in dropped
            if (value := (
                _numeric(rows[position][index]) if len(rows[position]) > index else None
            )) is not None
        ]
        if values:
            aggregate["truncated_sum"] = sum(values)
            aggregate["truncated_min"] = min(values)
            aggregate["truncated_max"] = max(values)
    segments["rows"] = [rows[position] for position in kept]
    segments["tail_aggregate"] = aggregate
    return {
        "check_id": packed_row[0],
        "candidate_reason": packed_row[1],
        "candidates_sent": len(kept),
        "candidates_aggregated": len(dropped),
        "criterion": criterion,
    }


def _truncate_candidate_groups(
    pack: dict[str, Any],
    byte_cap: int,
    system_prompt: str,
    *,
    top_n: int,
    impact_keys: list[str],
) -> int:
    """Прогрессивная обрезка кандидатов до попадания пакета в cap.

    Последний эшелон: вызывается, только когда весь опциональный контекст уже
    снят, а пакет всё ещё не влезает.

    Проход 1 — top_n применяется разом ко ВСЕМ группам (не только к тем, что
    превышают его по отдельности): клиент с сотнями мелких групп, ни одна из
    которых не превышает top_n, тоже должен пройти через снижение N, а не
    пропустить обрезку целиком.

    Если после этого пакет всё ещё вне cap, N снижается одинаковыми шагами
    сразу для ВСЕХ групп (не по одной группе за раз — иначе порядок обработки
    влиял бы на промежуточный результат при одинаковом финале), пока пакет не
    уложится либо N не дойдёт до 0 — тогда группа схлопывается целиком в один
    агрегат (`tail_aggregate` на всю группу, 0 кандидатов в segments.rows).

    Каждый уровень N применяется заново к ИСХОДНОМУ (до обрезки) состоянию
    группы, а не к уже обрезанному — иначе повторное снижение теряло бы
    данные для агрегата (сумма/мин/макс отброшенного хвоста).

    Порядок групп стабилен: сортировка по (check_id, candidate_reason).
    """
    rows = (pack.get("analysis_candidates") or {}).get("rows") or []
    if not rows:
        return _set_pack_size(pack, system_prompt)

    order = sorted(
        range(len(rows)),
        key=lambda position: (str(rows[position][0]), str(rows[position][1])),
    )
    original_segments = {
        position: copy.deepcopy(rows[position][4] if isinstance(rows[position][4], dict) else {})
        for position in order
    }

    def apply_n(n: int) -> dict[int, dict[str, Any]]:
        entries: dict[int, dict[str, Any]] = {}
        for position in order:
            packed_row = rows[position]
            packed_row[4] = copy.deepcopy(original_segments[position])
            entry = _truncate_group(packed_row, n, impact_keys)
            if entry is not None:
                entries[position] = entry
        return entries

    def apply_and_size(n: int) -> int:
        # audit.truncated_candidate_groups обновляется ДО замера размера —
        # иначе возвращаемый final_size не учитывал бы вес самого audit-списка
        # (для сотен групп это не мелочь) и мог соврать о фактической
        # укладке пакета в cap.
        entries = apply_n(n)
        pack["audit"]["truncated_candidate_groups"] = [
            entries[position] for position in order if position in entries
        ]
        return _set_pack_size(pack, system_prompt)

    final_size = apply_and_size(top_n)

    n = top_n
    while final_size >= byte_cap and n > 0:
        n = n // 2
        final_size = apply_and_size(n)

    return final_size


def _apply_byte_cap(
    pack: dict[str, Any],
    byte_cap: int,
    system_prompt: str,
    *,
    detected: int,
    defaults: dict[str, Any] | None = None,
) -> None:
    """Сначала зарезервировать всех кандидатов, затем урезать только контекст."""
    pack["audit"]["byte_cap_exceeded"] = False
    pack["audit"]["omitted_context"] = []
    pack["audit"]["truncated_candidate_groups"] = []
    final_size = _set_pack_size(pack, system_prompt)

    candidates = pack.get("analysis_candidates") or {}
    if final_size >= byte_cap and candidates.pop("context", None) is not None:
        pack["audit"]["omitted_context"].append("analysis_candidates.context")
        final_size = _set_pack_size(pack, system_prompt)

    optional_sections: list[tuple[int, str, dict[str, Any]]] = []
    for name, value in (pack.get("compact_context") or {}).items():
        optional_sections.append((_json_size(value), f"compact_context.{name}", pack["compact_context"]))
    for name, value in (pack.get("inputs") or {}).items():
        optional_sections.append((_json_size(value), f"inputs.{name}", pack["inputs"]))
    for _, dotted_name, container in sorted(optional_sections, key=lambda item: (-item[0], item[1])):
        if final_size < byte_cap:
            break
        key = dotted_name.rsplit(".", 1)[1]
        if container.pop(key, None) is not None:
            pack["audit"]["omitted_context"].append(dotted_name)
            final_size = _set_pack_size(pack, system_prompt)

    if final_size >= byte_cap:
        final_size = _truncate_candidate_groups(
            pack,
            byte_cap,
            system_prompt,
            top_n=resolve_candidate_top_n(defaults),
            impact_keys=resolve_candidate_impact_keys(defaults),
        )

    # coverage пересчитывается ПОСЛЕ обрезки кандидатных групп — иначе
    # candidates_included/omitted описывали бы пакет ДО обрезки, хотя в
    # модель и в аудиторский артефакт уходит уже обрезанное состояние.
    _refresh_coverage(pack, detected=detected)
    final_size = _set_pack_size(pack, system_prompt)

    pack["audit"]["byte_cap_exceeded"] = final_size >= byte_cap
    final_size = _set_pack_size(pack, system_prompt)
    if final_size >= byte_cap:
        raise ValueError(
            "конфигурационная ошибка: даже полное схлопывание всех групп "
            "кандидатов в агрегаты (прогрессивное снижение top-N до 0) не "
            "укладывает пакет analyze в byte-cap — поднимите "
            "analyze_input_pack_byte_cap"
        )


def _compact_degradation(
    report: dict[str, Any], used_check_ids: set[str] | None = None
) -> dict[str, Any]:
    """Компактная таблица деградации; при заданном used_check_ids — только по ним.

    `counts` остаётся общерегистровым: это сводка по всем 100 проверкам, она и
    показывает модели, что таблица сужена до задействованных проверок прогона.
    """
    columns = ["check_id", "runnable", "type_effective", "confidence_cap", "reason"]
    rows = [
        [
            check.get("check_id"), check.get("runnable"), check.get("type_effective"),
            check.get("confidence_cap"), check.get("reason_if_not_runnable"),
        ]
        for check in report.get("checks") or []
        if used_check_ids is None or check.get("check_id") in used_check_ids
    ]
    return {"columns": columns, "rows": rows, "counts": report.get("counts") or {}}


def _not_runnable_count(report: dict[str, Any]) -> int:
    """Сколько проверок реестра ушло в деградацию (runnable=false)."""
    return sum(1 for check in report.get("checks") or [] if not check.get("runnable"))


def _used_check_ids(analysis_candidates: dict[str, Any]) -> set[str]:
    """check_id, реально присутствующие в кандидатах этого прогона.

    Именно по этому множеству сужаются реестры пакета (check_names,
    degradation, source_cap_by_check, known_check_ids): проверки, по которым в
    прогоне нет ни одного кандидата, находкой стать всё равно не могут.
    Компенсация сужения — client_context.checks_total /
    client_context.checks_not_runnable, чтобы охват был виден явно.
    """
    columns = analysis_candidates.get("columns") or []
    if "check_id" not in columns:
        return set()
    index = columns.index("check_id")
    return {
        row[index]
        for row in analysis_candidates.get("rows") or []
        if isinstance(row, list) and len(row) > index and row[index]
    }


def _columnar_dict_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decoded = [
        {key: _compact_funnel_value(value) for key, value in row.items()}
        for row in rows
    ]
    columns = sorted({key for row in decoded for key in row})
    return {
        "columns": columns,
        "rows": [[row.get(column) for column in columns] for row in decoded],
    }


def _compact_funnel_value(value: Any) -> Any:
    """Перевести повторяющиеся строки компактного funnel-summary в columns+rows."""
    if isinstance(value, dict):
        return {key: _compact_funnel_value(item) for key, item in value.items()}
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return _columnar_dict_rows(value)
    if isinstance(value, list):
        return [_compact_funnel_value(item) for item in value]
    return value


def _compact_funnels(payload: Any) -> Any:
    """Оставить только totals/transitions/gaps/anomalies и идентификаторы воронок."""
    if not isinstance(payload, dict):
        return {}

    def project_funnel(value: Any) -> Any:
        if isinstance(value, list):
            rows = [project_funnel(item) for item in value if isinstance(item, dict)]
            return _columnar_dict_rows(rows) if rows else []
        if not isinstance(value, dict):
            return value
        projected = {
            key: value[key]
            for key in _FUNNEL_ID_KEYS
            if value.get(key) not in (None, "")
        }
        for key in _FUNNEL_SUMMARY_KEYS:
            if key in value:
                projected[key] = _compact_funnel_value(value[key])
        return projected

    result = {
        key: value
        for key, value in payload.items()
        if key not in {"funnels", *_FUNNEL_SUMMARY_KEYS}
        and not isinstance(value, (dict, list))
    }
    if "funnels" in payload:
        funnels = payload["funnels"]
        if isinstance(funnels, dict):
            result["funnels"] = {
                key: project_funnel(value) for key, value in funnels.items()
            }
        else:
            result["funnels"] = project_funnel(funnels)
    for key in _FUNNEL_SUMMARY_KEYS:
        if key in payload:
            result[key] = _compact_funnel_value(payload[key])
    return result


def _load_inputs(paths: Any) -> dict[str, Any]:
    """Прочитать все inputs/*.yaml клиента как {имя_файла_без_расширения: данные}."""
    inputs_dir = Path(paths.inputs)
    result: dict[str, Any] = {}
    if not inputs_dir.exists():
        return result
    for p in sorted(inputs_dir.glob("*.yaml")):
        with p.open("r", encoding="utf-8") as fh:
            value = _prune_empty(yaml.safe_load(fh) or {})
            if value not in (None, "", [], {}):
                result[p.stem] = value
    return result


def _load_degradation_report(paths: Any) -> dict[str, Any]:
    """Прочитать data/metrics/degradation_report.json; отсутствие -> пустой отчёт."""
    path = Path(paths.metrics) / "degradation_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _confidence_caps(degradation_report: dict[str, Any]) -> dict[str, str]:
    """{check_id: confidence_cap} — потолок №2 (источник), см. schemas.validate_finding."""
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (degradation_report.get("checks") or [])
        if c.get("check_id")
    }


def _client_context(
    config: dict[str, Any],
    *,
    checks_total: int = 0,
    checks_not_runnable: int = 0,
) -> dict[str, Any]:
    """Контекст клиента (ниша/гео/бренд/окно анализа) — не бизнес-числа, а рамка отчёта.

    `checks_total` / `checks_not_runnable` — охват реестра целиком, поверх
    которого реестры пакета сужены до задействованных проверок (см.
    _used_check_ids): сужение должно быть видно модели явно, а не выглядеть
    полным охватом.
    """
    config = config or {}
    client = config.get("client") or {}
    return {
        "name": client.get("name"),
        "niche": client.get("niche"),
        "geo": client.get("geo"),
        "brand_terms": config.get("brand_terms") or [],
        "data_window": config.get("data_window") or {},
        "checks_total": checks_total,
        "checks_not_runnable": checks_not_runnable,
    }


def resolve_byte_cap(defaults: dict[str, Any] | None = None) -> int:
    """Байтовый потолок отправляемого пакета: defaults.yaml, иначе константа."""
    return int((defaults or {}).get("analyze_input_pack_byte_cap") or INPUT_PACK_BYTE_CAP)


def resolve_warn_bytes(defaults: dict[str, Any] | None = None) -> int:
    """Порог предупреждения о размере пакета: defaults.yaml, иначе константа."""
    return int((defaults or {}).get("analyze_input_pack_warn_bytes") or INPUT_PACK_WARN_BYTES)


def resolve_candidate_top_n(defaults: dict[str, Any] | None = None) -> int:
    """Сколько кандидатов группы отправляется поимённо при последней обрезке.

    Явный `analyze_candidate_top_n: 0` в конфиге — предельное сжатие (группа
    сразу схлопывается в один агрегат), а не отсутствие настройки: `value or
    default` не отличает «ключа нет» от «ключ есть и равен 0» (0 — falsy),
    поэтому наличие ключа проверяется явно, а не через истинность значения.
    """
    defaults = defaults or {}
    if "analyze_candidate_top_n" not in defaults or defaults["analyze_candidate_top_n"] is None:
        return CANDIDATE_TOP_N
    return max(0, int(defaults["analyze_candidate_top_n"]))


def resolve_candidate_impact_keys(defaults: dict[str, Any] | None = None) -> list[str]:
    """Приоритет полей влияния, по которым выбирается top-N кандидатов группы."""
    value = (defaults or {}).get("analyze_candidate_impact_keys")
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    return list(CANDIDATE_IMPACT_KEYS)


def build_input_pack(
    paths: Any,
    config: dict[str, Any],
    methodology: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    *,
    return_full: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    """Собрать компактный пакет P06-кандидатов, контекста и ограничений.

    Возвращает **send_pack** — то, что уходит в модель и пишется как
    аудиторский артефакт (INPUT_PACK_ARTIFACT_NAME): уже после проекций-сжатий
    и после применения byte-cap.

    ``return_full=True`` дополнительно отдаёт **full_pack** — те же секции до
    любых проекций и до byte-cap. Он нужен только как корпус для сверки чисел
    находки (см. build_validation_corpus): валидация не имеет права зависеть от
    того, сколько данных поместилось в отправляемый пакет. В модель и в
    аудиторский артефакт full_pack не попадает.

    Пакет должен быть JSON-сериализуем целиком (это и есть будущее тело запроса
    к API) — используются только примитивы, списки и словари, никаких
    объектов Path/datetime и т.п.
    """
    defaults = defaults or {}
    degradation_report = _load_degradation_report(paths)
    raw_candidates = _load_json_artifact(paths, "analysis_candidates") or {
        "columns": [], "rows": [], "coverage": {}
    }
    raw_context = {
        stem: payload
        for stem in _COMPACT_CONTEXT_ARTIFACTS
        if (payload := _load_json_artifact(paths, stem)) is not None
    }
    inputs = _load_inputs(paths)
    analysis_candidates, excluded_candidates, detected_candidates = _project_analysis_candidates(
        raw_candidates
    )
    compact_context = {
        stem: _compact_funnels(payload) if stem == "funnels" else payload
        for stem, payload in raw_context.items()
    }
    byte_cap = resolve_byte_cap(defaults)
    used_check_ids = _used_check_ids(analysis_candidates)
    # coverage переезжает в единственное место — pack["coverage"]; телеметрия
    # сканирования артефактов (artifacts) — это QA пайплайна, не evidence.
    source_coverage = {
        key: value
        for key, value in (analysis_candidates.pop("coverage", None) or {}).items()
        if key not in _COVERAGE_TELEMETRY_KEYS
    }
    all_check_names = {
        c.get("id"): c.get("name")
        for c in (methodology.get("checks") or [])
        if c.get("id")
    }
    all_known_check_ids = sorted(schemas.known_check_ids(methodology))
    all_source_caps = _confidence_caps(degradation_report)
    common_sections = {
        "client_context": _client_context(
            config,
            checks_total=len(all_check_names),
            checks_not_runnable=_not_runnable_count(degradation_report),
        ),
        "check_names": {
            check_id: name
            for check_id, name in all_check_names.items()
            if check_id in used_check_ids
        },
        "known_check_ids": [
            check_id for check_id in all_known_check_ids if check_id in used_check_ids
        ],
        "constraints": {
            "sample_size_rule": {
                "min_sample_visits": defaults.get("min_sample_visits"),
                "significance_alpha": defaults.get("significance_alpha"),
            },
            "source_cap_by_check": {
                check_id: cap
                for check_id, cap in all_source_caps.items()
                if check_id in used_check_ids
            },
            "money_categories": dict(schemas.MONEY_CATEGORIES),
            "max_findings_per_run": schemas.MAX_FINDINGS_PER_RUN,
            "currency_round": defaults.get("currency_round", 0),
        },
    }
    pack = {
        **copy.deepcopy(common_sections),
        "analysis_candidates": analysis_candidates,
        "compact_context": compact_context,
        "inputs": {
            name: value
            for name, value in inputs.items()
            if name not in _NON_EVIDENCE_INPUTS
        },
        "degradation": _compact_degradation(degradation_report, used_check_ids),
        "coverage": source_coverage,
        "excluded_candidates": excluded_candidates,
        "audit": {"byte_cap": byte_cap, "warn_bytes": resolve_warn_bytes(defaults)},
    }
    # full_pack — корпус сверки assumptions, он обязан оставаться полным:
    # ни сужение реестров, ни отбор inputs на него не распространяются.
    full_pack = {
        "client_context": _client_context(
            config,
            checks_total=len(all_check_names),
            checks_not_runnable=_not_runnable_count(degradation_report),
        ),
        "check_names": all_check_names,
        "known_check_ids": all_known_check_ids,
        "constraints": {
            **common_sections["constraints"],
            "source_cap_by_check": all_source_caps,
        },
        "analysis_candidates": raw_candidates,
        "compact_context": raw_context,
        "inputs": inputs,
        "degradation": degradation_report,
    }
    # send_pack режется byte-cap'ом по месту — у full_pack должны быть свои
    # копии секций, иначе удаление секции из send_pack «схлопнет» и корпус.
    full_pack = copy.deepcopy(full_pack)

    system_prompt = build_system_prompt(defaults)
    _apply_byte_cap(
        pack, byte_cap, system_prompt, detected=detected_candidates, defaults=defaults
    )
    if return_full:
        return pack, full_pack
    return pack


def build_validation_corpus(full_pack: dict[str, Any]) -> dict[str, Any]:
    """Корпус для validate_finding_evidence(inputs=...) из ПОЛНОГО пакета.

    Числа в evidence и money_amount_rub по-прежнему сверяются с
    data/metrics/<check_id>.json (см. validate_findings.py); этот корпус
    подтверждает только числа в assumptions, поэтому он обязан браться из
    full_pack: иначе любое сжатие отправляемого пакета молча ужесточало бы
    валидацию и отбраковывало корректные находки.
    """
    return {
        **(full_pack.get("inputs") or {}),
        "analysis_candidates": full_pack.get("analysis_candidates") or {},
        "compact_context": full_pack.get("compact_context") or {},
    }


# ── Системный промт ─────────────────────────────────────────────────────────
def build_system_prompt(defaults: dict[str, Any] | None = None) -> str:
    """Текст системного промта будущего вызова модели (сам вызов — вне этой задачи).

    Запреты (обязательны, независимо от формулировки конкретной находки):
      1. Числа только из входного пакета — никаких досчётов, оценок «на глаз»
         или цифр из общих знаний о рынке/отрасли.
      2. significant=false запрещено: незначимая разница не становится находкой
         ни при каком confidence (см. sample_size_rule во входном пакете).
      3. Процентные пункты (п.п.) и проценты (%) не путать: разница долей —
         это п.п., а не «рост на N%».
      4. Денежные категории (4 шт., см. money_categories) не смешивать в одну
         сумму — у находки ровно одна категория либо money_not_assessable=true.
      5. Не больше max_findings_per_run находок после объединения сегментов.
      6. Без обвинений конкретных людей/менеджеров/подрядчика — только
         проверяемый факт в данных и рекомендация.
      7. confidence не выше меньшего из двух потолков (см.
         constraints во входном пакете); исключение — client-HIGH.
      8. check_id — только из known_check_ids входного пакета (новый реестр
         D/A/T/C/S), не из legacy-нумерации.
      9. Если у группы кандидатов проверки есть tail_aggregate — список по
         ней неполный: не достраивать и не экстраполировать недостающих
         кандидатов, не заявлять полноту охвата по этой проверке.
    """
    defaults = defaults or {}
    min_sample = defaults.get("min_sample_visits", 500)
    alpha = defaults.get("significance_alpha", 0.05)
    money_categories_text = "; ".join(
        f"{key} — {label}" for key, label in schemas.MONEY_CATEGORIES.items()
    )

    return (
        "Ты формулируешь черновики находок маркетинговой диагностики поверх уже "
        "посчитанных детерминированных чисел из входного пакета. Тебе запрещено:\n"
        "1. Использовать любые числа, которых нет во входном пакете (analysis_candidates/"
        "compact_context/inputs/degradation). Не досчитывать, не оценивать на глаз, "
        "не подставлять цифры "
        "из общих знаний о рынке или отрасли.\n"
        f"2. Публиковать находку с significant=false: различие с p >= {alpha} "
        f"(significance_alpha) или выборка < {min_sample} визитов (min_sample_visits) — "
        "не находка ни при каком confidence, как бы эффектно ни выглядело число.\n"
        "3. Путать процентные пункты (п.п.) и проценты (%): разница долей 12% и 15% — "
        "это 3 п.п., а не «рост на 25%». Проверяй формулировку каждой числовой разницы "
        "отдельно, до того как её записать.\n"
        "4. Смешивать денежные категории в одну сумму. Каждая денежная оценка относится "
        f"ровно к одной из четырёх категорий ({money_categories_text}) либо помечена "
        "money_not_assessable=true («в ₽ не оценить»). Складывать суммы из разных "
        "категорий в общий итог запрещено (каталог v2, правило 15).\n"
        f"5. После объединения повторяющихся сегментов одной проблемы формулировать больше "
        f"{schemas.MAX_FINDINGS_PER_RUN} находок за один прогон. Лимит применяется только "
        "после объединения.\n"
        "6. Обвинять конкретных людей, менеджеров, отдел продаж или подрядчика. Находка "
        "описывает проверяемый факт в данных и рекомендацию по нему, а не действия людей "
        "(каталог v2, §1 «не включено», §11 «что нельзя утверждать»).\n"
        "7. Повышать confidence выше любого из двух потолков (см. constraints "
        "входного пакета): потолок выборки (уже применён к кандидату) и потолок "
        "источника (confidence_cap проверки в degradation). Итоговая confidence — минимум "
        "из обоих. Исключение — client-HIGH: факт со слов клиента не подчиняется потолку "
        "источника.\n"
        "8. Придумывать check_id. Используй только ID из known_check_ids входного пакета "
        "(буква блока D/A/T/C/S + номер, напр. A07) — старые числовые ID (0.1, 2.2…) в "
        "этом слое не используются.\n"
        "9. Если у группы кандидатов проверки в analysis_candidates есть tail_aggregate — "
        "список кандидатов по ней неполный (обрезан byte-cap'ом). Не достраивай и не "
        "экстраполируй недостающих кандидатов, не заявляй в находке полный охват по этой "
        "проверке; если нужна сумма/диапазон по хвосту — используй truncated_sum/"
        "truncated_min/truncated_max явно как агрегат, а не как полный список.\n\n"
        "Каждая находка заполняется по единой карточке (каталог v2, §12) и обязана нести "
        "money_category (или money_not_assessable=true, если сумму в рублях оценить нельзя)."
    )


# ── Вызов модели (задача 6B; единственное место в пайплайне — принцип 3) ────
# Модель, ключ и base URL — из project env, НЕ из
# clients/<name>/.env — см. докстринг модуля.
LLM_MODEL_ENV_VAR = "ANALYZE_LLM_MODEL"
DEFAULT_LLM_MODEL = "gpt-5.6-terra"
LLM_BASE_URL_ENV_VAR = "ANALYZE_LLM_BASE_URL"
DEFAULT_LLM_BASE_URL = "https://api.proxyapi.ru/openai/v1"
PROXYAPI_API_KEY_ENV_VAR = "PROXYAPI_API_KEY"
LLM_MAX_TOKENS = 8000          # предсказуемый бюджет одного структурированного вызова
LLM_TIMEOUT_SECONDS = 180.0
LLM_MAX_RETRIES = 2            # ретраи транспортного уровня SDK (сеть/429/5xx)

_FINDING_FIELD_NAMES = frozenset(f.name for f in dataclasses.fields(schemas.Finding))


def _resolve_llm_model() -> str:
    return os.environ.get(LLM_MODEL_ENV_VAR) or DEFAULT_LLM_MODEL


def _resolve_llm_base_url() -> str:
    return os.environ.get(LLM_BASE_URL_ENV_VAR) or DEFAULT_LLM_BASE_URL


def _finding_item_schema(known_check_ids: list[str] | None = None) -> dict[str, Any]:
    """JSON Schema одной находки для text.format.

    Форма зеркалит schemas.Finding. Закрытые множества заданы здесь, чтобы
    неверные значения не могли пройти structured output.
    """
    return {
        "type": "object",
        "properties": {
            "check_id": {
                "type": "string",
                **({"enum": known_check_ids} if known_check_ids else {}),
            },
            "name": {"type": "string"},
            "status": {"type": "string", "enum": sorted(schemas.STATUS_VALUES)},
            "confidence": {"type": "string", "enum": sorted(schemas.CONFIDENCE_VALUES)},
            "significant": {"type": "boolean"},
            "period": {"type": "string"},
            "segment": {"type": ["string", "null"]},
            "data_source": {"type": "string"},
            "evidence": {"type": "string"},
            "control_metric": {"type": ["string", "null"]},
            "what_is_distorted": {"type": "string"},
            "money_category": {
                "type": ["string", "null"],
                "enum": [None, *sorted(schemas.MONEY_CATEGORY_VALUES)],
            },
            "money_amount_rub": {"type": ["number", "null"]},
            "money_not_assessable": {"type": "boolean"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {"type": "string"},
            "how_to_measure": {"type": "string"},
            "what_cannot_be_concluded": {"type": "string"},
            "source_check_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "check_id", "name", "status", "confidence", "significant", "period",
            "segment", "data_source", "evidence", "control_metric", "what_is_distorted",
            "money_category", "money_amount_rub", "money_not_assessable", "assumptions",
            "recommended_action", "how_to_measure", "what_cannot_be_concluded",
            "source_check_ids",
        ],
        "additionalProperties": False,
    }


def _findings_response_schema(known_check_ids: list[str] | None = None) -> dict[str, Any]:
    """Схема ответа целиком: {"findings": [...]} — один структурированный вызов."""
    return {
        "type": "object",
        "properties": {
            "findings": {"type": "array", "items": _finding_item_schema(known_check_ids)}
        },
        "required": ["findings"],
        "additionalProperties": False,
    }


def _call_llm(
    system_prompt: str, input_pack: dict[str, Any], *, client: Any = None
) -> dict[str, Any]:
    """Один структурированный вызов модели поверх input_pack. Возвращает {"findings": [...]}.

    Ретраи (``timeout``/``max_retries``) — только на транспортном уровне SDK
    (сетевые сбои, 429, 5xx). Если модель уже вернула валидный (парсящийся)
    ответ, повторный вызов не делается — дальнейшая фильтрация находок
    происходит локально в draft().

    ``client`` — точка подмены для тестов (см. tests/test_analyze*.py); по
    умолчанию создаётся openai.OpenAI() с ключом только из project env.
    """
    if client is None:
        api_key = os.environ.get(PROXYAPI_API_KEY_ENV_VAR)
        if not api_key:
            raise RuntimeError(
                "PROXYAPI_API_KEY не задан в project environment; "
                "analyze не читает clients/<name>/.env"
            )

        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=_resolve_llm_base_url(),
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )

    response = client.responses.create(
        model=_resolve_llm_model(),
        max_output_tokens=LLM_MAX_TOKENS,
        instructions=system_prompt,
        input=[{
            "role": "user",
            "content": (
                "Компактный входной пакет (кандидаты/контекст/ограничения) в "
                "формате JSON:\n\n" + json.dumps(
                    input_pack, ensure_ascii=False, separators=(",", ":")
                )
            ),
        }],
        text={"format": {
            "type": "json_schema",
            "name": "analyze_findings",
            "strict": True,
            "schema": _findings_response_schema(input_pack.get("known_check_ids") or []),
        }},
    )

    return json.loads(response.output_text)


def _finding_from_dict(item: Any) -> schemas.Finding | None:
    """Собрать Finding из одного элемента ответа модели; некорректная форма -> None.

    Только структурная сборка (нужные ключи, dataclass принял значения) —
    смысловую проверку полей делает schemas.validate_finding в draft().
    """
    if not isinstance(item, dict):
        return None
    kwargs = {k: v for k, v in item.items() if k in _FINDING_FIELD_NAMES}
    try:
        return schemas.Finding(**kwargs)
    except TypeError:
        return None


def _finding_filenames(findings: list[schemas.Finding]) -> list[str]:
    """F-<блок>-<nn>.yaml — nn последовательный внутри блока для этого прогона."""
    counters: dict[str, int] = {}
    names: list[str] = []
    for finding in findings:
        block = (finding.check_id[:1] if finding.check_id else "X").upper()
        counters[block] = counters.get(block, 0) + 1
        names.append(f"F-{block}-{counters[block]:02d}.yaml")
    return names


def _group_findings(findings: list[schemas.Finding]) -> list[schemas.Finding]:
    """Объединить повторяющиеся сегменты одной проблемы до применения лимита."""
    grouped: dict[tuple[Any, ...], schemas.Finding] = {}
    for finding in findings:
        key = (
            finding.check_id,
            finding.name,
            finding.status,
            finding.confidence,
            finding.period,
            finding.data_source,
            finding.what_is_distorted,
            finding.money_category,
            finding.money_amount_rub,
            finding.money_not_assessable,
            finding.recommended_action,
        )
        current = grouped.get(key)
        if current is None:
            grouped[key] = dataclasses.replace(
                finding,
                assumptions=list(finding.assumptions),
                source_check_ids=list(finding.source_check_ids),
            )
            continue
        for field_name in ("segment", "evidence", "control_metric"):
            values = [getattr(current, field_name), getattr(finding, field_name)]
            unique = list(dict.fromkeys(value for value in values if value))
            setattr(current, field_name, "; ".join(unique) if unique else None)
        current.assumptions = list(dict.fromkeys([*current.assumptions, *finding.assumptions]))
        current.source_check_ids = list(dict.fromkeys([
            *current.source_check_ids, *finding.source_check_ids
        ]))
    return list(grouped.values())


def _warn(log: Any, message: str) -> None:
    """Предупреждение стадии: в лог стадии, если он передан, иначе в logging."""
    if callable(log):
        log(f"WARNING: {message}")
    else:
        _LOGGER.warning(message)


REJECTED_DIRNAME = "rejected"


def _write_rejected(findings_draft_dir: Path, index: int, raw_item: Any, reasons: list[str]) -> str:
    """Записать один отклонённый ответ модели в findings/draft/rejected/R-<nn>.yaml.

    ``raw_item`` — необработанный элемент ответа модели (как есть, до сборки
    в schemas.Finding, чтобы причина отказа была видна вместе с исходным
    текстом даже для структурно сломанных ответов). ``reasons`` — машинные
    причины отказа (schemas.validate_finding + validate_findings_mod.
    validate_finding_evidence), не меньше одной.
    """
    rejected_dir = findings_draft_dir / REJECTED_DIRNAME
    rejected_dir.mkdir(parents=True, exist_ok=True)
    filename = f"R-{index:02d}.yaml"
    payload = {"reasons": reasons, "finding": raw_item}
    (rejected_dir / filename).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return filename


# ── Точка входа слоя (контракт: см. докстринг модуля) ──────────────────────
def draft(
    paths: Any,
    config: dict[str, Any],
    methodology: dict[str, Any],
    *,
    client: Any = None,
    log: Any = None,
) -> list[str]:
    """Собрать входной пакет, вызвать модель и записать находки в findings/draft/.

    Всегда пишет аудиторский артефакт (INPUT_PACK_ARTIFACT_NAME, имя начинается
    с "_", чтобы не перепутать с находкой) — пакет и системный промт для
    сверки/отладки. Затем вызывает модель один раз (см. _call_llm)
    и для каждой находки в ответе проверяет структурную форму
    (schemas.validate_finding) и evidence (validate_findings_mod.
    validate_finding_evidence — задача 6C: числа находки обязаны реально
    присутствовать в data/metrics, confidence не выше compute-уровня).
    Прошедшие обе проверки находки пишутся как
    findings/draft/F-<блок>-<nn>.yaml (повторы одной проблемы сначала
    объединяются, затем применяется schemas.MAX_FINDINGS_PER_RUN).
    Не прошедшие — в findings/draft/rejected/R-<nn>.yaml с машинной причиной
    (для ручного разбора аналитиком, не для повторной генерации).

    ``client`` — точка подмены для тестов, см. _call_llm.
    ``log`` — вызываемый логгер стадии (orchestrator.StageLogger); если не
    передан, предупреждения уходят в logging этого модуля.

    Возвращает список записанных имён файлов в findings/draft/ (не в
    rejected/): аудиторский артефакт + карточки прошедших валидацию находок.
    """
    paths.findings_draft.mkdir(parents=True, exist_ok=True)
    defaults = orchestrator_mod.load_defaults()

    pack, full_pack = build_input_pack(
        paths, config, methodology, defaults, return_full=True
    )
    system_prompt = build_system_prompt(defaults)

    out_path = Path(paths.findings_draft) / INPUT_PACK_ARTIFACT_NAME
    # Аудиторский артефакт — точный слепок ОТПРАВЛЕННОГО тела (send_pack +
    # system_prompt). full_pack в него не подмешивается.
    serialized_pack = json.dumps(
        {**pack, "system_prompt": system_prompt},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    final_size = len(serialized_pack.encode("utf-8"))
    if final_size >= pack["audit"]["byte_cap"]:
        raise RuntimeError(
            f"финальный analyze input-pack = {final_size} байт, "
            f"cap = {pack['audit']['byte_cap']} байт"
        )
    warn_bytes = pack["audit"].get("warn_bytes") or resolve_warn_bytes(defaults)
    if final_size >= warn_bytes:
        _warn(
            log,
            f"analyze input-pack = {final_size} байт при пороге предупреждения "
            f"{warn_bytes} байт (cap = {pack['audit']['byte_cap']} байт)",
        )
    out_path.write_text(serialized_pack, encoding="utf-8")
    written = [out_path.name]

    response_data = _call_llm(system_prompt, pack, client=client)
    raw_findings = response_data.get("findings") or []
    returned_check_ids = {
        item.get("check_id")
        for item in raw_findings
        if isinstance(item, dict) and isinstance(item.get("check_id"), str)
    }
    source_metrics = _load_source_metrics(paths, returned_check_ids)
    validation_corpus = build_validation_corpus(full_pack)

    known_ids = schemas.known_check_ids(methodology)
    degradation_report = _load_degradation_report(paths)
    confidence_caps = _confidence_caps(degradation_report)

    valid_findings: list[schemas.Finding] = []
    rejected_count = 0
    for item in raw_findings:
        finding = _finding_from_dict(item)
        if finding is None:
            rejected_count += 1
            _write_rejected(
                Path(paths.findings_draft), rejected_count, item,
                ["ответ модели не собирается в schemas.Finding (не совпадают поля)"],
            )
            continue

        cap = confidence_caps.get(finding.check_id)
        errors = schemas.validate_finding(finding, known_ids=known_ids, confidence_cap=cap)
        errors += validate_findings_mod.validate_finding_evidence(
            finding,
            metrics=source_metrics,
            inputs=validation_corpus,
            degradation_report=degradation_report,
        )
        if errors:
            rejected_count += 1
            _write_rejected(Path(paths.findings_draft), rejected_count, item, errors)
            continue
        valid_findings.append(finding)

    valid_findings = _group_findings(valid_findings)[: schemas.MAX_FINDINGS_PER_RUN]
    for finding, filename in zip(valid_findings, _finding_filenames(valid_findings)):
        finding_path = Path(paths.findings_draft) / filename
        finding_path.write_text(
            yaml.safe_dump(
                schemas.finding_to_ordered_dict(finding),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        written.append(filename)

    return written
