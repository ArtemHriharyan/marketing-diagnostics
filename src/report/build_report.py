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

Задача 7B: страница «Вердикт» (первая страница отчёта) — три главных
разрыва (топ утверждённых находок по тому же приоритету, что и раздел
находок) + вердикт по блоку 0 (доверие к данным, из degradation.skipped,
без пересчёта) + агрегат «SEO MED-cap» из уже посчитанного
metrics_summary["seo_confidence_cap"] (см. src/compute/common.py,
_seo_confidence_cap_summary — report только форматирует готовую долю,
не считает её заново).

Приоритет находок: явного поля ``priority`` карточка находки (schemas.Finding)
не несёт. Каталог угроз v2 даёт статичные баллы «Критичность/Реальность» на
уровне check_id, но они не входят в машинный реестр config/methodology.yaml и
не видны отдельной находке (см. CLAUDE.md, «Схема ID проверок»: реестр —
единственный машинный источник). Поэтому сортировка построена на полях,
которые реально есть на находке: уверенность, затем денежная сумма — см.
``_priority_key``.

Задача 7C: план действий + assignee + приложение (footnotes), закрывает
разрывы, намеренно оставленные 7A. Решения, не заданные явно источниками
истины (задокументировано, не угадано молча):
    * ``schemas.Finding`` не несёт поля длительности/трудоёмкости — в каталоге
      v2 и methodology-v2.md такого поля тоже нет. План «2 недели»/«2 месяца»
      поэтому строится на уже существующем и согласованном с остальным
      отчётом порядке приоритета (``_priority_key`` — та же сортировка, что
      «Три главных разрыва»): первые находки с рекомендацией — в план на
      2 недели (лимит ``MAX_ACTION_PLAN_2W``), следующие — на 2 месяца
      (лимит ``MAX_ACTION_PLAN_2M``). Не выдаётся за оценку трудоёмкости —
      это порядок приоритета, а не срок исполнения по существу.
    * ``schemas.Finding`` не несёт поля ``assignee`` (его нет ни в одном
      источнике истины). Карточка YAML может нести необязательный ключ
      ``assignee`` (аналитик проставляет вручную при утверждении находки);
      при отсутствии — «уточнить» (см. ``_assignee``), никогда не выдумывается.
    * Приложение выносит за пределы бюджета ≤10 страниц основного текста:
      находки уровня LOW (гипотезы — раздел «Ключевые находки» их больше не
      показывает) и находки сверх ``MAX_REPORT_FINDINGS`` — вместо того, чтобы
      молча обрезаться пометкой без списка (как было в 7A). Отдельно —
      подраздел «SEO-ядро — не посчитано»: те же элементы
      ``degradation.skipped``, что и в «Что не удалось проверить», но
      отфильтрованные по блоку 4 (S, каталог v2 §9) для быстрой навигации
      клиента к SEO-разрывам без прочтения всего списка.

Задача 7D: финальная сборка — приложения-таблицы (CSV), сноски на них из
основного текста и повестка звонка с клиентом. Решения, не заданные явно
источниками истины (задокументировано, не угадано молча):
    * ``llm_notes`` (вопросы к находке, которые нужно поднять на звонке) не
      существует ни в ``schemas.Finding``, ни в каталоге v2, ни в
      methodology-v2.md — ни один источник истины его не описывает. Заведён
      по тому же прецеденту, что и ``assignee`` (задача 7C, см. ``_assignee``):
      необязательный ключ карточки YAML вне формальной схемы находки, который
      report только читает через ``.get()`` (``_llm_notes``) и никогда не
      придумывает — нет ключа или он пуст → на звонке по этой находке
      открытых вопросов нет.
    * Сноски ``[1]``/``[2]``/``[3]`` — фиксированные номера у трёх машиночитаемых
      таблиц приложения (``_build_appendix_tables``): дополнительные находки,
      непройденные проверки реестра, непосчитанное SEO-ядро (подмножество
      второй). Схема статична (не автонумеруется по мере появления сносок в
      тексте), т.к. ровно эти три таблицы пишутся при каждой сборке отчёта.
    * Повестка звонка (``oral_review_agenda.md``) — бюджет 60 минут разложен
      явно на вступление/находки/вопросы (``ORAL_REVIEW_MINUTES_*``), лимит
      находок (``MAX_ORAL_REVIEW_FINDINGS = 5``) выведен из бюджета минут, а
      не назван отдельной константой без обоснования. Находки берутся из
      той же отсортированной по приоритету последовательности, что и «Три
      главных разрыва»/план действий (``_priority_key``) — топ-5, а не топ-3
      вердикта, т.к. под звонок отведено больше времени, чем под страницу
      вердикта.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from ..analyze import schemas as schemas_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

