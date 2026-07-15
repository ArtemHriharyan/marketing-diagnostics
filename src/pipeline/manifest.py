"""Сбор и чтение data/raw/manifest.json.

Контракт:
    Читает   — содержимое каталога data/raw/<source>/ (какие источники выгружены)
               и, при обновлении, метаданные конкретной выгрузки.
    Пишет    — data/raw/manifest.json: по каждому источнику фиксирует окно дат,
               число строк, время выгрузки, версию скрипта и перечень
               канонических таблиц, которые из него строятся.

Манифест — единственный «указатель истины» о том, что реально выгружено. Слой
compute и карта деградации (src.pipeline.degradation) опираются на него, а не на
config.yaml клиента (в конфиге источник может быть заявлен, но фактически пуст).

LLM здесь не вызывается.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "manifest.json"


def manifest_path(raw_dir: Path) -> Path:
    """Путь к manifest.json внутри каталога data/raw/."""
    return Path(raw_dir) / MANIFEST_NAME


def load_manifest(raw_dir: Path) -> dict[str, Any]:
    """Прочитать манифест. Отсутствие файла -> пустой манифест (не ошибка)."""
    path = manifest_path(raw_dir)
    if not path.exists():
        return {"sources": {}, "input_tables": [], "generated_at": None}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def update_source(
    raw_dir: Path,
    source: str,
    *,
    date_from: str,
    date_to: str,
    rows: int,
    script_version: str,
    canonical_tables: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Обновить (идемпотентно) запись одного источника и перезаписать манифест.

    Повторная выгрузка того же источника перезаписывает свою запись целиком —
    данные не дублируются (принцип идемпотентности этапа extract).

    ``extra`` — дополнительные поля источника (например, ``cost_basis`` у Директа).
    Служебные ключи записи (``rows``, ``date_from`` и т.п.) перезаписать нельзя.
    ``fetched_at`` — синоним ``extracted_at``: время фактической выгрузки.
    """
    manifest = load_manifest(raw_dir)
    manifest.setdefault("sources", {})
    fetched_at = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "fetched_at": fetched_at,
        "extracted_at": fetched_at,
        "script_version": script_version,
        "canonical_tables": canonical_tables,
    }
    for key, value in (extra or {}).items():
        if key not in entry:
            entry[key] = value
    manifest["sources"][source] = entry
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()

    path = manifest_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest


def update_global(raw_dir: Path, **fields: Any) -> dict[str, Any]:
    """Записать глобальные поля верхнего уровня в manifest.json, не трогая sources.

    Используется intake для записи primary_window, compare_window,
    current_month_is_partial до старта extract.
    """
    manifest = load_manifest(raw_dir)
    manifest.setdefault("sources", {})
    for key, value in fields.items():
        if key != "sources":
            manifest[key] = value
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    path = manifest_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest
