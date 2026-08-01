"""Тесты блока 3 compute: C01–C25 (CRO, сайт, воронка до обращения).

Задача 5G (C01–C12): обязательные сценарии из промта — воронка по сегментам
(C06), CrUX отсутствует -> ручной лабораторный замер с confidence MED (C01),
ручной input отсутствует -> unavailable (C03). Плюс минимум один сценарий на
остальные проверки C02/C04/C05/C07/C08/C09/C10/C11/C12.

Задача 5H (C13–C25): минимум один сценарий на каждую проверку — client_facts
(C13/C24), полностью ручные (C14/C17/C23), A+B без авто-части (C15/C16/C18/
C25), всегда-unavailable по структурному разрыву (C19/C22), device-конверсия
для попапов (C20), browser/os/screen сегментация (C21).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import yaml

from src.compute import block3, candidates  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_block0.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


DEFAULTS = {
    "min_sample_visits": 500,
    "significance_alpha": 0.05,
    "crux_min_field_data": True,
}


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visits.parquet")


def _write_visit_goals(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visit_goals.parquet")


def _write_funnels_config(paths: _Paths) -> None:
    paths.config_file.write_text(yaml.safe_dump({
        "funnels": {
            "booking": [
                {"stage": "open", "goal_ids": [1]},
                {"stage": "submit", "goal_ids": [2]},
            ]
        }
    }), encoding="utf-8")


def _write_site_pages(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "site_pages.parquet")


def _write_crux_raw(paths: _Paths, data: dict) -> None:
    out_dir = paths.raw / "crux"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "crux.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_input_yaml(paths: _Paths, name: str, data: dict) -> None:
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _write_d01(paths: _Paths, rows: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "d01.json").write_text(json.dumps(rows), encoding="utf-8")


def _write_degradation(paths: _Paths, checks: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps({"checks": checks}), encoding="utf-8"
    )


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _base_visit(**overrides) -> dict:
    row = {
        "device": "desktop", "source_group": "organic", "entry_page": "/",
        "is_ad": False, "form_open": False, "form_submit": False,
        "form_submit_count": 0, "call_click": False, "messenger_click": False,
    }
    row.update(overrides)
    return row


# ── C01 — медленная загрузка на мобильных ────────────────────────────────────
def test_c01_crux_field_data_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_crux_raw(paths, {
        "cwv_field_data_available": True,
        "records": [{
            "target_type": "origin", "target": "https://example.com",
            "field_data_available": True,
            "p75": {"largest_contentful_paint": 5000, "cumulative_layout_shift": 0.05},
        }],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C01"})

    assert "c01" in artifacts
    rows = _read_metric(paths, "c01")
    field_row = next(r for r in rows if r["finding"] == "field_cwv")
    assert field_row["largest_contentful_paint_rating"] == "poor"
    assert field_row["cumulative_layout_shift_rating"] == "good"
    assert field_row["any_metric_poor"] is True
    assert field_row["device_specific"] is False
    assert field_row["confidence"] == "MED"


def test_c01_crux_absent_falls_back_to_manual_med(tmp_path):
    """CrUX отсутствует -> ручной лабораторный замер, confidence принудительно MED."""
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "manual_cwv", {
        "meta": {"tested_at": "2026-07-20", "tool": "Lighthouse", "device": "mobile"},
        "patterns": [{"url": "/", "lcp_ms": 6000, "cls": 0.3, "inp_ms": 100, "note": "тяжёлые изображения"}],
        "conclusions": [],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C01"})

    assert "c01" in artifacts
    rows = _read_metric(paths, "c01")
    manual_row = next(r for r in rows if r["finding"] == "manual_lab_cwv")
    assert manual_row["source"] == "manual_lab"
    assert manual_row["lcp_rating"] == "poor"
    assert manual_row["cls_rating"] == "poor"
    assert manual_row["confidence"] == "MED"


def test_c01_neither_source_available_is_unavailable(tmp_path):
    """Ни CrUX, ни ручной замер -> unavailable, а не молчаливый пропуск."""
    paths = _Paths(tmp_path)

    artifacts = block3.run(paths, DEFAULTS, {"C01"})

    assert "c01" in artifacts
    rows = _read_metric(paths, "c01")
    assert rows[0]["check_id"] == "C01"
    assert rows[0]["status"] == "unavailable"
    assert rows[0]["row_role"] == "limitation"
    assert rows[0]["candidate"] is False


def test_c01_crux_404_no_field_data_falls_back(tmp_path):
    """cwv_field_data_available=false (штатный 404) -> тоже фолбэк на ручной замер."""
    paths = _Paths(tmp_path)
    _write_crux_raw(paths, {"cwv_field_data_available": False, "records": []})
    _write_input_yaml(paths, "manual_cwv", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"url": "/", "lcp_ms": 2000, "cls": 0.05, "inp_ms": 100}],
    })

    block3.run(paths, DEFAULTS, {"C01"})

    rows = _read_metric(paths, "c01")
    manual_row = next(r for r in rows if r["finding"] == "manual_lab_cwv")
    assert manual_row["lcp_rating"] == "good"


# ── C02 — отдельные шаблоны значительно медленнее среднего ──────────────────
def test_c02_template_significantly_slower_than_origin(tmp_path):
    paths = _Paths(tmp_path)
    _write_crux_raw(paths, {
        "cwv_field_data_available": True,
        "records": [
            {
                "target_type": "origin", "target": "https://example.com",
                "field_data_available": True,
                "p75": {"largest_contentful_paint": 2000},
            },
            {
                "target_type": "url", "target": "https://example.com/checkout",
                "field_data_available": True,
                "p75": {"largest_contentful_paint": 4000},
            },
        ],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C02"})

    assert "c02" in artifacts
    rows = _read_metric(paths, "c02")
    row = next(r for r in rows if r["target"] == "https://example.com/checkout")
    assert row["largest_contentful_paint_ratio_to_origin"] == 2.0
    assert row["template_significantly_slower"] is True


def test_c02_no_key_urls_checked_writes_empty(tmp_path):
    paths = _Paths(tmp_path)
    _write_crux_raw(paths, {
        "cwv_field_data_available": True,
        "records": [{
            "target_type": "origin", "target": "https://example.com",
            "field_data_available": True, "p75": {"largest_contentful_paint": 2000},
        }],
    })

    block3.run(paths, DEFAULTS, {"C02"})

    assert _read_metric(paths, "c02") == []


# ── C03/C08/C11 — полностью ручные проверки ─────────────────────────────────
def test_c03_manual_form_tests_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20", "tester": "аналитик"},
        "patterns": [{"step": "калькулятор", "issue": "JS-ошибка при выборе даты",
                      "severity": "critical"}],
        "conclusions": [{"conclusion": "форма ломается на Safari", "confidence": "LOW"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C03"})

    assert "c03" in artifacts
    rows = _read_metric(paths, "c03")
    pattern_row = next(r for r in rows if r["finding"] == "manual_pattern")
    assert pattern_row["issue"] == "JS-ошибка при выборе даты"
    assert pattern_row["confidence"] == "MED"
    conclusion_row = next(r for r in rows if r["finding"] == "manual_conclusion")
    assert conclusion_row["confidence"] == "LOW"


def test_c03_manual_form_tests_absent_is_unavailable(tmp_path):
    """Ручной input отсутствует (шаблон не заполнен) -> unavailable."""
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])

    artifacts = block3.run(paths, DEFAULTS, {"C03"})

    assert "c03" in artifacts
    rows = _read_metric(paths, "c03")
    assert rows[0]["status"] == "unavailable"


def test_c08_reuses_same_manual_bucket_mechanism(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "телефон", "issue": "маска не даёт ввести +7"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C08"})

    assert "c08" in artifacts
    rows = _read_metric(paths, "c08")
    assert any(r.get("check_id") == "C08" and r["finding"] == "manual_pattern" for r in rows)


def test_c11_runs_without_site_pages_when_manual_form_tests_filled(tmp_path):
    """FIX-input-tables-manifest-gate (расширенная версия): requires теперь
    [manual_form_tests], не [site_crawl] — C11 считается без site_pages в
    canonical, пока в runnable_ids (что определяет заполненная анкета форм)."""
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "manual_form_tests", {"meta": {"tested_at": "2026-07-20"}})

    artifacts = block3.run(paths, DEFAULTS, {"C11"})

    assert "c11" in artifacts
    assert (paths.metrics / "c11.json").exists()


# ── C04 — 404/5xx на посадочных ─────────────────────────────────────────────
def test_c04_flags_broken_landing_and_uncrawled(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [
        _base_visit(entry_page="/broken", is_ad=True) for _ in range(5)
    ] + [
        _base_visit(entry_page="/ok") for _ in range(3)
    ] + [
        _base_visit(entry_page="/not-crawled") for _ in range(2)
    ])
    _write_site_pages(paths, [
        {"url": "https://example.com/broken", "http_status": 404,
         "redirect_chain": "[]", "final_url": "https://example.com/broken"},
        {"url": "https://example.com/ok", "http_status": 200,
         "redirect_chain": "[]", "final_url": "https://example.com/ok"},
    ])

    artifacts = block3.run(paths, DEFAULTS, {"C04"})

    assert "c04" in artifacts
    rows = _read_metric(paths, "c04")
    broken = next(r for r in rows if r["entry_page"] == "/broken")
    ok = next(r for r in rows if r["entry_page"] == "/ok")
    uncrawled = next(r for r in rows if r["entry_page"] == "/not-crawled")
    assert broken["broken_landing"] is True
    assert broken["ad_visit_count"] == 5
    assert ok["broken_landing"] is False
    assert uncrawled["crawled"] is False
    assert uncrawled["broken_landing"] is False


def test_c04_without_site_pages_is_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit(entry_page="/")])

    block3.run(paths, DEFAULTS, {"C04"})

    rows = _read_metric(paths, "c04")
    assert rows[0]["status"] == "unavailable"


# ── C05 — лишние редиректы и цепочки ─────────────────────────────────────────
def test_c05_flags_excessive_redirect_chain(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [
        _base_visit(entry_page="/chain") for _ in range(4)
    ] + [
        _base_visit(entry_page="/single-hop") for _ in range(4)
    ])
    _write_site_pages(paths, [
        {"url": "https://example.com/chain", "http_status": 200,
         "redirect_chain": json.dumps(["https://example.com/old1", "https://example.com/old2"]),
         "final_url": "https://example.com/chain-final"},
        {"url": "https://example.com/single-hop", "http_status": 200,
         "redirect_chain": json.dumps(["https://example.com/old"]),
         "final_url": "https://example.com/single-hop-final"},
    ])

    block3.run(paths, DEFAULTS, {"C05"})

    rows = _read_metric(paths, "c05")
    chain_row = next(r for r in rows if r["entry_page"] == "/chain")
    single_row = next(r for r in rows if r["entry_page"] == "/single-hop")
    assert chain_row["redirect_hops"] == 2
    assert chain_row["excessive_redirect_chain"] is True
    assert single_row["redirect_hops"] == 1
    assert single_row["excessive_redirect_chain"] is False
    assert single_row["utm_preservation_verifiable"] is False


# ── C06 — воронка open->submit по сегментам ─────────────────────────────────
def test_c06_funnel_summary_and_segments(tmp_path):
    paths = _Paths(tmp_path)
    visits = []
    visit_goals = []
    for index in range(601):
        is_desktop = index < 400
        submits = index < 300 or 400 <= index < 450
        visit_id = f"v{index}"
        visits.append(_base_visit(
            visit_id=visit_id,
            date="2026-01-01",
            device="desktop" if is_desktop else "mobile",
            source_group="ad" if is_desktop else "organic",
        ))
        if index < 600:
            visit_goals.append({"visit_id": visit_id, "goal_id": 1, "achievement_count": 1})
        if submits:
            visit_goals.append({"visit_id": visit_id, "goal_id": 2, "achievement_count": 1})
    _write_visits(paths, visits)
    _write_visit_goals(paths, visit_goals)
    _write_funnels_config(paths)

    artifacts = block3.run(paths, DEFAULTS, {"C06"})

    assert "c06" in artifacts
    rows = _read_metric(paths, "c06")
    summary = next(r for r in rows if r["finding"] == "funnel_summary")
    assert summary["first_stage_visits"] == 600
    assert summary["last_stage_visits"] == 350
    assert summary["confidence"] == "HIGH"  # 600 >= min_sample_visits=500

    device_segments = [r for r in rows if r["finding"] == "funnel_by_segment" and r["segment_dimension"] == "device"]
    desktop_seg = next(r for r in device_segments if r["segment_value"] == "desktop")
    mobile_seg = next(r for r in device_segments if r["segment_value"] == "mobile")
    assert desktop_seg["first_stage_visits"] == 400
    assert desktop_seg["first_to_last_rate"] == 0.75
    assert mobile_seg["first_stage_visits"] == 200
    assert mobile_seg["first_to_last_rate"] == 0.25
    assert mobile_seg["confidence"] == "MED"  # 200 < min_sample_visits=500

    source_segments = [r for r in rows if r["finding"] == "funnel_by_segment" and r["segment_dimension"] == "channel"]
    assert {r["segment_value"] for r in source_segments} == {"ad", "organic"}


# ── C07 — лишние обязательные поля (контекст + вебвизор) ────────────────────
def test_c07_context_and_webvisor_enrichment(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, (
        [_base_visit(form_open=True, form_submit=True) for _ in range(10)]
        + [_base_visit(form_open=True, form_submit=False) for _ in range(20)]
    ))
    _write_input_yaml(paths, "webvisor_findings", {
        "meta": {"sessions_reviewed": 15, "date": "2026-07-20", "filter": "брошенная форма"},
        "patterns": [{"pattern": "остановка на поле дат", "count": 8, "of_total": 15}],
        "conclusions": [],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C07"})

    assert "c07" in artifacts
    rows = _read_metric(paths, "c07")
    context_row = next(r for r in rows if r["finding"] == "form_abandonment_context")
    assert context_row["form_open_visits"] == 30
    assert context_row["field_level_granularity_available"] is False
    webvisor_row = next(r for r in rows if r["finding"] == "manual_pattern")
    assert webvisor_row["pattern"] == "остановка на поле дат"
    assert webvisor_row["confidence"] == "MED"


def test_c07_without_webvisor_only_context(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit(form_open=True, form_submit=True)])

    block3.run(paths, DEFAULTS, {"C07"})

    rows = _read_metric(paths, "c07")
    assert all(r["finding"] == "form_abandonment_context" for r in rows)


# ── C09 — мобильные элементы неудобны (device CR) ───────────────────────────
def test_c09_mobile_underperforms_desktop(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(device="desktop", form_submit=True) for _ in range(200)]
        + [_base_visit(device="desktop", form_submit=False) for _ in range(100)]
        + [_base_visit(device="mobile", form_submit=True) for _ in range(10)]
        + [_base_visit(device="mobile", form_submit=False) for _ in range(90)]
    )
    _write_visits(paths, visits)

    artifacts = block3.run(paths, DEFAULTS, {"C09"})

    assert "c09" in artifacts
    rows = _read_metric(paths, "c09")
    mobile_row = next(r for r in rows if r["finding"] == "device_conversion" and r["device"] == "mobile")
    desktop_row = next(r for r in rows if r["finding"] == "device_conversion" and r["device"] == "desktop")
    assert desktop_row["form_submit_rate"] == pytest_approx(200 / 300)
    assert mobile_row["form_submit_rate"] == pytest_approx(10 / 100)
    assert mobile_row["device_underperforms_desktop"] is True


def pytest_approx(value: float) -> float:
    """Локальный помощник вместо pytest.approx — сравнение по round(...,4) в коде."""
    return round(value, 4)


# ── C10 — нет подтверждения отправки (сверка с D01.overtrigger) ────────────
def test_c10_confounded_by_goal_overtrigger(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, (
        [_base_visit(form_submit=True, form_submit_count=3) for _ in range(50)]
        + [_base_visit(form_submit=True, form_submit_count=1) for _ in range(10)]
    ))
    _write_d01(paths, [{"check_id": "D01", "goal_group": "form_submit", "overtrigger": True}])

    artifacts = block3.run(paths, DEFAULTS, {"C10"})

    assert "c10" in artifacts
    rows = _read_metric(paths, "c10")
    summary = next(r for r in rows if r["finding"] == "repeat_submit_signal")
    assert summary["confounded_by_goal_overtrigger"] is True
    assert summary["confidence"] == "LOW"


def test_c10_not_confounded_uses_sample_confidence(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit(form_submit=True, form_submit_count=1) for _ in range(600)])
    _write_d01(paths, [{"check_id": "D01", "goal_group": "form_submit", "overtrigger": False}])

    block3.run(paths, DEFAULTS, {"C10"})

    rows = _read_metric(paths, "c10")
    summary = next(r for r in rows if r["finding"] == "repeat_submit_signal")
    assert summary["confounded_by_goal_overtrigger"] is False
    assert summary["confidence"] == "HIGH"


# ── C12 — непонятный первый экран (zero-engagement по посадочной) ──────────
def test_c12_flags_unclear_first_screen_candidate(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(entry_page="/landing") for _ in range(90)]
        + [_base_visit(entry_page="/landing", form_open=True) for _ in range(10)]
        + [_base_visit(entry_page="/engaged", form_open=True) for _ in range(50)]
    )
    _write_visits(paths, visits)

    artifacts = block3.run(paths, DEFAULTS, {"C12"})

    assert "c12" in artifacts
    rows = _read_metric(paths, "c12")
    landing = next(r for r in rows if r["entry_page"] == "/landing")
    engaged = next(r for r in rows if r["entry_page"] == "/engaged")
    assert landing["zero_engagement_share"] == 0.9
    assert landing["unclear_first_screen_candidate"] is True
    assert engaged["zero_engagement_share"] == 0.0
    assert engaged["unclear_first_screen_candidate"] is False


# ── Диспетчер: confidence_cap из degradation_report ──────────────────────────
def test_confidence_cap_from_degradation_report_applied(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit(visit_id=f"v{i}") for i in range(600)])
    _write_visit_goals(paths, [
        {"visit_id": f"v{i}", "goal_id": goal_id, "achievement_count": 1}
        for i in range(600) for goal_id in (1, 2)
    ])
    _write_funnels_config(paths)
    _write_degradation(paths, [{"check_id": "C06", "confidence_cap": "MED"}])

    block3.run(paths, DEFAULTS, {"C06"})

    rows = _read_metric(paths, "c06")
    summary = next(r for r in rows if r["finding"] == "funnel_summary")
    # Без capping выборка 600 >= 500 дала бы HIGH — degradation_report прижимает к MED.
    assert summary["confidence"] == "MED"


def test_c_candidate_contract_has_full_coverage(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [
        _base_visit(visit_id=f"v{i}", form_open=True, form_submit=i < 20)
        for i in range(100)
    ])
    _write_visit_goals(paths, [
        {"visit_id": f"v{i}", "goal_id": goal_id, "achievement_count": 1}
        for i in range(100) for goal_id in ([1, 2] if i < 20 else [1])
    ])
    _write_funnels_config(paths)

    block3.run(paths, DEFAULTS, {"C06"})

    result = candidates.build_analysis_candidates(paths.metrics)
    coverage = result["coverage"]
    assert coverage["contract_coverage"] == 1.0
    assert coverage["missing_context_refs"] == []
    rows = [dict(zip(result["columns"], values)) for values in result["rows"]]
    c06 = next(row for row in rows if row["artifact"] == "c06" and row["candidate"])
    assert c06["candidate_reason"] == "c06_funnel_summary"


def _write_client_answers(paths: _Paths, data: dict) -> None:
    _write_input_yaml(paths, "client_answers", data)


# ── C13 — цена/условия раскрываются поздно (client_facts) ──────────────────
def test_c13_client_answers_price_and_deposit_facts(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_client_answers(paths, {
        "site_and_form": {
            "price_shown_before_submit": False,
            "deposit": {"exists": True, "amount_rub": 2000},
        },
    })

    artifacts = block3.run(paths, DEFAULTS, {"C13"})

    assert "c13" in artifacts
    rows = _read_metric(paths, "c13")
    price_row = next(r for r in rows if r["finding"] == "client_fact_price_disclosure")
    assert price_row["price_shown_before_submit"] is False
    assert price_row["confidence"] == "client-HIGH"
    deposit_row = next(r for r in rows if r["finding"] == "client_fact_deposit")
    assert deposit_row["deposit_amount_rub"] == 2000


def test_c13_unavailable_without_any_source(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C13"})

    assert "c13" in artifacts
    rows = _read_metric(paths, "c13")
    assert rows[0]["status"] == "unavailable"


# ── C14 — site_crawl + обязательный manual G2, Webvisor только enrichment ──
def test_c14_manual_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "первый экран", "issue": "нет ни одного отзыва или кейса"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C14"})

    assert "c14" in artifacts
    rows = _read_metric(paths, "c14")
    assert any(r.get("finding") == "manual_pattern" for r in rows)


def test_c14_unavailable_without_manual_or_webvisor(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])

    artifacts = block3.run(paths, DEFAULTS, {"C14"})

    assert "c14" in artifacts
    rows = _read_metric(paths, "c14")
    assert rows[0]["status"] == "unavailable"


def test_c14_webvisor_does_not_replace_required_manual_g2(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "webvisor_findings", {
        "meta": {"sessions_reviewed": 5, "date": "2026-07-20"},
        "patterns": [{"pattern": "нет отзывов"}],
    })

    block3.run(paths, DEFAULTS, {"C14"})

    rows = _read_metric(paths, "c14")
    assert rows[0]["status"] == "unavailable"
    assert "обязателен ручной G2" in rows[0]["reason"]


def test_c14_manual_g2_respects_degraded_confidence_cap(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "первый экран", "issue": "нет кейсов"}],
    })
    _write_degradation(paths, [{"check_id": "C14", "confidence_cap": "LOW"}])

    block3.run(paths, DEFAULTS, {"C14"})

    rows = _read_metric(paths, "c14")
    assert rows[0]["confidence"] == "LOW"


# ── C15/C16/C18/C25 — A+B без применимой авто-части ─────────────────────────
def test_c15_manual_fallback_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "главная", "issue": "кнопка CTA меняет текст между страницами"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C15"})

    assert "c15" in artifacts
    rows = _read_metric(paths, "c15")
    pattern_row = next(r for r in rows if r["finding"] == "manual_pattern")
    assert pattern_row["automatic_component"] == "unavailable"
    assert "limitation" in pattern_row


def test_c16_unavailable_without_manual(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C16"})

    assert "c16" in artifacts
    rows = _read_metric(paths, "c16")
    assert rows[0]["status"] == "unavailable"


def test_c18_unavailable_without_manual(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C18"})

    assert "c18" in artifacts
    rows = _read_metric(paths, "c18")
    assert rows[0]["status"] == "unavailable"


def test_c25_manual_fallback_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "conclusions": [{"conclusion": "блог не ведёт в каталог", "confidence": "MED"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C25"})

    assert "c25" in artifacts
    rows = _read_metric(paths, "c25")
    assert any(r.get("finding") == "manual_conclusion" for r in rows)


# ── C17/C23 — полностью ручные (тип B, как C03/C08/C11) ─────────────────────
def test_c17_runs_without_site_pages_when_manual_form_tests_filled(tmp_path):
    """Тот же сценарий, что test_c11_runs_without_site_pages_when_manual_form_tests_filled."""
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "manual_form_tests", {"meta": {"tested_at": "2026-07-20"}})

    artifacts = block3.run(paths, DEFAULTS, {"C17"})

    assert "c17" in artifacts
    assert (paths.metrics / "c17.json").exists()


def test_c03_c08_c11_c17_c23_run_without_site_crawl(tmp_path):
    """Ключевой сценарий FIX-input-tables-manifest-gate (расширенная версия):

    все пять проверок runnable исключительно от inputs/manual_form_tests.yaml,
    независимо от наличия/отсутствия site_pages (site_crawl) в canonical —
    requires в config/methodology.yaml для них больше не site_crawl.
    """
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "форма", "issue": "тестовый паттерн"}],
    })

    check_ids = {"C03", "C08", "C11", "C17", "C23"}
    artifacts = block3.run(paths, DEFAULTS, check_ids)

    for check_id in check_ids:
        name = check_id.lower()
        assert name in artifacts
        rows = _read_metric(paths, name)
        assert any(r.get("finding") == "manual_pattern" for r in rows)


def test_c23_manual_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_input_yaml(paths, "manual_form_tests", {
        "meta": {"tested_at": "2026-07-20"},
        "patterns": [{"step": "оплата", "issue": "ошибка 500 при подтверждении брони"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C23"})

    assert "c23" in artifacts
    rows = _read_metric(paths, "c23")
    assert any(r.get("finding") == "manual_pattern" for r in rows)


# ── C19/C22 — всегда unavailable (структурный разрыв, нет источника) ───────
def test_c19_always_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C19"})

    assert "c19" in artifacts
    rows = _read_metric(paths, "c19")
    assert rows[0]["status"] == "unavailable"


def test_c22_always_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C22"})

    assert "c22" in artifacts
    rows = _read_metric(paths, "c22")
    assert rows[0]["status"] == "unavailable"


# ── C20 — только ручной G2/Webvisor ─────────────────────────────────────────
def test_c20_webvisor_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "webvisor_findings", {
        "meta": {"sessions_reviewed": 5, "date": "2026-07-20", "filter": "мобильные"},
        "patterns": [{"pattern": "cookie-баннер перекрывает форму на мобильных", "count": 4}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C20"})

    assert "c20" in artifacts
    rows = _read_metric(paths, "c20")
    assert any(r.get("finding") == "manual_pattern" for r in rows)


def test_c20_unavailable_without_webvisor(tmp_path):
    paths = _Paths(tmp_path)

    artifacts = block3.run(paths, DEFAULTS, {"C20"})

    assert "c20" in artifacts
    rows = _read_metric(paths, "c20")
    assert rows[0]["status"] == "unavailable"


def test_c20_webvisor_g2_respects_degraded_confidence_cap(tmp_path):
    paths = _Paths(tmp_path)
    _write_input_yaml(paths, "webvisor_findings", {
        "meta": {"sessions_reviewed": 5, "date": "2026-07-20"},
        "patterns": [{"pattern": "баннер перекрывает CTA"}],
    })
    _write_degradation(paths, [{"check_id": "C20", "confidence_cap": "LOW"}])

    block3.run(paths, DEFAULTS, {"C20"})

    rows = _read_metric(paths, "c20")
    assert rows[0]["confidence"] == "LOW"


# ── C21 — browser/os/screen сегментация конверсии ────────────────────────────
def test_c21_browser_segment_underperforms(tmp_path):
    paths = _Paths(tmp_path)
    # os/screen_resolution заполнены одинаково для всех строк — иначе pandas
    # вообще не создаст эти колонки (в реальном visits.parquet они есть всегда,
    # см. _parse_visit_row в build_canonical.py, даже когда backfill не пришёл).
    common_fields = {"os": "windows", "screen_resolution": "1920x1080"}
    visits = (
        [_base_visit(browser="chrome", form_submit=True, **common_fields) for _ in range(200)]
        + [_base_visit(browser="chrome", form_submit=False, **common_fields) for _ in range(100)]
        + [_base_visit(browser="safari", form_submit=True, **common_fields) for _ in range(5)]
        + [_base_visit(browser="safari", form_submit=False, **common_fields) for _ in range(95)]
    )
    _write_visits(paths, visits)

    artifacts = block3.run(paths, DEFAULTS, {"C21"})

    assert "c21" in artifacts
    rows = _read_metric(paths, "c21")
    browser_rows = [r for r in rows if r["segment_dimension"] == "browser"]
    chrome_row = next(r for r in browser_rows if r["segment_value"] == "chrome")
    safari_row = next(r for r in browser_rows if r["segment_value"] == "safari")
    assert chrome_row["is_baseline"] is True
    assert safari_row["segment_underperforms_baseline"] is True


# ── C24 — без URL-level availability только UNVERIFIABLE ────────────────────
def test_c24_client_fact_does_not_replace_url_level_availability(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_client_answers(paths, {
        "capacity_limits": [{"limit": "нет свободных слотов на выходные", "period": "2026-08"}],
    })

    artifacts = block3.run(paths, DEFAULTS, {"C24"})

    assert "c24" in artifacts
    rows = _read_metric(paths, "c24")
    assert rows[0]["status"] == "unavailable"
    assert "UNVERIFIABLE" in rows[0]["reason"]
    assert "client_answers" in rows[0]["reason"]


def test_c24_unavailable_without_site_crawl(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block3.run(paths, DEFAULTS, {"C24"})

    assert "c24" in artifacts
    rows = _read_metric(paths, "c24")
    assert rows[0]["status"] == "unavailable"
    assert "site_pages недоступна" in rows[0]["reason"]


def test_c24_degraded_scenario_stays_unverifiable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_site_pages(paths, [{"url": "https://example.com/", "http_status": 200,
                               "redirect_chain": "[]", "final_url": "https://example.com/"}])
    _write_degradation(paths, [{"check_id": "C24", "confidence_cap": "LOW"}])

    block3.run(paths, DEFAULTS, {"C24"})

    rows = _read_metric(paths, "c24")
    assert rows[0]["status"] == "unavailable"
    assert "confidence" not in rows[0]
