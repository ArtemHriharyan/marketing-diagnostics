"""Экстрактор: импорт ручной выгрузки CRM из CSV (без API — локальный файл).

Контракт:
    Читает   — config.sources.crm_csv.path (по умолчанию inputs/crm_export.csv)
               и правила разбора config.crm_csv (column_map, status_map,
               date_formats, delimiter, hash_salt).
    Пишет    — data/raw/crm/leads.csv|parquet (нормализованные строки) +
               data/raw/crm/validation_report.json (принято/отброшено и почему)
               + manifest.json (canonical_tables: [crm]).
    Деградация — опционален; без CRM весь блок 6 (лид->сделка и т.д.) уходит
                 в degradation_report.
    LLM      — не используется. Файл заполняет клиент/аналитик вручную.

Правила нормализации:
    - Даты (lead_date) парсятся по набору форматов (config.crm_csv.date_formats
      + встроенный набор), с отбрасыванием времени.
    - Статусы приводятся через словарь config.crm_csv.status_map
      ({"успешно": "won", "отказ": "lost", ...}); неизвестные -> предупреждение,
      строка принимается со status=null.
    - phone_or_id: если это телефон (>=10 цифр) — нормализуем и кладём ТОЛЬКО
      SHA-256 хэш (для будущей склейки); сырой телефон в canonical НЕ переносится.
      Иначе значение трактуется как идентификатор и кладётся как есть.
    - Сумма (amount_rub) парсится с учётом пробелов/запятой-разделителя.
    - is_new_client приводится к bool по словарю синонимов.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.2.0"
SOURCE = "crm"
CANONICAL_TABLES = ["crm"]

# Каноничные колонки. column_map в конфиге сопоставляет их с заголовками CSV.
CANONICAL_COLUMNS = [
    "lead_date", "source", "phone_or_id", "status", "amount_rub", "is_new_client",
]
# Обязательные для приёма строки: без даты и без ключа лид бесполезен.
REQUIRED = ("lead_date", "phone_or_id")

# Колонки нормализованного сырья (сырой телефон НЕ выводится — только хэш).
RAW_FIELDS = [
    "lead_date", "source", "lead_kind", "lead_id", "phone_hash",
    "status", "status_raw", "amount_rub", "is_new_client",
]

# Встроенные форматы дат (config.crm_csv.date_formats добавляется в начало).
DEFAULT_DATE_FORMATS = [
    "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d",
    "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
]

# Синонимы для is_new_client.
_TRUE_TOKENS = {"1", "true", "да", "yes", "y", "new", "новый", "новая", "t"}
_FALSE_TOKENS = {"0", "false", "нет", "no", "n", "repeat", "повторный", "повтор", "f"}


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Проверка наличия и читаемости CSV-файла CRM."""
    path = _resolve_input_path(config, _paths_root=None)
    return path is not None and path.exists() and path.is_file()


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Провалидировать и нормализовать inputs/crm_export.csv в data/raw/crm/."""
    log = log or (lambda _msg: None)

    crm_cfg = config.get("crm_csv") or {}
    sources_cfg = (config.get("sources") or {}).get("crm_csv") or {}

    csv_path = _resolve_input_path(config, _paths_root=getattr(paths, "root", None))
    if csv_path is None or not csv_path.exists():
        raise C.SourceUnavailable(
            SOURCE, f"нет CSV-выгрузки CRM: {csv_path or sources_cfg.get('path')}"
        )

    column_map = crm_cfg.get("column_map") or {}
    status_map = _normalize_status_map(crm_cfg.get("status_map") or {})
    date_formats = list(crm_cfg.get("date_formats") or []) + DEFAULT_DATE_FORMATS
    salt = str(crm_cfg.get("hash_salt") or "")
    fmt = C.resolve_raw_format(sources_cfg)

    rows, headers, delimiter = _read_csv(csv_path, crm_cfg.get("delimiter"))
    log(f"{SOURCE}: {csv_path.name} — {len(rows)} строк, разделитель '{delimiter}'")

    accepted: list[dict[str, Any]] = []
    rejected_reasons: dict[str, int] = {}
    warnings: dict[str, int] = {}

    for row in rows:
        record, reason, warns = _normalize_row(
            row, column_map, status_map, date_formats, salt
        )
        for w in warns:
            warnings[w] = warnings.get(w, 0) + 1
        if reason:
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            continue
        accepted.append(record)

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    out = C.write_table(out_dir / "leads", accepted, RAW_FIELDS, fmt)

    report = {
        "input_file": str(csv_path),
        "delimiter": delimiter,
        "total_rows": len(rows),
        "accepted": len(accepted),
        "rejected": sum(rejected_reasons.values()),
        "rejected_reasons": rejected_reasons,
        "warnings": warnings,
        "columns_seen": headers,
        "column_map": column_map,
        "raw_format": fmt,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = _record_manifest(paths, accepted, report)
    log(
        f"{SOURCE}: принято {len(accepted)}, отброшено {report['rejected']} "
        f"({rejected_reasons or 'нет'}) -> {out.name}"
    )

    return {
        "source": SOURCE,
        "rows": len(accepted),
        "accepted": len(accepted),
        "rejected": report["rejected"],
        "rejected_reasons": rejected_reasons,
        "warnings": warnings,
        "raw_format": fmt,
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Разрешение пути входного файла ──────────────────────────────────────────
def _resolve_input_path(config: dict[str, Any], _paths_root: Any) -> Path | None:
    """Путь к CSV: абсолютный — как есть; относительный — от корня клиента."""
    sources_cfg = (config.get("sources") or {}).get("crm_csv") or {}
    raw_path = sources_cfg.get("path") or "inputs/crm_export.csv"
    p = Path(raw_path)
    if p.is_absolute():
        return p
    if _paths_root is not None:
        return Path(_paths_root) / raw_path
    return p  # относительный без известного корня (используется только в ping)


# ── Чтение CSV (кодировка + разделитель) ────────────────────────────────────
def _read_csv(path: Path, delimiter: str | None):
    """Прочитать CSV с автоопределением кодировки и разделителя.

    Возвращает (список словарей, список заголовков, использованный разделитель).
    Русские выгрузки из Excel часто в cp1251 и с ';' — учитываем оба случая.
    """
    import csv

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

    delim = delimiter or _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = list(reader)
    headers = list(reader.fieldnames or [])
    return rows, headers, delim


def _sniff_delimiter(text: str) -> str:
    """Определить разделитель по первой непустой строке (';' у RU-Excel частый)."""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {sep: first.count(sep) for sep in (";", "\t", ",")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


# ── Нормализация одной строки ───────────────────────────────────────────────
def _normalize_row(row, column_map, status_map, date_formats, salt):
    """Превратить сырую строку в нормализованную запись.

    Возвращает (record, reason|None, warnings). Если reason не None — строка
    отбраковывается, record частичный.
    """
    warns: list[str] = []

    lead_date = _parse_date(_field(row, "lead_date", column_map), date_formats)
    source = (_field(row, "source", column_map) or "").strip()
    raw_key = (_field(row, "phone_or_id", column_map) or "").strip()
    raw_status = (_field(row, "status", column_map) or "").strip()
    amount, amount_ok = _parse_amount(_field(row, "amount_rub", column_map))
    is_new = _parse_bool(_field(row, "is_new_client", column_map))

    kind, lead_id, phone_hash = _classify_key(raw_key, salt)
    status = status_map.get(raw_status.lower()) if raw_status else None
    if raw_status and status is None:
        warns.append("unknown_status")

    record = {
        "lead_date": lead_date,
        "source": source,
        "lead_kind": kind,
        "lead_id": lead_id,
        "phone_hash": phone_hash,
        "status": status,
        "status_raw": raw_status,
        "amount_rub": amount,
        "is_new_client": is_new,
    }

    if lead_date is None:
        return record, "bad_date", warns
    if not raw_key:
        return record, "missing_key", warns
    if not amount_ok:
        return record, "bad_amount", warns
    return record, None, warns


def _field(row: dict[str, Any], canonical: str, column_map: dict[str, str]) -> str:
    """Значение каноничной колонки: по алиасу из column_map или по имени как есть."""
    header = column_map.get(canonical, canonical)
    value = row.get(header)
    if value is None:  # алиас не совпал — пробуем каноничное имя напрямую
        value = row.get(canonical)
    return "" if value is None else str(value)


def _parse_date(value: str, formats: list[str]) -> str | None:
    """Дата в ISO (YYYY-MM-DD) по набору форматов; время отбрасывается."""
    value = (value or "").strip()
    if not value:
        return None
    head = re.split(r"[ T]", value, maxsplit=1)[0]
    for fmt in formats:
        try:
            return datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_amount(value: str) -> tuple[float | None, bool]:
    """Сумма в рублях -> (float|None, ok). Пусто — допустимо (None, True)."""
    value = (value or "").strip()
    if not value:
        return None, True
    cleaned = (
        value.replace(" ", "").replace(" ", "")
        .replace("₽", "").replace("руб.", "").replace("руб", "")
        .replace(",", ".")
    )
    try:
        return float(cleaned), True
    except ValueError:
        return None, False


def _parse_bool(value: str) -> bool | None:
    """is_new_client -> bool по словарю синонимов; неизвестное/пусто -> None."""
    token = (value or "").strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def _classify_key(raw_key: str, salt: str):
    """Классифицировать phone_or_id -> (kind, lead_id|None, phone_hash|None).

    Телефон (>=10 цифр) нормализуется и превращается в SHA-256 хэш; сырой
    телефон наружу не отдаётся. Иначе значение считается идентификатором.
    """
    if not raw_key:
        return None, None, None
    digits = re.sub(r"\D", "", raw_key)
    if len(digits) >= 10:
        normalized = _normalize_phone(digits)
        return "phone", None, _hash_phone(normalized, salt)
    return "id", raw_key, None


def _normalize_phone(digits: str) -> str:
    """Привести телефон к 11 цифрам с ведущей 7 (RU), где это применимо."""
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return digits


def _hash_phone(normalized: str, salt: str) -> str:
    """Стабильный SHA-256 хэш нормализованного телефона (для будущей склейки)."""
    return hashlib.sha256((salt + normalized).encode("utf-8")).hexdigest()


def _normalize_status_map(status_map: dict[str, Any]) -> dict[str, str]:
    """Ключи словаря статусов -> в нижний регистр без пробелов по краям."""
    return {str(k).strip().lower(): str(v) for k, v in status_map.items()}


def _record_manifest(paths, accepted, report) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    dates = [r["lead_date"] for r in accepted if r.get("lead_date")]
    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=(min(dates) if dates else ""),
        date_to=(max(dates) if dates else ""),
        rows=len(accepted), script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra={
            "input_file": report["input_file"],
            "rejected": report["rejected"],
            "rejected_reasons": report["rejected_reasons"],
            "warnings": report["warnings"],
        },
    )
