"""Экстрактор: РУЧНАЯ выгрузка Google Search Console (без API).

Активен, когда config.sources.gsc.mode == "manual" (сейчас всегда — API-доступа
к GSC у клиента нет). НЕ вызывает никакой API: только читает, валидирует и
нормализует CSV-файлы, которые аналитик выгрузил из интерфейса GSC и положил в
inputs/manual_exports/gsc/.

Контракт:
    Читает   — config.sources.gsc (manual_export_dir, column_map, опц. raw_format)
               и файлы gsc_YYYY-MM.csv (+ опц. gsc_YYYY-MM.meta.yaml).
    Пишет    — data/raw/gsc/gsc_YYYY-MM.{csv,parquet} — ТОТ ЖЕ выходной контракт,
               что у gsc_api.py (срез (query, page, device) x метрики), поэтому
               transform.build_seo_queries_gsc работает без правок. Плюс
               data/raw/gsc/validation_report.json и manifest.json
               (canonical_tables: [seo_queries]).
    Деградация — опционален; нет ручных выгрузок -> источник недоступен (принцип 4).
    LLM      — не используется.

Полнота НЕ верифицируется (в отличие от API нет контроля пагинации rowLimit/
startRow) — в manifest всегда пишется completeness: "unverified",
source_mode: "manual".

Вход по месяцам:
    gsc_YYYY-MM.csv — один файл на месяц. Колонки (алиасы в
    config.sources.gsc.column_map): query, page, device, clicks, impressions,
    ctr, position, month. Месяц берётся из имени файла (авторитетно).
    gsc_YYYY-MM.meta.yaml (опц.) — поле total_clicks_ui (число сверху отчёта в
    интерфейсе): нужно для оценки доли кликов, скрытой порогом анонимизации
    Google. Расхождение суммы clicks с total_clicks_ui > 10% -> caveat.

device:
    Если в конкретном месяце нет колонки device (быстрый экспорт GSC её не
    всегда даёт) — строки НЕ отбрасываем, пишем device: "unknown", а месяц
    заносим в device_missing_months. Ниже по пайплайну S20 (CWV vs устройство)
    обязан исключить такой месяц из посегментного разреза, а не притворяться,
    что данные по устройствам есть.
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
SOURCE = "gsc"
CANONICAL_TABLES = ["seo_queries"]

# Порядок колонок сырья фиксирован — на него опирается transform (тот же, что у
# gsc_api.py). Переключение mode: api <-> manual не должно ломать transform.
RAW_FIELDS = ["month", "query", "page", "device",
              "clicks", "impressions", "ctr", "position"]

# Порог расхождения суммы clicks с total_clicks_ui из meta.yaml.
CLICKS_UI_TOLERANCE = 0.10

# Имя файла помесячной выгрузки: gsc_YYYY-MM.csv (meta — gsc_YYYY-MM.meta.yaml).
_MONTH_RE = re.compile(r"gsc_(\d{4}-\d{2})\.csv$", re.IGNORECASE)


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка: есть ли хотя бы один файл ручной выгрузки GSC."""
    gsc = (config.get("sources") or {}).get("gsc") or {}
    manual_dir = _manual_dir(gsc, _paths_root=None)
    if manual_dir is None or not manual_dir.exists():
        return False
    return any(_MONTH_RE.search(p.name) for p in manual_dir.glob("gsc_*.csv"))


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
    column_map = gsc.get("column_map") or {}
    fmt = C.resolve_raw_format(gsc)

    manual_dir = _manual_dir(gsc, _paths_root=getattr(paths, "root", None))
    if manual_dir is None or not manual_dir.exists():
        raise C.SourceUnavailable(
            SOURCE, f"нет каталога ручных выгрузок GSC: {manual_dir}"
        )
    month_files = _month_files(manual_dir)
    if not month_files:
        raise C.SourceUnavailable(
            SOURCE, f"в {manual_dir} нет файлов gsc_YYYY-MM.csv"
        )

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    log(f"{SOURCE}[manual]: каталог {manual_dir}, файлов {len(month_files)}, формат {fmt}")

    total_rows = 0
    accepted_rows = 0
    rejected_reasons: dict[str, int] = {}
    device_missing_months: list[str] = []
    clicks_ui_caveats: list[dict[str, Any]] = []
    months: list[str] = []

    for month, csv_path in month_files:
        months.append(month)
        rows, has_device = _read_export(csv_path, column_map)
        records, accepted, rejected = _normalize_month(rows, month, has_device, column_map)
        total_rows += len(rows)
        accepted_rows += accepted
        for reason, n in rejected.items():
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + n
        if not has_device:
            device_missing_months.append(month)

        out = C.write_table(out_dir / f"gsc_{month}", records, RAW_FIELDS, fmt)

        caveat = _clicks_ui_caveat(csv_path, month, records)
        if caveat:
            clicks_ui_caveats.append(caveat)

        log(f"{SOURCE}[manual]: {month} — принято {accepted}, отброшено "
            f"{sum(rejected.values())}, device={'есть' if has_device else 'нет'} -> {out.name}")

    report = _write_validation_report(
        out_dir, month_files, months, total_rows, accepted_rows,
        rejected_reasons, device_missing_months, clicks_ui_caveats, column_map, fmt,
    )
    manifest = _record_manifest(
        paths, months, accepted_rows, fmt, device_missing_months, clicks_ui_caveats,
    )
    if device_missing_months:
        log(f"{SOURCE}[manual]: без device в месяцах {device_missing_months} — "
            "исключить из посегментного разреза S20")
    for c in clicks_ui_caveats:
        log(f"{SOURCE}[manual]: {c['month']} — расхождение с total_clicks_ui "
            f"{c['deviation_pct']}% (порог {int(CLICKS_UI_TOLERANCE * 100)}%)")
    log(f"{SOURCE}[manual]: готово — принято {accepted_rows} строк из {total_rows}")

    return {
        "source": SOURCE,
        "rows": accepted_rows,
        "accepted": accepted_rows,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "months": months,
        "device_missing_months": device_missing_months,
        "clicks_ui_caveats": clicks_ui_caveats,
        "source_mode": "manual",
        "completeness": "unverified",
        "raw_format": fmt,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "report": report,
    }


