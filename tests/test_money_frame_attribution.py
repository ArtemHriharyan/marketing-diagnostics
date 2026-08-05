"""Тесты уровня ключа атрибуции (задача 7G): src/compute/money_frame.py.

Сценарии:
1. L_UNKNOWN — CRM-источника нет вообще (нет crm.parquet).
2. L0 — CRM есть, source_norm забит заглушкой "unknown", ключа склейки нет.
3. L1 — заполнено поле источника выше порога, ключа склейки нет.
4. L2 — client_id заполнен и даёт фактический JOIN с visits выше порога.
5. L2 по контакту: phone_hash + таймстамп + фактический JOIN.
6. Порог ровно на границе (доля == порогу) — проходит.
7. Доказательство (имя колонки + фактическая доля непустых) присутствует
   при каждом уровне; уровень без доказательства не пишется.
8. unique_customers_available=False со статусом not_computable и причиной
   "нет ключа склейки повторных обращений" (постоянное ограничение
   источника), в отличие от not_computed_yet при отсутствии CRM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.compute import money_frame  # noqa: E402


ATTRIBUTION_CONFIG = {
    "min_join_key_fill_rate": 0.5,
    "min_join_success_rate": 0.5,
    "min_source_fill_rate": 0.5,
    "min_repeat_key_fill_rate": 0.5,
    "placeholder_values": ["unknown", "не определено", "(not set)", "none", "null", "-"],
    "join_keys": [
        {"crm_column": "client_id", "visits_column": "client_id"},
        {"crm_column": "yclid", "visits_column": "click_id"},
    ],
    "contact_keys": [{"crm_column": "phone_hash", "visits_column": "phone_hash"}],
    "timestamp_columns": ["lead_date"],
    "source_columns": ["source_norm", "utm_source"],
    "repeat_columns": ["is_new_client", "is_repeat"],
}

DEFAULTS = {"currency_round": 0, "attribution": ATTRIBUTION_CONFIG}


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_money_frame.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.config_file = root / "config.yaml"


def _paths(tmp_path: Path) -> _Paths:
    paths = _Paths(tmp_path)
    paths.canonical.mkdir(parents=True, exist_ok=True)
    paths.metrics.mkdir(parents=True, exist_ok=True)
    return paths


def _write_table(paths: _Paths, name: str, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_parquet(paths.canonical / f"{name}.parquet", index=False)


def _evidence(result: dict, role: str, column: str | None = None) -> dict:
    for item in result["evidence"]:
        if item.get("role") == role and (column is None or item.get("column") == column):
            return item
    raise AssertionError(f"нет доказательства role={role} column={column}")


# ── Фикстуры данных ─────────────────────────────────────────────────────────
def _crm_rows(n: int, **overrides) -> list[dict]:
    rows = []
    for i in range(n):
        row = {
            "lead_date": f"2026-01-{(i % 28) + 1:02d}",
            "source_norm": "unknown",
            "status_norm": "unknown",
            "amount_rub": 1000.0,
            "is_new_client": None,
            "phone_hash": None,
        }
        for key, values in overrides.items():
            row[key] = values[i]
        rows.append(row)
    return rows


def _visits_rows(client_ids: list, phone_hashes: list | None = None) -> list[dict]:
    rows = []
    for i, cid in enumerate(client_ids):
        rows.append({
            "visit_id": f"v{i}",
            "client_id": cid,
            "click_id": None,
            "phone_hash": (phone_hashes[i] if phone_hashes else None),
        })
    return rows


# ── 1. L_UNKNOWN ────────────────────────────────────────────────────────────
def test_l_unknown_when_no_crm_source(tmp_path):
    paths = _paths(tmp_path)
    _write_table(paths, "visits", _visits_rows(["a", "b"]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L_UNKNOWN
    assert _evidence(result, "table")["present"] is False
    assert result["unique_customers_available"] is False
    assert result["unique_customers_status"] == money_frame.STATUS_NOT_COMPUTED_YET


# ── 2. L0 ───────────────────────────────────────────────────────────────────
def test_l0_when_crm_exists_but_all_keys_empty(tmp_path):
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10))
    _write_table(paths, "visits", _visits_rows(["a", "b"]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L0
    source = _evidence(result, "source", "source_norm")
    assert source["present"] is True
    assert source["non_empty_share"] == 0.0  # "unknown" — заглушка, не значение
    assert source["passes"] is False


def test_l0_unique_customers_not_computable_with_reason(tmp_path):
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L0
    assert result["unique_customers_available"] is False
    assert result["unique_customers_status"] == money_frame.STATUS_NOT_COMPUTABLE
    assert result["unique_customers_reason"] == money_frame.NO_REPEAT_KEY_REASON
    assert _evidence(result, "repeat_key", "is_new_client")["non_empty_share"] == 0.0


def test_not_computable_is_not_not_computed_yet(tmp_path):
    """Постоянное ограничение источника != ещё не посчитанная величина."""
    with_crm = _paths(tmp_path / "with_crm")
    _write_table(with_crm, "crm", _crm_rows(10))
    without_crm = _paths(tmp_path / "without_crm")

    assert (
        money_frame.compute_attribution(with_crm, DEFAULTS)["unique_customers_status"]
        == money_frame.STATUS_NOT_COMPUTABLE
    )
    assert (
        money_frame.compute_attribution(without_crm, DEFAULTS)["unique_customers_status"]
        == money_frame.STATUS_NOT_COMPUTED_YET
    )


# ── 3. L1 ───────────────────────────────────────────────────────────────────
def test_l1_when_source_field_filled(tmp_path):
    paths = _paths(tmp_path)
    sources = ["yandex_direct"] * 6 + ["unknown"] * 4
    _write_table(paths, "crm", _crm_rows(10, source_norm=sources))
    _write_table(paths, "visits", _visits_rows(["a", "b"]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L1
    source = _evidence(result, "source", "source_norm")
    assert source["non_empty_share"] == 0.6
    assert source["passes"] is True


# ── 4-5. L2 ─────────────────────────────────────────────────────────────────
def test_l2_when_client_id_joins_with_visits(tmp_path):
    paths = _paths(tmp_path)
    client_ids = [f"c{i}" for i in range(10)]
    _write_table(paths, "crm", _crm_rows(10, client_id=client_ids))
    _write_table(paths, "visits", _visits_rows(client_ids[:8]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L2
    key = _evidence(result, "join_key", "client_id")
    assert key["non_empty_share"] == 1.0
    assert key["join_matched"] == 8
    assert key["join_success_rate"] == 0.8
    assert key["join_passes"] is True


def test_l2_requires_actual_join_not_just_column(tmp_path):
    """Колонка заполнена, но ни одна запись не сматчилась с визитами -> не L2."""
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10, client_id=[f"c{i}" for i in range(10)]))
    _write_table(paths, "visits", _visits_rows(["zzz"]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L0
    key = _evidence(result, "join_key", "client_id")
    assert key["passes"] is True
    assert key["join_success_rate"] == 0.0
    assert key["join_passes"] is False


def test_l2_by_contact_plus_timestamp(tmp_path):
    paths = _paths(tmp_path)
    hashes = [f"h{i}" for i in range(10)]
    _write_table(paths, "crm", _crm_rows(10, phone_hash=hashes))
    _write_table(paths, "visits", _visits_rows(["a"] * 10, phone_hashes=hashes))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L2
    contact = _evidence(result, "contact_key", "phone_hash")
    assert contact["join_passes"] is True
    assert _evidence(result, "timestamp", "lead_date")["passes"] is True
    # контакт одновременно закрывает склейку повторных обращений
    assert result["unique_customers_available"] is True
    assert result["unique_customers_status"] == money_frame.STATUS_AVAILABLE


def test_contact_without_join_is_not_l2(tmp_path):
    """Контакт есть, но в visits такой колонки нет — JOIN невозможен."""
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10, phone_hash=[f"h{i}" for i in range(10)]))
    pd.DataFrame([{"visit_id": "v1", "client_id": "a"}]).to_parquet(
        paths.canonical / "visits.parquet", index=False
    )

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == money_frame.ATTRIBUTION_L0
    contact = _evidence(result, "contact_key", "phone_hash")
    assert contact["join_possible"] is False
    assert contact["join_passes"] is False


# ── 6. Границы порогов ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "filled, expected_level, expected_share",
    [(5, money_frame.ATTRIBUTION_L1, 0.5), (4, money_frame.ATTRIBUTION_L0, 0.4)],
)
def test_source_threshold_boundary_is_inclusive(tmp_path, filled, expected_level, expected_share):
    paths = _paths(tmp_path / str(filled))
    sources = ["yandex_direct"] * filled + ["unknown"] * (10 - filled)
    _write_table(paths, "crm", _crm_rows(10, source_norm=sources))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == expected_level
    assert _evidence(result, "source", "source_norm")["non_empty_share"] == expected_share


@pytest.mark.parametrize(
    "matched, expected_level",
    [(5, money_frame.ATTRIBUTION_L2), (4, money_frame.ATTRIBUTION_L0)],
)
def test_join_success_threshold_boundary_is_inclusive(tmp_path, matched, expected_level):
    paths = _paths(tmp_path / str(matched))
    client_ids = [f"c{i}" for i in range(10)]
    _write_table(paths, "crm", _crm_rows(10, client_id=client_ids))
    _write_table(paths, "visits", _visits_rows(client_ids[:matched]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["level"] == expected_level
    assert _evidence(result, "join_key", "client_id")["join_success_rate"] == matched / 10


def test_thresholds_come_from_config_not_code(tmp_path):
    """Тот же датасет, поднятый порог -> уровень падает: порог читается из конфига."""
    paths = _paths(tmp_path)
    sources = ["yandex_direct"] * 6 + ["unknown"] * 4
    _write_table(paths, "crm", _crm_rows(10, source_norm=sources))

    strict = {**DEFAULTS, "attribution": {**ATTRIBUTION_CONFIG, "min_source_fill_rate": 0.7}}
    assert money_frame.compute_attribution(paths, DEFAULTS)["level"] == money_frame.ATTRIBUTION_L1
    assert money_frame.compute_attribution(paths, strict)["level"] == money_frame.ATTRIBUTION_L0


# ── 7. Доказательство обязательно ───────────────────────────────────────────
@pytest.mark.parametrize("case", ["l_unknown", "l0", "l1", "l2"])
def test_evidence_present_at_every_level(tmp_path, case):
    paths = _paths(tmp_path / case)
    if case != "l_unknown":
        client_ids = [f"c{i}" for i in range(10)] if case == "l2" else [None] * 10
        sources = ["yandex_direct"] * 10 if case == "l1" else ["unknown"] * 10
        _write_table(paths, "crm", _crm_rows(10, client_id=client_ids, source_norm=sources))
        _write_table(paths, "visits", _visits_rows([f"c{i}" for i in range(10)]))

    result = money_frame.compute_attribution(paths, DEFAULTS)

    assert result["evidence"], "уровень не пишется без доказательства"
    for item in result["evidence"]:
        assert "table" in item and "present" in item
        if item.get("role") in ("join_key", "contact_key", "source", "timestamp", "repeat_key"):
            assert item["column"]                      # имя проверенной колонки
            assert isinstance(item["non_empty_share"], float)  # фактическая доля


def test_level_not_written_without_attribution_config(tmp_path):
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10))

    assert money_frame.compute_attribution(paths, {"currency_round": 0}) is None


# ── Интеграция в артефакт money_frame.json ──────────────────────────────────
def test_attribution_row_written_to_money_frame(tmp_path):
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10))

    money_frame.run(paths, DEFAULTS, set())

    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    attribution = [r for r in rows if r.get("kind") == "attribution"]
    assert len(attribution) == 1
    row = attribution[0]
    assert row["attribution_level"] == money_frame.ATTRIBUTION_L0
    assert row["attribution_evidence"]
    assert row["unique_customers_available"] is False
    assert row["unique_customers_status"] == money_frame.STATUS_NOT_COMPUTABLE
    assert row["unique_customers_reason"] == money_frame.NO_REPEAT_KEY_REASON


def test_existing_money_frame_keys_not_renamed(tmp_path):
    """Строка атрибуции — только добавление: прочие kind и их ключи не меняются."""
    paths = _paths(tmp_path)
    _write_table(paths, "crm", _crm_rows(10))

    money_frame.run(paths, DEFAULTS, set())

    rows = json.loads((paths.metrics / "money_frame.json").read_text(encoding="utf-8"))
    seo_caveat = next(r for r in rows if r.get("kind") == "caveat")
    assert seo_caveat["description"] == money_frame.SEO_NOT_READY_NOTE
    assert {"check_id", "kind", "money_category", "amount_rub"} <= set(seo_caveat)
