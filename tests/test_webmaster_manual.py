"""Тесты webmaster_manual — ручная выгрузка «Популярные запросы» Яндекс.Вебмастера.

Обязательные сценарии:
    1. Норма — валидный CSV -> агрегация за окно, выходной контракт search_queries_popular.json.
    2. Отсутствие page/device (policy=degrade) -> зафиксировано в манифесте и отчёте.
    3. Отсутствие page/device (policy=aggregate) -> policy_effect отражает политику.

Дополнительно:
    4. column_map: нестандартные заголовки прозрачно маппируются.
    5. Нет файлов -> SourceUnavailable.
    6. Пустой query -> строка отклонена, counted в rejected_reasons.

Сетевые вызовы не используются: webmaster_manual работает только с локальными файлами.
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
        }
    }
}


def _put_csv(paths: Paths, filename: str, text: str) -> None:
    """Положить ручную выгрузку в inputs/manual_exports/webmaster/."""
    d = paths.root / "inputs" / "manual_exports" / "webmaster"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(text, encoding="utf-8")


# ── Сценарий 1: норма ─────────────────────────────────────────────────────────

def test_webmaster_manual_norm_accepts_rows_and_writes_contract(paths):
    """Норма: два месяца, правильные CSV -> агрегация, контракт, манифест."""
    _put_csv(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,600,30,3.0,2026-05\n"
        "прокат машин,100,5,7.0,2026-05\n",
    )
    _put_csv(paths, "webmaster_2026-06.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,400,20,5.0,2026-06\n",
    )

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    # Базовые поля ответа.
    assert result["source"] == "webmaster"
    assert result["source_mode"] == "manual"
    assert result["completeness"] == "unverified"
    assert result["rows"] == 2           # два уникальных запроса
    assert result["months"] == ["2026-05", "2026-06"]
    assert result["canonical_tables"] == ["seo_queries"]

    # search_queries_popular.json лежит в data/raw/webmaster/.
    popular_path = paths.raw / "webmaster" / "search_queries_popular.json"
    assert popular_path.exists()
    popular = json.loads(popular_path.read_text("utf-8"))

    # «аренда авто» первый (больше показов), агрегирован за два месяца.
    top = popular[0]
    assert top["query_text"] == "аренда авто"
    assert top["indicators"]["TOTAL_SHOWS"] == 1000       # 600 + 400
    assert top["indicators"]["TOTAL_CLICKS"] == 50        # 30 + 20
    # Средневзвешенная позиция по показам: (3*600 + 5*400) / 1000 = 3.8
    assert abs(top["indicators"]["AVG_SHOW_POSITION"] - 3.8) < 1e-6

    # Манифест: source_mode, canonical_tables, ограничение метода.
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["source_mode"] == "manual"
    assert entry["canonical_tables"] == ["seo_queries"]
    assert entry["completeness"] == "unverified"


# ── Сценарий 2: отсутствие page/device, policy=degrade ────────────────────────

def test_webmaster_manual_no_page_device_policy_degrade(paths):
    """Нет page/device в CSV -> зафиксировано явно; policy=degrade в манифесте."""
    _put_csv(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,600,30,3.0,2026-05\n",
    )

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    # Результат явно фиксирует отсутствие колонок (не предположение).
    assert result["has_page_column"] is False
    assert result["has_device_column"] is False
    assert result["page_device_breakdown"] is False
    assert result["page_device_absence_reason"] == "method_limitation"
    assert result["manual_no_page_breakdown_policy"] == "degrade"

    # Манифест содержит те же поля.
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["has_page_column"] is False
    assert entry["has_device_column"] is False
    assert entry["page_device_breakdown"] is False
    assert entry["page_device_absence_reason"] == "method_limitation"
    assert entry["manual_no_page_breakdown_policy"] == "degrade"
    # Ограничение метода зафиксировано, а не только ручного экспорта.
    assert any("ограничение метода" in n for n in entry["notes"])

    # validation_report.json содержит те же поля.
    report = json.loads(
        (paths.raw / "webmaster" / "validation_report.json").read_text("utf-8")
    )
    assert report["has_page_column"] is False
    assert report["has_device_column"] is False
    assert report["page_device_breakdown"] is False
    assert report["page_device_absence_reason"] == "method_limitation"
    assert "degrade" in report["policy_effect"]


# ── Сценарий 3: отсутствие page/device, policy=aggregate ──────────────────────

def test_webmaster_manual_no_page_device_policy_aggregate(paths):
    """Нет page/device в CSV, policy=aggregate -> policy_effect отражает агрегацию."""
    _put_csv(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "прокат машин,300,15,5.5,2026-05\n",
    )
    cfg = {
        "sources": {
            "webmaster": {
                "enabled": True,
                "mode": "manual",
                "manual_export_dir": "inputs/manual_exports/webmaster",
                "manual_no_page_breakdown_policy": "aggregate",
            }
        }
    }

    result = webmaster_manual.extract(cfg, {}, paths)

    assert result["has_page_column"] is False
    assert result["has_device_column"] is False
    assert result["page_device_breakdown"] is False
    assert result["page_device_absence_reason"] == "method_limitation"
    assert result["manual_no_page_breakdown_policy"] == "aggregate"

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["manual_no_page_breakdown_policy"] == "aggregate"
    assert entry["page_device_absence_reason"] == "method_limitation"

    report = json.loads(
        (paths.raw / "webmaster" / "validation_report.json").read_text("utf-8")
    )
    assert report["manual_no_page_breakdown_policy"] == "aggregate"
    assert "aggregate" in report["policy_effect"]
    assert report["page_device_breakdown"] is False


# ── Дополнительные сценарии ────────────────────────────────────────────────────

def test_webmaster_manual_column_map_alias(paths):
    """column_map: нестандартные заголовки CSV прозрачно маппируются на канонические."""
    cfg = {
        "sources": {
            "webmaster": {
                "enabled": True,
                "mode": "manual",
                "manual_export_dir": "inputs/manual_exports/webmaster",
                "column_map": {
                    "query": "Запрос",
                    "impressions": "Показы",
                    "clicks": "Клики",
                    "position": "Позиция",
                },
            }
        }
    }
    _put_csv(paths, "webmaster_2026-06.csv",
        "Запрос,Показы,Клики,Позиция,month\n"
        "аренда авто,500,25,4.0,2026-06\n",
    )

    result = webmaster_manual.extract(cfg, {}, paths)

    assert result["accepted"] == 1
    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    assert popular[0]["query_text"] == "аренда авто"
    assert popular[0]["indicators"]["TOTAL_SHOWS"] == 500


def test_webmaster_manual_no_exports_raises(paths):
    """Нет файлов webmaster_YYYY-MM.csv -> SourceUnavailable (управляемая деградация)."""
    with pytest.raises(C.SourceUnavailable):
        webmaster_manual.extract(CONFIG_MANUAL, {}, paths)


def test_webmaster_manual_missing_query_rejected(paths):
    """Пустой query -> строка отклонена, попадает в rejected_reasons."""
    _put_csv(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,500,25,3.0,2026-05\n"
        ",200,10,6.0,2026-05\n",    # пустой query -> reject
    )

    result = webmaster_manual.extract(CONFIG_MANUAL, {}, paths)

    assert result["accepted"] == 1
    assert result["rejected"] == 1
    assert result["rejected_reasons"] == {"missing_query": 1}
