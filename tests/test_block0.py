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

    artifacts = block0.run(paths, DEFAULTS, set())
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

    block0.run(paths, DEFAULTS, set())
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

    artifacts = block0.run(paths, DEFAULTS, set())
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

    block0.run(paths, DEFAULTS, set())
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

    artifacts = block0.run(paths, DEFAULTS, set())
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

    block0.run(paths, DEFAULTS, set())
    for name in ("d02", "d03"):
        rows = _read_metric(paths, name)
        assert rows[0]["status"] == "unavailable"
        assert rows[0]["reason"] == "goals metadata недоступна"


def test_d02_d03_run_even_when_not_in_runnable_ids_but_goals_present(tmp_path):
    """Известный разрыв extract-манифеста (4I-goals-canonical) не должен молча
    блокировать D02/D03, если goals.parquet физически присутствует и непуст."""
    paths = _Paths(tmp_path)
    _write_visits(paths, [_base_visit()])
    _write_goals(paths, [{"goal_id": "1", "name": "Заявка", "type": "url"}])
    _write_config(paths)

    # Пустой runnable_ids — как в реальном прогоне сегодня (D02/D03 никогда не
    # попадают в runnable_check_ids из-за пробела в CANONICAL_TABLES extract).
    artifacts = block0.run(paths, DEFAULTS, set())
    rows = _read_metric(paths, "d02")
    assert "status" not in rows[0]
    assert rows[0]["goal_id"] == "1"


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

def test_d07_flags_missing_declared_cost_and_double_counted_budget(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
         "cost_raw": 1000.0, "cost_normalized": 1000.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 15))
    ] + [
        {"date": d, "source_tag": "yandex_business", "campaign_id": None, "campaign_name": None,
         "cost_raw": 500.0, "cost_normalized": 500.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 15))
    ])
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
    dup = next(r for r in rows if r["finding"] == "possible_double_counted_budget")
    assert dup["both_present"] is True


def test_d07_no_finding_when_declared_cost_present_and_no_double_count(tmp_path):
    paths = _Paths(tmp_path)
    # Календарный месяц целиком (31 день января): факт совпадает с заявленным Q02.
    rows_in = [
        {"date": date(2026, 1, d), "source_tag": "agency_fee", "campaign_id": None,
         "campaign_name": None, "cost_raw": 100.0, "cost_normalized": 100.0,
         "cost_status": "net", "clicks": None, "impressions": None}
        for d in range(1, 32)
    ] + [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 500.0, "cost_normalized": 500.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ]
    _write_costs_d(paths, rows_in)
    _write_client_answers(paths, {
        "finance": {
            "hidden_costs_rub_month": [
                {"name": "Фикс подрядчика", "rub_month": 3100.0, "source_tag": "agency_fee"},
            ]
        }
    })

    block0.run(paths, DEFAULTS, {"D07"})
    rows = _read_metric(paths, "d07")
    declared = next(r for r in rows if r["finding"] == "declared_cost_check")
    assert declared["missing_in_data"] is False
    assert declared["amount_mismatch"] is False
    dup = next(r for r in rows if r["finding"] == "possible_double_counted_budget")
    assert dup["both_present"] is False


# ── D08 — архивные/остановленные кампании исключены из истории ─────────────

def test_d08_no_stopped_campaigns_detected_flag(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": cid, "campaign_name": f"camp-{cid}",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31)) for cid in ("1", "2")
    ])

    artifacts = block0.run(paths, DEFAULTS, {"D08"})
    assert "d08" in artifacts
    rows = _read_metric(paths, "d08")
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["total_campaigns"] == 2
    assert summary["stopped_campaign_count"] == 0
    assert summary["no_stopped_campaigns_detected"] is True


def test_d08_detects_campaign_stopped_before_window_end(tmp_path):
    paths = _Paths(tmp_path)
    rows_in = [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "still-running",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ] + [
        {"date": d, "source_tag": "direct", "campaign_id": "2", "campaign_name": "stopped-early",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 5))
    ]
    _write_costs_d(paths, rows_in)

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    stopped_row = next(r for r in rows if r.get("campaign_id") == "2")
    assert stopped_row["stopped_before_window_end"] is True
    summary = next(r for r in rows if r["finding"] == "summary")
    assert summary["stopped_campaign_count"] == 1
    assert summary["no_stopped_campaigns_detected"] is False


