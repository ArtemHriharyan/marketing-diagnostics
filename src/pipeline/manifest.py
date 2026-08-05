"""Сбор и чтение data/raw/manifest.json и data/metrics/manifest.json.

Контракт:
    Читает   — содержимое каталога data/raw/<source>/ (какие источники выгружены)
               и, при обновлении, метаданные конкретной выгрузки.
    Пишет    — data/raw/manifest.json: по каждому источнику фиксирует окно дат,
               число строк, время выгрузки, версию скрипта и перечень
               канонических таблиц, которые из него строятся.
             — data/metrics/manifest.json: реестр входов слоя metrics, по
               каждому артефакту — run_id прогона, который его записал
               (STATE-1, см. ниже).

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
    previous_entry = manifest["sources"].get(source) or {}
    for key, value in (extra or {}).items():
        if key not in entry:
            entry[key] = value
    # Temporal provenance описывает контракт уже выгруженных строк, а не момент
    # записи manifest. Не теряем его при служебном обновлении той же записи
    # (например, при добавлении lookback-статистики), если новый extract его не
    # передал явно.
    if "temporal_provenance" not in entry and "temporal_provenance" in previous_entry:
        entry["temporal_provenance"] = previous_entry["temporal_provenance"]
    manifest["sources"][source] = entry
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()

    path = manifest_path(raw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest


# ── Реестр входов слоя metrics (STATE-1) ────────────────────────────────────
# Прогон compute помечается run_id; каждый артефакт, записанный в этом прогоне,
# регистрируется здесь вместе с run_id, который его записал. Реестр НЕ
# обнуляется в начале прогона: запись предыдущего прогона обязана пережить
# текущий, иначе артефакт, который сегодня никто не переписал, невозможно
# отличить (stale) от файла, который в слое metrics вообще не должен лежать
# (unregistered). Потребитель реестра — src.compute.candidates: он строит
# список входов отсюда, а не глобом по каталогу.
#
# Файл лежит в data/metrics/ и намеренно исключён из побайтовой сверки
# прогонов (tests/test_compute_determinism._metric_files): он несёт время
# записи, то есть заведомо не воспроизводим — в отличие от самих артефактов.


def metrics_manifest_path(metrics_dir: Path) -> Path:
    """Путь к manifest.json внутри каталога data/metrics/."""
    return Path(metrics_dir) / MANIFEST_NAME


def load_metrics_manifest(metrics_dir: Path) -> dict[str, Any]:
    """Прочитать реестр входов metrics. Отсутствие/порча -> пустой реестр.

    Порча файла не роняет прогон (принцип 4): пустой реестр означает, что все
    найденные в каталоге артефакты окажутся ``unregistered`` и не попадут в
    кандидаты — состояние заметное, а не молчаливое.
    """
    path = metrics_manifest_path(metrics_dir)
    if not path.exists():
        return {"run_id": None, "generated_at": None, "artifacts": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = None
    if not isinstance(loaded, dict):
        return {"run_id": None, "generated_at": None, "artifacts": {}}
    artifacts = loaded.get("artifacts")
    loaded["artifacts"] = artifacts if isinstance(artifacts, dict) else {}
    return loaded


def _write_metrics_manifest(metrics_dir: Path, manifest: dict[str, Any]) -> Path:
    path = metrics_manifest_path(metrics_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def start_metrics_run(metrics_dir: Path, run_id: str) -> dict[str, Any]:
    """Открыть прогон: записать его run_id, сохранив регистрации прошлых прогонов."""
    manifest = load_metrics_manifest(metrics_dir)
    manifest["run_id"] = run_id
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    _write_metrics_manifest(metrics_dir, manifest)
    return manifest


def register_metrics_artifact(
    metrics_dir: Path, stem: str, run_id: str
) -> dict[str, Any]:
    """Зарегистрировать артефакт ``stem`` как записанный прогоном ``run_id``.

    Идемпотентно: повторная запись того же артефакта в том же прогоне только
    обновляет отметку времени.
    """
    manifest = load_metrics_manifest(metrics_dir)
    manifest["artifacts"][stem] = {
        "run_id": run_id,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_metrics_manifest(metrics_dir, manifest)
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
