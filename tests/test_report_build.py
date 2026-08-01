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


def _write_metrics_summary(paths: _Paths, seo_confidence_cap: dict | None = None) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    summary = {"counts": {"total": 100, "runnable": 80, "skipped": 20}}
    if seo_confidence_cap is not None:
        summary["seo_confidence_cap"] = seo_confidence_cap
    (paths.metrics / "metrics_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8",
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

def test_report_hides_non_publishable_approved_findings(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-visible.yaml", check_id="A04")
    _write_finding(paths, "F-unavailable.yaml", check_id="T03", status="unavailable")
    _write_finding(
        paths, "F-cause-unavailable.yaml", check_id="T09", status="unavailable_for_cause"
    )
    _write_finding(
        paths,
        "F-context.yaml",
        check_id="T09",
        finding="channel_anomaly_context",
        causal_claim=False,
    )

    findings = build_report.load_approved_findings(paths.findings_approved)

    assert [finding["check_id"] for finding in findings] == ["A04"]


def test_report_shows_t09_cause_limitation_without_registry_skip(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, skipped=[])
    _write_metrics_summary(paths)
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "t09.json").write_text(
        json.dumps({"summary": {"status": "unavailable_for_cause"}}),
        encoding="utf-8",
    )

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "**T09** (блок 2): аномалия наблюдается, причина не установлена" in text
    assert "Все проверки реестра выполнены при текущих источниках." not in text


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


# ── 7. Вердикт (задача 7B): три главных разрыва ─────────────────────────

def test_verdict_lists_top_gaps_in_priority_order(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-1.yaml", check_id="A01", confidence="MED", money_amount_rub=1000.0)
    _write_finding(paths, "F-2.yaml", check_id="A02", confidence="HIGH", money_amount_rub=100.0)
    _write_finding(paths, "F-3.yaml", check_id="A03", confidence="MED", money_amount_rub=50000.0)
    _write_finding(paths, "F-4.yaml", check_id="A04", confidence="LOW", money_amount_rub=999999.0)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "## Вердикт" in text
    assert "### Три главных разрыва" in text
    gaps_section = text.split("### Три главных разрыва")[1].split("###")[0]
    assert "1. **A02" in gaps_section
    assert "2. **A03" in gaps_section
    assert "3. **A01" in gaps_section
    assert "A04" not in gaps_section  # LOW-находка вне топ-3, не выдумываем лишний разрыв


def test_verdict_no_approved_findings_says_gaps_not_defined(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Утверждённых находок нет — главные разрывы не определены." in text


# ── 8. Вердикт по данным (блок 0) ────────────────────────────────────────

def test_verdict_data_block0_lists_skipped_verbatim(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[
        {"id": "D02", "block": 0, "reason": "нет источника: goals"},
        {"id": "A01", "block": 1, "reason": "нет источника: campaign_strategies"},
    ])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    verdict_section = text[text.index("## Вердикт"):text.index("## Резюме")]
    assert "**D02**: нет источника: goals" in verdict_section
    assert "A01" not in verdict_section  # блок 1, к вердикту по данным не относится


def test_verdict_data_block0_clean_when_no_skipped(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert (
        "Блок 0 (доверие к данным) пройден без ограничений при текущих источниках." in text
    )


# ── 9. SEO MED-cap агрегат из metrics_summary ────────────────────────────

def test_verdict_seo_med_cap_rendered_from_metrics_summary(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(
        paths,
        seo_confidence_cap={"runnable_count": 20, "med_cap_count": 15, "med_cap_share": 0.75},
    )

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "### SEO — потолок уверенности MED" in text
    assert "15 из 20 выполнимых проверок блока SEO (S) с потолком уверенности MED (75.0%)." in text


def test_verdict_seo_med_cap_absent_when_no_seo_data(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)  # без seo_confidence_cap — не выдумываем цифру

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Нет выполнимых проверок блока SEO (S) при текущих источниках." in text


# ── 10. Задача 7C: план действий (2 недели / 2 месяца) ──────────────────

def test_action_plan_splits_into_2w_and_2m_by_priority_order(tmp_path):
    paths = _Paths(tmp_path)
    count = build_report.MAX_ACTION_PLAN_2W + 2  # 9: заполнит 2 недели (7) + 2 месяца (2)
    for i in range(count):
        _write_finding(
            paths, f"F-A-{i:02d}.yaml",
            check_id="A04", confidence="MED",
            money_amount_rub=float(count - i),  # первая находка — самая дорогая -> первой по приоритету
            recommended_action=f"Действие №{i}",
        )
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "## План действий" in text
    plan_section = text.split("## План действий")[1].split("## Ключевые находки")[0]
    two_week = plan_section.split("### 2 недели")[1].split("### 2 месяца")[0]
    two_month = plan_section.split("### 2 месяца")[1]

    assert "1. **A04** — Действие №0" in two_week
    assert "7. **A04** — Действие №6" in two_week
    assert "Действие №7" not in two_week
    assert "1. **A04** — Действие №7" in two_month
    assert "2. **A04** — Действие №8" in two_month


def test_action_plan_empty_when_no_actionable_findings(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Утверждённых находок с рекомендацией нет — план не сформирован." in text
    assert "Дополнительных действий на горизонт 2 месяцев не выявлено." in text


# ── 11. Задача 7C: assignee ("уточнить" по умолчанию) ────────────────────

def test_finding_assignee_defaults_to_utochnit(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "ответственный: уточнить" in text


def test_finding_assignee_uses_explicit_value(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", assignee="Иван Иванов")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "ответственный: Иван Иванов" in text
    assert "ответственный: уточнить" not in text


# ── 12. Задача 7C: раздел «Что не удалось проверить и почему» ───────────

def test_skipped_section_header_includes_i_pochemu(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "## Что не удалось проверить и почему" in text


# ── 13. Задача 7C: приложение — LOW-находки и переполнение ──────────────

def test_low_finding_excluded_from_main_section_and_listed_in_appendix(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", check_id="A01", confidence="MED")
    _write_finding(paths, "F-A-02.yaml", check_id="A02", confidence="LOW",
                    recommended_action="Проверить гипотезу вручную")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    main_section = text.split("## Ключевые находки")[1].split("## Что не удалось проверить")[0]
    appendix_section = text.split("## Приложение")[1]

    assert "A01" in main_section
    assert "A02" not in main_section
    assert "A02" in appendix_section
    assert "уверенность: LOW" in appendix_section


def test_all_low_findings_main_section_points_to_appendix(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", check_id="A01", confidence="LOW")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    main_section = text.split("## Ключевые находки")[1].split("## Что не удалось проверить")[0]
    assert (
        "Находок уровня HIGH/MED/client-HIGH нет — утверждённые находки уровня LOW "
        "вынесены в приложение" in main_section
    )
    assert "A01" in text.split("## Приложение")[1]


def test_findings_beyond_limit_are_listed_in_appendix_not_dropped(tmp_path):
    paths = _Paths(tmp_path)
    count = build_report.MAX_REPORT_FINDINGS + 2
    for i in range(count):
        _write_finding(
            paths, f"F-A-{i:02d}.yaml",
            check_id="A04", confidence="MED", money_amount_rub=float(count - i),
        )
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    appendix_section = text.split("## Приложение")[1]
    assert "Дополнительных находок нет." not in appendix_section
    # Последние две (за пределами лимита) находки должны быть перечислены, не просто посчитаны.
    assert appendix_section.count("**A04 —") == 2


# ── 14. Задача 7C: приложение — SEO-ядро, не посчитанное ────────────────

def test_appendix_seo_core_lists_only_block_s_skips(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[
        {"id": "S07", "block": 4, "reason": "нет источника: спрос Wordstat"},
        {"id": "A01", "block": 1, "reason": "нет источника: campaign_strategies"},
    ])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    appendix_section = text.split("## Приложение")[1]
    seo_core_section = appendix_section.split("### SEO-ядро — не посчитано")[1]
    assert "**S07**: нет источника: спрос Wordstat" in seo_core_section
    assert "A01" not in seo_core_section


def test_appendix_seo_core_clean_when_no_seo_skips(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Все проверки блока SEO (S) выполнены при текущих источниках." in text


# ── 15. split_findings_for_report ─────────────────────────────────────────

def test_split_findings_for_report_moves_low_and_overflow_to_appendix():
    findings = [
        {"check_id": f"A{i:02d}", "confidence": "MED", "money_amount_rub": float(100 - i)}
        for i in range(build_report.MAX_REPORT_FINDINGS)
    ] + [
        {"check_id": "A99", "confidence": "MED", "money_amount_rub": 1.0},
        {"check_id": "L01", "confidence": "LOW"},
    ]
    shown, appendix = build_report.split_findings_for_report(findings)

    assert len(shown) == build_report.MAX_REPORT_FINDINGS
    assert all(f["confidence"] != "LOW" for f in shown)
    assert [f["check_id"] for f in appendix] == ["A99", "L01"]


# ── 16. Задача 7D: приложения-таблицы (CSV) ──────────────────────────────

def test_appendix_tables_written_with_findings_and_skipped_rows(tmp_path):
    import csv

    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", check_id="A01", confidence="MED")
    _write_finding(
        paths, "F-A-02.yaml", check_id="A02", confidence="LOW",
        recommended_action="Проверить гипотезу вручную", assignee="Иван",
    )
    _write_degradation(paths, skipped=[
        {"id": "D02", "block": 0, "reason": "нет источника: goals"},
        {"id": "S07", "block": 4, "reason": "нет источника: спрос Wordstat"},
    ])
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    tables_dir = paths.report / build_report.APPENDIX_TABLES_DIRNAME

    with (tables_dir / build_report.FINDINGS_APPENDIX_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["check_id"] for r in rows] == ["A02"]
    assert rows[0]["assignee"] == "Иван"

    with (tables_dir / build_report.SKIPPED_CHECKS_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["id"] for r in rows] == ["D02", "S07"]

    with (tables_dir / build_report.SEO_CORE_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["id"] for r in rows] == ["S07"]


def test_appendix_tables_written_empty_with_header_when_no_rows(tmp_path):
    import csv

    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths, skipped=[])
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    tables_dir = paths.report / build_report.APPENDIX_TABLES_DIRNAME
    for filename in (
        build_report.FINDINGS_APPENDIX_CSV,
        build_report.SKIPPED_CHECKS_CSV,
        build_report.SEO_CORE_CSV,
    ):
        with (tables_dir / filename).open(encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert len(rows) == 1  # только заголовок


# ── 17. Задача 7D: сноски [n] на конкретные таблицы ──────────────────────

def test_footnotes_section_lists_all_three_tables(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", check_id="A01", confidence="LOW")
    _write_degradation(paths, skipped=[{"id": "S07", "block": 4, "reason": "нет Wordstat"}])
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "## Сноски" in text
    assert f"appendix_tables/{build_report.FINDINGS_APPENDIX_CSV}" in text
    assert f"appendix_tables/{build_report.SKIPPED_CHECKS_CSV}" in text
    assert f"appendix_tables/{build_report.SEO_CORE_CSV}" in text


def test_footnote_markers_attached_to_section_headers(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml", check_id="A01", confidence="LOW")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    text = Path(out_path).read_text(encoding="utf-8")

    assert "Дополнительные находки (уровень LOW и сверх лимита раздела) [1]" in text
    assert "Что не удалось проверить и почему [2]" in text
    assert "SEO-ядро — не посчитано [3]" in text


# ── 18. Задача 7D: повестка звонка oral_review_agenda.md ─────────────────

def test_oral_review_agenda_written_with_top_findings_and_questions(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(
        paths, "F-1.yaml", check_id="A01", confidence="HIGH", money_amount_rub=1000.0,
        llm_notes=["Клиент подтверждал этот бюджет на прошлом звонке?"],
    )
    _write_finding(paths, "F-2.yaml", check_id="A02", confidence="MED", money_amount_rub=500.0)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    agenda_path = paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME
    text = agenda_path.read_text(encoding="utf-8")

    assert f"{build_report.ORAL_REVIEW_MINUTES_TOTAL} мин" in text
    assert "## Вступление" in text
    assert "## Главные находки" in text
    assert "1. **A01" in text
    assert "Вопрос: Клиент подтверждал этот бюджет на прошлом звонке?" in text
    assert "## Вопросы и дальнейшие шаги" in text


def test_oral_review_agenda_no_questions_when_llm_notes_absent(tmp_path):
    paths = _Paths(tmp_path)
    _write_finding(paths, "F-A-01.yaml")
    _write_degradation(paths)
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    text = (paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME).read_text(encoding="utf-8")
    assert "Вопрос:" not in text
    assert "Открытых вопросов к находкам нет" in text


def test_oral_review_agenda_limits_to_top_5_findings(tmp_path):
    paths = _Paths(tmp_path)
    for i in range(build_report.MAX_ORAL_REVIEW_FINDINGS + 2):
        _write_finding(
            paths, f"F-A-{i:02d}.yaml", check_id="A04", confidence="MED",
            money_amount_rub=float(100 - i),
        )
    _write_degradation(paths)
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    text = (paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME).read_text(encoding="utf-8")
    findings_section = text.split("## Главные находки")[1].split("## Вопросы")[0]
    assert findings_section.count("**A04 —") == build_report.MAX_ORAL_REVIEW_FINDINGS


def test_oral_review_agenda_no_approved_findings(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths)
    _write_metrics_summary(paths)

    build_report.build(paths, CONFIG, DEFAULTS)

    text = (paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME).read_text(encoding="utf-8")
    assert "Утверждённых находок нет — раздел находок не сформирован." in text


# ── 19. Задача 7D: смоук-тест полного отчёта ──────────────────────────────

def test_full_report_smoke_all_artifacts_present_and_consistent(tmp_path):
    """Сквозной прогон build() с разнородным набором находок: markdown-отчёт,
    3 CSV приложения и повестка звонка — все пишутся согласованно за один вызов.
    """
    import csv

    paths = _Paths(tmp_path)
    # HIGH/MED в пределах лимита раздела -> в основной секции.
    for i in range(3):
        _write_finding(
            paths, f"F-main-{i:02d}.yaml",
            check_id="A04", confidence="MED", money_amount_rub=float(1000 - i),
            recommended_action=f"Действие №{i}",
            llm_notes=[f"Вопрос по находке {i}"] if i == 0 else None,
        )
    # LOW -> уходит в приложение.
    _write_finding(
        paths, "F-low.yaml", check_id="C05", confidence="LOW",
        recommended_action="Проверить гипотезу вручную",
    )
    # Переполнение лимита раздела -> тоже в приложение.
    for i in range(build_report.MAX_REPORT_FINDINGS):
        _write_finding(
            paths, f"F-overflow-{i:02d}.yaml",
            check_id="T02", confidence="MED", money_amount_rub=1.0,
        )
    _write_degradation(paths, skipped=[
        {"id": "D02", "block": 0, "reason": "нет источника: goals"},
        {"id": "S07", "block": 4, "reason": "нет источника: спрос Wordstat"},
    ])
    _write_metrics_summary(
        paths, seo_confidence_cap={"runnable_count": 10, "med_cap_count": 5, "med_cap_share": 0.5},
    )

    out_path = build_report.build(paths, CONFIG, DEFAULTS)

    # 1. Основной markdown-отчёт.
    report_text = Path(out_path).read_text(encoding="utf-8")
    assert "## Вердикт" in report_text
    assert "## План действий" in report_text
    assert "## Ключевые находки" in report_text
    assert "## Приложение" in report_text
    assert "## Сноски" in report_text
    assert "## Глоссарий" in report_text

    # 2. Приложения-таблицы существуют и согласованы с markdown-приложением.
    tables_dir = paths.report / build_report.APPENDIX_TABLES_DIRNAME
    with (tables_dir / build_report.FINDINGS_APPENDIX_CSV).open(encoding="utf-8") as fh:
        appendix_rows = list(csv.DictReader(fh))
    appendix_section = report_text.split("## Приложение")[1].split("## Сноски")[0]
    assert len(appendix_rows) == appendix_section.count("**C05 —") + appendix_section.count("**T02 —")

    with (tables_dir / build_report.SKIPPED_CHECKS_CSV).open(encoding="utf-8") as fh:
        skipped_rows = list(csv.DictReader(fh))
    assert [r["id"] for r in skipped_rows] == ["D02", "S07"]

    with (tables_dir / build_report.SEO_CORE_CSV).open(encoding="utf-8") as fh:
        seo_rows = list(csv.DictReader(fh))
    assert [r["id"] for r in seo_rows] == ["S07"]

    # 3. Повестка звонка существует, содержит топ-находки и вопрос из llm_notes.
    agenda_text = (paths.report / build_report.ORAL_REVIEW_AGENDA_FILENAME).read_text(
        encoding="utf-8"
    )
    assert "## Главные находки" in agenda_text
    assert "Вопрос по находке 0" in agenda_text
