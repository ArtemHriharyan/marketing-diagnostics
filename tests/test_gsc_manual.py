"""Тесты gsc_manual — ручная выгрузка Google Search Console.

Новый формат входных данных: папки YYYY-MM с отдельными CSV-файлами по срезам
(Запросы, Страницы, Диаграмма, Устройства, Страны, Фильтры).

Пять обязательных сценариев:
    1. Норма — все 5 файлов, seo_queries из Запросы.csv, доп. parquet.
    2. Нет Устройства.csv — seo_queries строится, gsc_devices не создаётся.
    3. Расхождение clicks >10% — caveat clicks_diagram_vs_queries_mismatch.
    4. Нет обязательного файла — месяц пропускается, другие месяцы обрабатываются.
    5. Два месяца — оба попадают в manifest.

Сетевые вызовы не используются: gsc_manual работает только с локальными файлами.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import gsc_manual  # noqa: E402
from src.extract import _common as C  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Фикстуры ──────────────────────────────────────────────────────────────────

class Paths:
    def __init__(self, raw: Path, root: Path | None = None) -> None:
        self.raw = raw
        self.root = root if root is not None else raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "data" / "raw", root=tmp_path)


# column_map с заголовками реального GSC-экспорта.
# «Kлики»: K — ASCII (U+004B), лики — кириллица. Именно так выгружает GSC.
_COLUMN_MAP = {
    "query":        "Популярные запросы",
    "page":         "Популярные страницы",
    "device":       "Устройство",
    "country":      "Страна",
    "date":         "Дата",
    "clicks":       "Kлики",
    "impressions":  "Показы",
    "ctr":          "CTR",
    "position":     "Позиция",
    "filter_key":   "Фильтр",
    "filter_value": "Значение",
}

CONFIG_MANUAL = {
    "sources": {
        "gsc": {
            "enabled": True,
            "mode": "manual",
            "raw_format": "csv",
            "manual_export_dir": "inputs/manual_exports/gsc",
            "column_map": _COLUMN_MAP,
        }
    },
}


def _gsc_dir(paths: Paths) -> Path:
    d = paths.root / "inputs" / "manual_exports" / "gsc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _month_dir(paths: Paths, month: str) -> Path:
    d = _gsc_dir(paths) / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


def _queries_csv(rows: str = "") -> str:
    header = "Популярные запросы,Kлики,Показы,CTR,Позиция\n"
    return header + (rows or "аренда авто,10,200,5%,3.5\nпрокат машин,5,100,5%,6.0\n")


def _queries_csv_combined(rows: str = "") -> str:
    """Запросы.csv комбинированного формата (contract 3A): query+page+device в одной строке."""
    header = "Популярные запросы,Популярные страницы,Устройство,Kлики,Показы,CTR,Позиция\n"
    return header + (rows or (
        "аренда авто,https://pognali.rent/avto,Мобильный,10,200,5%,3.5\n"
        "прокат машин,https://pognali.rent/prokat,ПК,5,100,5%,6.0\n"
    ))


def _diagram_csv(rows: str = "") -> str:
    header = "Дата,Kлики,Показы,CTR,Позиция\n"
    return header + (rows or "2025-04-01,8,150,5.3%,4.1\n2025-04-02,7,150,4.7%,4.2\n")


def _pages_csv() -> str:
    return (
        "Популярные страницы,Kлики,Показы,CTR,Позиция\n"
        "https://pognali.rent/,15,300,5%,4.0\n"
    )


def _devices_csv() -> str:
    return (
        "Устройство,Kлики,Показы,CTR,Позиция\n"
        "Мобильный,10,200,5%,4.1\n"
        "ПК,5,100,5%,4.5\n"
    )


def _filters_csv() -> str:
    return (
        "Фильтр,Значение\n"
        "Тип поиска,Веб\n"
        "Дата,1 апр. 2025 г.-30 апр. 2025 г.\n"
    )


# ── Сценарий 1: норма — все 5 файлов ─────────────────────────────────────────

def test_gsc_full_slice_all_files(paths):
    """Норма: папка с всеми файлами. seo_queries из Запросы, доп. срезы записаны."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv())
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Страницы.csv", _pages_csv())
    _write(d, "Устройства.csv", _devices_csv())
    _write(d, "Фильтры.csv", _filters_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["source"] == "gsc"
    assert result["source_mode"] == "manual"
    assert result["completeness"] == "unverified"
    assert result["months"] == ["2025-04"]
    assert result["accepted"] == 2
    assert result["rejected"] == 0
    assert result["canonical_tables"] == ["seo_queries"]

    # device=unknown, page="" в seo_queries
    seo = paths.raw / "gsc" / "gsc_2025-04.csv"
    assert seo.exists()
    lines = seo.read_text("utf-8").splitlines()
    assert lines[0] == "month,query,page,device,clicks,impressions,ctr,position"
    for data_line in lines[1:]:
        parts = data_line.split(",")
        assert parts[0] == "2025-04"    # month из имени папки
        assert parts[2] == ""            # page пустой
        assert parts[3] == "unknown"     # device=unknown

    # Дополнительные срезы записаны
    assert (paths.raw / "gsc" / "gsc_daily_2025-04.csv").exists()
    assert (paths.raw / "gsc" / "gsc_pages_2025-04.csv").exists()
    assert (paths.raw / "gsc" / "gsc_devices_2025-04.csv").exists()

    # device_missing_months содержит 2025-04 (device всегда unknown в этой схеме)
    assert "2025-04" in result["device_missing_months"]

    # manifest содержит available_slices_by_month
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    slices = entry["available_slices_by_month"]["2025-04"]
    assert set(slices) >= {"diagram", "queries", "pages", "devices", "filters"}


# ── Сценарий 2: нет Устройства.csv ───────────────────────────────────────────

def test_gsc_no_devices_file(paths):
    """Нет Устройства.csv: seo_queries строится, gsc_devices не создаётся."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv())
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Страницы.csv", _pages_csv())
    # Устройства.csv намеренно не кладём

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["accepted"] == 2
    assert result["months"] == ["2025-04"]

    # gsc_devices НЕ создаётся
    assert not (paths.raw / "gsc" / "gsc_devices_2025-04.csv").exists()

    # В manifest нет devices в срезах этого месяца
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    slices = entry["available_slices_by_month"]["2025-04"]
    assert "devices" not in slices
    assert "queries" in slices


# ── Сценарий 3: расхождение clicks >10% ──────────────────────────────────────

def test_gsc_clicks_mismatch_over_10pct(paths):
    """Диаграмма total=100, Запросы total=50 → 50% расхождение → caveat."""
    d = _month_dir(paths, "2025-04")
    # Диаграмма: 60+40=100 кликов
    _write(d, "Диаграмма.csv", _diagram_csv(
        "2025-04-01,60,300,20%,3.0\n"
        "2025-04-02,40,200,20%,3.1\n"
    ))
    # Запросы: 30+20=50 кликов
    _write(d, "Запросы.csv", _queries_csv(
        "аренда авто,30,200,15%,3.5\n"
        "прокат машин,20,100,20%,4.0\n"
    ))
    _write(d, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    mismatch = [c for c in result["caveats"]
                if c.get("type") == "clicks_diagram_vs_queries_mismatch"]
    assert len(mismatch) == 1
    c = mismatch[0]
    assert c["month"] == "2025-04"
    assert c["diagram_clicks"] == 100
    assert c["query_clicks"] == 50
    assert c["deviation_pct"] == 50.0

    # Caveat отражён в manifest notes
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert any("Диаграмма" in n or "расхождение" in n for n in entry["notes"])


# ── Сценарий 4: нет обязательного файла ──────────────────────────────────────

def test_gsc_missing_required_file_skips_month(paths):
    """Месяц без Запросы.csv пропускается; другой месяц обрабатывается нормально."""
    # 2025-04: нет Запросы.csv → пропускается
    d04 = _month_dir(paths, "2025-04")
    _write(d04, "Диаграмма.csv", _diagram_csv())
    _write(d04, "Страницы.csv", _pages_csv())
    # Запросы.csv не кладём

    # 2025-05: полный комплект → обрабатывается
    d05 = _month_dir(paths, "2025-05")
    _write(d05, "Запросы.csv", _queries_csv())
    _write(d05, "Диаграмма.csv", _diagram_csv())
    _write(d05, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    # extract() не падает
    assert result["months"] == ["2025-05"]
    assert "2025-04" not in result["months"]

    # Caveat missing_required_files для пропущенного месяца
    missing_caveats = [c for c in result["caveats"]
                       if c.get("type") == "missing_required_files"]
    assert len(missing_caveats) == 1
    assert missing_caveats[0]["month"] == "2025-04"
    assert "queries" in missing_caveats[0]["missing"]


# ── Contract 3A: комбинированный формат Запросы.csv ──────────────────────────

def test_gsc_combined_dimensions_parses_page_device_from_queries_csv(paths):
    """Комбинированный Запросы.csv (query+page+device в одной строке) — contract 3A
    выполнен полностью: page/device реальные, incomplete_dimensions=false."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv_combined())
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["incomplete_dimensions"] is False
    assert result["incomplete_dimensions_months"] == []
    assert "2025-04" not in result["device_missing_months"]
    assert result["combined_dimensions_by_month"]["2025-04"] is True
    assert not any(c.get("type") == "incomplete_dimensions" for c in result["caveats"])

    lines = (paths.raw / "gsc" / "gsc_2025-04.csv").read_text("utf-8").splitlines()
    header = lines[0].split(",")
    row = dict(zip(header, lines[1].split(",")))
    assert row["page"] == "https://pognali.rent/avto"
    assert row["device"] == "Мобильный"


def test_gsc_combined_dimensions_makes_pages_file_optional(paths):
    """Комбинированный формат делает отдельный Страницы.csv необязательным —
    он больше не нужен, потому что page уже приходит из Запросы.csv."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv_combined())
    _write(d, "Диаграмма.csv", _diagram_csv())
    # Страницы.csv намеренно не кладём

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["months"] == ["2025-04"]
    assert result["incomplete_dimensions"] is False
    assert not (paths.raw / "gsc" / "gsc_pages_2025-04.csv").exists()


def test_gsc_legacy_format_flags_incomplete_dimensions(paths):
    """Старый раздельный формат (только query в Запросы.csv) парсится без падения,
    но помечается caveat'ом и флагом incomplete_dimensions=true."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv())  # только query, без page/device
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["months"] == ["2025-04"]
    assert result["incomplete_dimensions"] is True
    assert result["incomplete_dimensions_months"] == ["2025-04"]
    assert result["combined_dimensions_by_month"]["2025-04"] is False

    incomplete_caveats = [c for c in result["caveats"] if c.get("type") == "incomplete_dimensions"]
    assert len(incomplete_caveats) == 1
    assert incomplete_caveats[0]["month"] == "2025-04"

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["incomplete_dimensions_months"] == ["2025-04"]
    assert entry["incomplete_dimensions"] is True
    assert any("incomplete_dimensions" in n for n in entry["notes"])


def test_gsc_legacy_format_still_requires_pages_file(paths):
    """Раздельный (legacy) формат без Страницы.csv по-прежнему пропускает месяц —
    требование pages ослаблено только для комбинированного Запросы.csv."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Запросы.csv", _queries_csv())
    _write(d, "Диаграмма.csv", _diagram_csv())
    # Страницы.csv не кладём

    with pytest.raises(C.SourceUnavailable):
        gsc_manual.extract(CONFIG_MANUAL, {}, paths)


# ── Сценарий 5: два месяца ────────────────────────────────────────────────────

def test_gsc_two_months(paths):
    """Два месяца: оба в manifest, два файла seo_queries."""
    for month in ("2025-04", "2025-05"):
        d = _month_dir(paths, month)
        _write(d, "Запросы.csv", _queries_csv())
        _write(d, "Диаграмма.csv", _diagram_csv())
        _write(d, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)

    assert sorted(result["months"]) == ["2025-04", "2025-05"]
    assert result["accepted"] == 4  # 2 запроса × 2 месяца

    # Оба выходных файла seo_queries существуют
    gsc_dir = paths.raw / "gsc"
    assert (gsc_dir / "gsc_2025-04.csv").exists()
    assert (gsc_dir / "gsc_2025-05.csv").exists()

    # manifest содержит оба месяца
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    slices = entry["available_slices_by_month"]
    assert "2025-04" in slices
    assert "2025-05" in slices

    # device_missing_months содержит оба месяца
    assert "2025-04" in result["device_missing_months"]
    assert "2025-05" in result["device_missing_months"]


# ── Дополнительные инварианты ─────────────────────────────────────────────────

def test_gsc_no_folders_raises_source_unavailable(paths):
    """Нет папок YYYY-MM вообще → SourceUnavailable."""
    with pytest.raises(C.SourceUnavailable):
        gsc_manual.extract(CONFIG_MANUAL, {}, paths)


def test_gsc_ping_true_when_valid_folder_exists(paths):
    """ping() возвращает True при наличии корректной папки."""
    d = _month_dir(paths, "2025-04")
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Запросы.csv", _queries_csv())

    # ping() не получает paths, поэтому передаём абсолютный путь в конфиге
    config_abs = {
        "sources": {
            "gsc": {
                "manual_export_dir": str(_gsc_dir(paths)),
                "column_map": _COLUMN_MAP,
            }
        }
    }
    assert gsc_manual.ping(config_abs, {}) is True


def test_gsc_ping_false_when_no_dir(paths):
    """ping() возвращает False если каталог не существует."""
    config_abs = {
        "sources": {
            "gsc": {
                "manual_export_dir": str(_gsc_dir(paths) / "nonexistent"),
                "column_map": _COLUMN_MAP,
            }
        }
    }
    assert gsc_manual.ping(config_abs, {}) is False


def test_gsc_clicks_header_ascii_k(paths):
    """«Kлики»: K — ASCII U+004B (не Cyrillic U+041A). column_map обрабатывает корректно."""
    clicks_header = _COLUMN_MAP["clicks"]
    # Реальный GSC-экспорт: первая буква — ASCII K (U+004B)
    assert ord(clicks_header[0]) == 0x004B, (
        f"ожидался U+004B (ASCII K), получен U+{ord(clicks_header[0]):04X}"
    )

    d = _month_dir(paths, "2025-06")
    _write(d, "Запросы.csv",
        f"Популярные запросы,{clicks_header},Показы,CTR,Позиция\n"
        "тест запрос,42,100,10%,2.0\n"
    )
    _write(d, "Диаграмма.csv", _diagram_csv())
    _write(d, "Страницы.csv", _pages_csv())

    result = gsc_manual.extract(CONFIG_MANUAL, {}, paths)
    assert result["accepted"] == 1

    line = (paths.raw / "gsc" / "gsc_2025-06.csv").read_text("utf-8").splitlines()[1]
    parts = line.split(",")
    assert parts[4] == "42"  # clicks
