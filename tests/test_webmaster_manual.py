"""Тесты webmaster_manual — ручная выгрузка «Популярные запросы» Яндекс.Вебмастера.

Wide-формат: одна CSV-таблица за весь период, месяцы — в колонках.
Одна строка = одна пара (Query × Url).

Сценарии:
    1. Норма — wide CSV 2q×2p×3months → 4 объекта; агрегация и контракт.
    2. Нет колонки demand → DEMAND=null, manifest.has_demand_column=false.
    3. Месяц с shows=0 → пропускается при агрегации.
    4. Файл не найден → SourceUnavailable.
    5. column_map: нестандартные заголовки прозрачно маппируются.
    6. Пустой query → строка отклонена, counted в rejected_reasons.
    7. Разделитель ';' и кодировка cp1251 — корректно парсятся.
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
from src.extract import webmaster_manual  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Фикстуры ──────────────────────────────────────────────────────────────────

class Paths:
    """Мини-дублёр ClientPaths: webmaster_manual нужны .raw и .root."""

    def __init__(self, raw: Path, root: Path | None = None) -> None:
        self.raw = raw
        self.root = root if root is not None else raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "data" / "raw", root=tmp_path)


CONFIG_MANUAL = {
    "sources": {
        "webmaster": {
            "enabled": True,
            "mode": "manual",
            "manual_export_dir": "inputs/manual_exports/webmaster",
            "manual_export_file": "webmaster_export.csv",
        }
    }
}

# Wide CSV: 2 запроса × 2 страницы × 3 месяца.
# (q1, /p1/): 2026-06 shows=0 → пропускается.
# (q1, /p2/): 2026-05 shows=0 → пропускается.
# (q2, /p2/): 2026-04 shows=0 → пропускается.
#
# Ожидаемые агрегаты:
# (q1, /p1/): shows=300(100+200), clicks=15, pos=(3*100+6*200)/300=5.0, demand=max(500,800)=800
# (q1, /p2/): shows=200(50+150), clicks=10, pos=(7*50+6*150)/200=6.25, demand=max(400,600)=600
# (q2, /p1/): shows=240(80+120+40), clicks=12, pos=(4*80+3*120+5*40)/240=880/240, demand=350
# (q2, /p2/): shows=150(60+90), clicks=7, pos=(8*60+7*90)/150=1110/150=7.4, demand=250
_WIDE_CSV = (
    "Query,Url,"
    "2026-04_shows,2026-04_clicks,2026-04_position,2026-04_ctr,2026-04_demand,"
    "2026-05_shows,2026-05_clicks,2026-05_position,2026-05_ctr,2026-05_demand,"
    "2026-06_shows,2026-06_clicks,2026-06_position,2026-06_ctr,2026-06_demand\n"
    "q1,/p1/,100,5,3.0,0.05,500,200,10,6.0,0.05,800,0,0,0.0,0.0,0\n"
    "q1,/p2/,50,2,7.0,0.04,400,0,0,0.0,0.0,0,150,8,6.0,0.053,600\n"
    "q2,/p1/,80,4,4.0,0.05,300,120,6,3.0,0.05,350,40,2,5.0,0.05,320\n"
    "q2,/p2/,0,0,0.0,0.0,0,60,3,8.0,0.05,200,90,4,7.0,0.044,250\n"
)


def _put_export(paths: Paths, text: str, filename: str = "webmaster_export.csv") -> None:
    """Положить ручную выгрузку в inputs/manual_exports/webmaster/."""
    d = paths.root / "inputs" / "manual_exports" / "webmaster"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(text, encoding="utf-8")


# ── Сценарий 1: нормальный wide-импорт ────────────────────────────────────────

def test_webmaster_manual_wide_norm_count(paths):
    """2q×2p×3months → 4 объекта; все имеют поле page."""
    _put_export(paths, _WIDE_CSV)
    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["source"] == "webmaster"
    assert result["source_mode"] == "manual"
    assert result["completeness"] == "unverified"
    assert result["months"] == ["2026-04", "2026-05", "2026-06"]

    popular_path = paths.raw / "webmaster" / "search_queries_popular.json"
    assert popular_path.exists()
    popular = json.loads(popular_path.read_text("utf-8"))

    assert len(popular) == 4
    assert all("page" in obj for obj in popular)


def test_webmaster_manual_wide_norm_aggregation_shows_clicks(paths):
    """Агрегация shows и clicks — сумма по месяцам (месяц с shows=0 пропущен)."""
    _put_export(paths, _WIDE_CSV)
    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    q1p1 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p1/")
    assert q1p1["indicators"]["TOTAL_SHOWS"] == 300   # 100+200 (2026-06 пропущен)
    assert q1p1["indicators"]["TOTAL_CLICKS"] == 15   # 5+10

    q1p2 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p2/")
    assert q1p2["indicators"]["TOTAL_SHOWS"] == 200   # 50+150 (2026-05 пропущен)
    assert q1p2["indicators"]["TOTAL_CLICKS"] == 10


def test_webmaster_manual_wide_norm_position_weighted(paths):
    """Позиция — средневзвешенная по shows."""
    _put_export(paths, _WIDE_CSV)
    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    q1p1 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p1/")
    # (3.0*100 + 6.0*200) / 300 = 1500/300 = 5.0
    assert abs(q1p1["indicators"]["AVG_SHOW_POSITION"] - 5.0) < 1e-4

    q2p1 = next(o for o in popular if o["query_text"] == "q2" and o["page"] == "/p1/")
    # (4*80 + 3*120 + 5*40) / 240 = 880/240
    assert abs(q2p1["indicators"]["AVG_SHOW_POSITION"] - 880 / 240) < 1e-4

    q2p2 = next(o for o in popular if o["query_text"] == "q2" and o["page"] == "/p2/")
    # (8*60 + 7*90) / 150 = 1110/150 = 7.4
    assert abs(q2p2["indicators"]["AVG_SHOW_POSITION"] - 7.4) < 1e-4


def test_webmaster_manual_wide_norm_ctr_recalculated(paths):
    """CTR пересчитывается как clicks/shows после агрегации, не усредняется из колонки."""
    _put_export(paths, _WIDE_CSV)
    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    q1p1 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p1/")
    # CTR = 15 / 300 = 0.05 (не среднее из колонки ctr)
    assert abs(q1p1["indicators"]["CTR"] - 0.05) < 1e-6

    q2p2 = next(o for o in popular if o["query_text"] == "q2" and o["page"] == "/p2/")
    # CTR = 7 / 150
    assert abs(q2p2["indicators"]["CTR"] - 7 / 150) < 1e-6


def test_webmaster_manual_wide_norm_demand_max(paths):
    """DEMAND — максимум по месяцам."""
    _put_export(paths, _WIDE_CSV)
    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    q1p1 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p1/")
    assert q1p1["indicators"]["DEMAND"] == 800   # max(500, 800)

    q1p2 = next(o for o in popular if o["query_text"] == "q1" and o["page"] == "/p2/")
    assert q1p2["indicators"]["DEMAND"] == 600   # max(400, 600)

    q2p1 = next(o for o in popular if o["query_text"] == "q2" and o["page"] == "/p1/")
    assert q2p1["indicators"]["DEMAND"] == 350   # max(300, 350, 320)


def test_webmaster_manual_wide_norm_manifest_flags(paths):
    """Манифест: has_page_column=true, page_device_breakdown=true, has_demand_column=true."""
    _put_export(paths, _WIDE_CSV)
    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["source_mode"] == "manual"
    assert entry["has_page_column"] is True
    assert entry["page_device_breakdown"] is True
    assert entry["has_demand_column"] is True
    assert entry["page_device_absence_reason"] is None
    assert entry["canonical_tables"] == ["seo_queries"]
    assert entry["completeness"] == "unverified"


# ── Сценарий 2: файл без колонки demand ──────────────────────────────────────

def test_webmaster_manual_no_demand_column(paths):
    """Нет колонок _demand → DEMAND=null у всех объектов, has_demand_column=false."""
    csv_no_demand = (
        "Query,Url,"
        "2026-05_shows,2026-05_clicks,2026-05_position,2026-05_ctr,"
        "2026-06_shows,2026-06_clicks,2026-06_position,2026-06_ctr\n"
        "аренда авто,/catalog/,600,30,3.0,0.05,400,20,5.0,0.05\n"
    )
    _put_export(paths, csv_no_demand)

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["has_demand_column"] is False

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    assert len(popular) == 1
    assert popular[0]["indicators"]["DEMAND"] is None

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["has_demand_column"] is False


# ── Сценарий 3: месяц с shows=0 пропускается ──────────────────────────────────

def test_webmaster_manual_zero_shows_month_skipped(paths):
    """Месяц с shows=0 не участвует в агрегации позиции и спроса."""
    csv_text = (
        "Query,Url,"
        "2026-04_shows,2026-04_clicks,2026-04_position,2026-04_ctr,2026-04_demand,"
        "2026-05_shows,2026-05_clicks,2026-05_position,2026-05_ctr,2026-05_demand\n"
        # 2026-04: shows=0 → пропускается; только 2026-05 учитывается
        "прокат авто,/rent/,0,0,0.0,0.0,9999,200,10,5.0,0.05,800\n"
    )
    _put_export(paths, csv_text)

    webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    assert len(popular) == 1
    assert popular[0]["indicators"]["TOTAL_SHOWS"] == 200   # только 2026-05
    assert popular[0]["indicators"]["TOTAL_CLICKS"] == 10
    assert abs(popular[0]["indicators"]["AVG_SHOW_POSITION"] - 5.0) < 1e-4
    # demand=9999 из 2026-04 (shows=0) не попадает; берётся 800 из 2026-05
    assert popular[0]["indicators"]["DEMAND"] == 800


# ── Сценарий 4: файл не найден ─────────────────────────────────────────────────

def test_webmaster_manual_no_exports_raises(paths):
    """Файл выгрузки не найден → SourceUnavailable (управляемая деградация)."""
    d = paths.root / "inputs" / "manual_exports" / "webmaster"
    d.mkdir(parents=True, exist_ok=True)
    with pytest.raises(C.SourceUnavailable):
        webmaster_manual.extract(CONFIG_MANUAL, {}, paths)


# ── Сценарий 5: column_map — нестандартные заголовки ─────────────────────────

def test_webmaster_manual_column_map_alias(paths):
    """column_map: нестандартные имена колонок Query/Url прозрачно маппируются."""
    cfg = {
        "sources": {
            "webmaster": {
                "enabled": True,
                "mode": "manual",
                "manual_export_dir": "inputs/manual_exports/webmaster",
                "manual_export_file": "webmaster_export.csv",
                "column_map": {
                    "query": "Запрос",
                    "page": "Страница",
                },
            }
        }
    }
    csv_text = (
        "Запрос,Страница,2026-06_shows,2026-06_clicks,2026-06_position,2026-06_ctr\n"
        "аренда авто,/catalog/,500,25,4.0,0.05\n"
    )
    _put_export(paths, csv_text)

    result = webmaster_manual.extract(cfg, {}, paths)

    assert result["accepted"] == 1
    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    assert popular[0]["query_text"] == "аренда авто"
    assert popular[0]["page"] == "/catalog/"
    assert popular[0]["indicators"]["TOTAL_SHOWS"] == 500


# ── Сценарий 6: пустой query → отклонён ────────────────────────────────────────

def test_webmaster_manual_missing_query_rejected(paths):
    """Пустой query → строка отклонена, попадает в rejected_reasons."""
    csv_text = (
        "Query,Url,2026-05_shows,2026-05_clicks,2026-05_position,2026-05_ctr\n"
        "аренда авто,/catalog/,500,25,3.0,0.05\n"
        ",/other/,200,10,6.0,0.05\n"   # пустой query → reject
    )
    _put_export(paths, csv_text)

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert result["rejected_reasons"] == {"missing_query": 1}


# ── Сценарий 7: кодировка cp1251 и разделитель ';' ────────────────────────────

def test_webmaster_manual_encoding_cp1251_and_semicolon_delimiter(paths):
    """Кодировка cp1251 и разделитель ';' корректно определяются и парсятся."""
    csv_text = (
        "Query;Url;2026-05_shows;2026-05_clicks;2026-05_position;2026-05_ctr\n"
        "аренда авто;/catalog/;300;15;4.0;0.05\n"
    )
    d = paths.root / "inputs" / "manual_exports" / "webmaster"
    d.mkdir(parents=True, exist_ok=True)
    (d / "webmaster_export.csv").write_bytes(csv_text.encode("cp1251"))

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["accepted"] == 1
    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    assert popular[0]["query_text"] == "аренда авто"
    assert popular[0]["page"] == "/catalog/"
    assert popular[0]["indicators"]["TOTAL_SHOWS"] == 300
