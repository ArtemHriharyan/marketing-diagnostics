"""Общая инфраструктура слоя compute: загрузка входов, dispatch блоков,
единая валидация чисел/уверенности, атомарная запись артефактов.

Здесь НЕТ ни одной бизнес-проверки D/A/T/C/S — только каркас, которым
пользуются (и будут пользоваться) block0..block6. Контракт слоя — см.
src/compute/__init__.py и CLAUDE.md, раздел «Слои конвейера».

БЕЗ вызовов LLM (принцип 3).
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import duckdb
import yaml

from ..pipeline import degradation as degradation_mod


# ── Загрузка входов ─────────────────────────────────────────────────────────
def load_canonical(paths: Any) -> dict[str, Path]:
    """Вернуть {имя_таблицы: путь_к_parquet} для всех data/canonical/*.parquet.

    Имя таблицы — имя файла без расширения (совпадает с именами канонических
    таблиц из CLAUDE.md/methodology.yaml: visits, costs, seo_queries, ...).
    Отсутствующий каталог -> пустой словарь (принцип 4: источник опционален).
    """
    canonical_dir = Path(paths.canonical)
    if not canonical_dir.exists():
        return {}
    return {p.stem: p for p in sorted(canonical_dir.glob("*.parquet"))}


def _sql_quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_quote_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def open_duckdb(paths: Any) -> "duckdb.DuckDBPyConnection":
    """Открыть in-memory DuckDB-соединение с view на каждую каноническую таблицу.

    Без сервера (принцип 5): view читает напрямую parquet-файл на диске, имя
    view = имя канонической таблицы (см. load_canonical). ``CREATE VIEW`` не
    поддерживает подготовленные параметры в DuckDB, поэтому путь подставляется
    как экранированный SQL-литерал, а не через bind-параметр. Вызывающий код
    сам закрывает соединение (``con.close()``) или полагается на сборку мусора.
    """
    con = duckdb.connect(database=":memory:")
    for table, path in load_canonical(paths).items():
        view = _sql_quote_identifier(table)
        file_literal = _sql_quote_literal(str(path))
        con.execute(f"CREATE VIEW {view} AS SELECT * FROM read_parquet({file_literal})")
    return con


def load_inputs(paths: Any) -> dict[str, Any]:
    """Загрузить все inputs/*.yaml клиента как {имя_файла_без_расширения: данные}.

    Отсутствующий каталог/файл -> соответствующий ключ просто не появится
    (принцип 4). Разобрать содержимое конкретных файлов (client_answers,
    webvisor_findings, ...) — забота вызывающего блока.
    """
    inputs_dir = Path(paths.inputs)
    result: dict[str, Any] = {}
    if not inputs_dir.exists():
        return result
    for p in sorted(inputs_dir.glob("*.yaml")):
        with p.open("r", encoding="utf-8") as fh:
            result[p.stem] = yaml.safe_load(fh) or {}
    return result


def load_degradation(paths: Any) -> dict[str, Any]:
    """Прочитать data/metrics/degradation_report.json.

    Ожидается, что к моменту вызова run_compute уже записал этот файл в
    текущем прогоне (см. orchestrator.run_compute). Отсутствие файла -> пустой
    отчёт (нет runnable-проверок, нет skipped) — не ошибка.
    """
    path = Path(paths.metrics) / "degradation_report.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ── Единый валидатор выходного числа ────────────────────────────────────────
def validate_metric_value(value: Any, *, allow_none: bool = True) -> None:
    """Проверить, что значение метрики годится для записи в data/metrics/.

    Числовые значения (int/float, кроме bool) обязаны быть конечными — не NaN,
    не +-inf. Остальные скалярные/составные JSON-типы (str, bool, list, dict,
    None) пропускаются как есть. Бросает ValueError на невалидном значении —
    вызывающий блок обязан поймать ошибку до записи артефакта.
    """
    if value is None:
        if allow_none:
            return
        raise ValueError("значение метрики не может быть None")
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"значение метрики не конечно: {value!r}")
        return
    if isinstance(value, (str, list, dict)):
        return
    raise ValueError(f"неподдерживаемый тип значения метрики: {type(value)!r}")


def validate_row(row: dict[str, Any]) -> None:
    """Валидировать все значения строки метрики (см. validate_metric_value)."""
    for key, value in row.items():
        try:
            validate_metric_value(value)
        except ValueError as exc:
            raise ValueError(f"поле {key!r}: {exc}") from exc


# ── Запрет confidence > confidence_cap ──────────────────────────────────────
class ConfidenceCapViolation(ValueError):
    """Уровень уверенности превышает confidence_cap проверки (запрещено)."""


def assert_confidence_within_cap(confidence: str, confidence_cap: str) -> None:
    """Бросить ConfidenceCapViolation, если confidence строго выше confidence_cap.

    Использует тот же порядок HIGH > MED > LOW, что и карта деградации
    (src.pipeline.degradation.min_confidence) — единственный источник истины
    для сравнения уровней уверенности. compute может только капать уверенность
    вниз, никогда не поднимать её выше потолка проверки.
    """
    if degradation_mod.min_confidence(confidence, confidence_cap) != confidence:
        raise ConfidenceCapViolation(
            f"confidence={confidence!r} превышает confidence_cap={confidence_cap!r}"
        )


# ── Атомарная запись csv/json ───────────────────────────────────────────────
def _atomic_write_text(path: Path, text: str) -> None:
    """Записать текст в path атомарно: временный файл в той же папке + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any) -> Path:
    """Атомарно записать data как JSON (UTF-8, без ASCII-экранирования) в path."""
    path = Path(path)
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    """Собрать имена колонок как объединение ключей всех строк (порядок появления)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen_set:
                seen_set.add(key)
                seen.append(key)
    return seen


def write_metric_artifact(
    metrics_dir: Path,
    name: str,
    rows: list[dict[str, Any]],
    *,
    confidence_cap: str | None = None,
) -> tuple[Path, Path]:
    """Атомарно записать rows как <name>.csv и <name>.json в data/metrics/.

    Перед записью: (1) validate_row на каждую строку — запрет NaN/inf и прочих
    невалидных значений; (2) если строка несёт поле "confidence" и передан
    confidence_cap — assert_confidence_within_cap. Валидация всех строк идёт
    ДО записи файлов — на невалидном входе не остаётся частично записанных
    артефактов. Возвращает (csv_path, json_path).
    """
    for row in rows:
        validate_row(row)
        if confidence_cap is not None and "confidence" in row:
            assert_confidence_within_cap(row["confidence"], confidence_cap)

    metrics_dir = Path(metrics_dir)
    json_path = metrics_dir / f"{name}.json"
    csv_path = metrics_dir / f"{name}.csv"

    write_json_atomic(json_path, rows)

    if rows:
        fieldnames = _csv_fieldnames(rows)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)
        csv_text = buf.getvalue()
    else:
        csv_text = ""
    _atomic_write_text(csv_path, csv_text)

    return csv_path, json_path


# ── Dispatch блоков по runnable ─────────────────────────────────────────────
BLOCK_MODULE_NAMES: tuple[str, ...] = (
    "block0", "block1", "block2", "block3", "block4", "block5", "block6",
)


def _import_block(name: str) -> Any:
    import importlib

    return importlib.import_module(f"src.compute.{name}")


def dispatch_blocks(
    paths: Any,
    defaults: dict[str, Any],
    degradation_report: dict[str, Any],
    *,
    block_names: Iterable[str] = BLOCK_MODULE_NAMES,
    modules: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Вызвать run(paths, defaults, runnable_ids) каждого блока compute.

    runnable_ids берётся из degradation_report["runnable_check_ids"] — единый
    источник истины о том, какие проверки выполнимы при наличных данных (см.
    src.pipeline.degradation). Каждый блок сам решает, какие из runnable_ids
    относятся к нему (см. docstring блока) и возвращает список имён созданных
    артефактов.

    Блок, ещё не реализованный (raise NotImplementedError — текущее состояние
    всех block0..block6 до реализации бизнес-проверок), пропускается без
    остановки остальных блоков (принцип 4). Любая другая ошибка блока также не
    должна ронять весь compute — соседние блоки обязаны отработать.

    ``modules`` — явный список объектов с методом ``run`` вместо импорта по
    block_names; используется тестами для проверки dispatch-логики без
    реальных (пока нереализованных) block0..block6.
    """
    runnable_ids = set(degradation_report.get("runnable_check_ids") or [])

    if modules is None:
        named_modules = [(name, _import_block(name)) for name in block_names]
    else:
        named_modules = [(getattr(m, "__name__", str(i)), m) for i, m in enumerate(modules)]

    artifacts: list[str] = []
    block_status: dict[str, str] = {}
    block_errors: dict[str, str] = {}

    for name, module in named_modules:
        try:
            produced = module.run(paths, defaults, runnable_ids)
            artifacts.extend(produced or [])
            block_status[name] = "ok"
        except NotImplementedError:
            block_status[name] = "not_implemented"
        except Exception as exc:  # noqa: BLE001 — принцип 4: соседние блоки не должны падать
            block_status[name] = "error"
            block_errors[name] = f"{type(exc).__name__}: {exc}"

    result: dict[str, Any] = {
        "runnable_ids": sorted(runnable_ids),
        "artifacts": artifacts,
        "block_status": block_status,
    }
    if block_errors:
        result["block_errors"] = block_errors
    return result


# ── metrics_summary (без бизнес-чисел) ──────────────────────────────────────
def build_metrics_summary(
    degradation_report: dict[str, Any],
    dispatch_result: dict[str, Any],
) -> dict[str, Any]:
    """Собрать metrics_summary — только структурные факты о прогоне compute.

    НИ ОДНОГО бизнес-числа (сумм, ставок, метрик, долей) — они живут
    исключительно в артефактах конкретных проверок (data/metrics/<check>.csv
    /.json). Здесь — что выполнимо, что пропущено и почему, что вернул каждый
    блок. Годится для лога/аудита прогона, не для отчёта клиенту.
    """
    skipped = [
        {"id": s.get("id"), "block": s.get("block"), "reason": s.get("reason")}
        for s in (degradation_report.get("skipped") or [])
    ]
    summary: dict[str, Any] = {
        "counts": degradation_report.get("counts") or {},
        "skipped": skipped,
        "block_status": dispatch_result.get("block_status") or {},
        "artifacts": sorted(dispatch_result.get("artifacts") or []),
    }
    if dispatch_result.get("block_errors"):
        summary["block_errors"] = dispatch_result["block_errors"]
    return summary
