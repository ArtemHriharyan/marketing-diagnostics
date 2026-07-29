"""Тесты слоя analyze (задача 6C): src/analyze/validate_findings.py.

Глубокая программная проверка evidence находок — числа в evidence/
assumptions и money_amount_rub обязаны реально присутствовать в
data/metrics (source_file по check_id), а confidence не имеет права быть
выше compute-уровня для этой проверки (третий потолок, поверх
confidence_cap источника — см. schemas.validate_finding). Модуль ничего не
пишет сам — только возвращает причины отказа; запись
findings/draft/rejected/ делает draft_findings.draft() (см. сценарий 4 в
tests/test_analyze_draft_findings_llm.py).

Сценарии:
1. Валидная находка — числа evidence/money_amount_rub подтверждены
   source_file, confidence не выше compute-уровня -> без нарушений.
2. Выдуманное число в evidence, которого нет в source_file check_id, ->
   нарушение.
3. confidence находки выше compute-уровня (наивысший confidence среди строк
   source_file для check_id) -> нарушение; client-HIGH этот потолок
   обходит.
4. Несуществующий source_file (data/metrics/<check_id>.json не записан) ->
   нарушение по source_file + по каждому числу evidence/money_amount_rub.
5. Неподтверждённое число в assumptions (не встречается нигде во входном
   пакете metrics/inputs/degradation) -> нарушение; подтверждённое (из
   inputs) — без нарушений.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analyze import schemas, validate_findings  # noqa: E402


def _finding(**overrides) -> schemas.Finding:
    base = dict(
        check_id="A04",
        name="Кампания расходует деньги и не даёт ни одной чистой конверсии",
        status="подтверждена",
        confidence="MED",
        significant=True,
        period="2025-07..2026-06",
        data_source="Директ + Метрика",
        evidence="Кампания 'Х' потратила 10 000 ₽, чистых конверсий 0 за 6 месяцев",
        what_is_distorted="Бюджет уходит на кампанию без результата",
        recommended_action="Остановить или пересобрать кампанию",
        how_to_measure="Сравнить CPA/конверсии после изменения за аналогичный период",
        what_cannot_be_concluded="Нельзя утверждать, что кампания никогда не сработает",
        money_category="potentially_excludable_spend",
        money_amount_rub=10000.0,
    )
    base.update(overrides)
    return schemas.Finding(**base)


_METRICS_A04 = {
    "a04": [
        {
            "check_id": "A04",
            "campaign_id": "c1",
            "cost_normalized_rub": 10000.0,
            "net_conversions": 0,
            "period_months": 6,
            "confidence": "MED",
        }
    ]
}


# ── 1. Валидная находка ──────────────────────────────────────────────────

def test_valid_finding_has_no_evidence_errors():
    errors = validate_findings.validate_finding_evidence(_finding(), metrics=_METRICS_A04)
    assert errors == []


# ── 2. Выдуманное число в evidence ───────────────────────────────────────

def test_hallucinated_number_in_evidence_is_rejected():
    finding = _finding(evidence="Кампания 'Х' потратила 10 000 ₽, но конверсий было 777")
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert any("777" in e for e in errors)


def test_hallucinated_money_amount_is_rejected():
    finding = _finding(money_amount_rub=54321.0)
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert any("money_amount_rub" in e for e in errors)


# ── 3. confidence выше compute-уровня ────────────────────────────────────

def test_confidence_above_compute_level_is_rejected():
    finding = _finding(confidence="HIGH")  # source_file несёт confidence=MED
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert any("превышает уровень, посчитанный compute" in e for e in errors)


def test_confidence_at_or_below_compute_level_is_accepted():
    finding = _finding(confidence="LOW")
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert errors == []


def test_client_high_bypasses_compute_confidence_check():
    finding = _finding(confidence=schemas.CLIENT_CONFIDENCE)
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert errors == []


# ── 4. Несуществующий source_file ────────────────────────────────────────

def test_missing_source_file_is_rejected():
    finding = _finding(check_id="A09")  # a09.json отсутствует в metrics
    errors = validate_findings.validate_finding_evidence(finding, metrics=_METRICS_A04)
    assert any("source_file" in e and "A09" in e for e in errors)
    # Числа evidence/money_amount_rub тоже не подтверждены — пустой source_file.
    assert any("выдуманное значение" in e for e in errors)


def test_empty_metrics_pack_is_rejected():
    finding = _finding()
    errors = validate_findings.validate_finding_evidence(finding, metrics={})
    assert any("source_file" in e for e in errors)


# ── 5. Числа в assumptions ───────────────────────────────────────────────

def test_unconfirmed_number_in_assumptions_is_rejected():
    finding = _finding(assumptions=["Средний чек взят как 999999 ₽"])
    errors = validate_findings.validate_finding_evidence(
        finding, metrics=_METRICS_A04, inputs={}, degradation_report={}
    )
    assert any("assumptions" in e for e in errors)


def test_confirmed_number_in_assumptions_from_inputs_is_accepted():
    finding = _finding(assumptions=["Средний чек по словам клиента — 5000 ₽"])
    errors = validate_findings.validate_finding_evidence(
        finding,
        metrics=_METRICS_A04,
        inputs={"client_answers": {"business": {"avg_check_rub": 5000}}},
        degradation_report={},
    )
    assert errors == []


# ── extract_numbers: нормализация чисел из свободного текста ────────────

def test_extract_numbers_handles_thousands_separator_and_decimals():
    numbers = validate_findings.extract_numbers("10 000 ₽ и 20,9% за 6 месяцев, минус 3,5")
    assert numbers == [10000.0, 20.9, 6.0, 3.5]
