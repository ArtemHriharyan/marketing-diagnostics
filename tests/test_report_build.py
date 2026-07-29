"""Тесты слоя report (задача 7A): src/report/build_report.py.

Сценарии:
1. build(): читает approved/degradation/metrics_summary/glossary, пишет
   report/diagnostic_report.md, возвращает путь к нему.
2. sort_approved_findings: HIGH/client-HIGH раньше MED раньше LOW; внутри
   одной уверенности — большая |money_amount_rub| раньше; находки без суммы
   после находок с суммой.
3. Раздел «Что не удалось проверить» переносит skipped дословно (id/block/
   reason), включая случай отсутствия пропусков.
4. format_rub/format_percent/format_pp: округление до рубля, доля -> %,
   разница долей -> п.п. со знаком.
5. Лимит MAX_REPORT_FINDINGS: находок больше лимита -> в файле только топ-N
   и пометка об обрезке; находок не больше лимита -> без пометки.
6. Глоссарий подставляется из config/report_glossary.yaml (реальный файл,
   15-20 терминов).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.report import build_report  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_analyze_draft_findings.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.metrics = root / "data" / "metrics"
        self.findings_approved = root / "findings" / "approved"
        self.report = root / "report"


DEFAULTS = {"currency_round": 0}

CONFIG = {
    "client": {"name": "Клиент Тест", "niche": "аренда авто", "geo": "Москва"},
    "data_window": {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"},
}


def _write_finding(paths: _Paths, filename: str, **overrides) -> None:
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
    paths.findings_approved.mkdir(parents=True, exist_ok=True)
    (paths.findings_approved / filename).write_text(
        yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _write_degradation(paths: _Paths, skipped: list[dict] | None = None) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {
        "counts": {"total": 100, "runnable": 80, "skipped": 20},
        "skipped": skipped or [],
    }
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def _write_metrics_summary(paths: _Paths) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "metrics_summary.json").write_text(
        json.dumps({"counts": {"total": 100, "runnable": 80, "skipped": 20}}, ensure_ascii=False),
        encoding="utf-8",
    )


# ── 1. build(): сквозной прогон ─────────────────────────────────────────

def test_build_writes_report_and_returns_path(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)

    assert Path(out_path) == paths.report / "diagnostic_report.md"
    text = Path(out_path).read_text(encoding="utf-8")
    assert "Диагностика маркетинга — Клиент Тест" in text
    assert "A04" in text
    assert "## Глоссарий" in text
    assert "## Что не удалось проверить" in text


def test_build_handles_no_approved_findings(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)

    text = Path(out_path).read_text(encoding="utf-8")
    assert "Утверждённых находок нет." in text


# ── 2. Сортировка по приоритету ─────────────────────────────────────────

def test_sort_prefers_high_confidence_then_larger_money():
    findings = [
        {"check_id": "A01", "confidence": "MED", "money_amount_rub": 5000.0},
        {"check_id": "A02", "confidence": "HIGH", "money_amount_rub": 100.0},
        {"check_id": "A03", "confidence": "MED", "money_amount_rub": 50000.0},
        {"check_id": "A04", "confidence": "LOW"},
    ]
    ordered = build_report.sort_approved_findings(findings)
    assert [f["check_id"] for f in ordered] == ["A02", "A03", "A01", "A04"]


def test_sort_findings_without_money_come_after_findings_with_money_same_confidence():
    findings = [
        {"check_id": "C02", "confidence": "MED"},
        {"check_id": "C01", "confidence": "MED", "money_amount_rub": 1.0},
    ]
    ordered = build_report.sort_approved_findings(findings)
    assert [f["check_id"] for f in ordered] == ["C01", "C02"]


def test_sort_client_high_ranks_with_high():
    findings = [
        {"check_id": "A01", "confidence": "MED"},
        {"check_id": "A02", "confidence": build_report.schemas_mod.CLIENT_CONFIDENCE},
    ]
    ordered = build_report.sort_approved_findings(findings)
    assert ordered[0]["check_id"] == "A02"


# ── 3. Что не удалось проверить — дословный перенос ─────────────────────

def test_skipped_section_carries_reason_verbatim(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[
        {"id": "D02", "block": 0, "reason": "нет источника: goals"},
    ])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "**D02** (блок 0): нет источника: goals" in text


def test_skipped_section_empty_says_all_runnable(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Все проверки реестра выполнены при текущих источниках." in text


# ── 4. Форматирование ₽ / % / п.п. ──────────────────────────────────────

def test_format_rub_rounds_and_formats_thousands():
    assert build_report.format_rub(12345.6, currency_round=0) == "12 346 ₽"


def test_format_rub_none_is_not_assessable():
    assert build_report.format_rub(None) == "в ₽ не оценить"


def test_format_percent():
    assert build_report.format_percent(0.209) == "20.9%"


def test_format_pp_positive_and_negative():
    assert build_report.format_pp(0.05) == "+5.0 п.п."
    assert build_report.format_pp(-0.033, digits=1) == "-3.3 п.п."


# ── 5. Лимит топ-находок ─────────────────────────────────────────────────

def test_findings_section_truncates_to_limit(tmp_path):
    paths = _Paths(tmp_path)
    count = build_report.MAX_REPORT_FINDINGS + 3
    for i in range(count):
        _write_finding(
            paths, f"F-A-{i:02d}.yaml",
            check_id="A04", confidence="MED", money_amount_rub=float(i + 1),
        )
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert f"Показаны {build_report.MAX_REPORT_FINDINGS} находок из {count}" in text


def test_findings_section_no_truncation_note_when_within_limit(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Показаны" not in text


# ── 6. Глоссарий ─────────────────────────────────────────────────────────

def test_glossary_loaded_from_real_config_has_15_to_20_terms():
    glossary = build_report.load_glossary()
    assert 15 <= len(glossary) <= 20
    assert all("term" in entry and "definition" in entry for entry in glossary)
