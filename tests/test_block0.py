"""Тесты блока 0 compute (задача 5B): D01–D06.

По каждой проверке — один положительный (находка есть) и один отрицательный
(находки нет) сценарий, плюс: недоступность goals для D02/D03 (явная запись,
не молчаливый пропуск) и confidence_cap (compute капает вниз, не поднимает).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from src.compute import block0  # noqa: E402
from src.transform import build_canonical as bc  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_compute_common.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


DEFAULTS = {
    "min_sample_visits": 500,
    "goal_inflation_warning": 1.3,
    "utm_undefined_threshold": 0.25,
}


def _write_config(paths: _Paths, goals: dict | None = None) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    config = {"goals": goals} if goals is not None else {}
    paths.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")


def _write_visits(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "visits.parquet")


def _write_goals(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "goals.parquet")


def _write_costs(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "costs.parquet")


def _write_client_answers(paths: _Paths, data: dict) -> None:
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "client_answers.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _write_dated_parquet(path: Path, rows: list[dict], date_field: str = "date") -> None:
    """Записать rows с явным pyarrow date32 для date_field (нужно D07-D12: они

    делают арифметику над датами; pandas.to_parquet с object-колонкой из
    python date не гарантирует date32 на всех версиях pyarrow, а production
    write_canonical_table (src/transform/build_canonical.py) всегда пишет
    date-колонки как date32 — тест обязан воспроизводить тот же тип.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if date_field in df.columns:
        idx = table.schema.get_field_index(date_field)
        date_array = pa.array(list(df[date_field]), type=pa.date32())
        table = table.set_column(idx, pa.field(date_field, pa.date32()), date_array)
    pq.write_table(table, path)


def _write_costs_d(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "costs.parquet", rows)


def _write_campaign_status(paths: _Paths, rows: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(paths.canonical / "campaign_status.parquet")


def _campaign_status(campaign_id: str, state: str | None, **overrides) -> dict:
    row = {
        "campaign_id": campaign_id,
        "state": state,
        "status": "ACCEPTED",
        "status_payment": "ELIGIBLE",
        "status_clarification": None,
        "observed_at": "2026-06-30T12:00:00+00:00",
        "source": "direct.campaigns.get",
        "requested_states": '["ON", "OFF", "SUSPENDED", "ENDED", "CONVERTED", "ARCHIVED"]',
    }
    row.update(overrides)
    return row


def _write_visits_d(paths: _Paths, rows: list[dict]) -> None:
    _write_dated_parquet(paths.canonical / "visits.parquet", rows)


def _base_visit(**overrides) -> dict:
    row = {
        "device": "desktop",
        "source_group": "organic",
        "utm_source_raw": "",
        "form_open": False,
        "form_submit": False,
        "call_click": False,
        "messenger_click": False,
        "form_open_count": 0,
        "form_submit_count": 0,
        "call_click_count": 0,
        "messenger_click_count": 0,
    }
    row.update(overrides)
    return row


def _read_metric(paths: _Paths, name: str) -> list[dict]:
    with (paths.metrics / f"{name}.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ── D01 — переотработка ключевой цели ───────────────────────────────────────

def test_d01_overtrigger_detected(tmp_path):
    paths = _Paths(tmp_path)
    visits = [
        _base_visit(form_submit=True, form_submit_count=3) for _ in range(10)
    ] + [_base_visit() for _ in range(5)]
    _write_visits(paths, visits)

    artifacts = block0.run(paths, DEFAULTS, {"D01"})
    assert "d01" in artifacts

    rows = _read_metric(paths, "d01")
    by_group = {r["goal_group"]: r for r in rows}
    fs = by_group["form_submit"]
    assert fs["visits_with_goal"] == 10
    assert fs["achievements"] == 30
    assert fs["overtrigger"] is True


def test_d01_no_overtrigger_when_ratio_below_threshold(tmp_path):
    paths = _Paths(tmp_path)
    visits = [
        _base_visit(form_submit=True, form_submit_count=1) for _ in range(10)
    ]
    _write_visits(paths, visits)

    block0.run(paths, DEFAULTS, {"D01"})
    rows = _read_metric(paths, "d01")
    fs = next(r for r in rows if r["goal_group"] == "form_submit")
    assert fs["overtrigger"] is False
    assert fs["achievements_per_visit"] == 1.0


def test_d01_not_run_when_not_in_runnable_ids(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])

    artifacts = block0.run(paths, DEFAULTS, set())
    assert "d01" not in artifacts
    assert not (paths.metrics / "d01.json").exists()


# ── D02 — цель = клик/открытие, а не отправка ───────────────────────────────

def test_d02_flags_weak_type_goal_named_like_submission(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [
        {"goal_id": "1", "name": "Отправка заявки", "type": "action"},
    ])
    _write_config(paths)

    artifacts = block0.run(paths, DEFAULTS, {"D02"})
    assert "d02" in artifacts
    rows = _read_metric(paths, "d02")
    assert rows[0]["suspect_click_not_submit"] is True
    assert rows[0]["is_weak_type"] is True


def test_d02_strong_type_goal_not_flagged(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [
        {"goal_id": "1", "name": "Отправка заявки (success page)", "type": "url"},
    ])
    _write_config(paths)

    block0.run(paths, DEFAULTS, {"D02"})
    rows = _read_metric(paths, "d02")
    assert rows[0]["suspect_click_not_submit"] is False
    assert rows[0]["is_weak_type"] is False


# ── D03 — смешаны бизнес-цели и микроконверсии ──────────────────────────────

def test_d03_detects_goal_group_overlap(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [
        {"goal_id": "1", "name": "Заявка", "type": "url"},
        {"goal_id": "2", "name": "Клик по кнопке", "type": "action"},
    ])
    _write_config(paths, goals={
        "form_submit_goal_ids": [1],
        "call_click_goal_ids": [1, 2],  # 1 попал в обе группы — смешение
    })

    artifacts = block0.run(paths, DEFAULTS, {"D03"})
    assert "d03" in artifacts
    rows = _read_metric(paths, "d03")
    overlap_rows = [r for r in rows if r["finding"] == "goal_group_overlap"]
    assert len(overlap_rows) == 1
    assert overlap_rows[0]["overlapping_goal_ids"] == "1"
    summary = next(r for r in rows if r["finding"] == "goal_mix_summary")
    assert summary["has_overlap"] is True


def test_d03_no_overlap_when_groups_disjoint(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [
        {"goal_id": "1", "name": "Заявка", "type": "url"},
        {"goal_id": "2", "name": "Клик по кнопке звонка", "type": "action"},
    ])
    _write_config(paths, goals={
        "form_submit_goal_ids": [1],
        "call_click_goal_ids": [2],
    })

    block0.run(paths, DEFAULTS, {"D03"})
    rows = _read_metric(paths, "d03")
    overlap_rows = [r for r in rows if r["finding"] == "goal_group_overlap"]
    assert overlap_rows == []
    summary = next(r for r in rows if r["finding"] == "goal_mix_summary")
    assert summary["has_overlap"] is False
    assert summary["has_uncategorized"] is False


# ── D02/D03 — недоступность goals не должна пропускаться молча ─────────────

def test_d02_d03_unavailable_when_goals_missing(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    # goals.parquet не создаём вовсе.

    artifacts = block0.run(paths, DEFAULTS, {"D02", "D03"})
    assert "d02" in artifacts and "d03" in artifacts

    for name in ("d02", "d03"):
        rows = _read_metric(paths, name)
        assert len(rows) == 1
        assert rows[0]["status"] == "unavailable"
        assert rows[0]["reason"] == "goals metadata недоступна"


def test_d02_d03_unavailable_when_goals_empty(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [])  # пустой goals.parquet (0 строк)

    block0.run(paths, DEFAULTS, {"D02", "D03"})
    for name in ("d02", "d03"):
        rows = _read_metric(paths, name)
        assert rows[0]["status"] == "unavailable"
        assert rows[0]["reason"] == "goals metadata недоступна"


def test_d02_d03_not_run_when_not_in_runnable_ids_even_with_goals(tmp_path):
    """Непустой старый parquet не заменяет решение degradation."""
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [{"goal_id": "1", "name": "Заявка", "type": "url"}])
    _write_config(paths)

    artifacts = block0.run(paths, DEFAULTS, set())
    assert "d02" not in artifacts and "d03" not in artifacts
    assert not (paths.metrics / "d02.json").exists()
    assert not (paths.metrics / "d03.json").exists()


# ── D04 — покрытие трекингом по устройствам ─────────────────────────────────

def test_d04_flags_device_with_zero_tracked_conversions(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(device="desktop", form_submit=True, form_submit_count=1) for _ in range(20)]
        + [_base_visit(device="mobile") for _ in range(20)]
    )
    _write_visits(paths, visits)

    artifacts = block0.run(paths, DEFAULTS, {"D04"})
    assert "d04" in artifacts
    rows = _read_metric(paths, "d04")
    by_device = {r["device"]: r for r in rows}
    assert by_device["mobile"]["no_tracked_conversions"] is True
    assert by_device["desktop"]["no_tracked_conversions"] is False


def test_d04_no_finding_when_all_devices_convert(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(device="desktop", form_submit=True, form_submit_count=1) for _ in range(5)]
        + [_base_visit(device="mobile", form_submit=True, form_submit_count=1) for _ in range(5)]
    )
    _write_visits(paths, visits)

    block0.run(paths, DEFAULTS, {"D04"})
    rows = _read_metric(paths, "d04")
    assert all(r["no_tracked_conversions"] is False for r in rows)


