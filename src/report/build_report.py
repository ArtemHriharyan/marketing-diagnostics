"""Сборка итогового отчёта из утверждённых находок.

Контракт:
    Читает   — findings/approved/*.yaml (только утверждённые аналитиком),
               data/metrics/degradation_report.json, data/metrics/metrics_summary.json,
               config.yaml (ниша, гео, период — для заголовка), config/defaults.yaml
               (currency_round для вывода), config/report_glossary.yaml (термины).
    Пишет    — report/diagnostic_report.md. Раздел «Что не удалось проверить»
               берётся из degradation_report.skipped в неизменном виде (id/block/
               reason как есть, без перефразирования).
    Форматирование — здесь и только здесь: рубли округляются (currency_round),
               доли превращаются в проценты, разница долей — в процентные
               пункты (п.п.). БЕЗ LLM (текст находок уже утверждён аналитиком).
    Гейт     — вызывается оркестратором лишь при непустом findings/approved/.

Задача 7A: детерминированный рендерер-скелет. Раздел приложений-сносок и
повестка созвона с клиентом сюда намеренно не входят (следующая задача).

Приоритет находок: явного поля ``priority`` карточка находки (schemas.Finding)
не несёт. Каталог угроз v2 даёт статичные баллы «Критичность/Реальность» на
уровне check_id, но они не входят в машинный реестр config/methodology.yaml и
не видны отдельной находке (см. CLAUDE.md, «Схема ID проверок»: реестр —
единственный машинный источник). Поэтому сортировка построена на полях,
которые реально есть на находке: уверенность, затем денежная сумма — см.
``_priority_key``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..analyze import schemas as schemas_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

REPORT_FILENAME = "diagnostic_report.md"
GLOSSARY_FILENAME = "report_glossary.yaml"

# Бюджет отчёта — не больше 10 страниц. Заголовок, резюме, «что не удалось
# проверить» и глоссарий занимают ориентировочно 2 страницы фиксированного
# объёма; на находки (~1 страница на находку) остаётся оставшийся бюджет.
MAX_REPORT_FINDINGS = 8

_CONFIDENCE_RANK: dict[str, int] = {
    "HIGH": 0,
    schemas_mod.CLIENT_CONFIDENCE: 0,
    "MED": 1,
    "LOW": 2,
}
_BLOCK_ORDER: dict[str, int] = {"D": 0, "A": 1, "T": 2, "C": 3, "S": 4}


# ── Загрузка входов ──────────────────────────────────────────────────────
def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh) or {}


def load_approved_findings(findings_approved_dir: Path) -> list[dict[str, Any]]:
    """Загрузить утверждённые находки как словари, отсортированные по имени файла."""
    directory = Path(findings_approved_dir)
    if not directory.exists():
        return []
    findings: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        data = _load_yaml(path)
        if data:
            findings.append(data)
    return findings


def load_glossary(config_dir: Path | None = None) -> list[dict[str, str]]:
    """Загрузить термины config/report_glossary.yaml (список {term, definition})."""
    directory = Path(config_dir) if config_dir is not None else CONFIG_DIR
    data = _load_yaml(directory / GLOSSARY_FILENAME)
    return list(data.get("terms") or [])


# ── Сортировка находок ───────────────────────────────────────────────────
def _priority_key(finding: dict[str, Any]) -> tuple[int, int, float, int, str]:
    """Ключ сортировки: увереннее и дороже — выше; см. докстринг модуля."""
    confidence = finding.get("confidence") or "LOW"
    confidence_rank = _CONFIDENCE_RANK.get(confidence, 2)

    amount = finding.get("money_amount_rub")
    has_amount = 0 if isinstance(amount, (int, float)) else 1
    amount_rank = -abs(float(amount)) if isinstance(amount, (int, float)) else 0.0

    check_id = finding.get("check_id") or ""
    block_rank = _BLOCK_ORDER.get(check_id[:1], 99)

    return (confidence_rank, has_amount, amount_rank, block_rank, check_id)


def sort_approved_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отсортировать находки по приоритету (см. ``_priority_key``)."""
    return sorted(findings, key=_priority_key)


# ── Форматирование вывода (₽, %, п.п.) ───────────────────────────────────
def format_rub(value: float | None, currency_round: int = 0) -> str:
    """Отформатировать рублёвую сумму с округлением; None -> «в ₽ не оценить»."""
    if value is None:
        return "в ₽ не оценить"
    rounded = round(float(value), currency_round)
    if currency_round <= 0:
        text = f"{int(rounded):,}".replace(",", " ")
    else:
        text = f"{rounded:,.{currency_round}f}".replace(",", " ")
    return f"{text} ₽"


def format_percent(fraction: float, digits: int = 1) -> str:
    """Отформатировать долю (0.209) как проценты (20.9%)."""
    return f"{fraction * 100:.{digits}f}%"


def format_pp(fraction_diff: float, digits: int = 1) -> str:
    """Разница долей в процентных пунктах (п.п.) — НЕ проценты."""
    sign = "+" if fraction_diff >= 0 else ""
    return f"{sign}{fraction_diff * 100:.{digits}f} п.п."


def _format_money(finding: dict[str, Any], currency_round: int) -> str | None:
    category = finding.get("money_category")
    not_assessable = bool(finding.get("money_not_assessable"))
    if category is None and not not_assessable:
        return None
    label = schemas_mod.MONEY_CATEGORIES.get(category, category) if category else None
    value = format_rub(finding.get("money_amount_rub"), currency_round)
    return f"{label}: {value}" if label else value