# ── Раскладка входных файлов ────────────────────────────────────────────────
def _manual_dir(gsc_cfg: dict[str, Any], _paths_root: Any) -> Path | None:
    """Каталог ручных выгрузок: абсолютный — как есть; относительный — от корня."""
    raw_dir = gsc_cfg.get("manual_export_dir") or "inputs/manual_exports/gsc"
    p = Path(raw_dir)
    if p.is_absolute():
        return p
    if _paths_root is not None:
        return Path(_paths_root) / raw_dir
    return p  # относительный без корня — используется только в ping


def _month_files(manual_dir: Path) -> list[tuple[str, Path]]:
    """Список (месяц YYYY-MM, путь) по именам gsc_YYYY-MM.csv, отсортированный."""
    found: list[tuple[str, Path]] = []
    for path in manual_dir.glob("gsc_*.csv"):
        m = _MONTH_RE.search(path.name)
        if m:
            found.append((m.group(1), path))
    return sorted(found, key=lambda t: t[0])


# ── Чтение и нормализация одного экспорта ───────────────────────────────────
def _read_export(path: Path, column_map: dict[str, str]) -> tuple[list[dict[str, str]], bool]:
    r"""Прочитать CSV-экспорт GSC. Возвращает (строки, есть_ли_колонка_device).

    GSC-экспорт обычно UTF-8 с запятой; поддерживаем cp1251 и ';'/'\t' на всякий.
    """
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
    headers = list(reader.fieldnames or [])
    device_header = column_map.get("device", "device")
    has_device = device_header in headers or "device" in headers
    return list(reader), has_device


