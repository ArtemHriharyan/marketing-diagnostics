"""Тесты слоя transform: src/transform/direct_normalize.py.

После 4X-direct-cleanup модуль содержит только filter_ad_texts_by_state —
build_direct_placements/build_direct_geo_monthly/write_ad_texts_archive
удалены как орфанный/устаревший дублирующий код (никогда не вызывались
реальным пайплайном, см. docs/implementation_status.md, задачи
4X-direct-reconcile и 4X-direct-cleanup). Фильтрация текстов объявлений по
State=="ACTIVE" реально подключена к build_canonical.build() через ленивый
импорт; запись canonical/ad_texts.json + ad_texts_archived.json делает сам
build() инлайн (не эта функция) — сквозная проверка того инлайн-кода тоже
здесь, отдельно от юнит-тестов чистой функции.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.transform import direct_normalize as dn  # noqa: E402


# ═════════════════════════════ ad_texts State filter ═══════════════════════
def _write_ad_texts(direct_dir: Path, ads: list[dict]) -> None:
    direct_dir.mkdir(parents=True, exist_ok=True)
    (direct_dir / "ad_texts.json").write_text(
        json.dumps({"ads": ads, "extensions": []}, ensure_ascii=False), encoding="utf-8"
    )


def test_filter_ad_texts_mixed_states(tmp_path):
    direct_dir = tmp_path / "direct"
    _write_ad_texts(direct_dir, [
        {"Id": 1, "CampaignId": 1, "State": "ACTIVE", "TextAd": {"Title": "A"}},
        {"Id": 2, "CampaignId": 1, "State": "ARCHIVED", "TextAd": {"Title": "B"}},
        {"Id": 3, "CampaignId": 1, "State": "SUSPENDED", "TextAd": {"Title": "C"}},
        {"Id": 4, "CampaignId": 1, "State": "active", "TextAd": {"Title": "D"}},  # регистр
    ])

    active, archived = dn.filter_ad_texts_by_state(direct_dir)

    assert {a["Id"] for a in active} == {1, 4}
    assert {a["Id"] for a in archived} == {2, 3}


def test_filter_ad_texts_missing_file_returns_empty_lists(tmp_path):
    active, archived = dn.filter_ad_texts_by_state(tmp_path / "direct")
    assert active == []
    assert archived == []


def test_filter_ad_texts_missing_state_goes_to_archived(tmp_path):
    """Объявление без поля State (не должно попасть в LLM-проверку по умолчанию)."""
    direct_dir = tmp_path / "direct"
    _write_ad_texts(direct_dir, [{"Id": 1, "CampaignId": 1}])
    active, archived = dn.filter_ad_texts_by_state(direct_dir)
    assert active == []
    assert len(archived) == 1


# ═══════ build(): инлайн-логика записи ad_texts в build_canonical.py ═══════
# Юнит-тесты выше покрывают filter_ad_texts_by_state как чистую функцию;
# следующий тест сквозной — гоняет реальный build_canonical.build() и
# проверяет то, что 4X-direct-cleanup сверил построчно (см.
# docs/implementation_status.md): raw ad_texts.json не открывается на запись
# и не удаляется, canonical/ad_texts.json содержит только ACTIVE, а
# canonical/ad_texts_archived.json — всё остальное, включая записи без State.
class _Paths:
    def __init__(self, root: Path):
        self.root = root
        self.raw = root / "data" / "raw"
        self.canonical = root / "data" / "canonical"


def test_build_ad_texts_inline_logic_keeps_raw_intact_and_splits_correctly(tmp_path):
    from src.pipeline import manifest as manifest_mod
    from src.transform import build_canonical as bc

    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    direct_dir = paths.raw / "direct"
    direct_dir.mkdir(parents=True, exist_ok=True)

    raw_ads = [
        {"Id": 1, "CampaignId": 1, "State": "ACTIVE", "TextAd": {"Title": "A"}},
        {"Id": 2, "CampaignId": 1, "State": "ARCHIVED", "TextAd": {"Title": "B"}},
        {"Id": 3, "CampaignId": 1, "TextAd": {"Title": "C"}},  # без State -> архив
    ]
    _write_ad_texts(direct_dir, raw_ads)
    raw_path = direct_dir / "ad_texts.json"
    raw_bytes_before = raw_path.read_bytes()
    raw_mtime_before = raw_path.stat().st_mtime_ns

    manifest_mod.update_source(
        paths.raw, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=0, script_version="test", canonical_tables=["costs", "direct_queries"],
    )
    config = {"data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}}
    defaults = {"utm_undefined_threshold": 0.25}

    bc.build(paths, config, defaults)

    # raw ad_texts.json не изменён и не удалён — ни байты, ни mtime не тронуты.
    assert raw_path.exists()
    assert raw_path.read_bytes() == raw_bytes_before
    assert raw_path.stat().st_mtime_ns == raw_mtime_before

    active_payload = json.loads((paths.canonical / "ad_texts.json").read_text(encoding="utf-8"))
    archived_payload = json.loads(
        (paths.canonical / "ad_texts_archived.json").read_text(encoding="utf-8")
    )
    assert {a["Id"] for a in active_payload["ads"]} == {1}
    assert {a["Id"] for a in archived_payload["ads"]} == {2, 3}

    canonical_manifest = json.loads((paths.canonical / "manifest.json").read_text("utf-8"))
    assert canonical_manifest["flags"]["ad_texts"] == {"active_count": 1, "archived_count": 2}


def test_build_no_ad_texts_source_writes_no_ad_texts_files(tmp_path):
    """Без raw ad_texts.json — canonical ad_texts.json/ad_texts_archived.json не создаются."""
    from src.pipeline import manifest as manifest_mod
    from src.transform import build_canonical as bc

    paths = _Paths(tmp_path)
    paths.raw.mkdir(parents=True, exist_ok=True)
    direct_dir = paths.raw / "direct"
    queries_dir = direct_dir / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    (queries_dir / "2026-06.tsv").write_text(
        "Date\tCampaignId\tCampaignName\tAdGroupId\tQuery\tMatchType\tDevice\t"
        "Cost\tClicks\tImpressions\tConversions\n"
        "2026-06-01\t1\tПоиск\t11\tкупить машину\tbroad\tDESKTOP\t2000000\t3\t50\t1\n",
        encoding="utf-8",
    )
    manifest_mod.update_source(
        paths.raw, "direct", date_from="2026-06-01", date_to="2026-06-30",
        rows=1, script_version="test", canonical_tables=["direct_queries"],
    )

    bc.build(paths, {"data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}},
              {"utm_undefined_threshold": 0.25})

    assert not (paths.canonical / "ad_texts.json").exists()
    assert not (paths.canonical / "ad_texts_archived.json").exists()