# ── Разделы отчёта ───────────────────────────────────────────────────────
def _build_header(config: dict[str, Any]) -> list[str]:
    client = config.get("client") or {}
    name = client.get("name") or "клиент"
    window = config.get("data_window") or {}

    lines = [f"# Диагностика маркетинга — {name}", ""]

    meta: list[str] = []
    if client.get("niche"):
        meta.append(f"ниша: {client['niche']}")
    if client.get("geo"):
        meta.append(f"гео: {client['geo']}")
    date_from, date_to = window.get("date_from"), window.get("date_to")
    if date_from and date_to:
        meta.append(f"период: {date_from}..{date_to}")
    if meta:
        lines.append("_" + "; ".join(meta) + "_")
        lines.append("")

    lines.append(
        "_Черновой рендер (задача 7A): скелет отчёта без приложений-сносок "
        "и повестки созвона._"
    )
    lines.append("")
    return lines


def _build_summary_section(
    findings: list[dict[str, Any]],
    degradation: dict[str, Any],
    metrics_summary: dict[str, Any],
) -> str:
    counts = degradation.get("counts") or metrics_summary.get("counts") or {}
    total, runnable, skipped = counts.get("total"), counts.get("runnable"), counts.get("skipped")

    lines = ["## Резюме", ""]
    if total:
        lines.append(
            f"Проверок реестра выполнено: {runnable}/{total} "
            f"({format_percent(runnable / total)}); не выполнено: {skipped}."
        )
    lines.append(f"Утверждённых находок в этом отчёте: {len(findings)}.")
    lines.append("")
    return "\n".join(lines)


def _format_finding_md(finding: dict[str, Any], currency_round: int) -> str:
    check_id, name = finding.get("check_id", ""), finding.get("name", "")
    lines = [f"### {check_id} — {name}", ""]

    meta_parts = []
    for key, label in (("status", "статус"), ("confidence", "уверенность"),
                        ("segment", "сегмент"), ("period", "период")):
        if finding.get(key):
            meta_parts.append(f"{label}: {finding[key]}")
    if meta_parts:
        lines.append("_" + "; ".join(meta_parts) + "_")
        lines.append("")

    field_labels = (
        ("evidence", "Доказательство"),
        ("what_is_distorted", "Что искажается"),
    )
    for key, label in field_labels:
        if finding.get(key):
            lines.append(f"**{label}:** {finding[key]}")

    money_line = _format_money(finding, currency_round)
    if money_line:
        lines.append(f"**Деньги:** {money_line}")

    field_labels_tail = (
        ("control_metric", "Контрольная метрика"),
        ("recommended_action", "Рекомендация"),
        ("how_to_measure", "Как измерить результат"),
        ("what_cannot_be_concluded", "Что нельзя заключить"),
    )
    for key, label in field_labels_tail:
        if finding.get(key):
            lines.append(f"**{label}:** {finding[key]}")

    assumptions = finding.get("assumptions") or []
    if assumptions:
        lines.append("**Допущения:**")
        for item in assumptions:
            lines.append(f"- {item}")

    lines.append("")
    return "\n".join(lines)


def _build_findings_section(findings: list[dict[str, Any]], currency_round: int) -> str:
    lines = ["## Ключевые находки", ""]
    if not findings:
        lines.append("Утверждённых находок нет.")
        lines.append("")
        return "\n".join(lines)

    shown = findings[:MAX_REPORT_FINDINGS]
    if len(shown) < len(findings):
        lines.append(
            f"_Показаны {len(shown)} находок из {len(findings)} утверждённых "
            "(лимит рендера — бюджет отчёта ≤10 страниц)._"
        )
        lines.append("")

    for finding in shown:
        lines.append(_format_finding_md(finding, currency_round))
    return "\n".join(lines)


def _build_skipped_section(skipped: list[dict[str, Any]]) -> str:
    lines = ["## Что не удалось проверить", ""]
    if not skipped:
        lines.append("Все проверки реестра выполнены при текущих источниках.")
    else:
        for item in skipped:
            block_part = f" (блок {item['block']})" if item.get("block") is not None else ""
            lines.append(f"- **{item.get('id', '?')}**{block_part}: {item.get('reason', '')}")
    lines.append("")
    return "\n".join(lines)


def _build_glossary_section(glossary: list[dict[str, str]]) -> str:
    lines = ["## Глоссарий", ""]
    for entry in glossary:
        lines.append(f"- **{entry.get('term', '')}** — {entry.get('definition', '')}")
    lines.append("")
    return "\n".join(lines)


# ── Точка входа слоя ─────────────────────────────────────────────────────
def build(paths: Any, config: dict[str, Any], defaults: dict[str, Any]) -> str:
    """Собрать отчёт в report/diagnostic_report.md; вернуть путь к файлу."""
    findings = sort_approved_findings(load_approved_findings(paths.findings_approved))

    metrics_dir = Path(paths.metrics)
    degradation = _load_json(metrics_dir / "degradation_report.json")
    metrics_summary = _load_json(metrics_dir / "metrics_summary.json")
    glossary = load_glossary()

    currency_round = int(defaults.get("currency_round") or 0)

    lines: list[str] = []
    lines.extend(_build_header(config))
    lines.append(_build_summary_section(findings, degradation, metrics_summary))
    lines.append(_build_findings_section(findings, currency_round))
    lines.append(_build_skipped_section(degradation.get("skipped") or []))
    lines.append(_build_glossary_section(glossary))

    report_dir = Path(paths.report)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / REPORT_FILENAME
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return str(out_path)