REPORT_FILENAME = "diagnostic_report.md"
GLOSSARY_FILENAME = "report_glossary.yaml"

# Бюджет отчёта — не больше 10 страниц. Заголовок, резюме, план действий,
# «что не удалось проверить» и глоссарий занимают ориентировочно 2 страницы
# фиксированного объёма; на находки (~1 страница на находку) остаётся
# оставшийся бюджет. Находки сверх лимита и уровня LOW уходят в «Приложение»
# (задача 7C) — в 10-страничный бюджет основного текста не считаются.
MAX_REPORT_FINDINGS = 8

# Три главных разрыва на первой странице (вердикт) — top-N той же
# отсортированной последовательности, что и весь раздел находок.
MAX_VERDICT_GAPS = 3

# План действий (задача 7C) — короткие списки, лимиты по числу пунктов, не
# по трудоёмкости (см. докстринг модуля). Оба списка идут подряд из одной и
# той же отсортированной последовательности находок с непустой рекомендацией.
MAX_ACTION_PLAN_2W = 7
MAX_ACTION_PLAN_2M = 5

# Задача 7D: приложения-таблицы (CSV) + сноски на них ────────────────────
APPENDIX_TABLES_DIRNAME = "appendix_tables"
FINDINGS_APPENDIX_CSV = "findings_appendix.csv"
SKIPPED_CHECKS_CSV = "skipped_checks.csv"
SEO_CORE_CSV = "seo_core_gaps.csv"

# Повестка звонка с клиентом — бюджет 60 минут (см. докстринг модуля).
ORAL_REVIEW_AGENDA_FILENAME = "oral_review_agenda.md"
ORAL_REVIEW_MINUTES_TOTAL = 60
ORAL_REVIEW_MINUTES_INTRO = 5
ORAL_REVIEW_MINUTES_WRAP = 5
ORAL_REVIEW_MINUTES_PER_FINDING = 10
MAX_ORAL_REVIEW_FINDINGS = (
    ORAL_REVIEW_MINUTES_TOTAL - ORAL_REVIEW_MINUTES_INTRO - ORAL_REVIEW_MINUTES_WRAP
) // ORAL_REVIEW_MINUTES_PER_FINDING

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


def _assignee(finding: dict[str, Any]) -> str:
    """Ответственный за находку; «уточнить», если аналитик не проставил (см. докстринг модуля)."""
    value = finding.get("assignee")
    return value.strip() if isinstance(value, str) and value.strip() else "уточнить"


def _llm_notes(finding: dict[str, Any]) -> list[str]:
    """Вопросы к находке для звонка с клиентом (задача 7D) — необязательный
    ключ ``llm_notes`` карточки YAML вне ``schemas.Finding`` (тот же приём,
    что ``assignee``, см. докстринг модуля): нет ключа/пусто -> вопросов нет,
    не выдумываются.
    """
    value = finding.get("llm_notes")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


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
        f"_Повестка звонка с клиентом — отдельным файлом: `{ORAL_REVIEW_AGENDA_FILENAME}`._"
    )
    lines.append("")
    return lines


# ── Страница 1: вердикт (задача 7B) ──────────────────────────────────────
def _format_gap_line(rank: int, finding: dict[str, Any], currency_round: int) -> str:
    check_id, name = finding.get("check_id", ""), finding.get("name", "")
    money_line = _format_money(finding, currency_round) or format_rub(None)
    return f"{rank}. **{check_id} — {name}** — {money_line}"


def _build_top_gaps(findings: list[dict[str, Any]], currency_round: int) -> list[str]:
    lines = ["### Три главных разрыва", ""]
    if not findings:
        lines.append("Утверждённых находок нет — главные разрывы не определены.")
        lines.append("")
        return lines
    for rank, finding in enumerate(findings[:MAX_VERDICT_GAPS], start=1):
        lines.append(_format_gap_line(rank, finding, currency_round))
    lines.append("")
    return lines


def _build_data_verdict(degradation: dict[str, Any]) -> list[str]:
    lines = ["### Вердикт по данным (блок 0)", ""]
    block0_skipped = [
        item for item in (degradation.get("skipped") or []) if item.get("block") == 0
    ]
    if not block0_skipped:
        lines.append(
            "Блок 0 (доверие к данным) пройден без ограничений при текущих источниках."
        )
    else:
        lines.append("Ограничения доверия к данным (перенесены как есть):")
        for item in block0_skipped:
            lines.append(f"- **{item.get('id', '?')}**: {item.get('reason', '')}")
    lines.append("")
    return lines


