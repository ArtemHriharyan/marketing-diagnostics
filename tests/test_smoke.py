"""Смоук-тесты каркаса: intake на _template не падает, деградация корректна.

Не проверяют бизнес-логику (её ещё нет — слои-заглушки), только инварианты
каркаса: пайплайн не падает от отсутствия источников (принцип 4) и карта
деградации детерминированно работает на пустом манифесте.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.pipeline import degradation, orchestrator  # noqa: E402


# ── intake на шаблонном клиенте ────────────────────────────────────────────
def test_intake_template_does_not_crash(tmp_path, monkeypatch):
    """run_intake на _template должен пройти валидацию и вернуть True.

    Логи перенаправляем во временный каталог, чтобы не писать в репозиторий.
    """
    paths = orchestrator.ClientPaths("_template")
    assert paths.exists(), "шаблон clients/_template/config.yaml должен существовать"

    # Не засорять clients/_template/logs/ и data/raw/ во время тестов.
    monkeypatch.setattr(paths, "logs", tmp_path / "logs")
    monkeypatch.setattr(paths, "raw", tmp_path / "raw")

    with orchestrator.StageLogger(paths, "intake") as log:
        ok = orchestrator.run_intake(paths, log)

    assert ok is True


def test_template_config_declares_sources():
    """Шаблон объявляет ожидаемый набор источников с ключом enabled."""
    paths = orchestrator.ClientPaths("_template")
    config = orchestrator.load_client_config(paths)
    sources = config.get("sources", {})
    assert set(sources) >= {"metrika", "direct", "webmaster", "gsc", "wordstat", "crm_csv"}
    for spec in sources.values():
        assert "enabled" in spec


# ── карта деградации ───────────────────────────────────────────────────────
def test_degradation_empty_manifest_skips_everything():
    """Пустой манифест -> все проверки с непустыми requires невыполнимы."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, manifest=None)

    checks = methodology["checks"]
    # Проверки без requires (например 3.2 «приоритизация») остаются выполнимыми.
    no_requires = [c for c in checks if not c.get("requires")]
    assert report["counts"]["runnable"] == len(no_requires)
    assert report["counts"]["skipped"] == len(checks) - len(no_requires)
    assert report["counts"]["total"] == len(checks)


def test_degradation_reason_mentions_missing_source():
    """У пропущенной проверки причина называет недостающий источник."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, manifest=None)

    by_id = {s["id"]: s for s in report["skipped"]}
    # A09 требует direct_queries -> должна быть пропущена с внятной причиной.
    assert "A09" in by_id
    assert "direct_queries" in by_id["A09"]["missing"]
    assert "Директ" in by_id["A09"]["reason"]


def test_degradation_with_visits_enables_visit_only_checks():
    """При наличии только visits выполнимы проверки, где requires ⊆ {visits}."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(
        methodology, available={"visits"}
    )
    runnable = set(report["runnable_check_ids"])
    assert "D01" in runnable   # переотработка целей [visits]
    assert "C06" in runnable   # доходимость формы [visits]
    assert "A05" not in runnable  # требует ещё и costs


def test_available_tables_from_manifest_reads_canonical_lists():
    """Доступные таблицы собираются из canonical_tables источников манифеста."""
    manifest = {
        "sources": {
            "metrika_logs": {"canonical_tables": ["visits"]},
            "direct": {"canonical_tables": ["costs", "direct_queries"]},
        }
    }
    tables = degradation.available_tables_from_manifest(manifest)
    assert tables == {"visits", "costs", "direct_queries"}


def test_split_checks_preserves_order():
    """Порядок проверок сохраняется -> отчёт детерминирован."""
    checks = [
        {"id": "a", "requires": ["visits"]},
        {"id": "b", "requires": ["costs"]},
        {"id": "c", "requires": []},
    ]
    runnable, skipped = degradation.split_checks(checks, {"visits"})
    assert [c["id"] for c in runnable] == ["a", "c"]
    assert [s["id"] for s in skipped] == ["b"]


# ── схема ID реестра (патч под каталог v2) ─────────────────────────────────
def test_methodology_ids_and_legacy_unique():
    """id уникальны; ни один legacy_id не указывает на два разных id."""
    methodology = orchestrator.load_methodology()
    checks = methodology["checks"]

    ids = [c["id"] for c in checks]
    assert len(ids) == len(set(ids)), "дубликаты рабочих id"

    legacy_to_id: dict[str, str] = {}
    for c in checks:
        legacy = c.get("legacy_id")
        if legacy is None:
            continue
        assert legacy_to_id.get(legacy, c["id"]) == c["id"], (
            f"legacy_id {legacy} указывает на два id"
        )
        legacy_to_id[legacy] = c["id"]


