"""Общая инфраструктура слоя compute: загрузка входов, dispatch блоков,
единая валидация чисел/уверенности, атомарная запись артефактов.

Здесь НЕТ ни одной бизнес-проверки D/A/T/C/S — только каркас, которым
пользуются (и будут пользоваться) block0..block6. Контракт слоя — см.
src/compute/__init__.py и CLAUDE.md, раздел «Слои конвейера».

БЕЗ вызовов LLM (принцип 3).
"""

from __future__ import annotations

import csv
import hashlib
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


# ── evidence_id / evidence_label ────────────────────────────────────────────
# Идентификатор строки-доказательства НЕ зависит от её позиции в артефакте.
# Позиционный ID (`d08:0`) ломался дважды: между прогонами (DuckDB возвращает
# группы в произвольном порядке) и между периодами одного клиента (новая
# кампания сдвигала все последующие номера, и context_refs молча начинали
# указывать на другую сущность). Новый ID — короткий хеш от измерений строки.
EVIDENCE_ID_EXCLUDED_FIELDS: frozenset[str] = frozenset({
    # служебный контракт слоя analyze — не часть тождества строки
    "row_ref", "evidence_id", "evidence_label", "artifact",
    "candidate", "row_role", "candidate_reason", "context_refs",
    # производные от деградации, а не от самой строки
    "confidence", "confidence_cap",
    # отметки времени прогона (время прогона живёт только в manifest)
    "generated_at", "computed_at", "run_started_at",
})

_EVIDENCE_LABEL_PRIORITY: tuple[str, ...] = (
    "check_id", "finding", "status",
    "campaign_name", "campaign_id", "ad_group_id", "ad_id",
    "goal_name", "goal_id", "goal_group",
    "query", "phrase", "normalized_phrase", "match_type",
    "page", "entry_page", "href",
    "funnel_id", "first_stage", "last_stage",
    "segment_dimension", "segment_value", "segment",
    "source", "source_group", "source_tag", "channel", "device",
    "placement", "ad_network_type", "position_band", "month", "date",
)
_EVIDENCE_LABEL_MAX_FIELDS = 5
_EVIDENCE_LABEL_MAX_VALUE = 60


def _evidence_dimension_items(
    row: dict[str, Any], *, include_bools: bool
) -> list[list[Any]]:
    """Измерения строки: строковые (и опционально булевы) поля, кроме служебных.

    Числа сознательно не входят в тождество строки: это метрики, и они меняются
    от периода к периоду и от появления соседних строк (медианы, доли), тогда
    как ID обязан оставаться прежним у той же сущности.
    """
    items: list[list[Any]] = []
    for key in sorted(row):
        if key in EVIDENCE_ID_EXCLUDED_FIELDS:
            continue
        value = row[key]
        if isinstance(value, bool):
            if include_bools:
                items.append([key, value])
        elif isinstance(value, str):
            items.append([key, value])
    return items


def _evidence_hash(artifact: str, level: int, items: list[list[Any]], ordinal: int) -> str:
    payload = json.dumps(
        [artifact, level, items, ordinal], ensure_ascii=False, separators=(",", ":")
    )
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=5).hexdigest()
    return f"{artifact}:{digest}"


def evidence_label(row: dict[str, Any]) -> str:
    """Человекочитаемое описание строки для аналитика.

    Только для чтения глазами: ссылки (context_refs и любые перекрёстные)
    строятся ТОЛЬКО по evidence_id, никогда по label.
    """
    items = dict(_evidence_dimension_items(row, include_bools=False))
    ordered = [key for key in _EVIDENCE_LABEL_PRIORITY if key in items]
    ordered += [key for key in sorted(items) if key not in _EVIDENCE_LABEL_PRIORITY]
    parts = []
    for key in ordered[:_EVIDENCE_LABEL_MAX_FIELDS]:
        value = items[key]
        if len(value) > _EVIDENCE_LABEL_MAX_VALUE:
            value = value[: _EVIDENCE_LABEL_MAX_VALUE - 1] + "…"
        parts.append(f"{key}={value}" if key != "check_id" else value)
    return " · ".join(parts)


