"""Экстрактор: РУЧНАЯ выгрузка Яндекс.Вебмастера (без API).

Активен, когда config.sources.webmaster.mode == "manual". Читает,
валидирует и нормализует CSV-экспорт «Популярные запросы» в wide-формате,
который аналитик кладёт в inputs/manual_exports/webmaster/.

Контракт:
    Читает   — config.sources.webmaster (manual_export_dir, manual_export_file,
               column_map) и единственный CSV-файл wide-формата.
    Пишет    — data/raw/webmaster/search_queries_popular.json — список пар
               (query_text, page) с indicators TOTAL_SHOWS/TOTAL_CLICKS/
               AVG_SHOW_POSITION/CTR/DEMAND. Плюс validation_report.json и
               manifest.json (canonical_tables: [seo_queries]).
    Деградация — опционален; файл не найден -> SourceUnavailable (принцип 4).
    LLM      — не используется.

Формат входного файла (wide):
    Одна строка = одна пара (Query × Url). Месяцы развёрнуты в колонки:
    Query | Url | YYYY-MM_shows | YYYY-MM_position | YYYY-MM_demand |
    YYYY-MM_ctr | YYYY-MM_clicks | ...
    Один файл за весь период; имя задаётся manual_export_file
    (дефолт — webmaster_export.csv).

Полнота НЕ верифицируется -> completeness: "unverified", source_mode: "manual".
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

SCRIPT_VERSION = "0.2.0"
SOURCE = "webmaster"
CANONICAL_TABLES = ["seo_queries"]

# Колонка YYYY-MM_shows задаёт список месяцев в wide-файле.
_MONTH_SHOWS_RE = re.compile(r"^(\d{4}-\d{2})_shows$")

# Дефолтные имена заголовков в реальном экспорте Вебмастера.
_DEFAULT_COLUMN_MAP: dict[str, str] = {"query": "Query", "page": "Url"}


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка: есть ли файл ручной выгрузки Вебмастера."""
    wm = (config.get("sources") or {}).get("webmaster") or {}
    manual_dir = _manual_dir(wm, _paths_root=None)
    if manual_dir is None or not manual_dir.exists():
        return False
    return _export_path(wm, manual_dir).exists()


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Провалидировать и нормализовать ручную выгрузку Вебмастера в data/raw/webmaster/."""
    log = log or (lambda _msg: None)

    wm = (config.get("sources") or {}).get("webmaster") or {}
    column_map = _resolve_column_map(wm.get("column_map"))

    manual_dir = _manual_dir(wm, _paths_root=getattr(paths, "root", None))
    if manual_dir is None or not manual_dir.exists():
        raise C.SourceUnavailable(
            SOURCE, f"нет каталога ручных выгрузок Вебмастера: {manual_dir}"
        )
    export_path = _export_path(wm, manual_dir)
    if not export_path.exists():
        raise C.SourceUnavailable(
            SOURCE, f"файл выгрузки не найден: {export_path}"
        )

    # Не сбрасываем директорию: input-CSV лежит рядом с output-JSON
    # в том же data/raw/webmaster/, reset_dir удалил бы его.
    out_dir = C.source_dir(paths, SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}[manual]: файл {export_path}")

    rows = _read_export(export_path)
    total_rows = len(rows)

    headers = list(rows[0].keys()) if rows else []
    months = _detect_months(headers)
    has_demand = any(f"{m}_demand" in headers for m in months)

    log(f"{SOURCE}[manual]: строк {total_rows}, месяцев {len(months)}, demand={has_demand}")

    rejected_reasons: dict[str, int] = {}
    warnings: dict[str, int] = {}
    agg: dict[tuple[str, str], list] = {}

    _parse_and_accumulate(rows, months, column_map, has_demand, agg, rejected_reasons, warnings)

    popular = _to_popular(agg, has_demand)
    _dump(out_dir / "search_queries_popular.json", popular)

    report = _write_validation_report(
        out_dir, export_path, months, total_rows, len(popular),
        rejected_reasons, warnings, column_map, has_demand,
    )
    manifest = _record_manifest(paths, months, len(popular), has_demand)

    log(f"{SOURCE}[manual]: готово — уникальных пар query×page {len(popular)}")

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
        "has_page_column": True,
        "has_device_column": False,
        "page_device_breakdown": True,
        "page_device_absence_reason": None,
        "has_demand_column": has_demand,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "report": report,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_column_map(column_map: Any) -> dict[str, str]:
    result = dict(_DEFAULT_COLUMN_MAP)
    result.update(column_map or {})
    return result


def _manual_dir(wm_cfg: dict[str, Any], _paths_root: Any) -> Path | None:
    raw_dir = wm_cfg.get("manual_export_dir") or "inputs/manual_exports/webmaster"
    p = Path(raw_dir)
    if p.is_absolute():
        return p
    if _paths_root is not None:
        return Path(_paths_root) / raw_dir
    return p


def _export_path(wm_cfg: dict[str, Any], manual_dir: Path) -> Path:
    filename = wm_cfg.get("manual_export_file") or "webmaster_export.csv"
    return manual_dir / filename


def _detect_months(headers: list[str]) -> list[str]:
    months = []
    for h in headers:
        m = _MONTH_SHOWS_RE.match(h)
        if m:
            months.append(m.group(1))
    return sorted(months)


# ── Парсинг и агрегация ──────────────────────────────────────────────────────

def _parse_and_accumulate(
    rows: list[dict[str, str]],
    months: list[str],
    column_map: dict[str, str],
    has_demand: bool,
    agg: dict,
    rejected_reasons: dict,
    warnings: dict,
) -> None:
    """Развернуть wide-строки в long и накопить агрегат по (query, page)."""
    for row in rows:
        query = (_field(row, "query", column_map) or "").strip()
        if not query:
            rejected_reasons["missing_query"] = rejected_reasons.get("missing_query", 0) + 1
            continue
        page = (_field(row, "page", column_map) or "").strip()

        for month in months:
            shows = _to_int(row.get(f"{month}_shows", ""))
            if shows == 0:
                continue  # пропускаем месяц с нулевыми показами

            clicks = _to_int(row.get(f"{month}_clicks", ""))
            position = _to_float(row.get(f"{month}_position", ""))
            demand = _to_float(row.get(f"{month}_demand", "")) if has_demand else None

            if position is None:
                warnings["missing_position"] = warnings.get("missing_position", 0) + 1

            key = (query, page)
            if key not in agg:
                # [shows, clicks, pos_weighted, pos_sum, pos_count, demand_max]
                agg[key] = [0.0, 0.0, 0.0, 0.0, 0.0, None]
            slot = agg[key]
            slot[0] += shows
            slot[1] += clicks
            if position is not None:
                slot[2] += position * shows
                slot[3] += position
                slot[4] += 1
            if demand is not None:
                slot[5] = demand if slot[5] is None else max(slot[5], demand)


def _to_popular(
    agg: dict[tuple[str, str], list],
    has_demand: bool,
) -> list[dict[str, Any]]:
    """Свести агрегат (query×page) к выходному контракту search_queries_popular.json."""
    popular: list[dict[str, Any]] = []
    for (query, page), (shows, clicks, pos_w, pos_sum, pos_n, demand_max) in agg.items():
        if shows > 0:
            avg_position = pos_w / shows
        elif pos_n > 0:
            avg_position = pos_sum / pos_n
        else:
            avg_position = None

        ctr: float | None = (clicks / shows) if shows > 0 else None
        demand: int | None = int(round(demand_max)) if has_demand and demand_max is not None else None

        popular.append({
            "query_text": query,
            "page": page,
            "indicators": {
                "TOTAL_SHOWS": int(shows),
                "TOTAL_CLICKS": int(clicks),
                "AVG_SHOW_POSITION": round(avg_position, 4) if avg_position is not None else None,
                "CTR": round(ctr, 6) if ctr is not None else None,
                "DEMAND": demand,
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
    cleaned = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", "")
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


# ── Чтение CSV ───────────────────────────────────────────────────────────────

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


# ── Отчёт валидации, манифест, дамп ─────────────────────────────────────────

def _write_validation_report(
    out_dir: Path,
    export_path: Path,
    months: list[str],
    total_rows: int,
    accepted: int,
    rejected_reasons: dict,
    warnings: dict,
    column_map: dict,
    has_demand: bool,
) -> dict[str, Any]:
    report = {
        "source_mode": "manual",
        "completeness": "unverified",
        "input_file": str(export_path),
        "months": months,
        "total_rows": total_rows,
        "accepted": accepted,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "warnings": warnings,
        "has_page_column": True,
        "has_device_column": False,
        "page_device_breakdown": True,
        "page_device_absence_reason": None,
        "has_demand_column": has_demand,
        "column_map": column_map,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _record_manifest(paths, months: list[str], rows: int, has_demand: bool):
    from ..pipeline import manifest as manifest_mod

    date_from = f"{months[0]}-01" if months else ""
    date_to = f"{months[-1]}-01" if months else ""

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=date_from, date_to=date_to,
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "source_mode": "manual",
            "completeness": "unverified",
            "has_page_column": True,
            "has_device_column": False,
            "page_device_breakdown": True,
            "page_device_absence_reason": None,
            "has_demand_column": has_demand,
            "months": months,
        },
    )


def _dump(path: Path, obj: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
