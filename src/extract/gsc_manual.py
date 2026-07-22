"""Экстрактор: РУЧНАЯ выгрузка Google Search Console (без API).

Формат входных данных: папки YYYY-MM в manual_export_dir.
Каждая папка содержит отдельные CSV-файлы по срезам:
    Диаграмма.csv   — временной ряд по дням (обязательно)
    Запросы.csv     — запросы за период (обязательно)
    Страницы.csv    — топ страниц за период (обязательно, кроме комбинированного
                       формата ниже — там page уже приходит из Запросы.csv)
    Устройства.csv  — разбивка по устройствам (опционально)
    Страны.csv      — разбивка по странам (опционально)
    Фильтры.csv     — мета: период и тип поиска (опционально)

Contract 3A — комбинированный формат Запросы.csv (несколько измерений сразу):
    Если в GSC перед экспортом одновременно включены измерения Query + Page +
    Device (см. docs/gsc_export_instructions.md), Запросы.csv содержит колонки
    обоих дополнительных измерений в каждой строке — page и device берутся из
    строки, а не подставляются заглушкой. Это ровно контракт seo_queries:
    month, query, page, device, clicks, impressions, ctr, position — без
    выродившихся page="" / device="unknown".

    Если Запросы.csv содержит только query (старый раздельный экспорт — по
    одному измерению за раз), парсер НЕ падает: page="" и device="unknown" как
    раньше, но месяц помечается caveat'ом incomplete_dimensions (и попадает в
    incomplete_dimensions_months / device_missing_months) — явный сигнал, что
    contract 3A для этого месяца выполнен не полностью.

Дополнительно пишутся gsc_daily_YYYY-MM.{csv,parquet},
gsc_pages_YYYY-MM.{csv,parquet}, gsc_devices_YYYY-MM.{csv,parquet}
(при наличии соответствующих файлов).

transform.build_seo_queries_gsc работает без правок — выходной контракт seo_queries
не изменился, флаги source_mode и completeness в manifest те же.
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

SCRIPT_VERSION = "0.3.0"
SOURCE = "gsc"
CANONICAL_TABLES = ["seo_queries"]

RAW_FIELDS = ["month", "query", "page", "device",
              "clicks", "impressions", "ctr", "position"]

DAILY_FIELDS = ["date", "clicks", "impressions", "ctr", "position"]
PAGES_FIELDS = ["page", "clicks", "impressions", "ctr", "position"]
DEVICES_FIELDS = ["device", "clicks", "impressions", "ctr", "position"]

CLICKS_TOLERANCE = 0.10

_MONTH_FOLDER_RE = re.compile(r"^\d{4}-\d{2}$")

# Нормализованное имя стема → канонический ключ среза
_FILE_MAP: dict[str, str] = {
    "диаграмма": "diagram",
    "запросы": "queries",
    "страницы": "pages",
    "устройства": "devices",
    "страны": "countries",
    "фильтры": "filters",
}

_REQUIRED_SLICES = {"diagram", "queries", "pages"}

# Заголовки GSC-экспорта (RU-интерфейс).
# «Kлики»: K — ASCII (U+004B), остальные символы кириллические.
# Именно так выгружает GSC; не менять на Cyrillic К (U+041A).
_CLICKS_HEADER = "Kлики"  # "Kлики": K=U+004B, лики=Cyrillic

DEFAULT_COLUMN_MAP: dict[str, str] = {
    "query":        "Популярные запросы",
    "page":         "Популярные страницы",
    "device":       "Устройство",
    "country":      "Страна",
    "date":         "Дата",
    "clicks":       _CLICKS_HEADER,
    "impressions":  "Показы",
    "ctr":          "CTR",
    "position":     "Позиция",
    "filter_key":   "Фильтр",
    "filter_value": "Значение",
}


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """True если есть хотя бы одна папка YYYY-MM с Диаграмма.csv и Запросы.csv."""
    gsc = (config.get("sources") or {}).get("gsc") or {}
    manual_dir = _manual_dir(gsc, _paths_root=None)
    if manual_dir is None or not manual_dir.exists():
        return False
    for folder in sorted(manual_dir.iterdir()):
        if folder.is_dir() and _MONTH_FOLDER_RE.match(folder.name):
            slices = _detect_slices(folder)
            if "diagram" in slices and "queries" in slices:
                return True
    return False


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Провалидировать и нормализовать ручные выгрузки GSC в data/raw/gsc/."""
    log = log or (lambda _msg: None)

    gsc = (config.get("sources") or {}).get("gsc") or {}
    column_map = _effective_column_map(gsc)
    fmt = C.resolve_raw_format(gsc)

    manual_dir = _manual_dir(gsc, _paths_root=getattr(paths, "root", None))
    if manual_dir is None or not manual_dir.exists():
        raise C.SourceUnavailable(SOURCE, f"нет каталога ручных выгрузок GSC: {manual_dir}")

    month_folders = _month_folders(manual_dir)
    if not month_folders:
        raise C.SourceUnavailable(SOURCE, f"в {manual_dir} нет папок YYYY-MM")

    # Не сбрасываем директорию: manual_export_dir может совпадать с out_dir
    # (напр. data/raw/gsc/ содержит и входные папки YYYY-MM/, и выходные flat-файлы).
    out_dir = C.source_dir(paths, SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{SOURCE}[manual]: каталог {manual_dir}, папок {len(month_folders)}, формат {fmt}")

    total_rows = 0
    accepted_rows = 0
    rejected_reasons: dict[str, int] = {}
    caveats: list[dict[str, Any]] = []
    months: list[str] = []
    skipped_months: list[str] = []
    available_slices_by_month: dict[str, list[str]] = {}
    combined_by_month: dict[str, bool] = {}

    for month, folder in month_folders:
        slices = _detect_slices(folder)
        available = [k for k in ["diagram", "queries", "pages", "devices", "countries", "filters"]
                     if k in slices]
        available_slices_by_month[month] = available

        # Запросы.csv читается один раз здесь: заголовок нужен уже для проверки
        # обязательных срезов (комбинированный формат contract 3A ослабляет
        # требование Страницы.csv — page уже приходит из Запросы.csv).
        combined = False
        query_header: list[str] = []
        query_rows: list[dict[str, str]] = []
        if "queries" in slices:
            query_header, query_rows = _read_slice_csv_with_header(slices["queries"])
            combined = _is_combined_header(query_header, column_map)

        required = set(_REQUIRED_SLICES)
        if combined:
            required -= {"pages"}
        missing = required - set(slices)
        if missing:
            caveats.append({
                "month": month,
                "type": "missing_required_files",
                "missing": sorted(missing),
                "caveat": f"пропущены обязательные срезы: {sorted(missing)}",
            })
            log(f"{SOURCE}[manual]: {month} — пропущены обязательные срезы {sorted(missing)}, пропускаем")
            skipped_months.append(month)
            continue

        months.append(month)
        combined_by_month[month] = combined
        if not combined:
            caveats.append({
                "month": month,
                "type": "incomplete_dimensions",
                "caveat": (
                    "Запросы.csv не содержит колонок Page и Device одновременно — "
                    "раздельный экспорт по одному измерению вместо комбинированного "
                    "(contract 3A); page/device для этого месяца недостоверны."
                ),
            })

        # Фильтры.csv (опционально) — только для инфо в лог
        if "filters" in slices:
            filters_info = _parse_filters(slices["filters"], column_map)
            if filters_info.get("period"):
                log(f"{SOURCE}[manual]: {month} — период из Фильтры.csv: {filters_info['period']}")

        # Диаграмма.csv
        diagram_rows = _read_slice_csv(slices["diagram"])
        daily_records = _normalize_daily(diagram_rows, column_map)
        if daily_records:
            C.write_table(out_dir / f"gsc_daily_{month}", daily_records, DAILY_FIELDS, fmt)

        # Запросы.csv → seo_queries (query_rows уже прочитаны выше)
        query_records, q_accepted, q_rejected = _normalize_queries(
            query_rows, month, column_map, combined=combined,
        )
        total_rows += len(query_rows)
        accepted_rows += q_accepted
        for reason, n in q_rejected.items():
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + n

        C.write_table(out_dir / f"gsc_{month}", query_records, RAW_FIELDS, fmt)

        # Страницы.csv (опционально при комбинированном Запросы.csv)
        if "pages" in slices:
            pages_rows = _read_slice_csv(slices["pages"])
            pages_records = _normalize_pages(pages_rows, column_map)
            if pages_records:
                C.write_table(out_dir / f"gsc_pages_{month}", pages_records, PAGES_FIELDS, fmt)

        # Устройства.csv (опционально)
        if "devices" in slices:
            devices_rows = _read_slice_csv(slices["devices"])
            devices_records = _normalize_devices(devices_rows, column_map)
            if devices_records:
                C.write_table(out_dir / f"gsc_devices_{month}", devices_records, DEVICES_FIELDS, fmt)

        # Сверка кликов: Диаграмма vs Запросы
        caveat = _clicks_caveat(month, daily_records, query_records)
        if caveat:
            caveats.append(caveat)

        log(f"{SOURCE}[manual]: {month} — запросы: принято {q_accepted}, "
            f"отброшено {sum(q_rejected.values())}, срезы: {available}, "
            f"combined={combined}")

    if not months:
        raise C.SourceUnavailable(SOURCE, "нет успешно обработанных месяцев GSC")

    # device_missing_months (и, для этой схемы, page тоже) — только для месяцев
    # legacy-формата: комбинированный Запросы.csv даёт реальные page/device.
    incomplete_dimensions_months = [m for m in months if not combined_by_month.get(m, False)]
    device_missing_months = list(incomplete_dimensions_months)

    report = _write_validation_report(
        out_dir, month_folders, months, skipped_months, total_rows, accepted_rows,
        rejected_reasons, device_missing_months, caveats, column_map, fmt,
        available_slices_by_month, incomplete_dimensions_months,
    )
    manifest = _record_manifest(
        paths, months, accepted_rows, fmt, device_missing_months, caveats,
        available_slices_by_month, incomplete_dimensions_months,
    )

    if device_missing_months:
        log(f"{SOURCE}[manual]: incomplete_dimensions=true для месяцев {device_missing_months} — "
            "page/device недостоверны, исключить из посегментного разреза (S20)")

    log(f"{SOURCE}[manual]: готово — принято {accepted_rows} строк из {total_rows}, "
        f"месяцев {len(months)}")

    clicks_mismatch_caveats = [c for c in caveats
                               if c.get("type") == "clicks_diagram_vs_queries_mismatch"]

    return {
        "source": SOURCE,
        "rows": accepted_rows,
        "accepted": accepted_rows,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "months": months,
        "device_missing_months": device_missing_months,
        "incomplete_dimensions_months": incomplete_dimensions_months,
        "incomplete_dimensions": bool(incomplete_dimensions_months),
        "combined_dimensions_by_month": combined_by_month,
        "clicks_ui_caveats": clicks_mismatch_caveats,  # обратная совместимость
        "caveats": caveats,
        "source_mode": "manual",
        "completeness": "unverified",
        "raw_format": fmt,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "report": report,
    }


# ── Раскладка входных папок ──────────────────────────────────────────────────

def _manual_dir(gsc_cfg: dict[str, Any], _paths_root: Any) -> Path | None:
    raw_dir = gsc_cfg.get("manual_export_dir") or "inputs/manual_exports/gsc"
    p = Path(raw_dir)
    if p.is_absolute():
        return p
    if _paths_root is not None:
        return Path(_paths_root) / raw_dir
    return p


def _month_folders(manual_dir: Path) -> list[tuple[str, Path]]:
    """Список (YYYY-MM, Path) папок-месяцев, отсортированный по имени."""
    found: list[tuple[str, Path]] = []
    for item in manual_dir.iterdir():
        if item.is_dir() and _MONTH_FOLDER_RE.match(item.name):
            found.append((item.name, item))
    return sorted(found, key=lambda t: t[0])


def _detect_slices(folder: Path) -> dict[str, Path]:
    """Найти CSV-файлы среза в папке месяца. Ключ — канонический (diagram/queries/…)."""
    result: dict[str, Path] = {}
    for csv_file in folder.glob("*.csv"):
        normalized = csv_file.stem.strip().lower()
        key = _FILE_MAP.get(normalized)
        if key:
            result[key] = csv_file
    return result


def _effective_column_map(gsc_cfg: dict[str, Any]) -> dict[str, str]:
    """DEFAULT_COLUMN_MAP + переопределения из config.sources.gsc.column_map."""
    result = dict(DEFAULT_COLUMN_MAP)
    result.update(gsc_cfg.get("column_map") or {})
    return result


# ── Чтение CSV ────────────────────────────────────────────────────────────────

def _read_slice_csv(path: Path) -> list[dict[str, str]]:
    """Прочитать CSV-файл среза: UTF-8/cp1251, авто-разделитель, skipinitialspace."""
    return _read_slice_csv_with_header(path)[1]


def _read_slice_csv_with_header(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Как _read_slice_csv, но дополнительно отдаёт заголовок.

    Нужен для детекции комбинированного формата Запросы.csv (contract 3A):
    наличие колонок Page/Device в заголовке отличает комбинированный экспорт
    от старого раздельного (только query).
    """
    data = path.read_bytes()
    text: str | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")

    delim = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim, skipinitialspace=True)
    rows = list(reader)
    return list(reader.fieldnames or []), rows


def _is_combined_header(header: list[str], column_map: dict[str, str]) -> bool:
    """True если Запросы.csv содержит колонки Page и Device одновременно (contract 3A)."""
    page_header = column_map.get("page", "page")
    device_header = column_map.get("device", "device")
    return page_header in header and device_header in header


def _sniff_delimiter(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


# ── Нормализация срезов ───────────────────────────────────────────────────────

def _normalize_queries(
    rows: list[dict[str, str]], month: str, column_map: dict[str, str], *, combined: bool,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """Запросы.csv → seo_queries строки.

    combined=True  — Запросы.csv содержит Page и Device одновременно (contract
                      3A, комбинированный экспорт) — берём их из строки.
    combined=False — legacy: только query; page="" device="unknown" как раньше
                      (месяц помечается caveat'ом incomplete_dimensions на
                      уровне extract()).
    """
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for row in rows:
        query = _field(row, "query", column_map).strip()
        if not query:
            rejected["missing_query"] = rejected.get("missing_query", 0) + 1
            continue
        if combined:
            page = _field(row, "page", column_map).strip()
            device = _field(row, "device", column_map).strip() or "unknown"
        else:
            page = ""
            device = "unknown"
        records.append({
            "month": month,
            "query": query,
            "page": page,
            "device": device,
            "clicks": _to_int(_field(row, "clicks", column_map)),
            "impressions": _to_int(_field(row, "impressions", column_map)),
            "ctr": _to_ctr(_field(row, "ctr", column_map)),
            "position": _to_float(_field(row, "position", column_map)),
        })
    return records, len(records), rejected


def _normalize_daily(
    rows: list[dict[str, str]], column_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Диаграмма.csv → временной ряд. Строки без даты отбраковываются."""
    records: list[dict[str, Any]] = []
    for row in rows:
        date_val = _field(row, "date", column_map).strip()
        if not date_val:
            continue
        records.append({
            "date": date_val,
            "clicks": _to_int(_field(row, "clicks", column_map)),
            "impressions": _to_int(_field(row, "impressions", column_map)),
            "ctr": _to_ctr(_field(row, "ctr", column_map)),
            "position": _to_float(_field(row, "position", column_map)),
        })
    return records


def _normalize_pages(
    rows: list[dict[str, str]], column_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Страницы.csv → агрегат по страницам."""
    records: list[dict[str, Any]] = []
    for row in rows:
        page = _field(row, "page", column_map).strip()
        if not page:
            continue
        records.append({
            "page": page,
            "clicks": _to_int(_field(row, "clicks", column_map)),
            "impressions": _to_int(_field(row, "impressions", column_map)),
            "ctr": _to_ctr(_field(row, "ctr", column_map)),
            "position": _to_float(_field(row, "position", column_map)),
        })
    return records


def _normalize_devices(
    rows: list[dict[str, str]], column_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Устройства.csv → агрегат по устройствам."""
    records: list[dict[str, Any]] = []
    for row in rows:
        device = _field(row, "device", column_map).strip()
        if not device:
            continue
        records.append({
            "device": device,
            "clicks": _to_int(_field(row, "clicks", column_map)),
            "impressions": _to_int(_field(row, "impressions", column_map)),
            "ctr": _to_ctr(_field(row, "ctr", column_map)),
            "position": _to_float(_field(row, "position", column_map)),
        })
    return records


def _parse_filters(path: Path, column_map: dict[str, str]) -> dict[str, str]:
    """Фильтры.csv → {period: "..."} (только строка с Дата)."""
    try:
        rows = _read_slice_csv(path)
    except Exception:
        return {}
    for row in rows:
        key = _field(row, "filter_key", column_map).strip().lower()
        if key in ("дата", "date"):
            return {"period": _field(row, "filter_value", column_map).strip()}
    return {}


# ── Вспомогательные функции поля/значения ────────────────────────────────────

def _field(row: dict[str, Any], canonical: str, column_map: dict[str, str]) -> str:
    header = column_map.get(canonical, canonical)
    value = row.get(header)
    if value is None:
        value = row.get(canonical)
    return "" if value is None else str(value)


def _to_int(value: str) -> int:
    cleaned = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _to_float(value: str) -> float | None:
    cleaned = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not cleaned or cleaned.lower() == "nan":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_ctr(value: str) -> float | None:
    raw = (value or "").strip()
    if not raw or raw.lower() == "nan":
        return None
    pct = raw.endswith("%")
    num = _to_float(raw.rstrip("%"))
    if num is None:
        return None
    return num / 100 if pct else num


# ── Сверка кликов ─────────────────────────────────────────────────────────────

def _clicks_caveat(
    month: str,
    daily_records: list[dict[str, Any]],
    query_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Диаграмма vs Запросы: расхождение >10% → caveat clicks_diagram_vs_queries_mismatch."""
    diagram_clicks = sum(int(r.get("clicks") or 0) for r in daily_records)
    query_clicks = sum(int(r.get("clicks") or 0) for r in query_records)

    if diagram_clicks <= 0:
        return None  # нет базы для сравнения

    deviation = abs(diagram_clicks - query_clicks) / diagram_clicks
    if deviation <= CLICKS_TOLERANCE:
        return None

    return {
        "month": month,
        "type": "clicks_diagram_vs_queries_mismatch",
        "diagram_clicks": diagram_clicks,
        "query_clicks": query_clicks,
        "deviation_pct": round(deviation * 100, 1),
        "caveat": "расхождение суммы кликов между Диаграммой и Запросами",
    }


# ── Отчёт и манифест ──────────────────────────────────────────────────────────

def _write_validation_report(
    out_dir: Path,
    month_folders: list[tuple[str, Path]],
    months: list[str],
    skipped_months: list[str],
    total_rows: int,
    accepted: int,
    rejected_reasons: dict[str, int],
    device_missing_months: list[str],
    caveats: list[dict[str, Any]],
    column_map: dict[str, str],
    fmt: str,
    available_slices_by_month: dict[str, list[str]],
    incomplete_dimensions_months: list[str],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "source_mode": "manual",
        "completeness": "unverified",
        "input_folders": [str(folder) for _m, folder in month_folders],
        "months": months,
        "skipped_months": skipped_months,
        "total_rows": total_rows,
        "accepted": accepted,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "device_missing_months": device_missing_months,
        "incomplete_dimensions_months": incomplete_dimensions_months,
        "incomplete_dimensions": bool(incomplete_dimensions_months),
        "available_slices_by_month": available_slices_by_month,
        "caveats": caveats,
        "column_map": column_map,
        "raw_format": fmt,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _record_manifest(
    paths: Any,
    months: list[str],
    rows: int,
    fmt: str,
    device_missing_months: list[str],
    caveats: list[dict[str, Any]],
    available_slices_by_month: dict[str, list[str]],
    incomplete_dimensions_months: list[str],
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    date_from = f"{months[0]}-01" if months else ""
    date_to = f"{months[-1]}-01" if months else ""

    notes: list[str] = []
    if incomplete_dimensions_months:
        notes.append(
            "incomplete_dimensions=true для месяцев "
            f"{incomplete_dimensions_months} — Запросы.csv не содержит Page/Device "
            "одновременно (раздельный экспорт вместо комбинированного, contract 3A "
            "не выполнен полностью); исключить из посегментного разреза S20."
        )
    for c in caveats:
        if c.get("type") == "clicks_diagram_vs_queries_mismatch":
            notes.append(
                f"{c['month']}: расхождение суммы кликов Диаграмма({c['diagram_clicks']}) "
                f"vs Запросы({c['query_clicks']}) = {c['deviation_pct']}%."
            )
        elif c.get("type") == "missing_required_files":
            notes.append(
                f"{c['month']}: пропущены обязательные файлы {c['missing']}, месяц пропущен."
            )

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=date_from, date_to=date_to,
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "engine": "google",
            "source_mode": "manual",
            "completeness": "unverified",
            "raw_format": fmt,
            "device_missing_months": device_missing_months,
            "incomplete_dimensions_months": incomplete_dimensions_months,
            "incomplete_dimensions": bool(incomplete_dimensions_months),
            "available_slices_by_month": available_slices_by_month,
            "notes": notes,
        },
    )
