"""Выделенные тесты карты деградации (task 1B).

Сценарии:
1. Недоступный источник -> runnable=False, reason_if_not_runnable не None.
2. type downgrade: истинное условие -> type_downgraded; ложное -> type_default.
3. Один manual-источник в requires -> confidence_cap=MED.
4. Все requires из api-источников -> confidence_cap=HIGH.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline.degradation import (  # noqa: E402
    build_degradation_report,
    evaluate_check,
    table_source_modes,
)


def _modes(config=None):
    return table_source_modes(config)


# ── 1. Недоступный источник ─────────────────────────────────────────────────

def test_unavailable_source_not_runnable():
    """Проверка, у которой requires недоступен, получает runnable=False и reason."""
    check = {
        "id": "X01",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(check, available=set(), source_modes=_modes())
    assert result["runnable"] is False
    assert result["reason_if_not_runnable"] is not None
    assert len(result["reason_if_not_runnable"]) > 0


def test_available_source_is_runnable():
    """Все requires доступны -> runnable=True и reason=None."""
    check = {
        "id": "X02",
        "requires": ["visits"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(check, available={"visits"}, source_modes=_modes())
    assert result["runnable"] is True
    assert result["reason_if_not_runnable"] is None


def test_manifest_goals_makes_d02_d03_runnable():
    """goals из manifest extract закрывает requires D02/D03 штатно."""
    methodology = {
        "checks": [
            {"id": check_id, "requires": ["visits", "goals"], "type_default": "A"}
            for check_id in ("D02", "D03")
        ]
    }
    manifest = {
        "sources": {
            "metrika_reports": {"canonical_tables": ["visits", "goals"]},
        }
    }

    report = build_degradation_report(methodology, manifest=manifest)
    assert report["runnable_check_ids"] == ["D02", "D03"]


def test_campaign_status_is_api_requirement_for_d08():
    """D08 штатно skipped, пока extract не объявил campaign_status."""
    methodology = {"checks": [{"id": "D08", "requires": ["costs", "campaign_status"], "type_default": "A"}]}
    missing = build_degradation_report(
        methodology, manifest={"sources": {"direct": {"canonical_tables": ["costs"]}}}
    )
    assert missing["runnable_check_ids"] == []
    assert missing["skipped"][0]["missing"] == ["campaign_status"]

    available = build_degradation_report(
        methodology,
        manifest={"sources": {"direct": {"canonical_tables": ["costs", "campaign_status"]}}},
    )
    assert available["runnable_check_ids"] == ["D08"]
    assert available["checks"][0]["source_modes"] == {"costs": "api", "campaign_status": "api"}


# ── requires в degradation_report ────────────────────────────────────────────

def test_report_serializes_registry_requires_for_c14_c20_c24():
    """requires реестра есть в checks и skipped без изменения прежних полей."""
    methodology = {
        "checks": [
            {"id": "C14", "block": 3, "name": "trust", "requires": ["site_crawl", "manual_form_tests"], "type_default": "B"},
            {"id": "C20", "block": 3, "name": "overlay", "requires": ["webvisor_findings"], "type_default": "B"},
            {"id": "C24", "block": 3, "name": "availability", "requires": ["visits", "site_crawl"], "type_default": "A+B"},
        ]
    }

    report = build_degradation_report(methodology, available={"visits"})

    expected = {
        "C14": ["site_crawl", "manual_form_tests"],
        "C20": ["webvisor_findings"],
        "C24": ["visits", "site_crawl"],
    }
    assert {item["id"]: item["requires"] for item in report["skipped"]} == expected
    assert {item["check_id"]: item["requires"] for item in report["checks"]} == expected
    assert {item["id"]: item["missing"] for item in report["skipped"]} == {
        "C14": ["site_crawl", "manual_form_tests"],
        "C20": ["webvisor_findings"],
        "C24": ["site_crawl"],
    }
    assert all(item["reason"] for item in report["skipped"])


# ── 2. type downgrade ────────────────────────────────────────────────────────

def test_type_downgrade_applies_when_condition_true():
    """Условие type_downgrade_if истинно -> type_effective = type_downgraded."""
    check = {
        "id": "X03",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": "some_flag == false",
        "type_downgraded": "B",
    }
    # some_flag отсутствует в flags -> false -> "== false" истинно.
    result = evaluate_check(
        check, available={"costs"}, source_modes=_modes(), flags={}
    )
    assert result["type_effective"] == "B"


def test_type_downgrade_skipped_when_condition_false():
    """Условие type_downgrade_if ложно -> type_effective = type_default."""
    check = {
        "id": "X04",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": "some_flag == true",
        "type_downgraded": "B",
    }
    # some_flag отсутствует -> false -> "== true" ложно.
    result = evaluate_check(
        check, available={"costs"}, source_modes=_modes(), flags={}
    )
    assert result["type_effective"] == "A"


def test_d11_permanent_degradation_with_full_sources_and_positive_flags():
    """D11 остаётся permanent_LOW даже при полном API-покрытии и true-флагах."""
    methodology = {
        "checks": [
            {
                "id": "D11",
                "requires": ["visits"],
                "type_default": "A+B",
                "type_downgrade_if": None,
                "type_downgraded": "permanent_LOW",
            }
        ]
    }
    manifest = {
        "sources": {
            "metrika_logs": {
                "canonical_tables": ["visits"],
                "bot_detection_available": True,
            }
        },
        "flags": {"bot_detection_available": True},
    }

    result = build_degradation_report(methodology, manifest=manifest)["checks"][0]

    assert result["runnable"] is True
    assert result["type_effective"] == "permanent_LOW"
    assert result["confidence_cap"] == "LOW"


def test_d11_permanent_degradation_with_missing_sources_and_flags():
    """D11 сохраняет постоянный тип и cap при пустом manifest и без visits."""
    methodology = {
        "checks": [
            {
                "id": "D11",
                "requires": ["visits"],
                "type_default": "A+B",
                "type_downgrade_if": None,
                "type_downgraded": "permanent_LOW",
            }
        ]
    }

    result = build_degradation_report(methodology, manifest=None)["checks"][0]

    assert result["runnable"] is False
    assert result["type_effective"] == "permanent_LOW"
    assert result["confidence_cap"] == "LOW"


# ── 3. Один manual-источник в requires -> MED ────────────────────────────────

def test_one_manual_required_caps_confidence_at_med():
    """Хотя бы один requires — manual -> confidence_cap=MED."""
    # client_answers входит в _MANUAL_TABLES -> всегда mode=manual.
    check = {
        "id": "X05",
        "requires": ["costs", "client_answers"],
        "type_default": "A+Q",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(
        check,
        available={"costs", "client_answers"},
        source_modes=_modes(),
        manual_cap="MED",
    )
    assert result["confidence_cap"] == "MED"
    assert result["source_modes"]["client_answers"] == "manual"
    assert result["source_modes"]["costs"] == "api"


def test_manual_form_tests_required_caps_confidence_at_med():
    """FIX-input-tables-manifest-gate (расширенная версия): manual_form_tests

    добавлен в _MANUAL_TABLES вместе с requires C03/C08/C11/C17/C23 в
    config/methodology.yaml (site_crawl -> manual_form_tests) — без этого
    confidence_cap этих пяти проверок остался бы HIGH вместо MED.
    """
    check = {
        "id": "C03",
        "requires": ["manual_form_tests"],
        "type_default": "B",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(
        check,
        available={"manual_form_tests"},
        source_modes=_modes(),
        manual_cap="MED",
    )
    assert result["runnable"] is True
    assert result["confidence_cap"] == "MED"
    assert result["source_modes"]["manual_form_tests"] == "manual"


# ── 4. Все requires из api-источников -> HIGH ────────────────────────────────

def test_all_api_required_keeps_confidence_high():
    """Все requires — api-источники -> confidence_cap=HIGH."""
    check = {
        "id": "X06",
        "requires": ["visits", "costs"],
        "type_default": "A",
        "type_downgrade_if": None,
        "type_downgraded": None,
    }
    result = evaluate_check(
        check,
        available={"visits", "costs"},
        source_modes=_modes(),
        manual_cap="MED",
    )
    assert result["confidence_cap"] == "HIGH"
    assert result["source_modes"]["visits"] == "api"
    assert result["source_modes"]["costs"] == "api"


# ── 5. confidence_cap_downgraded (A24: ad_extensions_price_fields_available) ─

def test_confidence_cap_downgraded_applies_when_type_downgrade_fires():
    """type_downgrade_if истинно + confidence_cap_downgraded задан -> потолок ниже."""
    check = {
        "id": "A24",
        "requires": ["direct_queries"],
        "type_default": "A+B",
        "type_downgrade_if": "ad_extensions_price_fields_available == false",
        "type_downgraded": "B",
        "confidence_cap_downgraded": "MED",
    }
    result = evaluate_check(
        check,
        available={"direct_queries"},
        source_modes=_modes(),
        manual_cap="MED",
        flags={"ad_extensions_price_fields_available": False},
    )
    assert result["type_effective"] == "B"
    assert result["confidence_cap"] == "MED"


def test_confidence_cap_downgraded_ignored_when_flag_true():
    """Флаг true -> type_downgrade_if ложно -> confidence_cap_downgraded не применяется."""
    check = {
        "id": "A24",
        "requires": ["direct_queries"],
        "type_default": "A+B",
        "type_downgrade_if": "ad_extensions_price_fields_available == false",
        "type_downgraded": "B",
        "confidence_cap_downgraded": "MED",
    }
    result = evaluate_check(
        check,
        available={"direct_queries"},
        source_modes=_modes(),
        manual_cap="MED",
        flags={"ad_extensions_price_fields_available": True},
    )
    assert result["type_effective"] == "A+B"
    assert result["confidence_cap"] == "HIGH"


def test_confidence_cap_downgraded_absent_leaves_cap_unaffected():
    """Проверки без confidence_cap_downgraded (напр. A07) не меняют confidence_cap."""
    check = {
        "id": "A07",
        "requires": ["costs"],
        "type_default": "A",
        "type_downgrade_if": "some_flag == false",
        "type_downgraded": "B",
    }
    result = evaluate_check(
        check, available={"costs"}, source_modes=_modes(), flags={}
    )
    assert result["type_effective"] == "B"
    assert result["confidence_cap"] == "HIGH"