def test_methodology_covers_catalog_v2_blocks():
    """Реестр содержит все проверки каталога v2 по префиксам и блокам."""
    methodology = orchestrator.load_methodology()
    checks = methodology["checks"]
    ids = {c["id"] for c in checks}
    assert len(checks) == 100
    for prefix, count, block in [
        ("D", 12, 0), ("A", 26, 1), ("T", 10, 2), ("C", 25, 3), ("S", 27, 4)
    ]:
        group = [c for c in checks if c["id"].startswith(prefix)]
        assert len(group) == count, f"{prefix}: ожидалось {count}"
        assert all(c["block"] == block for c in group), f"{prefix}: блок != {block}"
    assert {"D01", "A07", "T02", "C06", "S03"} <= ids


# ── типы находок и потолок уверенности (контракт degradation) ──────────────
def test_report_includes_detailed_checks():
    """Отчёт несёт запись checks[*] по контракту analyze."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, available={"visits"})
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert set(by_id) == {c["id"] for c in methodology["checks"]}
    entry = by_id["D01"]
    assert set(entry) == {
        "check_id", "runnable", "type_effective",
        "source_modes", "confidence_cap", "reason_if_not_runnable",
    }


def test_type_effective_downgrades_on_manifest_flag():
    """A07: тип A -> B, когда манифест НЕ сообщил долю потерянных показов."""
    methodology = orchestrator.load_methodology()

    # Флаг отсутствует (или false) -> понижение A -> B.
    manifest_no_flag = {
        "sources": {"direct": {"canonical_tables": ["costs"]}}
    }
    rep = degradation.build_degradation_report(methodology, manifest=manifest_no_flag)
    by_id = {c["check_id"]: c for c in rep["checks"]}
    assert by_id["A07"]["type_effective"] == "B"

    # Флаг true -> тип остаётся A.
    manifest_flag = {
        "sources": {
            "direct": {
                "canonical_tables": ["costs"],
                "campaign_report_has_lost_impression_share": True,
            }
        }
    }
    rep2 = degradation.build_degradation_report(methodology, manifest=manifest_flag)
    by_id2 = {c["check_id"]: c for c in rep2["checks"]}
    assert by_id2["A07"]["type_effective"] == "A"


def test_type_effective_defaults_without_downgrade_rule():
    """Проверка без правила понижения отдаёт свой type_default как есть."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(methodology, available={"visits"})
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert by_id["D01"]["type_effective"] == "A"        # type_default A
    assert by_id["D02"]["type_effective"] == "A+B"      # комбинированный тип


def test_manual_source_caps_confidence_at_med():
    """seo_queries из источника mode=manual -> confidence_cap = MED."""
    methodology = orchestrator.load_methodology()
    config = {"sources": {"gsc": {"enabled": True, "mode": "manual"}}}
    defaults = {"manual_source_confidence_cap": "MED"}
    report = degradation.build_degradation_report(
        methodology, available={"seo_queries"}, config=config, defaults=defaults
    )
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert by_id["S01"]["confidence_cap"] == "MED"           # requires seo_queries
    assert by_id["S01"]["source_modes"]["seo_queries"] == "manual"


def test_api_only_requires_keep_confidence_high():
    """Проверка на чисто api-источниках сохраняет потолок HIGH."""
    methodology = orchestrator.load_methodology()
    config = {"sources": {"gsc": {"enabled": True, "mode": "api"}}}
    report = degradation.build_degradation_report(
        methodology, available={"visits", "seo_queries"}, config=config
    )
    by_id = {c["check_id"]: c for c in report["checks"]}
    assert by_id["D01"]["confidence_cap"] == "HIGH"          # visits, api-only
    assert by_id["S01"]["confidence_cap"] == "HIGH"          # seo_queries mode=api


def test_always_manual_source_caps_confidence():
    """requires с всегда-ручной таблицей (client_answers) -> MED."""
    methodology = orchestrator.load_methodology()
    report = degradation.build_degradation_report(
        methodology, available={"costs", "client_answers"}
    )
    by_id = {c["check_id"]: c for c in report["checks"]}
    # D07 requires [costs, client_answers]; client_answers всегда manual.
    assert by_id["D07"]["confidence_cap"] == "MED"
    assert by_id["D07"]["source_modes"]["client_answers"] == "manual"


def test_table_source_modes_defaults_api():
    """Источник без ключа mode трактуется как api; manual — как manual."""
    modes = degradation.table_source_modes(
        {"sources": {"webmaster": {"mode": "manual"}, "metrika": {}}}
    )
    assert modes["seo_queries"] == "manual"   # webmaster питает seo_queries
    assert modes["visits"] == "api"
    assert modes["client_answers"] == "manual"  # всегда ручной источник


# ── гейт перед report ──────────────────────────────────────────────────────
def test_report_gate_blocks_when_approved_empty():
    """При пустом findings/approved/ гейт закрыт (approved_findings_present=False)."""
    paths = orchestrator.ClientPaths("_template")
    # В шаблоне findings/approved/ содержит только .gitkeep -> находок нет.
    assert orchestrator.approved_findings_present(paths) is False
    msg = orchestrator.report_gate_message(paths)
    assert "approved" in msg