def _sniff_delimiter(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _normalize_month(
    rows: list[dict[str, str]], month: str, has_device: bool, column_map: dict[str, str],
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """Нормализовать строки одного месяца. Возвращает (records, accepted, rejected).

    Месяц берётся из имени файла (не из колонки month) — это авторитетный
    источник. Пустой query -> строка отбраковывается (reason missing_query).
    """
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}

    for row in rows:
        query = (_field(row, "query", column_map) or "").strip()
        if not query:
            rejected["missing_query"] = rejected.get("missing_query", 0) + 1
            continue

        device = (_field(row, "device", column_map) or "").strip() if has_device else ""
        records.append({
            "month": month,
            "query": query,
            "page": (_field(row, "page", column_map) or "").strip(),
            "device": device or "unknown",
            "clicks": _to_int(_field(row, "clicks", column_map)),
            "impressions": _to_int(_field(row, "impressions", column_map)),
            "ctr": _to_ctr(_field(row, "ctr", column_map)),
            "position": _to_float(_field(row, "position", column_map)),
        })

    return records, len(records), rejected


def _field(row: dict[str, Any], canonical: str, column_map: dict[str, str]) -> str:
    """Значение каноничной колонки: по алиасу из column_map или по имени как есть."""
    header = column_map.get(canonical, canonical)
    value = row.get(header)
    if value is None:  # алиас не совпал — пробуем каноничное имя напрямую
        value = row.get(canonical)
    return "" if value is None else str(value)


def _to_int(value: str) -> int:
    """Целое из строки экспорта (пробелы/запятые как разделители тысяч). Пусто -> 0."""
    cleaned = (value or "").strip().replace(" ", "").replace(" ", "").replace(",", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _to_float(value: str) -> float | None:
    """Дробное из строки; запятая -> точка. Пусто/мусор -> None."""
    cleaned = (value or "").strip().replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_ctr(value: str) -> float | None:
    """CTR в долях. Экспорт GSC даёт проценты ('4.2%') — приводим к доле (0.042)."""
    raw = (value or "").strip()
    if not raw:
        return None
    pct = raw.endswith("%")
    num = _to_float(raw.rstrip("%"))
    if num is None:
        return None
    return num / 100 if pct else num


# ── Сверка суммы clicks с total_clicks_ui из meta.yaml ──────────────────────
def _clicks_ui_caveat(csv_path: Path, month: str, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Сравнить сумму clicks с total_clicks_ui из соседнего gsc_YYYY-MM.meta.yaml.

    Расхождение > CLICKS_UI_TOLERANCE -> caveat: существенная доля кликов
    скрыта порогом анонимизации Google или экспорт неполный.
    """
    total_ui = _read_total_clicks_ui(csv_path)
    if total_ui is None or total_ui <= 0:
        return None
    sum_clicks = sum(int(r.get("clicks") or 0) for r in records)
    deviation = abs(sum_clicks - total_ui) / total_ui
    if deviation <= CLICKS_UI_TOLERANCE:
        return None
    return {
        "month": month,
        "total_clicks_ui": total_ui,
        "sum_clicks": sum_clicks,
        "deviation_pct": round(deviation * 100, 1),
        "caveat": ("существенная доля кликов скрыта порогом анонимизации Google "
                   "или экспорт неполный"),
    }


def _read_total_clicks_ui(csv_path: Path) -> float | None:
    """Прочитать total_clicks_ui из gsc_YYYY-MM.meta.yaml рядом с CSV (если есть)."""
    meta_path = csv_path.with_name(csv_path.stem + ".meta.yaml")
    if not meta_path.exists():
        return None
    import yaml

    try:
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    value = meta.get("total_clicks_ui")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ── Отчёт валидации и манифест ──────────────────────────────────────────────
def _write_validation_report(
    out_dir, month_files, months, total_rows, accepted, rejected_reasons,
    device_missing_months, clicks_ui_caveats, column_map, fmt,
) -> dict[str, Any]:
    report = {
        "source_mode": "manual",
        "completeness": "unverified",
        "input_files": [str(p) for _m, p in month_files],
        "months": months,
        "total_rows": total_rows,
        "accepted": accepted,
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "device_missing_months": device_missing_months,
        "clicks_ui_caveats": clicks_ui_caveats,
        "column_map": column_map,
        "raw_format": fmt,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _record_manifest(paths, months, rows, fmt, device_missing_months, clicks_ui_caveats):
    from ..pipeline import manifest as manifest_mod

    date_from = f"{months[0]}-01" if months else ""
    date_to = f"{months[-1]}-01" if months else ""

    notes: list[str] = []
    if device_missing_months:
        notes.append(
            "нет разбивки по device в месяцах "
            f"{', '.join(device_missing_months)} — исключить их из посегментного "
            "разреза по устройству (S20)."
        )
    for c in clicks_ui_caveats:
        notes.append(
            f"{c['month']}: расхождение суммы clicks с total_clicks_ui "
            f"{c['deviation_pct']}% — {c['caveat']}."
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
            "notes": notes,
        },
    )