def _build_seo_med_cap(metrics_summary: dict[str, Any]) -> list[str]:
    lines = ["### SEO — потолок уверенности MED", ""]
    seo_cap = metrics_summary.get("seo_confidence_cap") or {}
    runnable_count = seo_cap.get("runnable_count")
    med_cap_count = seo_cap.get("med_cap_count")
    med_cap_share = seo_cap.get("med_cap_share")
    if not runnable_count:
        lines.append("Нет выполнимых проверок блока SEO (S) при текущих источниках.")
    else:
        lines.append(
            f"{med_cap_count} из {runnable_count} выполнимых проверок блока SEO (S) "
            f"с потолком уверенности MED ({format_percent(med_cap_share or 0.0)})."
        )
    lines.append("")
    return lines


def _build_verdict_section(
    findings: list[dict[str, Any]],
    degradation: dict[str, Any],
    metrics_summary: dict[str, Any],
    currency_round: int,
) -> str:
    lines = ["## Вердикт", ""]
    lines.extend(_build_top_gaps(findings, currency_round))
    lines.extend(_build_data_verdict(degradation))
    lines.extend(_build_seo_med_cap(metrics_summary))
    return "\n".join(lines)


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


# ── План действий (задача 7C) ────────────────────────────────────────────
def _format_action_line(rank: int, finding: dict[str, Any]) -> str:
    check_id = finding.get("check_id", "")
    action = finding.get("recommended_action") or ""
    return f"{rank}. **{check_id}** — {action} _(ответственный: {_assignee(finding)})_"


def _build_action_plan_section(findings: list[dict[str, Any]]) -> str:
    actionable = [f for f in findings if (f.get("recommended_action") or "").strip()]
    two_week = actionable[:MAX_ACTION_PLAN_2W]
    two_month = actionable[MAX_ACTION_PLAN_2W:MAX_ACTION_PLAN_2W + MAX_ACTION_PLAN_2M]

    lines = ["## План действий", ""]

    lines.append("### 2 недели")
    lines.append("")
    if not two_week:
        lines.append("Утверждённых находок с рекомендацией нет — план не сформирован.")
    else:
        for rank, finding in enumerate(two_week, start=1):
            lines.append(_format_action_line(rank, finding))
    lines.append("")

    lines.append("### 2 месяца")
    lines.append("")
    if not two_month:
        lines.append("Дополнительных действий на горизонт 2 месяцев не выявлено.")
    else:
        for rank, finding in enumerate(two_month, start=1):
            lines.append(_format_action_line(rank, finding))
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
    meta_parts.append(f"ответственный: {_assignee(finding)}")
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


def _is_low_confidence(finding: dict[str, Any]) -> bool:
    return (finding.get("confidence") or "LOW") == "LOW"


