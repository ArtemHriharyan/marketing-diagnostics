"""Глубокая программная проверка evidence находок (задача 6C).

Дополняет структурную `schemas.validate_finding` (форма карточки) —
проверяет, что числа, которые LLM вписала в находку, реально существуют в
уже посчитанных данных, а не выдуманы. Ничего не пишет сама — только
возвращает список причин отказа; запись `findings/draft/rejected/` делает
вызывающий код (`src/analyze/draft_findings.py: draft()`).

Три проверки поверх schemas.validate_finding:

    1. source_file — файл `data/metrics/<check_id в нижнем регистре>.json`
       (тот же файл, что `src.compute.*` пишет через
       `common.write_metric_artifact(metrics_dir, "<check_id>", rows)`)
       обязан существовать в пакете metrics находки. Отсутствие -> отказ.
    2. Каждое число в `evidence` и в `money_amount_rub` обязано находиться
       (с допуском на округление до рубля/десятичных знаков) среди числовых
       значений source_file. Число, которого там нет, — вероятная
       галлюцинация LLM.
    3. confidence находки не имеет права быть выше наивысшего confidence,
       который compute посчитал для этой проверки (третий потолок — поверх
       confidence_cap источника из schemas.validate_finding). client-HIGH
       этому потолку не подчиняется (факт со слов клиента, не из compute) —
       как и потолку источника (methodology-v2.md §10.3).
    4. Каждое число в `assumptions` обязано подтверждаться где-то во всём
       входном пакете (metrics ∪ inputs ∪ degradation), не только в
       source_file конкретной проверки — assumptions часто опираются на
       данные соседних проверок или на анкету клиента.

LLM здесь не вызывается (принцип 3 CLAUDE.md) — только сверка уже
посчитанных чисел с текстом находки.
"""

from __future__ import annotations

import re
from typing import Any

from ..pipeline import degradation as degradation_mod
from . import schemas

# Число: цифры, возможно разбитые пробелами/неразрывными пробелами (тысячи),
# с опциональной десятичной частью через "." или ",". Валюта/проценты
# обрамляют число нецифровыми символами, поэтому в сам токен не входят.
_NUMBER_RE = re.compile(r"-?\d[\d\s ]*(?:[.,]\d+)?")

# Допуск на сравнение: округление до рубля (CLAUDE.md, принцип 7) и
# плавающая точка при пересчётах compute.
_ABS_TOL = 0.5
_REL_TOL = 1e-3

# HIGH/MED/LOW — то же множество, что использует degradation.min_confidence
# для сравнения уровней; client-HIGH сюда не входит (см. validate_finding_evidence).
_CONFIDENCE_LEVELS = frozenset({"HIGH", "MED", "LOW"})
_NON_FINDING_STATUSES = frozenset({"unavailable", "unavailable_for_cause"})
_DIAGNOSTIC_CONTEXT_MARKERS = frozenset({"channel_anomaly_context"})


def _is_non_finding_metric(payload: Any) -> bool:
    """True, только если весь артефакт, а не соседняя строка, недоступен."""
    if isinstance(payload, dict):
        if payload.get("status") in _NON_FINDING_STATUSES:
            return True
        if payload.get("finding") in _DIAGNOSTIC_CONTEXT_MARKERS:
            return True
        return False
    if isinstance(payload, list):
        return bool(payload) and all(_is_non_finding_metric(value) for value in payload)
    return False


def _normalize_number(raw: str) -> float | None:
    """Строковый токен числа -> float, либо None, если это не число."""
    text = raw.strip().replace(" ", "").replace(" ", "")
    if not text or text == "-":
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def extract_numbers(text: str | None) -> list[float]:
    """Извлечь все числа из свободного текста (evidence/assumption)."""
    if not text:
        return []
    numbers: list[float] = []
    for match in _NUMBER_RE.finditer(text):
        value = _normalize_number(match.group())
        if value is not None:
            numbers.append(value)
    return numbers