# ── D05 — UTM/источник теряются или перезаписываются ────────────────────────

def test_d05_threshold_exceeded(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(source_group="ad", utm_source_raw="") for _ in range(4)]
        + [_base_visit(source_group="ad", utm_source_raw="yandex") for _ in range(6)]
    )
    _write_visits(paths, visits)

    artifacts = block0.run(paths, DEFAULTS, {"D05"})
    assert "d05" in artifacts
    rows = _read_metric(paths, "d05")
    row = rows[0]
    assert row["ad_visits"] == 10
    assert row["ad_visits_undefined_utm"] == 4
    assert row["frac_undefined_utm"] == 0.4
    assert row["threshold_exceeded"] is True


def test_d05_threshold_not_exceeded(tmp_path):
    paths = _Paths(tmp_path)
    visits = (
        [_base_visit(source_group="ad", utm_source_raw="") for _ in range(1)]
        + [_base_visit(source_group="ad", utm_source_raw="yandex") for _ in range(9)]
    )
    _write_visits(paths, visits)

    block0.run(paths, DEFAULTS, {"D05"})
    rows = _read_metric(paths, "d05")
    assert rows[0]["threshold_exceeded"] is False


# ── D06 — расходы на разной базе НДС ────────────────────────────────────────

def test_d06_detects_mixed_basis_and_unapplied_answer(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs(paths, [
        {"date": "2026-01-01", "source_tag": "direct", "cost_raw": 100.0,
         "cost_normalized": 100.0, "cost_status": "net", "clicks": 1, "impressions": 1},
        {"date": "2026-01-01", "source_tag": "yandex_business", "cost_raw": 50.0,
         "cost_normalized": None, "cost_status": "vat_basis_unknown",
         "clicks": 1, "impressions": 1},
    ])
    _write_client_answers(paths, {
        "finance": {
            "vat_basis_by_source": [
                # Клиент говорит "с НДС" (gross), но в costs.parquet зафиксирован "net" —
                # ответ клиента не применён при нормализации.
                {"source": "direct", "vat_included": True, "evidence": "счёт"},
            ]
        }
    })

    artifacts = block0.run(paths, DEFAULTS, {"D06"})
    assert "d06" in artifacts
    rows = _read_metric(paths, "d06")
    direct_row = next(r for r in rows if r["source_tag"] == "direct")
    assert direct_row["answer_not_applied"] is True
    assert direct_row["expected_cost_status"] == "gross"
    assert direct_row["actual_cost_status"] == "net"
    yb_row = next(r for r in rows if r["source_tag"] == "yandex_business")
    assert yb_row["has_client_evidence"] is False
    assert all(r["mixed_basis_across_sources"] is False for r in rows)  # net vs unknown, не gross+net


def test_d06_no_mismatch_when_answer_matches_and_single_basis(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs(paths, [
        {"date": "2026-01-01", "source_tag": "direct", "cost_raw": 100.0,
         "cost_normalized": 100.0, "cost_status": "net", "clicks": 1, "impressions": 1},
    ])
    _write_client_answers(paths, {
        "finance": {
            "vat_basis_by_source": [
                {"source": "direct", "vat_included": False, "evidence": "договор"},
            ]
        }
    })

    block0.run(paths, DEFAULTS, {"D06"})
    rows = _read_metric(paths, "d06")
    assert rows[0]["answer_not_applied"] is False
    assert rows[0]["mixed_basis_across_sources"] is False


# ── D07 — расходы неполные или задвоены ─────────────────────────────────────

def _d07_cost_rows(*, include_yandex_business: bool = False, agency_source: str | None = None) -> list[dict]:
    rows = [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 1000.0, "cost_normalized": 1000.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ]
    if include_yandex_business:
        rows.append(
            {"date": date(2026, 1, 15), "source_tag": "yandex_business", "campaign_id": None,
             "campaign_name": None, "cost_raw": 500.0, "cost_normalized": 500.0,
             "cost_status": "net", "clicks": 1, "impressions": 1}
        )
    if agency_source:
        rows.append(
            {"date": date(2026, 1, 31), "source_tag": agency_source, "campaign_id": None,
             "campaign_name": None, "cost_raw": 200.0, "cost_normalized": 200.0,
             "cost_status": "net", "clicks": None, "impressions": None}
        )
    return rows


def test_d07_uses_canonical_hidden_costs_field(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows(include_yandex_business=True))
    _write_client_answers(paths, {
        "finance": {
            "hidden_costs_rub_month": [
                {"name": "Фикс подрядчика", "rub_month": 20000.0, "source_tag": "agency_fee"},
            ]
        }
    })

    artifacts = block0.run(paths, DEFAULTS, {"D07"})
    assert "d07" in artifacts
    rows = _read_metric(paths, "d07")
    declared = next(r for r in rows if r["finding"] == "declared_cost_check")
    assert declared["missing_in_data"] is True
    assert declared["declared_cost_field"] == "hidden_costs_rub_month"
    dup = next(r for r in rows if r["finding"] == "possible_double_counted_budget")
    assert dup["both_present"] is True


def test_d07_uses_legacy_costs_outside_cabinet_alias(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows(agency_source="agency_fee"))
    _write_client_answers(paths, {
        "finance": {
            "costs_outside_cabinet": [
                {"name": "Фикс подрядчика", "rub_month": 200.0, "source_tag": "agency_fee"},
            ]
        }
    })

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    declared = next(r for r in rows if r["finding"] == "declared_cost_check")
    assert declared["missing_in_data"] is False
    assert declared["amount_mismatch"] is False
    assert declared["declared_cost_field"] == "costs_outside_cabinet"


def test_d07_accepts_equal_canonical_and_legacy_values_once(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows(agency_source="agency_fee"))
    declared = [{"name": "Фикс подрядчика", "rub_month": 200.0, "source_tag": "agency_fee"}]
    _write_client_answers(paths, {
        "finance": {
            "hidden_costs_rub_month": declared,
            "costs_outside_cabinet": list(declared),
        }
    })

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    assert len([r for r in rows if r["finding"] == "declared_cost_check"]) == 1
    assert not any(r["finding"] == "conflicting_declared_cost_inputs" for r in rows)


def test_d07_reports_conflicting_canonical_and_legacy_values(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows(agency_source="canonical_fee"))
    _write_client_answers(paths, {
        "finance": {
            "hidden_costs_rub_month": [
                {"name": "Канонический фикс", "rub_month": 200.0, "source_tag": "canonical_fee"},
            ],
            "costs_outside_cabinet": [
                {"name": "Старый фикс", "rub_month": 300.0, "source_tag": "legacy_fee"},
            ],
        }
    })

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    declared = next(r for r in rows if r["finding"] == "declared_cost_check")
    conflict = next(r for r in rows if r["finding"] == "conflicting_declared_cost_inputs")
    assert declared["source_tag"] == "canonical_fee"
    assert conflict["using_field"] == "hidden_costs_rub_month"


def test_d07_reports_malformed_declared_cost_input(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows())
    _write_client_answers(paths, {
        "finance": {"hidden_costs_rub_month": {"rub_month": 200.0}}
    })

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    malformed = next(r for r in rows if r["finding"] == "malformed_declared_cost_input")
    assert malformed["field"] == "hidden_costs_rub_month"
    assert not any(r["finding"] == "declared_cost_check" for r in rows)


def test_d07_records_no_declared_cost_as_absent_not_zero(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, _d07_cost_rows())
    _write_client_answers(paths, {"finance": {}})

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    assert [r["finding"] for r in rows] == ["possible_double_counted_budget"]


# ── D08 — API-статус кампании + historical costs ───────────────────────────

def test_d08_active_campaign_with_spend_is_not_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "camp-1",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ])
    _write_campaign_status(paths, [_campaign_status("1", "ON")])

    artifacts = block0.run(paths, DEFAULTS, {"D08"})
    assert "d08" in artifacts
    rows = _read_metric(paths, "d08")
    evidence = next(r for r in rows if r["finding"] == "campaign_status_evidence")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert evidence["state"] == "ON"
    assert evidence["has_problem"] is False
    assert summary["coverage_complete"] is True
    assert summary["status"] == "pass"