def test_d08_confidence_capped_by_degradation_report(tmp_path):
    paths = _Paths(tmp_path)
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {"checks": [{"check_id": "D08", "confidence_cap": "LOW"}]}
    (paths.metrics / "degradation_report.json").write_text(json.dumps(report), encoding="utf-8")

    block0.run(paths, DEFAULTS, {"D08"})
    rows = _read_metric(paths, "d08")
    # Без потолка per-campaign строки были бы HIGH, summary — MED.
    assert all(r["confidence"] == "LOW" for r in rows)


# ── D09 — периоды/часовые пояса/даты не приведены к единому правилу ────────

def test_d09_flags_incomplete_month_and_period_mismatch(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1"),
        _base_visit(date=date(2026, 2, 10), client_id="c2", visit_id="v2"),
    ])
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ])

    artifacts = block0.run(paths, DEFAULTS, {"D09"})
    assert "d09" in artifacts
    rows = _read_metric(paths, "d09")
    incomplete = next(r for r in rows if r["finding"] == "incomplete_last_month")
    assert incomplete["is_incomplete"] is True
    assert incomplete["last_month"] == "2026-02"
    mismatch = next(r for r in rows if r["finding"] == "visits_costs_period_mismatch")
    assert mismatch["mismatch"] is True


def test_d09_no_finding_when_month_complete_and_periods_aligned(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=d, client_id=f"c{d.day}", visit_id=f"v{d.day}")
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ])
    _write_costs_d(paths, [
        {"date": d, "source_tag": "direct", "campaign_id": "1", "campaign_name": "c1",
         "cost_raw": 10.0, "cost_normalized": 10.0, "cost_status": "net",
         "clicks": 1, "impressions": 1}
        for d in (date(2026, 1, 1), date(2026, 1, 31))
    ])

    block0.run(paths, DEFAULTS, {"D09"})
    rows = _read_metric(paths, "d09")
    incomplete = next(r for r in rows if r["finding"] == "incomplete_last_month")
    assert incomplete["is_incomplete"] is False
    mismatch = next(r for r in rows if r["finding"] == "visits_costs_period_mismatch")
    assert mismatch["mismatch"] is False


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

def test_d12_detects_duplicate_costs_key(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1"),
    ])
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ])

    artifacts = block0.run(paths, DEFAULTS, {"D12"})
    assert "d12" in artifacts
    rows = _read_metric(paths, "d12")
    costs_row = next(r for r in rows if r["table"] == "costs")
    assert costs_row["has_duplicate_keys"] is True
    assert costs_row["duplicate_key_count"] == 1
    visits_row = next(r for r in rows if r["table"] == "visits")
    assert visits_row["has_duplicate_keys"] is False


def test_d12_no_duplicates_when_keys_unique(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1"),
        _base_visit(date=date(2026, 1, 2), client_id="c2", visit_id="v2"),
    ])
    _write_costs_d(paths, [
        {"date": date(2026, 1, 1), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
        {"date": date(2026, 1, 2), "source_tag": "direct", "campaign_id": "1",
         "campaign_name": "c1", "cost_raw": 10.0, "cost_normalized": 10.0,
         "cost_status": "net", "clicks": 1, "impressions": 1},
    ])

    block0.run(paths, DEFAULTS, {"D12"})
    rows = _read_metric(paths, "d12")
    assert all(r["has_duplicate_keys"] is False for r in rows)


def test_d12_confidence_capped_by_degradation_report(tmp_path):
    paths = _Paths(tmp_path)
    _write_visits_d(paths, [
        _base_visit(date=date(2026, 1, 1), client_id="c1", visit_id="v1"),
    ])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {"checks": [{"check_id": "D12", "confidence_cap": "MED"}]}
    (paths.metrics / "degradation_report.json").write_text(json.dumps(report), encoding="utf-8")

    block0.run(paths, DEFAULTS, {"D12"})
    rows = _read_metric(paths, "d12")
    # visits-строка сама по себе была бы HIGH (точный подсчёт дублей) — не выше потолка.
    assert all(r["confidence"] == "MED" for r in rows)


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
