"""Контракт temporal_provenance для D09 без обращения к API."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import direct, metrika_logs  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402
from src.transform import build_canonical as canonical_mod  # noqa: E402


def test_temporal_provenance_builders_are_deterministic():
    """В provenance нет текущего времени и два вызова дают одинаковый контракт."""
    counter_timezone = {
        "status": "known",
        "time_zone_name": "Asia/Vladivostok",
        "time_zone_offset": 600,
        "evidence": "metrika_management_counter",
    }
    metrika_one = metrika_logs._temporal_provenance(
        date(2026, 6, 1), date(2026, 6, 30), counter_timezone,
    )
    metrika_two = metrika_logs._temporal_provenance(
        date(2026, 6, 1), date(2026, 6, 30), counter_timezone,
    )
    direct_one = direct._temporal_provenance(date(2026, 6, 1), date(2026, 6, 30))
    direct_two = direct._temporal_provenance(date(2026, 6, 1), date(2026, 6, 30))

    assert metrika_one == metrika_two
    assert direct_one == direct_two
    assert "fetched_at" not in str(metrika_one)
    assert "generated_at" not in str(direct_one)


def test_manifest_preserves_temporal_provenance_on_service_update(tmp_path):
    """Повторный update_source не удаляет contract, если его не передали заново."""
    provenance = direct._temporal_provenance(date(2026, 6, 1), date(2026, 6, 30))
    manifest_mod.update_source(
        tmp_path, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["costs"],
        extra={"temporal_provenance": provenance},
    )
    manifest_mod.update_source(
        tmp_path, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["costs"],
        extra={"lookback_rows": 0},
    )

    entry = manifest_mod.load_manifest(tmp_path)["sources"]["direct"]
    assert entry["temporal_provenance"] == provenance


def test_canonical_temporal_provenance_is_deterministic_and_keeps_unknowns():
    """Canonical temporal-contract не добавляет timestamp и не угадывает старый raw."""
    sources = {
        "direct": {"temporal_provenance": direct._temporal_provenance(
            date(2026, 6, 3), date(2026, 7, 28),
        )},
        "metrika_logs": {"temporal_provenance": metrika_logs._temporal_provenance(
            date(2026, 6, 1), date(2026, 6, 30),
            {"status": "unknown", "reason": "counter_time_zone_metadata_missing"},
        )},
        "legacy_source": {},
    }

    first = canonical_mod.build_temporal_provenance(sources)
    second = canonical_mod.build_temporal_provenance(sources)

    assert first == second
    assert "generated_at" not in str(first)
    assert first["raw_sources"]["legacy_source"] == {
        "status": "unknown", "reason": "raw_temporal_provenance_missing",
    }
    assert first["canonical_fields"]["visits"]["dt"]["timezone_conversion"] == "none"
    assert first["canonical_fields"]["costs"]["date"]["raw_field_contract"] == (
        sources["direct"]["temporal_provenance"]["fields"]["Date"]
    )
