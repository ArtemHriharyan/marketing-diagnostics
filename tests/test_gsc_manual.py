"""Тесты gsc_manual — ручная выгрузка Google Search Console.

Три обязательных сценария:
    1. Норма — валидный CSV -> принятые строки, тот же выходной контракт что у gsc_api.
    2. Расхождение >10% с total_clicks_ui -> caveat в отчёте и манифесте.
    3. Отсутствие колонки device -> device=«unknown», строка сохранена, месяц в
       device_missing_months; ниже по пайплайну S20 обязан исключить такой месяц.

Сетевые вызовы не используются: gsc_manual работает только с локальными файлами.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402
from src.extract import gsc_manual  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Фикстуры ──────────────────────────────────────────────────────────────────

class Paths:
    """Мини-дублёр ClientPaths: gsc_manual нужны только .raw и .root."""

    def __init__(self, raw: Path, root: Path | None = None) -> None:
        self.raw = raw
        self.root = root if root is not None else raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "data" / "raw", root=tmp_path)


CONFIG_MANUAL = {
    "sources": {
        "gsc": {
            "enabled": True,
            "mode": "manual",
            "raw_format": "csv",
            "manual_export_dir": "inputs/manual_exports/gsc",
        }
    },
}


def _put_csv(paths: Paths, filename: str, text: str, meta: str | None = None) -> None:
    """Положить ручную выгрузку в inputs/manual_exports/gsc/."""
    d = paths.root / "inputs" / "manual_exports" / "gsc"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(text, encoding="utf-8")
    if meta is not None:
        stem = Path(filename).stem
        (d / f"{stem}.meta.yaml").write_text(meta, encoding="utf-8")


# ── Сценарий 1: норма ─────────────────────────────────────────────────────────

def test_gsc_manual_norm_accepts_rows_and_writes_contract(paths):
    """Норма: валидный CSV — принятые строки, заголовок идентичен контракту gsc_api."""
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://example.com/cars,DESKTOP,15,200,7.5%,2.1\n"
        "прокат машин,https://example.com/,MOBILE,3,60,5%,5.4\n"
        ",https://example.com/x,DESKTOP,1,10,1%,9.0\n",  # пустой query -> reject
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["source"] == "gsc"
    assert result["source_mode"] == "manual"
    assert result["completeness"] == "unverified"
    assert result["accepted"] == 2
    assert result["rejected"] == 1
    assert result["rejected_reasons"] == {"missing_query": 1}
    assert result["months"] == ["2026-05"]
    assert result["device_missing_months"] == []
    assert result["clicks_ui_caveats"] == []
    assert result["canonical_tables"] == ["seo_queries"]

    # Заголовок выходного CSV совпадает с контрактом gsc_api (RAW_FIELDS).
    out = paths.raw / "gsc" / "gsc_2026-05.csv"
    assert out.exists()
    header = out.read_text("utf-8").splitlines()[0]
    assert header == "month,query,page,device,clicks,impressions,ctr,position"

    # Месяц берётся из имени файла, не из данных.
    first_row = out.read_text("utf-8").splitlines()[1]
    assert first_row.startswith("2026-05,")

    # Манифест: completeness=unverified, source_mode=manual.
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["source_mode"] == "manual"
    assert entry["completeness"] == "unverified"
    assert entry["canonical_tables"] == ["seo_queries"]


def test_gsc_manual_norm_column_map_alias(paths):
    """column_map: нестандартные заголовки CSV прозрачно маппируются на канонические."""
    config = {
        "sources": {
            "gsc": {
                "enabled": True,
                "mode": "manual",
                "raw_format": "csv",
                "manual_export_dir": "inputs/manual_exports/gsc",
                "column_map": {"query": "Запрос", "clicks": "Клики",
                               "impressions": "Показы", "ctr": "CTR",
                               "position": "Позиция"},
            }
        }
    }
    _put_csv(paths, "gsc_2026-06.csv",
        "Запрос,page,device,Клики,Показы,CTR,Позиция\n"
        "ренда авто,https://example.com/,DESKTOP,8,120,6.7%,3.5\n",
    )

    result = gsc_manual.extract(config, {}, paths)

    assert result["accepted"] == 1
    row = (paths.raw / "gsc" / "gsc_2026-06.csv").read_text("utf-8").splitlines()[1]
    parts = row.split(",")
    assert parts[1] == "ренда авто"   # query


def test_gsc_manual_no_exports_raises(paths):
    """Нет файлов gsc_YYYY-MM.csv -> SourceUnavailable (управляемая деградация)."""
    with pytest.raises(C.SourceUnavailable):
        gsc_manual.extract(CONFIG_MANUAL, {}, paths)


# ── Сценарий 2: расхождение суммы clicks с total_clicks_ui > 10% ──────────────

def test_gsc_manual_clicks_ui_mismatch_over_10pct_produces_caveat(paths):
    """Расхождение >10%: caveat попадает в result, validation_report и манифест."""
    # сумма clicks = 80 + 5 = 85; total_clicks_ui = 100 -> расхождение 15% > 10%.
    _put_csv(paths, "gsc_2026-06.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://example.com/cars,DESKTOP,80,1000,8%,3.1\n"
        "прокат авто,https://example.com/,MOBILE,5,80,6%,7.0\n",
        meta="total_clicks_ui: 100\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    caveats = result["clicks_ui_caveats"]
    assert len(caveats) == 1
    c = caveats[0]
    assert c["month"] == "2026-06"
    assert c["total_clicks_ui"] == 100
    assert c["sum_clicks"] == 85
    assert c["deviation_pct"] == 15.0
    assert "caveat" in c

    # Caveat попадает в validation_report.json.
    report = json.loads((paths.raw / "gsc" / "validation_report.json").read_text("utf-8"))
    assert report["clicks_ui_caveats"][0]["deviation_pct"] == 15.0

    # Caveat попадает в notes манифеста.
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert any("total_clicks_ui" in n for n in entry["notes"])


def test_gsc_manual_clicks_ui_within_tolerance_no_caveat(paths):
    """Расхождение ≤10% (ровно на границе) -> caveat НЕ появляется."""
    # сумма = 90; ui = 100 -> 10.0%, граница. deviation <= tolerance -> нет caveat.
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://example.com/,DESKTOP,90,500,18%,1.2\n",
        meta="total_clicks_ui: 100\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["clicks_ui_caveats"] == []


def test_gsc_manual_no_meta_yaml_no_caveat(paths):
    """Нет meta.yaml -> сравнение недоступно -> caveat НЕ появляется."""
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://example.com/,DESKTOP,10,100,10%,2.0\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["clicks_ui_caveats"] == []


# ── Сценарий 3: отсутствие колонки device ────────────────────────────────────

def test_gsc_manual_missing_device_sets_unknown_and_flags_month(paths):
    """Нет колонки device -> строки НЕ отбрасываются, device=unknown, месяц в device_missing_months."""
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,clicks,impressions,ctr,position\n"   # без device
        "аренда авто,https://example.com/cars,10,100,4%,3.1\n"
        "прокат машин,https://example.com/,5,80,6%,5.0\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    # Строки не потеряны.
    assert result["accepted"] == 2

    # device_missing_months содержит этот месяц.
    assert result["device_missing_months"] == ["2026-05"]

    # В выходном CSV device=unknown для всех строк.
    lines = (paths.raw / "gsc" / "gsc_2026-05.csv").read_text("utf-8").splitlines()
    for line in lines[1:]:
        parts = line.split(",")
        assert parts[3] == "unknown", f"ожидалось unknown, получено {parts[3]!r}"

    # Манифест: device_missing_months и notes.
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["device_missing_months"] == ["2026-05"]
    assert any("device" in n for n in entry["notes"])


def test_gsc_manual_device_present_not_flagged(paths):
    """Если device-колонка есть — месяц НЕ попадает в device_missing_months."""
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://example.com/,DESKTOP,10,100,10%,2.0\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["device_missing_months"] == []


def test_gsc_manual_missing_device_excluded_from_device_segment(paths):
    """Месяц без device попадает в device_missing_months -> ниже по пайплайну S20 исключает его."""
    # Два месяца: один без device, один с device.
    _put_csv(paths, "gsc_2026-04.csv",
        "query,page,clicks,impressions,ctr,position\n"   # без device
        "запрос без девайса,https://example.com/,7,70,10%,4.0\n",
    )
    _put_csv(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "запрос с девайсом,https://example.com/,MOBILE,3,30,10%,6.0\n",
    )

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["accepted"] == 2
    # Только апрель без device — исключается из разреза по устройству.
    assert result["device_missing_months"] == ["2026-04"]
    assert "2026-05" not in result["device_missing_months"]
