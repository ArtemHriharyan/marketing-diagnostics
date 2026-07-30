"""Тесты CRM-экстрактора на реальной схеме клиента pognali.rent.

Реальная выгрузка (см. AUDIT-crm-real-file-ingestion,
docs/implementation_status.md) — не canonical-именование
crm_import.CANONICAL_COLUMNS, а плоский лид/сделка-экспорт:
``lead_id;created_at;source;utm_source;utm_campaign;stage;is_repeat;
deal_amount_rub;closed_at`` — ';'-разделитель, UTF-8, точка как
десятичный разделитель, дата dd.mm.yyyy HH:MM. source/stage/is_repeat
пусты во всех строках реального файла — денежная сверка возможна,
атрибуция/статус нет. Задача CRM-scope-money-only фиксирует это
явным manifest-флагом ``crm_attribution_reliable``, а не тихим
пропуском пустых колонок.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import crm_import  # noqa: E402


class _Paths:
    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"


@pytest.fixture
def paths(tmp_path):
    return _Paths(tmp_path)


# Строки в реальной схеме клиента: source/stage/is_repeat/utm_* пусты,
# заполнены только lead_id, created_at, deal_amount_rub, closed_at.
REAL_SCHEMA_HEADER = (
    "lead_id;created_at;source;utm_source;utm_campaign;stage;is_repeat;"
    "deal_amount_rub;closed_at"
)
REAL_SCHEMA_ROWS = [
    "653065;08.07.2026 11:40;;;;;;8200.0;10.07.2026 12:00",
    "651768;06.07.2026 19:24;;;;;;9000.0;08.07.2026 22:00",
    "650273;04.07.2026 10:06;;;;;;13700.0;08.07.2026 10:00",
]


def _write_real_schema_csv(root: Path) -> Path:
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    path = inputs / "crm_export.csv"
    path.write_text(
        "\r\n".join([REAL_SCHEMA_HEADER, *REAL_SCHEMA_ROWS]) + "\r\n",
        encoding="utf-8",
    )
    return path


COLUMN_MAP = {
    "lead_date": "created_at",
    "phone_or_id": "lead_id",
    "amount_rub": "deal_amount_rub",
}


def _config(*, column_map=None, attribution_reliable=None, reason=None):
    crm_csv: dict = {"column_map": column_map or {}}
    if attribution_reliable is not None:
        crm_csv["attribution_reliable"] = attribution_reliable
    if reason is not None:
        crm_csv["attribution_unreliable_reason"] = reason
    return {
        "sources": {"crm_csv": {"enabled": True, "path": "inputs/crm_export.csv", "raw_format": "csv"}},
        "crm_csv": crm_csv,
    }


def test_without_column_map_real_schema_all_rows_rejected_bad_date(paths):
    """Регрессия аудита: без column_map реальные заголовки не совпадают
    с CANONICAL_COLUMNS (lead_date vs created_at и т.д.) — 0 строк принято."""
    _write_real_schema_csv(paths.root)
    config = _config()  # без column_map

    result = crm_import.extract(config, env={}, paths=paths)

    assert result["accepted"] == 0
    assert result["rejected"] == len(REAL_SCHEMA_ROWS)
    assert result["rejected_reasons"] == {"bad_date": len(REAL_SCHEMA_ROWS)}


def test_with_column_map_real_schema_all_rows_accepted(paths):
    """С минимальным column_map (3 ключа) реальная выгрузка парсится полностью."""
    _write_real_schema_csv(paths.root)
    config = _config(column_map=COLUMN_MAP)

    result = crm_import.extract(config, env={}, paths=paths)

    assert result["accepted"] == len(REAL_SCHEMA_ROWS)
    assert result["rejected"] == 0


def test_money_and_date_fields_match_source(paths):
    """Денежные поля и даты совпадают с исходником построчно (см. аудит, п.3)."""
    _write_real_schema_csv(paths.root)
    config = _config(column_map=COLUMN_MAP)

    crm_import.extract(config, env={}, paths=paths)

    leads_csv = paths.raw / "crm" / "leads.csv"
    rows = leads_csv.read_text(encoding="utf-8").strip().splitlines()
    header = rows[0].split(",")
    records = [dict(zip(header, r.split(","))) for r in rows[1:]]
    by_amount = {rec["amount_rub"]: rec for rec in records}

    assert by_amount["8200.0"]["lead_date"] == "2026-07-08"
    assert by_amount["9000.0"]["lead_date"] == "2026-07-06"
    assert by_amount["13700.0"]["lead_date"] == "2026-07-04"


def test_source_column_preserved_in_raw_even_when_empty(paths):
    """raw неизменяем (принцип 2 CLAUDE.md): source не удаляется из RAW_FIELDS,
    даже если пуст во всех строках — помечается ненадёжным на уровне manifest,
    не вычищается из данных."""
    _write_real_schema_csv(paths.root)
    config = _config(column_map=COLUMN_MAP)

    crm_import.extract(config, env={}, paths=paths)

    leads_csv = paths.raw / "crm" / "leads.csv"
    header = leads_csv.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "source" in header
    assert "status" in header


def test_attribution_reliable_false_recorded_in_manifest_and_report(paths):
    """crm_attribution_reliable: false — явный флаг с причиной, а не молчание."""
    _write_real_schema_csv(paths.root)
    reason = "source/stage пусты в 100% строк — атрибуция недоступна"
    config = _config(column_map=COLUMN_MAP, attribution_reliable=False, reason=reason)

    crm_import.extract(config, env={}, paths=paths)

    report = json.loads((paths.raw / "crm" / "validation_report.json").read_text(encoding="utf-8"))
    assert report["attribution_reliable"] is False
    assert report["attribution_unreliable_reason"] == reason

    manifest = json.loads((paths.raw / "manifest.json").read_text(encoding="utf-8"))
    crm_entry = manifest["sources"]["crm"]
    assert crm_entry["crm_attribution_reliable"] is False
    assert crm_entry["crm_attribution_unreliable_reason"] == reason


def test_attribution_reliable_defaults_true_without_flag(paths):
    """Клиенты без crm_csv.attribution_reliable не затронуты (default True)."""
    _write_real_schema_csv(paths.root)
    config = _config(column_map=COLUMN_MAP)  # флаг не задан

    crm_import.extract(config, env={}, paths=paths)

    manifest = json.loads((paths.raw / "manifest.json").read_text(encoding="utf-8"))
    crm_entry = manifest["sources"]["crm"]
    assert crm_entry["crm_attribution_reliable"] is True
    assert crm_entry["crm_attribution_unreliable_reason"] is None


def test_manifest_flag_visible_to_degradation(paths):
    """crm_attribution_reliable=false доступен degradation.collect_manifest_flags
    как обычный булев флаг манифеста (для будущего type_downgrade_if)."""
    from src.pipeline import degradation

    _write_real_schema_csv(paths.root)
    reason = "source/stage пусты"
    config = _config(column_map=COLUMN_MAP, attribution_reliable=False, reason=reason)
    crm_import.extract(config, env={}, paths=paths)

    manifest = json.loads((paths.raw / "manifest.json").read_text(encoding="utf-8"))
    flags = degradation.collect_manifest_flags(manifest)
    assert flags["crm_attribution_reliable"] is False
