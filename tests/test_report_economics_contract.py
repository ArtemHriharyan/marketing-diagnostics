"""Тесты контракта экономики для отчёта (задача 7E):
``build_report.load_report_economics`` + ``config/report_economics_map.yaml``.

Сценарии:
1. Полный набор metrics: значения строк берутся из файлов как есть.
2. Отсутствует один файл metrics -> его строки «экономика не посчитана»,
   сборка не падает, остальные строки живы.
3. Отсутствует один ключ -> строка остаётся со статусом «не посчитано» и
   причиной; значение None, а не 0, и строка не выброшена.
4. L0 -> ``cost_per_deal_by_source`` недоступно с причиной «источник сделки
   не фиксируется в CRM» и НЕ равно стоимости веб-конверсии.
5. ``attribution_level`` берётся из metrics (L0/L1/L2), не хардкодится;
   без поля — L_UNKNOWN.
6. В коде контракта нет арифметики над значениями (проверка по AST).
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.report import build_report  # noqa: E402


COST_SUMMARY = {
    "money_basis": "gross_final_rub",
    "component_columns": ["month", "component_id", "channel", "kind", "amount_rub"],
    "component_rows": [["2025-04", "direct_media", "direct", "media", 27467.13]],
    "component_total_columns": ["component_id", "channel", "kind", "amount_rub"],
    "component_total_rows": [["direct_media", "direct", "media", 492661.44]],
    "channel_total_columns": ["channel", "amount_rub"],
    "channel_total_rows": [["direct", 920161.44], ["seo", 517500.0]],
    "monthly_total_columns": ["month", "amount_rub"],
    "monthly_total_rows": [["2025-04", 99967.13]],
    "total_rub": 1580161.44,
    "limitations": [],
}

ACQUISITION_ECONOMICS = {
    "money_basis": "gross_final_rub",
    "crm": {
        "record_unit": "paid_booking",
        "record_count": 1357,
        "total_revenue_rub": 24281956.0,
        "average_revenue_rub": 17893.85,
        "median_revenue_rub": 9100.0,
        "unique_customers": None,
        "average_revenue_per_customer_rub": None,
        "limitations": [],
        "status": "ok",
    },
    "models": [
        {
            "id": "estimated_site_booking",
            "mode": "crm_share_estimate",
            "status": "ok",
            "basis": "estimate",
            "result_name": "Оценочная стоимость сайт-брони",
            "numerator": {"money_basis": "gross_final_rub", "amount_rub": 1580161.44, "components": []},
            "denominator": {"value": 1221.3, "method": {"type": "crm_share_estimate"}},
            "formula": "gross_spend_rub / denominator_records_or_visits",
            "value_rub": 1293.83,
            "unit": "rub_per_paid_booking",
            "assumptions": [{"code": "client_configured_crm_share", "value": 0.9}],
            "limitations": [],
        },
        {
            "id": "tracked_direct_booking",
            "mode": "tracked_funnel",
            "status": "ok",
            "basis": "tracked_proxy",
            "result_name": "Стоимость отслеживаемой конверсии",
            "numerator": {"money_basis": "gross_final_rub", "amount_rub": 920161.44, "components": []},
            "denominator": {"value": 280, "method": {"type": "tracked_funnel_unique_visits"}},
            "formula": "gross_spend_rub / denominator_records_or_visits",
            "value_rub": 3286.29,
            "unit": "rub_per_tracked_conversion",
            "assumptions": [],
            "limitations": [],
        },
    ],
}

MONEY_FRAME = [
    {
        "check_id": "A17",
        "money_category": "potentially_excludable_spend",
        "amount_rub": 60.0,
        "unit": "RUB",
        "confidence": "MED",
        "kind": "category_item",
    },
    {
        "check_id": "M",
        "kind": "category_total",
        "money_category": "potentially_excludable_spend",
        "amount_rub": 921.0,
    },
]


def _attribution_row(level: str, unique_available: bool = False) -> dict:
    """Строка money_frame.json, которую пишет compute (7G) — источник уровня."""
    return {
        "check_id": "M",
        "kind": "attribution",
        "metric": "attribution_level",
        "value": level,
        "attribution_level": level,
        "attribution_evidence": [{"role": "table", "table": "crm", "present": True}],
        "unique_customers_available": unique_available,
        "unique_customers_status": "available" if unique_available else "not_computable",
        "unique_customers_reason": (
            None if unique_available else "нет ключа склейки повторных обращений"
        ),
    }


def _money_frame_with(level: str, unique_available: bool = False) -> list:
    return [*MONEY_FRAME, _attribution_row(level, unique_available)]

DEGRADATION = {"counts": {"total": 100, "runnable": 80, "skipped": 20}, "skipped": []}


def _write_metrics(
    tmp_path: Path,
    cost_summary: dict | None = COST_SUMMARY,
    acquisition: dict | None = ACQUISITION_ECONOMICS,
    money_frame: list | None = MONEY_FRAME,
) -> Path:
    """Разложить набор metrics-файлов; None -> файл не создаётся вовсе."""
    metrics_dir = tmp_path / "data" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "cost_summary.json": cost_summary,
        "acquisition_economics.json": acquisition,
        "money_frame.json": money_frame,
    }
    for filename, payload in payloads.items():
        if payload is None:
            continue
        (metrics_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return metrics_dir


def _row(economics: dict, row_id: str) -> dict:
    return next(row for row in economics["rows"] if row["id"] == row_id)


# ── 1. Полный набор metrics ──────────────────────────────────────────────
def test_full_metrics_values_taken_as_is(tmp_path):
    metrics_dir = _write_metrics(tmp_path)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert _row(economics, "spend_total_rub")["value"] == 1580161.44
    assert _row(economics, "spend_by_channel_rows")["value"] == [
        ["direct", 920161.44],
        ["seo", 517500.0],
    ]
    assert _row(economics, "crm_record_count")["value"] == 1357
    assert _row(economics, "crm_total_revenue_rub")["value"] == 24281956.0
    assert _row(economics, "acq_tracked_direct_booking_value_rub")["value"] == 3286.29
    assert _row(economics, "acq_estimated_site_booking_denominator_value")["value"] == 1221.3
    assert _row(economics, "money_frame_items")["value"] == MONEY_FRAME
    assert all(
        state["available"] for state in economics["sources"].values()
    )


def test_full_metrics_cost_per_web_conversion_lists_both_models(tmp_path):
    metrics_dir = _write_metrics(tmp_path)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)
    web = economics["cost_per_web_conversion"]

    assert web["available"] is True
    assert [item["id"] for item in web["items"]] == [
        "estimated_site_booking",
        "tracked_direct_booking",
    ]
    assert [item["value_rub"] for item in web["items"]] == [1293.83, 3286.29]
    assert [item["unit"] for item in web["items"]] == [
        "rub_per_paid_booking",
        "rub_per_tracked_conversion",
    ]


def test_null_value_in_metrics_is_not_computed_not_zero(tmp_path):
    """unique_customers = null в metrics -> «не посчитано», а не 0."""
    metrics_dir = _write_metrics(tmp_path)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)
    row = _row(economics, "crm_unique_customers")

    assert row["value"] is None
    assert row["status"] == build_report.ECONOMICS_STATUS_MISSING
    assert row["reason"]
    assert economics["status"] == "partial"


# ── 2. Отсутствует один файл metrics ─────────────────────────────────────
def test_missing_one_metrics_file_marks_section_not_computed(tmp_path):
    metrics_dir = _write_metrics(tmp_path, money_frame=None)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)
    row = _row(economics, "money_frame_items")

    assert row["status"] == build_report.ECONOMICS_SECTION_NOT_COMPUTED
    assert "money_frame.json" in row["reason"]
    assert row["value"] is None
    assert economics["sources"]["money_frame"]["available"] is False
    # остальные файлы продолжают отдавать значения
    assert _row(economics, "spend_total_rub")["value"] == 1580161.44
    assert economics["status"] == "partial"


def test_all_metrics_files_missing_does_not_raise(tmp_path):
    metrics_dir = _write_metrics(tmp_path, cost_summary=None, acquisition=None, money_frame=None)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert economics["status"] == "not_computed"
    assert economics["section_note"] == build_report.ECONOMICS_SECTION_NOT_COMPUTED
    assert all(
        row["status"] == build_report.ECONOMICS_SECTION_NOT_COMPUTED
        for row in economics["rows"]
    )
    assert economics["attribution_level"] == build_report.ATTRIBUTION_LEVEL_UNKNOWN


def test_build_does_not_crash_without_economics_files(tmp_path):
    """Полная сборка отчёта без экономических файлов metrics не падает."""
    metrics_dir = _write_metrics(tmp_path, cost_summary=None, acquisition=None, money_frame=None)
    (metrics_dir / "degradation_report.json").write_text(
        json.dumps(DEGRADATION, ensure_ascii=False), encoding="utf-8"
    )

    class _Paths:
        def __init__(self, root: Path):
            self.metrics = root / "data" / "metrics"
            self.findings_approved = root / "findings" / "approved"
            self.report = root / "report"

    paths = _Paths(tmp_path)
    out_path = build_report.build(
        paths,
        {"client": {"name": "Клиент Тест"}},
        {"currency_round": 0},
    )

    assert Path(out_path).exists()


# ── 3. Ключ есть, значения нет ───────────────────────────────────────────
def test_null_key_keeps_row_with_reason(tmp_path):
    """Ключ на месте, значение null -> строка со статусом и причиной.

    Задача 7H: полностью отсутствующего ключа в этом сценарии быть не
    может — compute пишет все ключи файла, а неразрешимый адрес карты
    теперь роняет сборку (см. ``test_unresolvable_map_address_*``).
    """
    cost_summary = dict(COST_SUMMARY, total_rub=None)
    metrics_dir = _write_metrics(tmp_path, cost_summary=cost_summary)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)
    row = _row(economics, "spend_total_rub")

    assert row["available"] is False
    assert row["value"] is None
    assert row["status"] == build_report.ECONOMICS_STATUS_MISSING
    assert row["reason_code"] == build_report.REASON_VALUE_NULL
    assert "total_rub" in row["reason"]
    assert "cost_summary.json" in row["reason"]


def test_missing_key_reason_comes_from_degradation_when_declared(tmp_path, monkeypatch):
    """Если карта объявляет degradation_check_ids, причина берётся оттуда."""
    metrics_dir = _write_metrics(tmp_path, cost_summary=dict(COST_SUMMARY, total_rub=None))
    degradation = {
        "skipped": [{"id": "A04", "block": 1, "reason": "нет источника: расход не выгружен"}]
    }

    original = build_report.load_economics_map

    def _patched(config_dir=None):
        economics_map = original(config_dir)
        for row_map in economics_map["rows"]:
            if row_map["id"] == "spend_total_rub":
                row_map["degradation_check_ids"] = ["A04"]
        return economics_map

    monkeypatch.setattr(build_report, "load_economics_map", _patched)

    economics = build_report.load_report_economics(metrics_dir, degradation)
    row = _row(economics, "spend_total_rub")

    assert row["reason"] == "A04: нет источника: расход не выгружен"


# ── 4-5. Уровень атрибуции и два независимых поля ────────────────────────
def test_attribution_unknown_when_row_absent(tmp_path):
    """Строки уровня в money_frame нет (секция attribution не задана) ->
    L_UNKNOWN с причиной, а не подставленный L0; сборка не падает."""
    metrics_dir = _write_metrics(tmp_path)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert economics["attribution_level"] == build_report.ATTRIBUTION_LEVEL_UNKNOWN
    assert economics["attribution_level_source"]["resolved"] is False
    assert economics["attribution_level_source"]["reason"]
    assert economics["attribution_level_source"]["evidence"] is None


def test_attribution_level_read_from_money_frame_not_hardcoded(tmp_path):
    for level in ("L0", "L1", "L2"):
        metrics_dir = _write_metrics(tmp_path / level, money_frame=_money_frame_with(level))

        economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

        assert economics["attribution_level"] == level
        assert economics["attribution_level_source"]["resolved"] is True
        assert economics["attribution_level_source"]["evidence"] == [
            {"role": "table", "table": "crm", "present": True}
        ]


def test_unique_customers_pulled_into_contract(tmp_path):
    """unique_customers_available и его статус нужны пунктам 3 и 5 секции."""
    metrics_dir = _write_metrics(tmp_path, money_frame=_money_frame_with("L0"))
    unique = build_report.load_report_economics(metrics_dir, DEGRADATION)["unique_customers"]

    assert unique["resolved"] is True
    assert unique["available"] is False
    assert unique["status"] == build_report.STATUS_NOT_COMPUTABLE

    metrics_dir = _write_metrics(
        tmp_path / "with", money_frame=_money_frame_with("L1", unique_available=True)
    )
    unique = build_report.load_report_economics(metrics_dir, DEGRADATION)["unique_customers"]

    assert unique["available"] is True
    assert unique["status"] == build_report.STATUS_AVAILABLE


def test_unique_customers_defaults_to_not_computed_yet_without_row(tmp_path):
    metrics_dir = _write_metrics(tmp_path)
    unique = build_report.load_report_economics(metrics_dir, DEGRADATION)["unique_customers"]

    assert unique["resolved"] is False
    assert unique["available"] is False
    assert unique["status"] == build_report.STATUS_NOT_COMPUTED_YET


def test_l0_cost_per_deal_unavailable_and_not_inherited(tmp_path):
    metrics_dir = _write_metrics(tmp_path, money_frame=_money_frame_with("L0"))

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)
    deal = economics["cost_per_deal_by_source"]
    web = economics["cost_per_web_conversion"]

    assert economics["attribution_level"] == "L0"
    assert deal["available"] is False
    assert deal["value"] is None
    assert deal["status"] == build_report.ECONOMICS_STATUS_UNAVAILABLE
    assert deal["reason"] == "недоступно: источник сделки не фиксируется в CRM"
    # стоимость веб-конверсии при этом посчитана и НЕ перетекает в стоимость сделки
    assert web["available"] is True
    assert deal["value"] not in [item["value_rub"] for item in web["items"]]


def test_unknown_level_cost_per_deal_unavailable_with_own_reason(tmp_path):
    metrics_dir = _write_metrics(tmp_path)

    deal = build_report.load_report_economics(metrics_dir, DEGRADATION)["cost_per_deal_by_source"]

    assert deal["status"] == build_report.ECONOMICS_STATUS_UNAVAILABLE
    assert deal["value"] is None
    assert "уровень атрибуции не определён" in deal["reason"]
    assert deal["reason"] != "недоступно: источник сделки не фиксируется в CRM"


def test_l1_without_key_is_not_computed_not_unavailable(tmp_path):
    """L1/L2: величина разрешена уровнем, но compute ключ не отдал ->
    «не посчитано» (это другой случай, чем принципиальная недоступность L0).
    """
    metrics_dir = _write_metrics(tmp_path, money_frame=_money_frame_with("L1"))

    deal = build_report.load_report_economics(metrics_dir, DEGRADATION)["cost_per_deal_by_source"]

    assert deal["status"] == build_report.ECONOMICS_STATUS_MISSING
    assert deal["value"] is None
    assert "cost_per_deal_by_source" in deal["reason"]


# ── 6. Карта и отсутствие арифметики ─────────────────────────────────────
def test_map_rows_reference_declared_files_only():
    economics_map = build_report.load_economics_map()
    declared = set(economics_map["files"])

    assert declared == {"cost_summary", "acquisition_economics", "money_frame"}
    assert {row["file"] for row in economics_map["rows"]} <= declared
    assert economics_map["cost_per_web_conversion"]["file"] in declared
    assert economics_map["cost_per_deal_by_source"]["file"] in declared
    assert len({row["id"] for row in economics_map["rows"]}) == len(economics_map["rows"])


ECONOMICS_FUNCTIONS = (
    "load_report_economics",
    "_economics_row",
    "_economics_documents",
    "_resolve_map_path",
    "_attribution_level",
    "_cost_per_web_conversion",
    "_cost_per_deal_by_source",
    "_path_text",
    "_degradation_reason",
)

_ARITHMETIC_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def test_economics_contract_contains_no_arithmetic():
    """Report собирает уже посчитанное и не вычисляет ничего сам."""
    source = inspect.getsource(build_report)
    module = ast.parse(source)
    functions = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in ECONOMICS_FUNCTIONS:
        assert name in functions, name
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.BinOp):
                assert not isinstance(node.op, _ARITHMETIC_OPS), f"{name}: арифметика в контракте"
            if isinstance(node, ast.AugAssign):
                assert not isinstance(node.op, _ARITHMETIC_OPS), f"{name}: арифметика в контракте"


def test_map_declares_no_formulas():
    """В карте только адреса ключей — ни одной формулы."""
    text = (build_report.CONFIG_DIR / build_report.ECONOMICS_MAP_FILENAME).read_text(
        encoding="utf-8"
    )
    payload_lines = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]

    for line in payload_lines:
        for token in (" + ", " * ", " / ", "sum(", "avg("):
            assert token not in line, line


# ── 7. Валидация адресов карты (задача 7H) ───────────────────────────────
def _patch_map(monkeypatch, mutate):
    original = build_report.load_economics_map

    def _patched(config_dir=None):
        economics_map = original(config_dir)
        mutate(economics_map)
        return economics_map

    monkeypatch.setattr(build_report, "load_economics_map", _patched)


def test_unresolvable_map_address_in_rows_fails_the_build(tmp_path, monkeypatch):
    """Подмена адреса строки на несуществующий -> сборка падает."""
    metrics_dir = _write_metrics(tmp_path)

    def _mutate(economics_map):
        for row_map in economics_map["rows"]:
            if row_map["id"] == "spend_total_rub":
                row_map["path"] = ["total_rub_typo"]

    _patch_map(monkeypatch, _mutate)

    with pytest.raises(build_report.EconomicsMapError) as excinfo:
        build_report.load_report_economics(metrics_dir, DEGRADATION)

    message = str(excinfo.value)
    assert build_report.ECONOMICS_MAP_FILENAME in message
    assert "spend_total_rub" in message
    assert "total_rub_typo" in message


def test_unresolvable_map_address_in_section_fails_the_build(tmp_path, monkeypatch):
    """Тот же запрет для верхнеуровневых секций карты, не только для rows."""
    metrics_dir = _write_metrics(tmp_path, money_frame=_money_frame_with("L0"))

    _patch_map(
        monkeypatch,
        lambda m: m["attribution_level"].__setitem__(
            "path", [{"where": {"field": "kind", "equals": "attribution"}}, "level_typo"]
        ),
    )

    with pytest.raises(build_report.EconomicsMapError) as excinfo:
        build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert "attribution_level" in str(excinfo.value)


def test_marked_address_is_allowed_to_stay_unresolvable(tmp_path, monkeypatch):
    """Помеченный source_status адрес не роняет сборку — это второй путь."""
    metrics_dir = _write_metrics(tmp_path)

    def _mutate(economics_map):
        for row_map in economics_map["rows"]:
            if row_map["id"] == "spend_total_rub":
                row_map["path"] = ["total_rub_typo"]
                row_map["source_status"] = build_report.MAP_SOURCE_STATUS_NOT_EMITTED

    _patch_map(monkeypatch, _mutate)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert _row(economics, "spend_total_rub")["status"] == build_report.ECONOMICS_STATUS_MISSING


def test_missing_metrics_file_does_not_fail_validation(tmp_path):
    """Отсутствие файла целиком — управляемая деградация, не ошибка адреса."""
    metrics_dir = _write_metrics(tmp_path, cost_summary=None)

    economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert _row(economics, "spend_total_rub")["status"] == (
        build_report.ECONOMICS_SECTION_NOT_COMPUTED
    )


def test_real_map_addresses_resolve_on_real_client_metrics():
    """Карта из репозитория на фактических metrics pognali.rent: L0, не L_UNKNOWN.

    money_frame.json клиента может быть старее задачи 7G и не нести строки
    уровня — тогда она достраивается тем же кодом compute из canonical
    клиента, чтобы проверялся факт данных, а не зафиксированный артефакт.
    """
    client_metrics = REPO_ROOT / "clients" / "pognali.rent" / "data" / "metrics"
    if not (client_metrics / "acquisition_economics.json").exists():
        pytest.skip("нет фактических metrics клиента pognali.rent")

    money_frame = json.loads(
        (client_metrics / "money_frame.json").read_text(encoding="utf-8")
    )
    if not any(row.get("kind") == "attribution" for row in money_frame):
        money_frame.append(_pognali_attribution_row())

    with tempfile.TemporaryDirectory() as tmp:
        metrics_dir = Path(tmp)
        for name in ("cost_summary.json", "acquisition_economics.json"):
            shutil.copy(client_metrics / name, metrics_dir / name)
        (metrics_dir / "money_frame.json").write_text(
            json.dumps(money_frame, ensure_ascii=False), encoding="utf-8"
        )

        economics = build_report.load_report_economics(metrics_dir, DEGRADATION)

    assert economics["attribution_level"] == "L0"
    assert economics["attribution_level_source"]["resolved"] is True
    assert economics["unique_customers"]["available"] is False


def _pognali_attribution_row() -> dict:
    """Строка уровня, посчитанная compute по canonical клиента (7G)."""
    from src.compute import money_frame as money_frame_mod
    from src.pipeline import orchestrator as orchestrator_mod

    paths = orchestrator_mod.ClientPaths("pognali.rent")
    defaults = orchestrator_mod.load_defaults()
    attribution = money_frame_mod.compute_attribution(paths, defaults)
    if attribution is None:
        pytest.skip("секция attribution не задана в config/defaults.yaml")
    return money_frame_mod._attribution_row(attribution)