def _flatten_numeric_leaves(obj: Any, out: set[float]) -> None:
    """Собрать все числовые листья вложенной структуры (metrics/inputs/...)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            _flatten_numeric_leaves(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _flatten_numeric_leaves(value, out)
    elif isinstance(obj, str):
        for value in extract_numbers(obj):
            out.add(value)


def _confirmed_pool(*sources: Any) -> set[float]:
    """Множество «подтверждённых» чисел из произвольных источников пакета.

    Для чисел в правдоподобном диапазоне доли/процента добавляются также
    варианты ×100/÷100 — доли (0.209) и проценты (20.9) в данных пайплайна
    взаимозаменяемы (CLAUDE.md, принцип 7: доли хранятся как 0.209,
    форматирование — только на выводе), а LLM в тексте находки почти всегда
    пишет процент. Диапазон ограничен намеренно: без него ×100 у денежной
    суммы (напр. 10000 -> 1 000 000) создаёт ложные совпадения и глушит
    реальные галлюцинации LLM в этом же порядке величины.
    """
    base: set[float] = set()
    for source in sources:
        _flatten_numeric_leaves(source, base)
    expanded = set(base)
    for value in base:
        if abs(value) <= 1:
            expanded.add(round(value * 100, 6))
        if abs(value) <= 1000:
            expanded.add(round(value / 100, 6))
    return expanded


def _approx_in(value: float, pool: set[float]) -> bool:
    for candidate in pool:
        if abs(value - candidate) <= max(_ABS_TOL, abs(candidate) * _REL_TOL):
            return True
    return False


def check_source_metrics(check_id: str, metrics: dict[str, Any]) -> Any:
    """Артефакт data/metrics/<check_id в нижнем регистре>.json для проверки.

    Соглашение об имени — то же, что использует compute
    (`common.write_metric_artifact(metrics_dir, "<check_id>", rows)`,
    см. src/compute/block*.py). Отсутствие в пакете metrics -> None
    (несуществующий source_file).
    """
    if not check_id:
        return None
    return metrics.get(check_id.lower())


def compute_confidence_for_check(check_id: str, source_rows: Any) -> str | None:
    """Наивысший confidence среди строк source_rows для check_id (потолок №3)."""
    if not isinstance(source_rows, list):
        return None
    best: str | None = None
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        if row.get("check_id") not in (None, check_id):
            continue
        confidence = row.get("confidence")
        if confidence not in _CONFIDENCE_LEVELS:
            continue
        if best is None or degradation_mod.min_confidence(best, confidence) == best:
            best = confidence
    return best


def validate_finding_evidence(
    finding: schemas.Finding,
    *,
    metrics: dict[str, Any],
    inputs: dict[str, Any] | None = None,
    degradation_report: dict[str, Any] | None = None,
) -> list[str]:
    """Вернуть причины отказа (пустой список — evidence подтверждён).

    Дополняет `schemas.validate_finding` (та проверяет только форму
    карточки) — здесь числа находки сверяются с уже посчитанными данными.
    Ничего не бросает — решение о том, что делать с находкой (отбросить в
    `findings/draft/rejected/`), принимает вызывающий код.
    """
    errors: list[str] = []
    inputs = inputs or {}
    degradation_report = degradation_report or {}

    source_rows = check_source_metrics(finding.check_id, metrics)
    if finding.status in _NON_FINDING_STATUSES:
        errors.append(
            f"status={finding.status!r} — ограничение compute не может стать finding"
        )
    if _is_non_finding_metric(source_rows):
        errors.append(
            f"check_id={finding.check_id!r} содержит ограничение или диагностический контекст "
            "и не может стать finding"
        )
        return errors
    if source_rows is None:
        errors.append(
            f"source_file для check_id={finding.check_id!r} не найден в data/metrics "
            f"(ожидался файл '{(finding.check_id or '').lower()}.json')"
        )
        source_pool: set[float] = set()
    else:
        source_pool = _confirmed_pool(source_rows)

    for number in extract_numbers(finding.evidence):
        if not _approx_in(number, source_pool):
            errors.append(
                f"evidence содержит число {number!r}, которого нет в source_file "
                f"check_id={finding.check_id!r} — похоже на выдуманное значение"
            )

    if finding.money_amount_rub is not None and not _approx_in(finding.money_amount_rub, source_pool):
        errors.append(
            f"money_amount_rub={finding.money_amount_rub!r} не подтверждён числами "
            f"source_file check_id={finding.check_id!r}"
        )

    if source_rows is not None and finding.confidence != schemas.CLIENT_CONFIDENCE:
        compute_confidence = compute_confidence_for_check(finding.check_id, source_rows)
        if compute_confidence and degradation_mod.min_confidence(
            finding.confidence, compute_confidence
        ) != finding.confidence:
            errors.append(
                f"confidence={finding.confidence!r} превышает уровень, посчитанный compute "
                f"для check_id={finding.check_id!r} (compute confidence={compute_confidence!r})"
            )

    whole_pack_pool = _confirmed_pool(metrics, inputs, degradation_report)
    for assumption in finding.assumptions:
        for number in extract_numbers(assumption):
            if not _approx_in(number, whole_pack_pool):
                errors.append(
                    f"assumptions содержит неподтверждённое число {number!r}: {assumption!r}"
                )

    return errors