def test_d08_archived_and_suspended_with_historical_spend_are_problems(tmp_path):
    paths = _Paths(tmp_path)
    rows_in = [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "archived",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 5))
    ] + [
        {"date": d, "source_tag": "direct", "campaign_id": "2", "campaign_name": "suspended",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ]
    _write_costs_d(paths, rows_in)
    _write_campaign_status(paths, [
        _campaign_status("1", "ARCHIVED"), _campaign_status("2", "SUSPENDED"),
    ])

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    evidence = [r for r in rows if r["finding"] == "campaign_status_evidence"]
    summary = next(r for r in rows if r["finding"] == "summary")
    assert all(r["has_problem"] is True for r in evidence)
    assert {r["state"] for r in evidence} == {"ARCHIVED", "SUSPENDED"}
    assert summary["confirmed_non_active_with_spend_count"] == 2
    assert summary["status"] == "problem"


def test_d08_zero_spend_non_active_campaign_is_not_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1", "campaign_name": "zero",
         "cost_raw": 0.0, "cost_normalized": 0.0, "cost_status": "net", "clicks": 0, "impressions": 0},
        {"date": date(2026, 1, 31), "source_tag": "direct", "campaign_id": "2", "campaign_name": "active",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1},
    ])
    _write_campaign_status(paths, [
        _campaign_status("1", "ARCHIVED"), _campaign_status("2", "ON"),
    ])

    block0.run(paths, DEFAULTS, {"D08"})
    evidence = next(r for r in _read_metric(paths, "d08") if r.get("campaign_id") == "1")
    assert evidence["has_historical_spend"] is False
    assert evidence["has_problem"] is False


