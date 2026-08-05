"""Тесты секции «Экономика привлечения» отчёта (задача 7F).

Секция рендерится из контракта 7E (``load_report_economics``) и ничего не
считает. Сценарии:
1. Секция идёт второй — сразу после вердикта и до карточек находок.
2. Пустой ``findings/approved/`` -> секция всё равно в отчёте (базовая рамка,
   а не находка).
3. L0 -> пункт «Стоимость сделки по источникам» текстовый (не таблица) и не
   содержит ни одного числа.
4. L0 -> в секции нет слов CAC / ROI / LTV / ROMI / «прибыль».
5. Отсутствие CRM -> пункты 2 и 3 «не посчитано», пункты 1 и 4 рендерятся.
6. Расход печатается построчно на статью с пометкой базы НДС у каждой строки.
7. Результат периода — два отдельных числа (CRM и веб-конверсии), без
   приведения к одному.
8. Каждая цифра несёт сноску [4]/[5]/[6] на таблицу приложения; таблицы
   пишутся.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.report import build_report  # noqa: E402


class _Paths:
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

COST_SUMMARY = {
    "money_basis": "gross_final_rub",
    "component_total_columns": ["component_id", "channel", "kind", "amount_rub"],
    "component_total_rows": [
        ["contractor_fixed", "agency", "fixed", 517500.0],
        ["direct_media", "direct", "media", 920161.44],
        ["services", "site", "services", 142500.0],
    ],
    "channel_total_columns": ["channel", "amount_rub"],
    "channel_total_rows": [["direct", 920161.44]],
    "component_columns": ["month", "component_id", "channel", "kind", "amount_rub"],
    "component_rows": [],
    "monthly_total_columns": ["month", "amount_rub"],
    "monthly_total_rows": [["2025-07", 99967.13]],
    "total_rub": 1580161.44,
    "limitations": [],
}

CRM_BLOCK = {
    "record_unit": "paid_booking",
    "record_count": 1357,
    "total_revenue_rub": 24281956.0,
    "average_revenue_rub": 17893.85,
    "median_revenue_rub": 9100.0,
    "unique_customers": None,
    "average_revenue_per_customer_rub": None,
    "limitations": [],
    "status": "ok",
}

MODEL_CRM_ESTIMATE = {
    "id": "estimated_site_booking",
    "mode": "crm_share_estimate",
    "status": "ok",
    "basis": "estimate",
    "result_name": "оценочная стоимость сайт-брони",
    "numerator": {"money_basis": "gross_final_rub", "amount_rub": 1580161.44, "components": []},
    "denominator": {"value": 1221.3, "method": {"type": "crm_share_estimate"}},
    "formula": "gross_spend_rub / denominator_records_or_visits",
    "value_rub": 1293.83,
    "unit": "rub_per_paid_booking",
    "assumptions": [{"code": "client_configured_crm_share", "value": 0.9}],
    "limitations": [],
}

MODEL_TRACKED = {
    "id": "tracked_direct_booking",
    "mode": "tracked_funnel",
    "status": "ok",
    "basis": "tracked_proxy",
    "result_name": "стоимость отслеженной отправки формы",
    "numerator": {"money_basis": "gross_final_rub", "amount_rub": 920161.44, "components": []},
    "denominator": {"value": 280, "method": {"type": "tracked_funnel_unique_visits"}},
    "formula": "gross_spend_rub / denominator_records_or_visits",
    "value_rub": 3286.29,
    "unit": "rub_per_tracked_conversion",
    "assumptions": [],
    "limitations": [],
}

DEGRADATION = {"counts": {"total": 100, "runnable": 80, "skipped": 20}, "skipped": []}


def _acquisition(crm: dict | None = None, models: list | None = None) -> dict:
    payload: dict = {
        "money_basis": "gross_final_rub",
        "models": models if models is not None else [MODEL_CRM_ESTIMATE, MODEL_TRACKED],
    }
    if crm is not None:
        payload["crm"] = crm
    return payload


def _money_frame(
    attribution_level: str | None = "L0",
    unique_available: bool = False,
    unique_status: str | None = None,
) -> list:
    """money_frame.json со строкой ``kind="attribution"`` — так пишет compute.

    ``attribution_level=None`` -> строки нет вовсе (секция ``attribution``
    не задана в defaults): контракт фиксирует L_UNKNOWN.
    """
    if attribution_level is None:
        return []
    return [{
        "check_id": "M",
        "kind": "attribution",
        "metric": "attribution_level",
        "value": attribution_level,
        "attribution_level": attribution_level,
        "attribution_evidence": [{"role": "table", "table": "crm", "present": True}],
        "unique_customers_available": unique_available,
        "unique_customers_status": unique_status or (
            "available" if unique_available else "not_computable"
        ),
        "unique_customers_reason": None,
    }]


def _write_inputs(
    paths: _Paths,
    acquisition: dict | None = None,
    cost_summary: dict | None = COST_SUMMARY,
    money_frame: list | None = None,
) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    payloads = {
        "degradation_report.json": DEGRADATION,
        "metrics_summary.json": {"counts": DEGRADATION["counts"]},
        "cost_summary.json": cost_summary,
        "acquisition_economics.json": (
            acquisition if acquisition is not None else _acquisition(crm=CRM_BLOCK)
        ),
        "money_frame.json": money_frame if money_frame is not None else _money_frame(),
    }
    for filename, payload in payloads.items():
        if payload is None:
            continue
        (paths.metrics / filename).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


def _report_text(tmp_path: Path, **kwargs) -> str:
    paths = _Paths(tmp_path)
    _write_inputs(paths, **kwargs)
    out_path = build_report.build(paths, CONFIG, DEFAULTS)
    return Path(out_path).read_text(encoding="utf-8")


def _section(text: str) -> str:
    return text.split(build_report.ECONOMICS_SECTION_TITLE)[1].split("## Резюме")[0]


def _item(section: str, header: str) -> str:
    return section.split(header)[1].split("### ")[0]


# ── 1-2. Место секции в отчёте и независимость от approved ───────────────
def test_economics_section_goes_second_after_verdict_before_findings(tmp_path):
    text = _report_text(tmp_path)

    assert text.index("## Вердикт") < text.index(build_report.ECONOMICS_SECTION_TITLE)
    assert text.index(build_report.ECONOMICS_SECTION_TITLE) < text.index("## Ключевые находки")
    assert text.index(build_report.ECONOMICS_SECTION_TITLE) < text.index("## План действий")


def test_economics_section_rendered_with_empty_approved(tmp_path):
    """Гейт утверждённых находок на секцию не влияет — это базовая рамка."""
    text = _report_text(tmp_path)

    assert "Утверждённых находок нет." in text  # approved пуст
    section = _section(text)
    assert "### Полный расход за период" in section
    assert "### Результат периода" in section
    assert "### Общая стоимость одной сделки" in section
    assert "### Стоимость веб-конверсии" in section
    assert "### Стоимость сделки по источникам" in section


def test_attribution_level_wording_comes_from_report_economics(tmp_path):
    for level in ("L0", "L1", "L2"):
        text = _report_text(tmp_path / level, money_frame=_money_frame(level))
        assert f"_Уровень атрибуции: {level} —" in _section(text)


# ── 3. L0: пункт 5 — текст без чисел, не таблица ─────────────────────────
def test_l0_cost_per_deal_is_text_without_numbers(tmp_path):
    section = _section(_report_text(tmp_path))
    part = _item(section, "### Стоимость сделки по источникам")

    assert re.search(r"\d", part) is None
    assert "|" not in part  # не таблица
    assert "источник сделки не фиксируется в CRM" in part
    # что именно нельзя посчитать и что внедрить, чтобы стало можно
    assert "не считается" in part
    assert "поле источника" in part


def test_l0_cost_per_deal_never_inherits_web_conversion_value(tmp_path):
    section = _section(_report_text(tmp_path))
    part = _item(section, "### Стоимость сделки по источникам")

    assert "3 286" not in part
    assert "1 294" not in part


# ── 4. L0: запрещённые термины ───────────────────────────────────────────
def test_l0_section_has_no_forbidden_terms(tmp_path):
    section = _section(_report_text(tmp_path)).lower()

    for term in ("cac", "roi", "ltv", "romi", "прибыл"):
        assert term not in section, term


def test_web_conversion_header_is_literal(tmp_path):
    section = _section(_report_text(tmp_path))
    header = "### Стоимость веб-конверсии"
    table = _item(section, header)

    assert header in section
    table_header = next(line for line in table.splitlines() if line.startswith("| источник"))
    assert "стоимость веб-конверсии" in table_header
    for wrong in ("лид", "заявк", "клиент"):
        assert wrong not in table_header.lower(), wrong


# ── 5. Отсутствие CRM ────────────────────────────────────────────────────
CRM_BLOCK_UNAVAILABLE = {
    # Так compute пишет недоступную CRM: ключи на месте, значения пустые
    # (src/compute/acquisition_economics.py, _crm_summary) — «нет ключа»
    # в этом файле не бывает, бывает «нет значения».
    "record_unit": "unknown",
    "record_count": None,
    "total_revenue_rub": None,
    "average_revenue_rub": None,
    "median_revenue_rub": None,
    "unique_customers": None,
    "average_revenue_per_customer_rub": None,
    "limitations": [{"code": "crm_unavailable"}],
    "status": "unavailable",
}


def _acquisition_without_crm() -> dict:
    """CRM недоступна: блок crm пуст, CRM-модель без знаменателя и значения."""
    unavailable = dict(
        MODEL_CRM_ESTIMATE,
        status="unavailable",
        value_rub=None,
        denominator={"value": None, "method": {"type": "crm_share_estimate"}},
        limitations=[{"code": "crm_unavailable"}],
    )
    return _acquisition(crm=CRM_BLOCK_UNAVAILABLE, models=[unavailable, MODEL_TRACKED])


def test_missing_crm_marks_result_and_total_cost_not_computed(tmp_path):
    section = _section(_report_text(tmp_path, acquisition=_acquisition_without_crm()))

    result = _item(section, "### Результат периода")
    total = _item(section, "### Общая стоимость одного обращения")

    assert build_report.ECONOMICS_STATUS_MISSING in result
    assert "Сделки и клиенты по данным CRM:** не посчитано" in result
    assert build_report.ECONOMICS_STATUS_MISSING in total
    assert "1 294" not in total  # цифра не подставляется вместо CRM


def test_missing_crm_keeps_spend_and_web_conversion_rendered(tmp_path):
    section = _section(_report_text(tmp_path, acquisition=_acquisition_without_crm()))

    spend = _item(section, "### Полный расход за период")
    web = _item(section, "### Стоимость веб-конверсии")

    assert "**direct_media**" in spend
    assert "1 580 161 ₽" in spend
    assert "| стоимость отслеженной отправки формы |" in web
    assert "3 286 ₽" in web


# ── 6. Расход построчно с базой НДС ──────────────────────────────────────
def test_spend_lists_every_component_with_vat_basis(tmp_path):
    spend = _item(_section(_report_text(tmp_path)), "### Полный расход за период")

    for component_id in ("contractor_fixed", "direct_media", "services"):
        assert f"**{component_id}**" in spend
    assert spend.count("база НДС:") == 4  # три статьи + итог
    assert "920 161 ₽" in spend
    assert "**Итого расход за период:** 1 580 161 ₽" in spend


def test_spend_not_computed_without_cost_summary(tmp_path):
    section = _section(_report_text(tmp_path, cost_summary=None))
    spend = _item(section, "### Полный расход за период")

    assert build_report.ECONOMICS_STATUS_MISSING in spend
    # причина — фраза из закрытого словаря, а не имя файла (задача 7H)
    assert "данные этого источника за период не собраны" in spend
    assert "₽" not in spend  # ни одной подставленной цифры


# ── 7. Результат периода — два отдельных числа ───────────────────────────
def test_result_shows_two_separate_numbers_with_explanation(tmp_path):
    result = _item(_section(_report_text(tmp_path)), "### Результат периода")

    assert "**Сделки и клиенты по данным CRM:** 1 357" in result
    assert "**Веб-конверсии с сайта:** 280" in result
    assert "сводить их к одному нельзя" in result
    assert "ограничивает выводы уровнем веб-конверсии" in result


def test_total_cost_is_single_figure_for_all_channels(tmp_path):
    total = _item(_section(_report_text(tmp_path)), "### Общая стоимость одной сделки")

    assert "**1 294 ₽**" in total
    assert "по всем каналам сразу" in total
    assert total.count("₽") == 1  # ровно одна цифра


# ── 8. Сноски и таблицы приложения ───────────────────────────────────────
def test_every_economics_figure_carries_footnote(tmp_path):
    section = _section(_report_text(tmp_path))

    for line in section.splitlines():
        if "₽" not in line and not re.search(r"\d", line):
            continue
        if line.startswith("_") or line.startswith("|---"):
            continue
        if "₽" in line or "Сделки и клиенты" in line or "Веб-конверсии с сайта" in line:
            assert re.search(r"\[[456]\]", line), line


def test_economics_appendix_tables_written(tmp_path):
    paths = _Paths(tmp_path)
    _write_inputs(paths)
    build_report.build(paths, CONFIG, DEFAULTS)

    tables_dir = paths.report / build_report.APPENDIX_TABLES_DIRNAME

    with (tables_dir / build_report.ECONOMICS_SPEND_CSV).open(encoding="utf-8") as fh:
        spend_rows = list(csv.DictReader(fh))
    assert [row["component_id"] for row in spend_rows] == [
        "contractor_fixed", "direct_media", "services",
    ]
    assert {row["money_basis"] for row in spend_rows} == {"gross_final_rub"}

    with (tables_dir / build_report.ECONOMICS_WEB_CONVERSION_CSV).open(encoding="utf-8") as fh:
        web_rows = list(csv.DictReader(fh))
    assert [row["id"] for row in web_rows] == ["tracked_direct_booking"]

    with (tables_dir / build_report.ECONOMICS_RESULT_CSV).open(encoding="utf-8") as fh:
        result_rows = list(csv.DictReader(fh))
    assert "crm_record_count" in {row["id"] for row in result_rows}
    assert "cost_per_deal_by_source" in {row["id"] for row in result_rows}


def test_footnotes_section_lists_economics_tables(tmp_path):
    text = _report_text(tmp_path)

    footnotes = text.split("## Сноски")[1]
    assert f"appendix_tables/{build_report.ECONOMICS_SPEND_CSV}" in footnotes
    assert f"appendix_tables/{build_report.ECONOMICS_RESULT_CSV}" in footnotes
    assert f"appendix_tables/{build_report.ECONOMICS_WEB_CONVERSION_CSV}" in footnotes


# ── 9. Клиентская безопасность текста секции (задача 7H) ─────────────────
# Адрес ключа в секции узнаётся по форме «слово_слово.слово_слово» —
# именно так печатался внутренний путь до задачи 7H.
_ADDRESS_TOKEN = re.compile(r"[a-z]+_[a-z_]*\.[a-z]+_[a-z_]*")
_FORBIDDEN_SUBSTRINGS = ("task", "7G", "7H", "check_id", "json", "parquet", "compute", "metrics")


def _forbidden_report_variants(tmp_path: Path) -> list[tuple[str, str]]:
    """Секция при разных состояниях данных — включая все ветки «не посчитано»."""
    return [
        ("полные данные", _section(_report_text(tmp_path / "full"))),
        (
            "нет CRM",
            _section(_report_text(tmp_path / "nocrm", acquisition=_acquisition_without_crm())),
        ),
        ("нет расхода", _section(_report_text(tmp_path / "nospend", cost_summary=None))),
        (
            "нет строки уровня",
            _section(_report_text(tmp_path / "nolevel", money_frame=_money_frame(None))),
        ),
        (
            "склейка есть",
            _section(
                _report_text(tmp_path / "unique", money_frame=_money_frame("L0", True))
            ),
        ),
    ]


def test_section_never_contains_key_address_token(tmp_path):
    for label, section in _forbidden_report_variants(tmp_path):
        for line in section.splitlines():
            assert _ADDRESS_TOKEN.search(line) is None, f"{label}: {line}"


def test_section_never_contains_internal_substrings(tmp_path):
    for label, section in _forbidden_report_variants(tmp_path):
        for token in _FORBIDDEN_SUBSTRINGS:
            assert token not in section, f"{label}: {token}"


def test_unknown_reason_code_falls_back_to_closed_phrase(tmp_path):
    """Причина без фразы в словаре -> «данных источника недостаточно»."""
    economics = {"client_reason_phrases": {"default": "данных источника недостаточно"}}

    assert build_report._client_reason(economics, "какой_то_внутренний_код") == (
        "данных источника недостаточно"
    )
    assert build_report._client_reason(economics, None) == "данных источника недостаточно"
    assert build_report._client_reason({}, "x") == build_report.CLIENT_REASON_FALLBACK


# ── 10. Пункт 3 по факту склейки повторных (задача 7H) ───────────────────
def test_l0_without_unique_customers_title_avoids_the_word_customer(tmp_path):
    section = _section(_report_text(tmp_path, money_frame=_money_frame("L0", False)))

    header = next(
        line for line in section.splitlines() if line.startswith("### Общая стоимость")
    )
    assert header.startswith("### Общая стоимость одной сделки")
    assert "клиент" not in _item(section, header).lower()


def test_lead_record_unit_renames_item_to_obraschenie(tmp_path):
    """Единица записи CRM = обращение -> «стоимость одного обращения»."""
    crm = dict(CRM_BLOCK, record_unit="lead")
    section = _section(
        _report_text(tmp_path, acquisition=_acquisition(crm=crm), money_frame=_money_frame("L0"))
    )

    assert "### Общая стоимость одного обращения" in section
    assert "### Общая стоимость одной сделки" not in section


def test_unique_customers_available_switches_to_customer_cost(tmp_path):
    section = _section(_report_text(tmp_path, money_frame=_money_frame("L0", True)))
    item = _item(section, "### Общая стоимость клиента")

    assert "**1 294 ₽**" in item
    assert "Повторные обращения:" in item  # отдельная строка про повторные


# ── 11. not_computable против not_computed_yet (задача 7H) ───────────────
def test_not_computable_and_not_computed_yet_render_differently(tmp_path):
    permanent = _item(
        _section(
            _report_text(
                tmp_path / "permanent",
                money_frame=_money_frame("L0", False, unique_status="not_computable"),
            )
        ),
        "### Общая стоимость одной сделки",
    )
    temporary = _item(
        _section(
            _report_text(
                tmp_path / "temporary",
                money_frame=_money_frame("L0", False, unique_status="not_computed_yet"),
            )
        ),
        "### Общая стоимость одной сделки",
    )

    assert permanent != temporary
    assert "Постоянное ограничение" in permanent
    assert "нужно внедрить" in permanent          # что именно внедрять
    assert "Временно отсутствует" in temporary
    assert "Внедрять ничего не нужно" in temporary
    assert "Постоянное ограничение" not in temporary


def test_new_glossary_terms_present_and_distinct():
    glossary = {entry["term"]: entry["definition"] for entry in build_report.load_glossary()}

    for term in (
        "веб-конверсия",
        "стоимость веб-конверсии",
        "сделка",
        "клиент",
        "общая стоимость клиента",
        "уровень атрибуции (L0 / L1 / L2)",
    ):
        assert term in glossary, term
    assert glossary["стоимость веб-конверсии"] != glossary["общая стоимость клиента"]
    assert "не равна стоимости веб-конверсии" in glossary["общая стоимость клиента"]
    assert "не совпадают" in glossary["сделка"]


def test_glossary_separates_deal_from_customer_by_repeat_merge():
    """Задача 7H: «сделка» и «клиент» различаются признаком склейки повторных."""
    glossary = {entry["term"]: entry["definition"] for entry in build_report.load_glossary()}

    assert glossary["сделка"] != glossary["клиент"]
    assert "не склеиваются" in glossary["сделка"]
    assert "склеены" in glossary["клиент"]
    assert "не больше, чем сделок" in glossary["клиент"]