def assign_evidence_ids(artifact: str, rows: list[dict[str, Any]]) -> list[str]:
    """Вернуть evidence_id для каждой строки rows (в том же порядке).

    Три уровня, каждый следующий применяется только к строкам, которые
    предыдущий не различил:
      1) строковые измерения строки;
      2) + булевы измерения (``is_brand`` и подобные настоящие разрезы);
      3) + порядковый номер внутри группы неразличимых строк — последнее
         средство, когда строки совпадают по всем измерениям.
    """
    level1 = [_evidence_dimension_items(row, include_bools=False) for row in rows]
    groups: dict[str, list[int]] = {}
    for index, items in enumerate(level1):
        groups.setdefault(json.dumps(items, ensure_ascii=False), []).append(index)

    ids: list[str | None] = [None] * len(rows)
    for indexes in groups.values():
        if len(indexes) == 1:
            index = indexes[0]
            ids[index] = _evidence_hash(artifact, 1, level1[index], 0)
            continue
        subgroups: dict[str, list[int]] = {}
        for index in indexes:
            items = _evidence_dimension_items(rows[index], include_bools=True)
            subgroups.setdefault(json.dumps(items, ensure_ascii=False), []).append(index)
        for sub_indexes in subgroups.values():
            unique = len(sub_indexes) == 1
            for ordinal, index in enumerate(sub_indexes):
                items = _evidence_dimension_items(rows[index], include_bools=True)
                ids[index] = _evidence_hash(
                    artifact, 2 if unique else 3, items, 0 if unique else ordinal
                )
    return [str(evidence_id) for evidence_id in ids]


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


def canonical_float(value: float) -> float:
    """Обрезать float до 12 значащих цифр — снять шум порядка суммирования.

    DuckDB суммирует double параллельно, и порядок слагаемых между прогонами
    может отличаться последними битами мантиссы. 12 значащих цифр заведомо
    шире любого делового округления (блоки округляют до 2-4 знаков), поэтому
    ни одно опубликованное число не меняется, а побайтовое сравнение файлов
    перестаёт ловить нули в 16-м знаке.
    """
    if not math.isfinite(value):
        return value
    return float(f"{value:.12g}")