def test_d08_not_returned_status_is_coverage_gap_not_finding(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [{
        "date": date(2026, 1, 31), "source_tag": "direct", "campaign_id": "1", "campaign_name": "missing",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    _write_campaign_status(paths, [_campaign_status("2", "ARCHIVED")])

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    evidence = next(r for r in rows if r["finding"] == "campaign_status_evidence")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert evidence["coverage_gap"] is True
    assert evidence["coverage_gap_reason"] == "status_not_returned"
    assert evidence["has_problem"] is False
    assert summary["status"] == "unverifiable"


def test_d08_partial_coverage_keeps_confirmed_archived_finding_and_caps_confidence(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1", "campaign_name": "archived",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1},
        {"date": date(2026, 1, 31), "source_tag": "direct", "campaign_id": "2", "campaign_name": "missing",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1},
    ])
    _write_campaign_status(paths, [_campaign_status("1", "ARCHIVED")])

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    archived = next(r for r in rows if r.get("campaign_id") == "1")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert archived["has_problem"] is True
    assert archived["confidence"] == "MED"
    assert summary["coverage_complete"] is False
    assert summary["coverage_gap_count"] == 1


def test_d08_missing_campaign_status_writes_unavailable(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [{
        "date": date(2026, 1, 31), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])

    artifacts = block0.run(paths, DEFAULTS, {"D08"})
    assert artifacts == ["d08"]
    assert _read_metric(paths, "d08") == [{
        "check_id": "D08", "status": "unavailable", "reason": "нет источника: статусы кампаний Директа",
    }]


def test_d08_confidence_capped_by_degradation_report(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ])
    _write_campaign_status(paths, [_campaign_status("1", "ARCHIVED")])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {"checks": [{"check_id": "D08", "confidence_cap": "LOW"}]}
    (paths.metrics / "degradation_report.json").write_text(json.dumps(report), encoding="utf-8")

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    # Без потолка per-campaign строки были бы HIGH, summary — MED.
    assert all(r["confidence"] == "LOW" for r in rows)


# ── D09 — периоды/часовые пояса/даты не приведены к единому правилу ────────

def _d09_metrika_contract(
    date_from: str = "2026-06-01", date_to: str = "2026-06-30", *,
    offset: int = 180, boundary_semantics: str = "inclusive",
) -> dict:
    return {
        "requested_window": {
            "date_from": date_from, "date_to": date_to,
            "boundary_semantics": boundary_semantics,
        },
        "fields": {
            "ym:s:dateTime": {
                "event": "visit", "data_type": "datetime",
                "timezone": {
                    "status": "known", "time_zone_name": "Europe/Moscow",
                    "time_zone_offset": offset,
                },
            },
            "ym:s:goalsDateTime": {
                "event": "goal_achievement", "data_type": "array_datetime",
                "timezone_contract": "UTC+03:00",
            },
        },
    }


def _d09_direct_contract(
    date_from: str = "2026-06-01", date_to: str = "2026-06-30", *,
    boundary_semantics: str = "inclusive", zero_day_policy: str = "known",
) -> dict:
    return {
        "requested_window": {
            "date_from": date_from, "date_to": date_to,
            "boundary_semantics": boundary_semantics,
        },
        "zero_day_policy": zero_day_policy,
        "fields": {
            "Date": {
                "event": "direct_statistics_day", "data_type": "date",
                "timezone_contract": "Europe/Moscow", "evidence": "direct_reports_contract",
            },
        },
    }


def _d09_partial(date_from: str, date_to: str) -> dict:
    return {
        "first_month": {
            "month": date_from[:7],
            "status": "declared_partial" if date_from[-2:] != "01" else "declared_complete",
        },
        "last_month": {
            "month": date_to[:7],
            "status": "declared_partial" if date_to[-2:] != "30" else "declared_complete",
        },
        "basis": "requested_window_only",
    }


def _write_d09_manifest(
    paths: _Paths,
    *,
    metrika: dict | None = None,
    direct: dict | None = None,
    goal_status: dict | None = None,
) -> None:
    metrika = metrika or _d09_metrika_contract()
    raw_sources = {"metrika_logs": metrika}
    canonical_fields = {
        "visits": {
            "dt": {
                "raw_source": "metrika_logs", "raw_field": "ym:s:dateTime",
                "raw_field_contract": metrika["fields"]["ym:s:dateTime"],
                "timezone_conversion": "none", "local_time_basis": "counter_local_time",
            },
            "date": {
                "derived_from": "visits.dt",
                "operation": "calendar_date_without_timezone_conversion",
            },
        },
        "goal_achievements": {
            "goal_datetime": {
                "raw_source": "metrika_logs", "raw_field": "ym:s:goalsDateTime",
                "raw_field_contract": metrika["fields"]["ym:s:goalsDateTime"],
                "timezone_conversion": "none",
            },
        },
    }
    partial_months = {
        "metrika_logs": _d09_partial(
            metrika["requested_window"]["date_from"], metrika["requested_window"]["date_to"],
        ),
    }
    if direct is not None:
        raw_sources["direct"] = direct
        canonical_fields["costs"] = {
            "date": {
                "raw_source": "direct", "raw_field": "Date",
                "raw_field_contract": direct["fields"]["Date"],
                "timezone_conversion": "none",
            },
        }
        partial_months["direct"] = _d09_partial(
            direct["requested_window"]["date_from"], direct["requested_window"]["date_to"],
        )
    paths.canonical.mkdir(parents=True, exist_ok=True)
    (paths.canonical / "manifest.json").write_text(json.dumps({
        "temporal_provenance": {
            "raw_sources": raw_sources,
            "canonical_fields": canonical_fields,
            "partial_months": partial_months,
        },
        "flags": {"goal_achievements": goal_status or {
            "status": "available", "mismatched_visits": 0, "malformed_goal_datetime": 0,
        }},
    }), encoding="utf-8")


def _d09_summary(paths: _Paths) -> dict:
    return next(row for row in _read_metric(paths, "d09") if row["finding"] == "summary")


def test_d09_legacy_manifest_is_unverifiable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 6, 1), client_id="c1", visit_id="v1"),
    ])
    assert "d09" in block0.run(paths, DEFAULTS, {"D09"})
    summary = _d09_summary(paths)
    assert summary["status"] == "unverifiable"
    assert summary["has_problem"] is False
    assert summary["reason"] == "canonical_temporal_manifest_missing"


