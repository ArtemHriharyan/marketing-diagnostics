"""Реестр методологии: requires: [site_crawl] переименован в реальные
канонические таблицы site_pages / site_link_graph (FIX-site-crawl-canonical-tables-rename).

"site_crawl" — имя источника extract, а не канонической таблицы: transform
пишет из data/raw/site_crawl/ две отдельные таблицы, site_pages.parquet и
site_link_graph.parquet (см. src/transform/build_canonical.py). До фикса
requires: [site_crawl] у S15/S18/S19 никогда не мог быть выполнен — "site_crawl"
не входит в available_tables_from_manifest ни при каком манифесте.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import degradation, orchestrator  # noqa: E402


def _check(methodology: dict, check_id: str) -> dict:
    by_id = {c["id"]: c for c in methodology["checks"]}
    return by_id[check_id]


def test_s15_requires_site_pages():
    methodology = orchestrator.load_methodology()
    assert _check(methodology, "S15")["requires"] == ["site_pages"]


def test_s18_s19_require_site_link_graph():
    methodology = orchestrator.load_methodology()
    for check_id in ("S18", "S19"):
        assert _check(methodology, check_id)["requires"] == ["site_link_graph"]


def test_affected_checks_runnable_with_site_crawl_canonical_tables():
    """S15/S18/S19 становятся runnable, когда доступны реальные канонические
    таблицы site_pages/site_link_graph (непустой краулинг сайта)."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(
        methodology, available={"site_pages", "site_link_graph"}
    )
    by_id = {c["check_id"]: c for c in report["checks"]}
    for check_id in ("S15", "S18", "S19"):
        assert by_id[check_id]["runnable"] is True


def test_affected_checks_not_runnable_without_site_crawl_tables():
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, available=set())
    by_id = {c["check_id"]: c for c in report["checks"]}
    for check_id in ("S15", "S18", "S19"):
        assert by_id[check_id]["runnable"] is False


def test_manual_form_tests_checks_unaffected():
    """C03/C08/C11/C17/C23 уже переведены на manual_form_tests отдельной
    задачей — этот фикс их requires не трогает (регрессионная проверка)."""
    methodology = orchestrator.load_methodology()
    for check_id in ("C03", "C08", "C11", "C17", "C23"):
        assert _check(methodology, check_id)["requires"] == ["manual_form_tests"]


def test_c14_requires_unchanged():
    """C14 осознанно оставлена на requires: [site_crawl] отдельным аудитом —
    этот фикс её не трогает."""
    methodology = orchestrator.load_methodology()
    assert _check(methodology, "C14")["requires"] == ["site_crawl"]