def canonicalize(data: Any) -> Any:
    """Рекурсивно применить canonical_float ко всем числам структуры."""
    if isinstance(data, bool):
        return data
    if isinstance(data, float):
        return canonical_float(data)
    if isinstance(data, dict):
        return {key: canonicalize(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [canonicalize(item) for item in data]
    return data


def write_json_atomic(path: Path, data: Any) -> Path:
    """Атомарно записать data как JSON (UTF-8, без ASCII-экранирования) в path."""
    path = Path(path)
    _atomic_write_text(path, json.dumps(canonicalize(data), ensure_ascii=False, indent=2))
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

    # Единый порядок ключей для json и csv: объединение колонок в порядке
    # появления. Порядок строк задан блоком (ORDER BY по измерениям), поэтому
    # он воспроизводим между прогонами.
    fieldnames = _csv_fieldnames(rows)
    rows = [
        canonicalize({key: row[key] for key in fieldnames if key in row})
        for row in rows
    ]

    write_json_atomic(json_path, rows)

    if rows:
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
# CHECKPOINT-full-pipeline-e2e (2026-07-30): "block4" был заглушкой старой
# нумерации (атрибуция 4.1/4.2, raise NotImplementedError) — реальный SEO-блок
# (S01-S27) реализован в block4_seo.py и никогда не вызывался диспетчером.
# S-проверки не считались ни в одном реальном прогоне. block5/block6 пока
# остаются NotImplementedError-заглушками (вне скоупа этого фикса).
BLOCK_MODULE_NAMES: tuple[str, ...] = (
    "block0", "block1", "block2", "funnels", "seasonality", "block3", "block4_seo", "block5", "block6",
    "cost_summary", "acquisition_economics", "money_frame",
    "candidates",
)


BLOCK_PACKAGE = "src.compute"


def _import_block(name: str, package: str = BLOCK_PACKAGE) -> Any:
    import importlib

    return importlib.import_module(f"{package}.{name}")


def _missing_module_name(exc: ImportError, block: str) -> str:
    """Имя ненайденного модуля из ImportError (fallback — текст/имя блока)."""
    return getattr(exc, "name", None) or str(exc) or block


def _import_named_blocks(
    block_names: Iterable[str],
    package: str = BLOCK_PACKAGE,
) -> tuple[list[tuple[str, Any]], dict[str, str]]:
    """Импортировать модули блоков; вернуть (named_modules, missing_dependencies).

    В try обёрнута ТОЛЬКО строка импорта и ловится ТОЛЬКО ImportError — блок,
    которому не хватает пакета из requirements.txt, уходит в деградацию с
    причиной "отсутствует зависимость: <модуль>", остальные блоки считаются
    (принцип 4). Любое другое исключение на уровне модуля (ошибка в коде
    блока, падение при инициализации) намеренно НЕ перехватывается и роняет
    прогон: иначе баг маскируется под деградацию источника.

    У модуля с отсутствующей зависимостью в named_modules стоит ``None`` —
    порядок блоков сохраняется, чтобы block_status читался в порядке реестра.
    """
    named_modules: list[tuple[str, Any]] = []
    missing: dict[str, str] = {}
    for name in block_names:
        try:
            module = _import_block(name, package)
        except ImportError as exc:
            missing[name] = _missing_module_name(exc, name)
            named_modules.append((name, None))
        else:
            named_modules.append((name, module))
    return named_modules, missing


def _remove_skipped_metric_artifacts(paths: Any, degradation_report: dict[str, Any]) -> None:
    """Удалить устаревшие парные metric-артефакты текущих skipped-проверок.

    Актуальная причина unavailable остаётся единой в degradation_report и
    metrics_summary; здесь не создаётся обобщённый ряд без причины проверки.
    """
    metrics_dir = Path(paths.metrics)
    for skipped in degradation_report.get("skipped") or []:
        check_id = skipped.get("id") if isinstance(skipped, dict) else None
        if not isinstance(check_id, str):
            continue
        for suffix in (".json", ".csv"):
            try:
                (metrics_dir / f"{check_id.lower()}{suffix}").unlink()
            except FileNotFoundError:
                pass


def dispatch_blocks(
    paths: Any,
    defaults: dict[str, Any],
    degradation_report: dict[str, Any],
    *,
    block_names: Iterable[str] = BLOCK_MODULE_NAMES,
    modules: Iterable[Any] | None = None,
    block_package: str = BLOCK_PACKAGE,
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

    То же на границе импорта: ImportError (нет пакета из requirements.txt)
    даёт статус "missing_dependency" и запись в ``missing_dependencies``
    результата; прочие исключения на уровне модуля роняют прогон намеренно
    (см. _import_named_blocks).

    ``modules`` — явный список объектов с методом ``run`` вместо импорта по
    block_names; используется тестами для проверки dispatch-логики без
    реальных (пока нереализованных) block0..block6.
    """
    runnable_ids = set(degradation_report.get("runnable_check_ids") or [])
    _remove_skipped_metric_artifacts(paths, degradation_report)

    missing_dependencies: dict[str, str] = {}
    if modules is None:
        named_modules, missing_dependencies = _import_named_blocks(
            block_names, block_package
        )
    else:
        named_modules = [(getattr(m, "__name__", str(i)), m) for i, m in enumerate(modules)]

    artifacts: list[str] = []
    block_status: dict[str, str] = {}
    block_errors: dict[str, str] = {}

    for name, module in named_modules:
        if module is None:
            from ..pipeline.degradation import missing_dependency_reason

            block_status[name] = "missing_dependency"
            block_errors[name] = missing_dependency_reason(missing_dependencies[name])
            continue
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
    if missing_dependencies:
        result["missing_dependencies"] = missing_dependencies
    return result


def _seo_confidence_cap_summary(degradation_report: dict[str, Any]) -> dict[str, Any]:
    """Агрегат по блоку 4 (SEO, id начинается с "S"): сколько runnable-проверок

    капнуты до confidence_cap=MED и какая это доля — не бизнес-число конкретной
    находки, а структурная сводка методологии по всему блоку (та же природа,
    что уже существующий degradation_report["counts"]), чтобы report мог
    показать это одной цифрой без обхода всех s*.json (задача 5bC, промт).
    """
    s_checks = [
        c for c in (degradation_report.get("checks") or [])
        if isinstance(c.get("check_id"), str) and c["check_id"].startswith("S") and c.get("runnable")
    ]
    total = len(s_checks)
    med_cap = sum(1 for c in s_checks if c.get("confidence_cap") == "MED")
    return {
        "runnable_count": total,
        "med_cap_count": med_cap,
        "med_cap_share": round(med_cap / total, 4) if total else None,
    }


# ── metrics_summary (без бизнес-чисел) ──────────────────────────────────────
def build_metrics_summary(
    degradation_report: dict[str, Any],
    dispatch_result: dict[str, Any],
) -> dict[str, Any]:
    """Собрать metrics_summary — только структурные факты о прогоне compute.

    НИ ОДНОГО бизнес-числа (сумм, ставок, метрик, долей находок) — они живут
    исключительно в артефактах конкретных проверок (data/metrics/<check>.csv
    /.json). Здесь — что выполнимо, что пропущено и почему, что вернул каждый
    блок, плюс сводка по confidence_cap блока 4 (SEO) — структурный факт о
    самом прогоне, не бизнес-метрика. Годится для лога/аудита прогона, не для
    отчёта клиенту.
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
        "seo_confidence_cap": _seo_confidence_cap_summary(degradation_report),
    }
    if dispatch_result.get("block_errors"):
        summary["block_errors"] = dispatch_result["block_errors"]
    if dispatch_result.get("missing_dependencies"):
        summary["missing_dependencies"] = dispatch_result["missing_dependencies"]
    return summary
