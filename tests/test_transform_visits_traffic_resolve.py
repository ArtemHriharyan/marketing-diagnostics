"""Тесты resolve_traffic_source / compute_traffic_resolve_stats (T02/T03 carry-forward).

Контракт задачи 4X-traffic-resolve: визиты с сырым lastsign-источником
internal/undefined получают источник ближайшего ПРЕДЫДУЩЕГО (по времени)
визита того же clientID с реальным источником; без такого визита в пределах
доступной истории (включая lookback-окно) — остаются как есть,
traffic_source_resolved=False (ожидаемое поведение, не ошибка).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from src.transform import build_canonical as bc


def _visit(client_id: str, dt_str: str, raw_source: str | None) -> dict:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return {
        "visit_id": f"{client_id}-{dt_str}",
        "client_id": client_id,
        "dt": dt,
        "date": dt.date(),
        "last_sign_traffic_source_raw": raw_source,
        "source_group": bc.classify_traffic_source(raw_source),
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_chain_ad_internal_internal_direct_only_forward_fill():
    """ad -> internal -> internal -> direct: оба internal получают ad, не direct."""
    rows = [
        _visit("c1", "2026-06-01 10:00:00", "ad"),
        _visit("c1", "2026-06-02 10:00:00", "internal"),
        _visit("c1", "2026-06-03 10:00:00", "internal"),
        _visit("c1", "2026-06-04 10:00:00", "direct"),
    ]
    out = bc.resolve_traffic_source(_df(rows)).set_index("visit_id")

    assert out.loc["c1-2026-06-02 10:00:00", "source_group_resolved"] == "ad"
    assert out.loc["c1-2026-06-02 10:00:00", "traffic_source_resolved"] == True  # noqa: E712
    assert out.loc["c1-2026-06-03 10:00:00", "source_group_resolved"] == "ad"
    assert out.loc["c1-2026-06-03 10:00:00", "traffic_source_resolved"] == True  # noqa: E712
    # Последний визит (direct) не откатывается назад под влиянием будущих строк.
    assert out.loc["c1-2026-06-04 10:00:00", "source_group_resolved"] == "direct"


def test_client_without_any_real_source_stays_unresolved():
    """clientID без единого реального источника в истории -> unresolved, без ошибки."""
    rows = [
        _visit("c2", "2026-06-01 09:00:00", "internal"),
        _visit("c2", "2026-06-02 09:00:00", "undefined"),
    ]
    out = bc.resolve_traffic_source(_df(rows))

    assert (out["traffic_source_resolved"] == False).all()  # noqa: E712
    # Значение не выдумывается: остаётся исходная классификация (internal/other).
    assert set(out["source_group_resolved"]) == {"internal", "other"}


def test_visit_with_real_source_is_unchanged():
    """Визит с реальным источником проходит как есть."""
    rows = [_visit("c3", "2026-06-01 09:00:00", "search_engine")]
    out = bc.resolve_traffic_source(_df(rows)).iloc[0]

    assert out["traffic_source_resolved"] == True  # noqa: E712
    assert out["source_group_resolved"] == out["source_group"] == "organic"


def test_lookback_boundary_excludes_source_before_cutoff():
    """Реальный источник за пределами lookback-окна не используется."""
    rows = [
        _visit("c4", "2026-05-01 09:00:00", "ad"),        # раньше границы lookback
        _visit("c4", "2026-06-05 09:00:00", "internal"),  # в отчётном окне
    ]
    out = bc.resolve_traffic_source(_df(rows), lookback_cutoff=date(2026, 6, 1))
    row = out[out["date"] == date(2026, 6, 5)].iloc[0]

    assert row["traffic_source_resolved"] == False  # noqa: E712
    assert row["source_group_resolved"] == "internal"  # осталось как есть


def test_lookback_boundary_is_inclusive_of_cutoff_date():
    """Реальный источник ровно на границе lookback-окна — используется (включительно)."""
    rows = [
        _visit("c5", "2026-06-01 00:00:00", "ad"),
        _visit("c5", "2026-06-05 09:00:00", "internal"),
    ]
    out = bc.resolve_traffic_source(_df(rows), lookback_cutoff=date(2026, 6, 1))
    row = out[out["date"] == date(2026, 6, 5)].iloc[0]

    assert row["traffic_source_resolved"] == True  # noqa: E712
    assert row["source_group_resolved"] == "ad"


def test_output_order_matches_input_order_regardless_of_chronology():
    """Порядок строк на выходе = порядок на входе, даже если он не хронологический."""
    rows = [
        _visit("c8", "2026-06-03 09:00:00", "internal"),
        _visit("c8", "2026-06-01 09:00:00", "ad"),
        _visit("c8", "2026-06-02 09:00:00", "internal"),
    ]
    df_in = _df(rows)
    df_out = bc.resolve_traffic_source(df_in)

    assert list(df_out["visit_id"]) == list(df_in["visit_id"])
    by_id = df_out.set_index("visit_id")
    assert by_id.loc["c8-2026-06-02 09:00:00", "source_group_resolved"] == "ad"
    assert by_id.loc["c8-2026-06-03 09:00:00", "source_group_resolved"] == "ad"


def test_empty_dataframe_returns_empty_with_new_columns():
    df = pd.DataFrame(columns=["client_id", "dt", "date", "last_sign_traffic_source_raw"])
    out = bc.resolve_traffic_source(df)

    assert out.empty
    assert "source_group_resolved" in out.columns
    assert "traffic_source_resolved" in out.columns


def test_compute_traffic_resolve_stats_fraction():
    rows = [
        _visit("c6", "2026-06-01 09:00:00", "ad"),
        _visit("c6", "2026-06-02 09:00:00", "internal"),   # resolved -> ad
        _visit("c7", "2026-06-01 09:00:00", "undefined"),  # unresolved (нет реального до)
    ]
    out = bc.resolve_traffic_source(_df(rows))
    stats = bc.compute_traffic_resolve_stats(out)

    assert stats["internal_or_undefined_total"] == 2
    assert stats["unresolved_count"] == 1
    assert stats["unresolved_frac"] == pytest.approx(0.5)


def test_compute_traffic_resolve_stats_no_ambiguous_visits_is_zero():
    rows = [_visit("c9", "2026-06-01 09:00:00", "ad")]
    out = bc.resolve_traffic_source(_df(rows))
    stats = bc.compute_traffic_resolve_stats(out)

    assert stats == {
        "internal_or_undefined_total": 0,
        "unresolved_count": 0,
        "unresolved_frac": 0.0,
    }
