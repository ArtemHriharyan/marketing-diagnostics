"""Тесты денежной рамки (задача 5I): src/compute/money_frame.py.

Сценарии:
1. Плоские величины (A04 zero-conversion spend, A10 wasted_spend) -> money
   category potentially_excludable_spend.
2. CPA-outlier (A05) -> excess над медианой, cpa_reduction_same_budget.
3. Категории не смешиваются: два подытога, нет общего "гранд-тотала".
4. Сценарий C06 (доходимость формы по сегменту) + сквозной CPA A04 ->
   equivalent_additional_conversions, помечен "сценарий, не прогноз",
   confidence = min(сегмент, сайт в целом).
5. C06 без A04 -> сценарий пишется, но amount_rub=None ("в ₽ не оценить").
6. SEO не готова (нет s??.json) -> явная оговорка "SEO не учтён: источник
   не готов"; SEO готова -> оговорки нет.
7. confidence никогда не выше confidence_cap проверки.
8. findings_registry.csv skeleton: заголовок — карточка каталога v2 §12,
   нарративные колонки пустые, деньги/уверенность/сегмент заполнены.
9. Пустой прогон (нет ни одного aXX/cXX артефакта) не падает.
10. money_frame подключён к common.dispatch_blocks.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import common, money_frame  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_block1.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


DEFAULTS = {"currency_round": 0}


def _write_json(metrics_dir: Path, name: str, rows: list[dict]) -> None:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / f"{name}.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )


def _write_degradation(paths: _Paths, caps: dict[str, str]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {"checks": [{"check_id": cid, "confidence_cap": cap} for cid, cap in caps.items()]}
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def _rows_by_kind(rows: list[dict], kind: str) -> list[dict]:
    return [r for r in rows if r.get("kind") == kind]


# ── 1. Плоские величины ─────────────────────────────────────────────────────

def test_a04_zero_conversion_campaign_is_potentially_excludable(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A04": "HIGH"})
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "Кампания 1",
         "cost_normalized_rub": 10000.0, "net_conversions": 0,
         "zero_conversion_campaign": True, "confidence": "MED"},
        {"check_id": "A04", "campaign_id": "c2", "campaign_name": "Кампания 2",
         "cost_normalized_rub": 5000.0, "net_conversions": 3,
         "zero_conversion_campaign": False, "confidence": "MED"},
    ])

    artifacts = money_frame.run(paths, DEFAULTS, {"A04"})
    assert "money_frame" in artifacts

    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    items = _rows_by_kind(rows, "category_item")
    assert len(items) == 1
    assert items[0]["check_id"] == "A04"
    assert items[0]["money_category"] == money_frame.POTENTIALLY_EXCLUDABLE_SPEND
    assert items[0]["amount_rub"] == 10000.0
    assert items[0]["segment"] == "Кампания 1"


def test_a10_wasted_spend_uses_its_own_field_not_cost_normalized(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A10": "MED"})
    _write_json(paths.metrics, "a10", [
        {"check_id": "A10", "query": "бесплатно", "match_type": "KEYWORD",
         "wasted_spend_rub": 1230.0, "recurring_months_count": 3,
         "missing_negative_keyword_candidate": True, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"A10"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    items = _rows_by_kind(rows, "category_item")
    assert len(items) == 1
    assert items[0]["amount_rub"] == 1230.0
    assert items[0]["money_category"] == money_frame.POTENTIALLY_EXCLUDABLE_SPEND


# ── 2. CPA-outlier (excess над бенчмарком) ──────────────────────────────────

def test_a05_cpa_outlier_computes_excess_over_median(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A05": "HIGH"})
    _write_json(paths.metrics, "a05", [
        {"check_id": "A05", "campaign_id": "c1", "campaign_name": "Слабая кампания",
         "cost_normalized_rub": 30000.0, "net_conversions": 10,
         "cpa_rub": 3000.0, "median_cpa_rub": 1000.0,
         "cpa_persistently_worse": True, "confidence": "MED"},
        {"check_id": "A05", "campaign_id": "c2", "campaign_name": "Норма",
         "cost_normalized_rub": 10000.0, "net_conversions": 10,
         "cpa_rub": 1000.0, "median_cpa_rub": 1000.0,
         "cpa_persistently_worse": False, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"A05"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    items = _rows_by_kind(rows, "category_item")
    assert len(items) == 1
    # excess = cost - net_conv*median = 30000 - 10*1000 = 20000
    assert items[0]["amount_rub"] == 20000.0
    assert items[0]["money_category"] == money_frame.CPA_REDUCTION_SAME_BUDGET


def test_a18_nested_campaigns_summed_only_when_no_null_cost(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A18": "MED"})
    _write_json(paths.metrics, "a18", [
        {"check_id": "A18", "query": "услуга X", "campaign_count": 2, "total_clicks": 50,
         "campaigns": [
             {"campaign_id": "c1", "cost_normalized_rub": 4000.0, "clicks": 30},
             {"campaign_id": "c2", "cost_normalized_rub": 2000.0, "clicks": 20},
         ],
         "competing_campaigns": True, "confidence": "MED"},
        {"check_id": "A18", "query": "услуга Y", "campaign_count": 2, "total_clicks": 40,
         "campaigns": [
             {"campaign_id": "c3", "cost_normalized_rub": None, "clicks": 20},
             {"campaign_id": "c4", "cost_normalized_rub": 1000.0, "clicks": 20},
         ],
         "competing_campaigns": True, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"A18"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    items = _rows_by_kind(rows, "category_item")
    assert len(items) == 1
    assert items[0]["segment"] == "услуга X"
    assert items[0]["amount_rub"] == 6000.0


# ── 3. Категории не смешиваются ─────────────────────────────────────────────

def test_category_totals_are_kept_separate_no_grand_total(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A04": "HIGH", "A05": "HIGH"})
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "K1",
         "cost_normalized_rub": 1000.0, "net_conversions": 0,
         "zero_conversion_campaign": True, "confidence": "MED"},
    ])
    _write_json(paths.metrics, "a05", [
        {"check_id": "A05", "campaign_id": "c2", "campaign_name": "K2",
         "cost_normalized_rub": 5000.0, "net_conversions": 5,
         "cpa_rub": 1000.0, "median_cpa_rub": 200.0,
         "cpa_persistently_worse": True, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"A04", "A05"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    totals = _rows_by_kind(rows, "category_total")
    categories = {t["money_category"] for t in totals}
    assert categories == {
        money_frame.POTENTIALLY_EXCLUDABLE_SPEND,
        money_frame.CPA_REDUCTION_SAME_BUDGET,
    }
    # Нет строки, суммирующей обе категории в одну цифру.
    excludable = next(t for t in totals if t["money_category"] == money_frame.POTENTIALLY_EXCLUDABLE_SPEND)
    reduction = next(t for t in totals if t["money_category"] == money_frame.CPA_REDUCTION_SAME_BUDGET)
    assert excludable["amount_rub"] == 1000.0
    # excess A05 = 5000 - 5*200 = 4000
    assert reduction["amount_rub"] == 4000.0


# ── 4/5. Сценарий C06 ────────────────────────────────────────────────────────

def _c06_rows(segment_confidence: str = "MED") -> list[dict]:
    return [
        {"check_id": "C06", "finding": "funnel_summary",
         "form_open_visits": 1000, "form_submit_visits": 400,
         "open_to_submit_rate": 0.4, "confidence": "HIGH"},
        {"check_id": "C06", "finding": "funnel_by_segment",
         "segment_dimension": "device", "segment_value": "mobile",
         "form_open_visits": 600, "form_submit_visits": 120,
         "open_to_submit_rate": 0.2, "confidence": segment_confidence},
    ]


def test_c06_scenario_with_a04_blended_cpa(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"C06": "MED", "A04": "HIGH"})
    _write_json(paths.metrics, "c06", _c06_rows())
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "K1",
         "cost_normalized_rub": 100000.0, "net_conversions": 50,
         "zero_conversion_campaign": False, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"C06", "A04"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    scenarios = _rows_by_kind(rows, "scenario")
    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario["check_id"] == "C06"
    assert scenario["money_category"] == money_frame.EQUIVALENT_ADDITIONAL_CONVERSIONS
    assert scenario["scenario_label"] == money_frame.SCENARIO_LABEL
    # additional_conversions = 600*(0.4-0.2) = 120; blended_cpa = 100000/50 = 2000
    assert scenario["amount_rub"] == 240000.0
    # confidence = min(сегмент MED, сайт в целом HIGH) = MED
    assert scenario["confidence"] == "MED"
    assert set(scenario["source_check_ids"]) == {"C06", "A04"}


def test_c06_scenario_without_a04_has_no_money_amount(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"C06": "MED"})
    _write_json(paths.metrics, "c06", _c06_rows())

    money_frame.run(paths, DEFAULTS, {"C06"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    scenarios = _rows_by_kind(rows, "scenario")
    assert len(scenarios) == 1
    assert scenarios[0]["amount_rub"] is None
    assert any("CPA недоступен" in a for a in scenarios[0]["assumptions"])


def test_c06_low_confidence_segment_is_not_scenario_material(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"C06": "MED"})
    _write_json(paths.metrics, "c06", _c06_rows(segment_confidence="LOW"))

    money_frame.run(paths, DEFAULTS, {"C06"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    assert _rows_by_kind(rows, "scenario") == []


# ── 6. SEO caveat ────────────────────────────────────────────────────────────

def test_seo_not_ready_adds_explicit_caveat(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {})

    money_frame.run(paths, DEFAULTS, set())
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    caveats = _rows_by_kind(rows, "caveat")
    assert len(caveats) == 1
    assert caveats[0]["description"] == money_frame.SEO_NOT_READY_NOTE


def test_seo_ready_when_s_check_has_data(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {})
    _write_json(paths.metrics, "s01", [
        {"check_id": "S01", "query": "спрос X", "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, set())
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    assert _rows_by_kind(rows, "caveat") == []


def test_seo_unavailable_status_row_does_not_count_as_ready(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {})
    _write_json(paths.metrics, "s01", [
        {"check_id": "S01", "status": "unavailable", "reason": "нет источника"},
    ])

    money_frame.run(paths, DEFAULTS, set())
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    assert len(_rows_by_kind(rows, "caveat")) == 1


# ── 7. confidence <= confidence_cap ──────────────────────────────────────────

def test_confidence_never_exceeds_confidence_cap(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A04": "MED"})
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "K1",
         "cost_normalized_rub": 1000.0, "net_conversions": 0,
         "zero_conversion_campaign": True, "confidence": "HIGH"},
    ])

    money_frame.run(paths, DEFAULTS, {"A04"})
    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    item = _rows_by_kind(rows, "category_item")[0]
    assert item["confidence"] == "MED"
    assert item["confidence_cap"] == "MED"


# ── 8. findings_registry.csv skeleton ───────────────────────────────────────

def test_findings_registry_skeleton_header_and_narrative_columns_blank(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A04": "HIGH"})
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "K1",
         "cost_normalized_rub": 1000.0, "net_conversions": 0,
         "zero_conversion_campaign": True, "confidence": "MED"},
    ])

    money_frame.run(paths, DEFAULTS, {"A04"})
    csv_text = (paths.metrics / "findings_registry.csv").read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(csv_text))
    assert reader.fieldnames == list(money_frame._CARD_FIELDS)

    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["ID угрозы"] == "A04"
    assert row["Название"]  # взято из methodology.yaml, непусто
    assert row["Денежная категория"] == "потенциально исключаемый расход"
    assert row["Оценка в рублях или «в ₽ не оценить»"] == "1000.0"
    assert row["Статус"] == ""
    assert row["Доказательство"] == ""
    assert row["Рекомендуемое действие"] == ""
    assert row["Как измерить результат после изменения"] == ""


def test_findings_registry_marks_scenarios_explicitly(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"C06": "MED"})
    _write_json(paths.metrics, "c06", _c06_rows())

    money_frame.run(paths, DEFAULTS, {"C06"})
    csv_text = (paths.metrics / "findings_registry.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1
    assert rows[0]["Допущения"].startswith(money_frame.SCENARIO_LABEL)
    assert rows[0]["Оценка в рублях или «в ₽ не оценить»"] == "в ₽ не оценить"


def test_findings_registry_amount_shows_unscored_marker_when_null(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"C06": "MED"})
    _write_json(paths.metrics, "c06", _c06_rows())

    money_frame.run(paths, DEFAULTS, {"C06"})
    csv_text = (paths.metrics / "findings_registry.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert rows[0]["Оценка в рублях или «в ₽ не оценить»"] == "в ₽ не оценить"


# ── 9. Пустой прогон не падает ───────────────────────────────────────────────

def test_empty_run_does_not_crash_and_writes_only_seo_caveat(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, {})

    artifacts = money_frame.run(paths, DEFAULTS, set())
    assert artifacts == ["money_frame", "findings_registry"]

    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    assert _rows_by_kind(rows, "category_item") == []
    assert _rows_by_kind(rows, "scenario") == []
    assert len(_rows_by_kind(rows, "caveat")) == 1

    registry_rows = json.loads((paths.metrics / "findings_registry.json").read_text(encoding="utf-8"))
    assert registry_rows == []


def test_missing_degradation_report_does_not_crash(tmp_path):
    paths = _Paths(tmp_path)
    paths.metrics.mkdir(parents=True, exist_ok=True)
    artifacts = money_frame.run(paths, DEFAULTS, set())
    assert artifacts == ["money_frame", "findings_registry"]


# ── 10. Подключение к dispatcher ────────────────────────────────────────────

def test_money_frame_registered_in_block_module_names():
    """money_frame идёт до candidates, но последним модулем не является.

    Раньше тест требовал BLOCK_MODULE_NAMES[-1] == "money_frame". Последним
    блоком стал candidates: он читает выход money_frame наравне с остальными
    артефактами, поэтому обязан идти после него. Два теста на «последний
    модуль» несовместимы (второй — test_candidates.py:186), и позицию
    money_frame правильно фиксировать относительно его потребителя.
    """
    assert "money_frame" in common.BLOCK_MODULE_NAMES
    assert common.BLOCK_MODULE_NAMES.index("money_frame") > common.BLOCK_MODULE_NAMES.index("block3")
    assert common.BLOCK_MODULE_NAMES.index("money_frame") < common.BLOCK_MODULE_NAMES.index("candidates")


def test_dispatch_blocks_runs_money_frame_by_default(tmp_path):
    """Дефолтный dispatch (без явного modules=) импортирует money_frame по
    имени из common.BLOCK_MODULE_NAMES и вызывает его после block1/block3 —
    ранее записанный a04.json (canonical пуст, block1 его не тронет) должен
    попасть в вывод money_frame."""
    paths = _Paths(tmp_path)
    _write_degradation(paths, {"A04": "HIGH"})
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "campaign_name": "K1",
         "cost_normalized_rub": 1000.0, "net_conversions": 0,
         "zero_conversion_campaign": True, "confidence": "MED"},
    ])

    report = {"runnable_check_ids": [], "checks": []}
    result = common.dispatch_blocks(paths, DEFAULTS, report)
    assert result["block_status"]["money_frame"] == "ok"
    assert "money_frame" in result["artifacts"]

    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    items = _rows_by_kind(rows, "category_item")
    assert len(items) == 1
    assert items[0]["check_id"] == "A04"
