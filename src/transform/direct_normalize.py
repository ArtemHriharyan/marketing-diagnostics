"""Фильтр текстов объявлений Директа по State (ad_texts.json).

Контракт:
    Читает — data/raw/direct/ad_texts.json.
    Пишет  — ничего (чистая функция; запись canonical/ad_texts.json и
             canonical/ad_texts_archived.json делает build_canonical.build()
             инлайн — см. её докстринг там и сверку в
             docs/implementation_status.md, задача 4X-direct-cleanup).
    LLM    — не используется.

История (см. docs/implementation_status.md по порядку задач):
    Этот модуль раньше содержал ещё build_direct_placements и
    build_direct_geo_monthly — они были орфанным дублированием: реальный
    пайплайн (run.py --stage transform -> orchestrator.run_transform ->
    build_canonical.build()) всегда вызывал собственные копии этих функций
    внутри build_canonical.py, а копии здесь никогда никем не вызывались
    (подтверждено разведкой 4X-direct-reconcile). Кроме того, копия
    build_direct_geo_monthly здесь разошлась с рабочей версией — сохраняла
    старую коллизию имён (cost_normalized как валютная конверсия вместо
    cost_rub), что делало её опасным "образцом" для будущих правок. Обе
    удалены задачей 4X-direct-cleanup вместе с write_ad_texts_archive (тоже
    не вызывалась нигде — build_canonical.build() пишет ad_texts_archived.json
    инлайн). Единственная функция, которую build_canonical.build() реально
    вызывает из этого файла (через ленивый импорт, чтобы не создавать
    циклический импорт с build_canonical, который сам импортирует этот
    модуль) — filter_ad_texts_by_state, она и остаётся здесь.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def filter_ad_texts_by_state(direct_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ad_texts.json -> (active_ads, archived_ads) по полю State.

    Только State=="ACTIVE" идёт в LLM-проверку текстов (A20-A24). Остальные
    состояния (ARCHIVED, SUSPENDED, MODERATION и т.п., а также отсутствие
    поля State вовсе) не удаляются — они возвращаются отдельным списком для
    сохранения (см. build_canonical.build(), которая пишет оба списка в
    canonical/ad_texts.json и canonical/ad_texts_archived.json).
    """
    path = Path(direct_dir) / "ad_texts.json"
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    ads = payload.get("ads") or []
    active = [a for a in ads if (a.get("State") or "").strip().upper() == "ACTIVE"]
    archived = [a for a in ads if (a.get("State") or "").strip().upper() != "ACTIVE"]
    return active, archived