def split_findings_for_report(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Разбить отсортированные находки: тело отчёта (≤``MAX_REPORT_FINDINGS``,
    без LOW) + приложение (LOW-находки и находки сверх лимита, см. докстринг
    модуля, задача 7C). Порядок сохраняется — обе части остаются частью той
    же отсортированной по приоритету последовательности.
    """
    main_candidates = [f for f in findings if not _is_low_confidence(f)]
    shown = main_candidates[:MAX_REPORT_FINDINGS]
    overflow = main_candidates[MAX_REPORT_FINDINGS:]
    low_findings = [f for f in findings if _is_low_confidence(f)]
    return shown, overflow + low_findings


def _build_findings_section(
    shown: list[dict[str, Any]],
    main_candidate_count: int,
    has_any_approved: bool,
    currency_round: int,
) -> str:
    lines = ["## Ключевые находки", ""]
    if not shown:
        if has_any_approved:
            lines.append(
                "Находок уровня HIGH/MED/client-HIGH нет — утверждённые находки "
                "уровня LOW вынесены в приложение (см. «Приложение»)."
            )
        else:
            lines.append("Утверждённых находок нет.")
        lines.append("")
        return "\n".join(lines)

    if len(shown) < main_candidate_count:
        lines.append(
            f"_Показаны {len(shown)} находок из {main_candidate_count} утверждённых "
            "уровня HIGH/MED/client-HIGH (лимит рендера — бюджет отчёта ≤10 страниц; "
            "остальное — в приложении)._"
        )
        lines.append("")

    for finding in shown:
        lines.append(_format_finding_md(finding, currency_round))
    return "\n".join(lines)


def _build_skipped_section(skipped: list[dict[str, Any]]) -> str:
    lines = ["## Что не удалось проверить и почему [2]", ""]
    if not skipped:
        lines.append("Все проверки реестра выполнены при текущих источниках.")
    else:
        for item in skipped:
            block_part = f" (блок {item['block']})" if item.get("block") is not None else ""
            lines.append(f"- **{item.get('id', '?')}**{block_part}: {item.get('reason', '')}")
    lines.append("")
    return "\n".join(lines)


# ── Приложение (задача 7C) ───────────────────────────────────────────────
def _format_appendix_finding_line(finding: dict[str, Any], currency_round: int) -> str:
    check_id, name = finding.get("check_id", ""), finding.get("name", "")
    confidence = finding.get("confidence") or "LOW"
    money_line = _format_money(finding, currency_round) or format_rub(None)
    action = finding.get("recommended_action") or "рекомендация не задана"
    return (
        f"- **{check_id} — {name}** (уверенность: {confidence}; "
        f"ответственный: {_assignee(finding)}) — {money_line}. "
        f"Рекомендация: {action}"
    )


def _build_appendix_findings(appendix_findings: list[dict[str, Any]], currency_round: int) -> list[str]:
    lines = [
        "### Дополнительные находки (уровень LOW и сверх лимита раздела) [1]", "",
    ]
    if not appendix_findings:
        lines.append("Дополнительных находок нет.")
    else:
        for finding in appendix_findings:
            lines.append(_format_appendix_finding_line(finding, currency_round))
    lines.append("")
    return lines


def _build_appendix_seo_core(degradation: dict[str, Any]) -> list[str]:
    lines = ["### SEO-ядро — не посчитано [3]", ""]
    seo_skipped = [
        item for item in (degradation.get("skipped") or []) if item.get("block") == _BLOCK_ORDER["S"]
    ]
    if not seo_skipped:
        lines.append("Все проверки блока SEO (S) выполнены при текущих источниках.")
    else:
        for item in seo_skipped:
            lines.append(f"- **{item.get('id', '?')}**: {item.get('reason', '')}")
    lines.append("")
    return lines


def _build_appendix_section(
    appendix_findings: list[dict[str, Any]],
    degradation: dict[str, Any],
    currency_round: int,
) -> str:
    lines = ["## Приложение", ""]
    lines.extend(_build_appendix_findings(appendix_findings, currency_round))
    lines.extend(_build_appendix_seo_core(degradation))
    return "\n".join(lines)


def _build_glossary_section(glossary: list[dict[str, str]]) -> str:
    lines = ["## Глоссарий", ""]
    for entry in glossary:
        lines.append(f"- **{entry.get('term', '')}** — {entry.get('definition', '')}")
    lines.append("")
    return "\n".join(lines)


# ── Приложения-таблицы (CSV) + сноски (задача 7D) ─────────────────────────
def _write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _build_appendix_tables(
    report_dir: Path,
    appendix_findings: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    currency_round: int,
) -> None:
    """Машиночитаемые CSV-таблицы приложения — на них ссылаются сноски
    [1]/[2]/[3] основного текста (см. докстринг модуля). Пишутся всегда,
    даже пустыми (только заголовок), т.к. сноски на них ссылаются безусловно.
    """
    tables_dir = report_dir / APPENDIX_TABLES_DIRNAME

    _write_csv(
        tables_dir / FINDINGS_APPENDIX_CSV,
        ("check_id", "name", "confidence", "money_category", "money_amount_rub", "assignee", "recommended_action"),
        [
            (
                f.get("check_id", ""),
                f.get("name", ""),
                f.get("confidence", ""),
                f.get("money_category") or "",
                f.get("money_amount_rub") if isinstance(f.get("money_amount_rub"), (int, float)) else "",
                _assignee(f),
                f.get("recommended_action", "") or "",
            )
            for f in appendix_findings
        ],
    )

    _write_csv(
        tables_dir / SKIPPED_CHECKS_CSV,
        ("id", "block", "reason"),
        [(item.get("id", ""), item.get("block", ""), item.get("reason", "")) for item in skipped],
    )

    seo_skipped = [item for item in skipped if item.get("block") == _BLOCK_ORDER["S"]]
    _write_csv(
        tables_dir / SEO_CORE_CSV,
        ("id", "reason"),
        [(item.get("id", ""), item.get("reason", "")) for item in seo_skipped],
    )


def _build_footnotes_section() -> str:
    lines = ["## Сноски", ""]
    lines.append(
        f"[1]: `{APPENDIX_TABLES_DIRNAME}/{FINDINGS_APPENDIX_CSV}` — полный список "
        "дополнительных находок (уровень LOW и сверх лимита раздела) в машиночитаемом виде."
    )
    lines.append(
        f"[2]: `{APPENDIX_TABLES_DIRNAME}/{SKIPPED_CHECKS_CSV}` — полный список "
        "непройденных проверок реестра («Что не удалось проверить и почему»)."
    )
    lines.append(
        f"[3]: `{APPENDIX_TABLES_DIRNAME}/{SEO_CORE_CSV}` — непосчитанные проверки "
        "SEO-ядра (блок S, каталог v2 §9), подмножество сноски [2]."
    )
    lines.append("")
    return "\n".join(lines)


# ── Повестка звонка с клиентом (задача 7D) ────────────────────────────────
def _format_oral_review_finding(rank: int, finding: dict[str, Any], currency_round: int) -> str:
    check_id, name = finding.get("check_id", ""), finding.get("name", "")
    money_line = _format_money(finding, currency_round) or format_rub(None)
    lines = [
        f"{rank}. **{check_id} — {name}** ({ORAL_REVIEW_MINUTES_PER_FINDING} мин) — {money_line}"
    ]
    for question in _llm_notes(finding):
        lines.append(f"   - Вопрос: {question}")
    return "\n".join(lines)


def build_oral_review_agenda(
    findings: list[dict[str, Any]], config: dict[str, Any], currency_round: int
) -> str:
    """Повестка звонка с клиентом на ``ORAL_REVIEW_MINUTES_TOTAL`` минут: топ-
    ``MAX_ORAL_REVIEW_FINDINGS`` находок по тому же приоритету, что и «Три
    главных разрыва»/план действий (``_priority_key``), с вопросами из
    ``llm_notes`` там, где они проставлены (см. ``_llm_notes`` — необязательное
    поле, не выдумывается при отсутствии).
    """
    client = config.get("client") or {}
    name = client.get("name") or "клиент"

    lines = [f"# Повестка созвона — {name} ({ORAL_REVIEW_MINUTES_TOTAL} мин)", ""]

    lines.append(f"## Вступление ({ORAL_REVIEW_MINUTES_INTRO} мин)")
    lines.append("")
    lines.append("Контекст диагностики, формат звонка, что клиент получит на выходе.")
    lines.append("")

    top = findings[:MAX_ORAL_REVIEW_FINDINGS]
    lines.append(f"## Главные находки ({len(top) * ORAL_REVIEW_MINUTES_PER_FINDING} мин)")
    lines.append("")
    if not top:
        lines.append("Утверждённых находок нет — раздел находок не сформирован.")
    else:
        for rank, finding in enumerate(top, start=1):
            lines.append(_format_oral_review_finding(rank, finding, currency_round))
    lines.append("")

    lines.append(f"## Вопросы и дальнейшие шаги ({ORAL_REVIEW_MINUTES_WRAP} мин)")
    lines.append("")
    all_questions = [q for f in top for q in _llm_notes(f)]
    if all_questions:
        lines.append("Свод открытых вопросов к находкам (см. также построчно выше):")
        for question in all_questions:
            lines.append(f"- {question}")
    else:
        lines.append("Открытых вопросов к находкам нет — переходим сразу к следующим шагам.")
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

    shown_findings, appendix_findings = split_findings_for_report(findings)
    main_candidate_count = len(findings) - sum(1 for f in findings if _is_low_confidence(f))

    lines: list[str] = []
    lines.extend(_build_header(config))
    lines.append(_build_verdict_section(findings, degradation, metrics_summary, currency_round))
    lines.append(_build_summary_section(findings, degradation, metrics_summary))
    lines.append(_build_action_plan_section(findings))
    lines.append(
        _build_findings_section(shown_findings, main_candidate_count, bool(findings), currency_round)
    )
    lines.append(_build_skipped_section(degradation.get("skipped") or []))
    lines.append(_build_appendix_section(appendix_findings, degradation, currency_round))
    lines.append(_build_footnotes_section())
    lines.append(_build_glossary_section(glossary))

    report_dir = Path(paths.report)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / REPORT_FILENAME
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    _build_appendix_tables(
        report_dir, appendix_findings, degradation.get("skipped") or [], currency_round
    )

    agenda_text = build_oral_review_agenda(findings, config, currency_round)
    (report_dir / ORAL_REVIEW_AGENDA_FILENAME).write_text(
        agenda_text.rstrip() + "\n", encoding="utf-8"
    )

    return str(out_path)