def test_d09_unknown_boundaries_are_unverifiable_not_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1"),
    ])
    _write_d09_manifest(paths, metrika=_d09_metrika_contract(boundary_semantics="unknown"))
    block0.run(paths, DEFAULTS, {"D09"})
    boundary = next(r for r in _read_metric(paths, "d09") if r["finding"] == "visits_boundary_semantics")
    assert boundary["status"] == "unverifiable"
    assert boundary["reason"] == "visits_boundary_semantics_unknown"
    assert _d09_summary(paths)["status"] == "unverifiable"


def test_d09_unknown_direct_boundary_and_zero_day_are_separate_unverifiable_subchecks(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_costs_d(paths, [{
        "date": date(2026, 6, 10), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    _write_d09_manifest(paths, direct=_d09_direct_contract(
        boundary_semantics="unknown", zero_day_policy="unknown",
    ))

    block0.run(paths, DEFAULTS, {"D09"})
    rows = {row["finding"]: row for row in _read_metric(paths, "d09")}
    assert rows["costs_boundary_semantics"]["status"] == "unverifiable"
    assert rows["costs_zero_day_policy"]["status"] == "unverifiable"
    assert _d09_summary(paths)["has_problem"] is False


def test_d09_passes_for_same_requested_windows_and_compatible_timezones(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_costs_d(paths, [{
        "date": date(2026, 6, 10), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    _write_d09_manifest(paths, direct=_d09_direct_contract())

    block0.run(paths, DEFAULTS, {"D09"})
    assert _d09_summary(paths)["status"] == "pass"


def test_d09_event_date_mapping_conflict_is_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_d09_manifest(paths)
    manifest_path = paths.canonical / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["temporal_provenance"]["canonical_fields"]["visits"]["date"]["operation"] = "utc_date"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    block0.run(paths, DEFAULTS, {"D09"})
    visits = next(r for r in _read_metric(paths, "d09") if r["finding"] == "visits_contract")
    assert visits["status"] == "problem"
    assert visits["reason"] == "visits_event_date_mapping_conflict"


def test_d09_requested_window_difference_is_proven_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_costs_d(paths, [{
        "date": date(2026, 6, 10), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    _write_d09_manifest(paths, direct=_d09_direct_contract(date_from="2026-06-02"))

    block0.run(paths, DEFAULTS, {"D09"})
    row = next(r for r in _read_metric(paths, "d09") if r["finding"] == "visits_costs_requested_window")
    assert row["status"] == "problem" and row["has_problem"] is True
    assert _d09_summary(paths)["status"] == "problem"


def test_d09_incompatible_known_timezones_are_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_costs_d(paths, [{
        "date": date(2026, 6, 10), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    _write_d09_manifest(paths, metrika=_d09_metrika_contract(offset=600), direct=_d09_direct_contract())

    block0.run(paths, DEFAULTS, {"D09"})
    row = next(r for r in _read_metric(paths, "d09") if r["finding"] == "costs_contract")
    assert row["status"] == "problem"
    assert row["reason"] == "visits_costs_timezone_incompatible"


def test_d09_direct_absence_is_not_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_d09_manifest(paths)

    block0.run(paths, DEFAULTS, {"D09"})
    costs = next(r for r in _read_metric(paths, "d09") if r["finding"] == "costs_contract")
    assert costs["status"] == "not_applicable" and costs["has_problem"] is False
    assert _d09_summary(paths)["status"] == "pass"


def test_d09_declared_partial_and_missing_cost_day_are_not_findings(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 6, 3), client_id="c1", visit_id="v1"),
        _base_visit(date=date(2026, 6, 28), client_id="c2", visit_id="v2"),
    ])
    _write_costs_d(paths, [{
        "date": date(2026, 6, 10), "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
        "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net", "clicks": 1, "impressions": 1,
    }])
    metrika = _d09_metrika_contract(date_from="2026-06-03", date_to="2026-06-28")
    direct = _d09_direct_contract(date_from="2026-06-03", date_to="2026-06-28")
    _write_d09_manifest(paths, metrika=metrika, direct=direct)

    block0.run(paths, DEFAULTS, {"D09"})
    partial = next(r for r in _read_metric(paths, "d09") if r["finding"] == "direct_partial_months")
    assert partial["declared_first_month"]["status"] == "declared_partial"
    assert partial["has_problem"] is False
    assert _d09_summary(paths)["status"] == "pass"


def test_d09_canonical_date_outside_requested_window_is_problem(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 7, 1), client_id="c1", visit_id="v1")])
    _write_d09_manifest(paths)

    block0.run(paths, DEFAULTS, {"D09"})
    observed = next(r for r in _read_metric(paths, "d09") if r["finding"] == "visits_observed_range")
    assert observed["status"] == "problem"
    assert observed["reason"] == "canonical_dates_outside_requested_window"


def test_d09_degraded_goal_achievements_are_unverifiable(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_d09_manifest(paths, goal_status={
        "status": "degraded", "mismatched_visits": 1, "malformed_goal_datetime": 0,
    })

    block0.run(paths, DEFAULTS, {"D09"})
    goals = next(r for r in _read_metric(paths, "d09") if r["finding"] == "goal_achievements_contract")
    assert goals["status"] == "unverifiable"
    assert goals["reason"] == "goal_achievements_mismatched_visits"
    assert _d09_summary(paths)["status"] == "unverifiable"


def test_d09_confidence_is_capped_and_output_is_deterministic(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 6, 10), client_id="c1", visit_id="v1")])
    _write_d09_manifest(paths)
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps({"checks": [{"check_id": "D09", "confidence_cap": "LOW"}]}), encoding="utf-8",
    )

    block0.run(paths, DEFAULTS, {"D09"})
    first = _read_metric(paths, "d09")
    block0.run(paths, DEFAULTS, {"D09"})
    assert _read_metric(paths, "d09") == first
    assert all(row["confidence"] == "LOW" for row in first)


# ── D10 — выгрузка неполная (пагинация/лимиты/фильтры/семплирование) ───────

def test_d10_detects_missing_dates_gap(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1"),
        _base_visit(date=date(2026, 1, 5), client_id="c2", visit_id="v2"),
    ])

    artifacts = block0.run(paths, DEFAULTS, {"D10"})
    assert "d10" in artifacts
    rows = _read_metric(paths, "d10")
    row = rows[0]
    assert row["days_in_range"] == 5
    assert row["days_with_visits"] == 2
    assert row["missing_days_count"] == 3
    assert row["has_gap"] is True
    assert "2026-01-02" in row["missing_dates_sample"]


def test_d10_no_gap_when_every_day_present(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, d), client_id=f"c{d}", visit_id=f"v{d}")
        for d in range(1, 4)
    ])

    block0.run(paths, DEFAULTS, {"D10"})
    rows = _read_metric(paths, "d10")
    assert rows[0]["has_gap"] is False
    assert rows[0]["missing_days_count"] == 0


# ── D11 — сотрудники/тесты/боты в данных ────────────────────────────────────

def test_d11_flags_high_frequency_client_id(tmp_path):
    paths = _Paths(tmp_path)
    visits = [
        _base_visit(date=date(2026, 1, 1), client_id="employee1", visit_id=f"v{i}")
        for i in range(60)
    ] + [
        _base_visit(date=date(2026, 1, 1), client_id=f"customer{i}", visit_id=f"c{i}",
                     utm_source_raw="yandex")
        for i in range(10)
    ]
    _write_visits_d(paths, visits)

    artifacts = block0.run(paths, DEFAULTS, {"D11"})
    assert "d11" in artifacts
    rows = _read_metric(paths, "d11")
    flagged = next(r for r in rows if r.get("finding") == "high_frequency_client_id")
    assert flagged["client_id"] == "employee1"
    assert flagged["visit_count"] == 60
    assert flagged["confidence"] == "LOW"
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["high_frequency_client_id_count"] == 1


def test_d11_no_high_frequency_and_test_marker_detected(tmp_path):
    paths = _Paths(tmp_path)
    visits = [
        _base_visit(date=date(2026, 1, 1), client_id=f"customer{i}", visit_id=f"v{i}",
                     utm_source_raw="yandex")
        for i in range(5)
    ] + [
        _base_visit(date=date(2026, 1, 1), client_id="tester1", visit_id="vtest",
                     utm_source_raw="internal_test"),
    ]
    _write_visits_d(paths, visits)

    block0.run(paths, DEFAULTS, {"D11"})
    rows = _read_metric(paths, "d11")
    assert not [r for r in rows if r.get("finding") == "high_frequency_client_id"]
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["high_frequency_client_id_count"] == 0
    assert summary["test_marker_visit_count"] == 1


# ── D12 — таблицы соединяются на неверном уровне детализации ───────────────

def _join_record(**overrides) -> dict:
    record = {
        "join_id": "test_join",
        "tables": {"left": "left_table", "right": "right_table", "output": "output_table"},
        "keys": ["business_key"],
        "expected_cardinality": "1:1",
        "status": "PASS",
        "pre": {"rows": 1, "distinct_keys": 1, "checksums": {"cost_rub": 10.0},
                "non_null_counts": {"cost_rub": 1}},
        "right": {"rows": 1, "distinct_keys": 1, "checksums": {}, "non_null_counts": {}},
        "post": {"rows": 1, "distinct_keys": 1, "checksums": {"cost_rub": 10.0},
                 "non_null_counts": {"cost_rub": 1}},
        "matched": 1,
        "unmatched": {"left": 0, "right": 0},
        "unmatched_policy": {"left": "allowed", "right": "forbidden"},
        "preserved_controls": ["cost_rub"],
    }
    record.update(overrides)
    return record


def _write_join_integrity(paths: _Paths, records: list[dict]) -> None:
    paths.canonical.mkdir(parents=True, exist_ok=True)
    (paths.canonical / "manifest.json").write_text(
        json.dumps({"join_integrity": records}), encoding="utf-8",
    )


def _run_d12(paths: _Paths, records: list[dict]) -> list[dict]:
    _write_visits_d(paths, [_base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1")])
    _write_join_integrity(paths, records)
    assert "d12" in block0.run(paths, DEFAULTS, {"D12"})
    return _read_metric(paths, "d12")


def test_d12_accepts_correct_one_to_one_join(tmp_path):
    paths = _Paths(tmp_path)
    rows = _run_d12(paths, [_join_record()])
    assert rows == [{"check_id": "D12", "join_id": "test_join", "status": "pass",
                     "violations": [], "has_problem": False, "confidence": "HIGH"}]


def test_d12_detects_fan_out(tmp_path):
    paths = _Paths(tmp_path)
    record = _join_record(post={"rows": 2, "distinct_keys": 2, "checksums": {"cost_rub": 20.0},
                                "non_null_counts": {"cost_rub": 2}})
    row = _run_d12(paths, [record])[0]
    assert row["status"] == "problem"
    assert "fan_out_rows" in row["violations"]


def test_d12_detects_preserved_checksum_mismatch(tmp_path):
    paths = _Paths(tmp_path)
    record = _join_record(post={"rows": 1, "distinct_keys": 1, "checksums": {"cost_rub": 9.0},
                                "non_null_counts": {"cost_rub": 1}})
    row = _run_d12(paths, [record])[0]
    assert row["status"] == "problem"
    assert "checksum_mismatch_cost_rub" in row["violations"]


def test_d12_detects_required_unmatched(tmp_path):
    paths = _Paths(tmp_path)
    record = _join_record(right={"rows": 2, "distinct_keys": 2, "checksums": {}, "non_null_counts": {}},
                          matched=1, unmatched={"left": 0, "right": 1})
    row = _run_d12(paths, [record])[0]
    assert row["status"] == "problem"
    assert "required_match_unmatched_right" in row["violations"]


def test_d12_marks_missing_controls_unverifiable(tmp_path):
    paths = _Paths(tmp_path)
    record = _join_record(post={"rows": 1, "distinct_keys": 1, "checksums": {},
                                "non_null_counts": {"cost_rub": 1}})
    row = _run_d12(paths, [record])[0]
    assert row["status"] == "unverifiable"
    assert "post_checksum_cost_rub_absent" in row["missing_controls"]


def test_d12_not_applicable_is_not_problem(tmp_path):
    paths = _Paths(tmp_path)
    record = _join_record(status="NOT_APPLICABLE", reason="source_absent")
    row = _run_d12(paths, [record])[0]
    assert row["status"] == "not_applicable"
    assert row["has_problem"] is False


def test_d12_does_not_treat_costs_segmentation_as_fan_out(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ])
    row = _run_d12(paths, [_join_record()])[0]
    assert row["status"] == "pass"
    assert row["has_problem"] is False


def test_d12_confidence_capped_by_degradation_report(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [_base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1")])
    _write_join_integrity(paths, [_join_record()])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {"checks": [{"check_id": "D12", "confidence_cap": "MED"}]}
    (paths.metrics / "degradation_report.json").write_text(json.dumps(report), encoding="utf-8")
    block0.run(paths, DEFAULTS, {"D12"})
    assert _read_metric(paths, "d12")[0]["confidence"] == "MED"


# ── confidence_cap — compute капает вниз, никогда не поднимает ─────────────

def test_confidence_is_capped_to_degradation_report_ceiling(tmp_path):
    paths = _Paths(tmp_path)
    # Выборка заведомо выше min_sample_visits -> без потолка была бы HIGH.
    visits = [
        _base_visit(form_submit=True, form_submit_count=1) for _ in range(600)
    ]
    _write_visits(paths, visits)

    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {
        "checks": [
            {"check_id": "D01", "confidence_cap": "MED"},
        ]
    }
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    block0.run(paths, DEFAULTS, {"D01"})
    rows = _read_metric(paths, "d01")
    fs = next(r for r in rows if r["goal_group"] == "form_submit")
    assert fs["confidence"] == "MED"  # не HIGH, несмотря на большую выборку


def test_d06_q01_answer_is_applied_by_transform(tmp_path):
    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True)
    config = {
        "costs_manual": {"agency_fee_rub_month": 12000},
        "data_window": {"date_from": "2026-01-01", "date_to": "2026-01-01"},
    }
    client_answers = {"finance": {"vat_basis_by_source": [
        {"source": "agency_fee", "vat_included": True, "evidence": "invoice"},
    ]}}
    bc.build(paths, config, DEFAULTS, client_answers=client_answers)
    _write_client_answers(paths, client_answers)

    block0.run(paths, DEFAULTS, {"D06"})
    row = _read_metric(paths, "d06")[0]

    assert row["actual_cost_status"] == "gross"
    assert row["expected_cost_status"] == "gross"
    assert row["answer_not_applied"] is False
