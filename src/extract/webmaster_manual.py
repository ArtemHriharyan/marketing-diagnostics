"""Экстрактор: РУЧНАЯ выгрузка Яндекс.Вебмастера (без API).

Активен, когда config.sources.webmaster.mode == "manual" (сейчас всегда —
API-доступа к Вебмастеру у клиента нет). НЕ вызывает никакой API: читает,
валидирует и нормализует CSV-экспорт «Популярные запросы», который аналитик
кладёт в inputs/manual_exports/webmaster/.

Контракт:
    Читает   — config.sources.webmaster (manual_export_dir, column_map,
               manual_no_page_breakdown_policy) и файлы webmaster_YYYY-MM.csv.
    Пишет    — data/raw/webmaster/search_queries_popular.json — ТОТ ЖЕ выходной
               контракт, что у webmaster_api.py (список запросов с indicators
               TOTAL_SHOWS/TOTAL_CLICKS/AVG_SHOW_POSITION), поэтому
               transform.build_seo_queries_webmaster работает без правок. Плюс
               data/raw/webmaster/validation_report.json и manifest.json
               (canonical_tables: [seo_queries]).
    Деградация — опционален; нет ручных выгрузок -> источник недоступен (принцип 4).
    LLM      — не используется.

Полнота НЕ верифицируется (нет контроля пагинации, как в API) -> в manifest
всегда completeness: "unverified", source_mode: "manual".

СТРУКТУРНОЕ ОГРАНИЧЕНИЕ (подтверждено, не предположение):
    Отчёт «Популярные запросы» отдаёт данные ТОЛЬКО на уровне запроса
    (query x показы/клики/позиция) БЕЗ разбивки по page/device. Это ограничение
    самого метода, а не только ручного экспорта: API v4 Вебмастера
    (search-queries/popular, см. webmaster_api.QUERY_INDICATORS) отдаёт ровно те
    же четыре индикатора без измерений page/device. Поэтому ограничение помечено
    как свойство метода в обоих режимах, а не как дефект ручной выгрузки.

    Что делать с проверками, которым нужен разрез по page (S08–S10, S23, S24),
    решает config.sources.webmaster.manual_no_page_breakdown_policy, а НЕ скрипт:
        "degrade"   — эти проверки уходят в degradation (нет разбивки по page);
        "aggregate" — считаются агрегатом по домену с понижением confidence_cap.
    Дефолт в _template — "degrade".

Вход по месяцам:
    webmaster_YYYY-MM.csv — колонки (алиасы в config.sources.webmaster.column_map):
    query, impressions, clicks, position, month. Экспорт помесячный; для
    seo_queries значения агрегируются по запросу за всё окно (как это делает и
    transform для стороны Вебмастера).
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.1.0"
SOURCE = "webmaster"
CANONICAL_TABLES = ["seo_queries"]

# Допустимые значения политики разреза по page (см. докстринг).
POLICIES = ("degrade", "aggregate")
DEFAULT_POLICY = "degrade"

# Имя файла помесячной выгрузки: webmaster_YYYY-MM.csv.
_MONTH_RE = re.compile(r"webmaster_(\d{4}-\d{2})\.csv$", re.IGNORECASE)


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка: есть ли хотя бы один файл ручной выгрузки Вебмастера."""
    wm = (config.get("sources") or {}).get("webmaster") or {}
    manual_dir = _manual_dir(wm, _paths_root=None)
    if manual_dir is None or not manual_dir.exists():
        return False
    return any(_MONTH_RE.search(p.name) for p in manual_dir.glob("webmaster_*.csv"))


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Провалидировать и нормализовать ручные выгрузки Вебмастера в data/raw/webmaster/."""
    log = log or (lambda _msg: None)

    wm = (config.get("sources") or {}).get("webmaster") or {}
    column_map = wm.get("column_map") or {}
    policy = _resolve_policy(wm.get("manual_no_page_breakdown_policy"))

    manual_dir = _manual_dir(wm, _paths_root=getattr(paths, "root", None))
    if manual_dir is None or not manual_dir.exists():
        raise C.SourceUnavailable(
            SOURCE, f"нет каталога ручных выгрузок Вебмастера: {manual_dir}"
        )
    month_files = _month_files(manual_dir)
    if not month_files:
        raise C.SourceUnavailable(
            SOURCE, f"в {manual_dir} нет файлов webmaster_YYYY-MM.csv"
        )

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    log(f"{SOURCE}[manual]: каталог {manual_dir}, файлов {len(month_files)}, "
        f"политика page-разреза '{policy}'")

    total_rows = 0
    rejected_reasons: dict[str, int] = {}
    warnings: dict[str, int] = {}
    months: list[str] = []
    has_page_overall = False
    has_device_overall = False
    # Агрегат по запросу за всё окно: query -> [shows, clicks, pos*shows, pos_sum, n].
    agg: dict[str, list[float]] = {}

    for month, csv_path in month_files:
        months.append(month)
        rows = _read_export(csv_path)
        total_rows += len(rows)
        has_p, has_d = _detect_page_device_columns(rows, column_map)
        if has_p:
            has_page_overall = True
        if has_d:
            has_device_overall = True
        _accumulate(rows, column_map, agg, rejected_reasons, warnings)
        log(f"{SOURCE}[manual]: {month} — строк {len(rows)}")

    # page_device_breakdown определяется из фактически найденных колонок.
    # Если ни page, ни device не обнаружены — ограничение метода (не только ручного
    # экспорта): API v4 search-queries/popular тоже не отдаёт эти измерения.
    page_device_breakdown = has_page_overall and has_device_overall
    page_device_absence_reason: str | None = (
        "method_limitation" if not page_device_breakdown else None
    )

    popular = _to_popular(agg)
    _dump(out_dir / "search_queries_popular.json", popular)

    report = _write_validation_report(
        out_dir, month_files, months, total_rows, len(popular),
        rejected_reasons, warnings, policy, column_map,
        has_page_column=has_page_overall,
        has_device_column=has_device_overall,
        page_device_absence_reason=page_device_absence_reason,
    )
    manifest = _record_manifest(
        paths, months, len(popular), policy,
        has_page_column=has_page_overall,
        has_device_column=has_device_overall,
        page_device_absence_reason=page_device_absence_reason,
    )
    log(f"{SOURCE}[manual]: готово — уникальных запросов {len(popular)} "
        f"из {total_rows} строк (page/device-разреза нет — ограничение метода)")

    return {
        "source": SOURCE,
        "rows": len(popular),
        "accepted": len(popular),
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "warnings": warnings,
        "months": months,
        "source_mode": "manual",
        "completeness": "unverified",
        "has_page_column": has_page_overall,
        "has_device_column": has_device_overall,
        "page_device_breakdown": page_device_breakdown,
        "page_device_absence_reason": page_device_absence_reason,
        "manual_no_page_breakdown_policy": policy,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "report": report,
    }


# ── Политика разреза по page ────────────────────────────────────────────────
def _resolve_policy(value: Any) -> str:
    """Нормализовать manual_no_page_breakdown_policy; неизвестное -> дефолт."""
    policy = str(value or DEFAULT_POLICY).strip().lower()
    return policy if policy in POLICIES else DEFAULT_POLICY


def _detect_page_device_columns(
    rows: list[dict[str, str]], column_map: dict[str, str]
) -> tuple[bool, bool]:
    """Фактически проверить наличие колонок page/device в CSV — не предполагать заранее.

    Смотрит на реальные ключи первой строки данных. column_map задаёт алиасы
    (canonical -> csv_header), поэтому ищем именно mapped-заголовок.
    """
    if not rows:
        return False, False
    keys = rows[0].keys()
    page_col = column_map.get("page", "page")
    device_col = column_map.get("device", "device")
    return page_col in keys, device_col in keys


# ── Раскладка входных файлов ────────────────────────────────────────────────
def _manual_dir(wm_cfg: dict[str, Any], _paths_root: Any) -> Path | None:
    raw_dir = wm_cfg.get("manual_export_dir") or "inputs/manual_exports/webmaster"
    p = Path(raw_dir)
    if p.is_absolute():
        return p
    if _paths_root is not None:
        return Path(_paths_root) / raw_dir
    return p


def _month_files(manual_dir: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in manual_dir.glob("webmaster_*.csv"):
        m = _MONTH_RE.search(path.name)
        if m:
            found.append((m.group(1), path))
    return sorted(found, key=lambda t: t[0])


# ── Чтение и агрегация ──────────────────────────────────────────────────────
def _read_export(path: Path) -> list[dict[str, str]]:
    data = Path(path).read_bytes()
    text: str | None = None
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")

    delim = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    return list(reader)


def _sniff_delimiter(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _accumulate(rows, column_map, agg, rejected_reasons, warnings) -> None:
    """Накопить показы/клики/позицию по запросу за все месяцы окна."""
    for row in rows:
        query = (_field(row, "query", column_map) or "").strip()
        if not query:
            rejected_reasons["missing_query"] = rejected_reasons.get("missing_query", 0) + 1
            continue
        shows = _to_int(_field(row, "impressions", column_map))
        clicks = _to_int(_field(row, "clicks", column_map))
        position = _to_float(_field(row, "position", column_map))
        if position is None:
            warnings["missing_position"] = warnings.get("missing_position", 0) + 1

        slot = agg.setdefault(query, [0.0, 0.0, 0.0, 0.0, 0.0])
        slot[0] += shows
        slot[1] += clicks
        if position is not None:
            slot[2] += position * shows   # для взвешивания по показам
            slot[3] += position           # запас, если показов нет
            slot[4] += 1


def _to_popular(agg: dict[str, list[float]]) -> list[dict[str, Any]]:
    """Свести агрегат к структуре, совместимой с webmaster_api / transform.

    AVG_SHOW_POSITION — средневзвешенная по показам позиция (если показов нет —
    простое среднее). Сортировка по TOTAL_SHOWS убыв. (как «популярные запросы»).
    """
    popular: list[dict[str, Any]] = []
    for query, (shows, clicks, pos_weighted, pos_sum, n) in agg.items():
        if shows > 0:
            avg_position = pos_weighted / shows
        elif n > 0:
            avg_position = pos_sum / n
        else:
            avg_position = None
        popular.append({
            "query_text": query,
            "indicators": {
                "TOTAL_SHOWS": int(shows),
                "TOTAL_CLICKS": int(clicks),
                "AVG_SHOW_POSITION": (round(avg_position, 4)
                                      if avg_position is not None else None),
            },
        })
    popular.sort(key=lambda q: q["indicators"]["TOTAL_SHOWS"], reverse=True)
    return popular


def _field(row: dict[str, Any], canonical: str, column_map: dict[str, str]) -> str:
    header = column_map.get(canonical, canonical)
    value = row.get(header)
    if value is None:
        value = row.get(canonical)
    return "" if value is None else str(value)


def _to_int(value: str) -> int:
    cleaned = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _to_float(value: str) -> float | None:
    cleaned = (value or "").strip().replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


# ── Отчёт валидации, манифест, дамп ─────────────────────────────────────────
def _limitation_note() -> str:
    return (
        "отчёт «Популярные запросы» не даёт разбивку по page/device — подтверждено "
        "как ограничение метода: и ручной экспорт, и API v4 (search-queries/popular) "
        "отдают только query-уровень (TOTAL_SHOWS/TOTAL_CLICKS/AVG_*_POSITION)."
    )


def _policy_note(policy: str) -> str:
    if policy == "aggregate":
        return ("политика 'aggregate': S08–S10, S23, S24 считаются агрегатом по "
                "домену с понижением confidence_cap.")
    return ("политика 'degrade': S08–S10, S23, S24 (нужен разрез по page) уходят "
            "в degradation.")


def _write_validation_report(
    out_dir, month_files, months, total_rows, accepted, rejected_reasons,
    warnings, policy, column_map,
    *,
    has_page_column: bool,
    has_device_column: bool,
    page_device_absence_reason: str | None,
) -> dict[str, Any]:
    page_device_breakdown = has_page_column and has_device_column
    report = {
        "source_mode": "manual",
        "completeness": "unverified",
        "input_files": [str(p) for _m, p in month_files],
        "months": months,
        "total_rows": total_rows,
        "accepted": accepted,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "warnings": warnings,
        "has_page_column": has_page_column,
        "has_device_column": has_device_column,
        "page_device_breakdown": page_device_breakdown,
        "page_device_absence_reason": page_device_absence_reason,
        "structural_limitation": _limitation_note(),
        "manual_no_page_breakdown_policy": policy,
        "policy_effect": _policy_note(policy),
        "column_map": column_map,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _record_manifest(
    paths, months, rows, policy,
    *,
    has_page_column: bool,
    has_device_column: bool,
    page_device_absence_reason: str | None,
):
    from ..pipeline import manifest as manifest_mod

    date_from = f"{months[0]}-01" if months else ""
    date_to = f"{months[-1]}-01" if months else ""
    notes = [_limitation_note(), _policy_note(policy)]

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=date_from, date_to=date_to,
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "source_mode": "manual",
            "completeness": "unverified",
            "has_page_column": has_page_column,
            "has_device_column": has_device_column,
            "page_device_breakdown": has_page_column and has_device_column,
            "page_device_absence_reason": page_device_absence_reason,
            "manual_no_page_breakdown_policy": policy,
            "notes": notes,
        },
    )


def _dump(path: Path, obj: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
