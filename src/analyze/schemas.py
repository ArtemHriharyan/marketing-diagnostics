"""Типизированная схема находки слоя analyze (задача 6A) + её валидация.

Контракт полей — единая карточка находки, каталог v2 §12
(`catalog-proveryaemyh-marketingovyh-ugroz-v2.md`). Здесь ТОЛЬКО форма и
проверки формы: сам текст находки формирует LLM (src/analyze/draft_findings.py,
другая задача) — в этом модуле нет ни одного обращения к API (принцип 3
CLAUDE.md).

Четыре денежные категории (каталог v2, правило 15) заданы здесь напрямую по
тому же первоисточнику, что и `src.compute.money_frame.MONEY_CATEGORIES` —
не импортируются оттуда, чтобы схема находки (лёгкий модуль) не тянула за
собой зависимости слоя compute (duckdb и т.д.). Значения ОБЯЗАНЫ совпадать —
при правке одного списка сверять со вторым.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..pipeline import degradation as degradation_mod


# ── Денежные категории (каталог v2, правило 15) — см. докстринг модуля ──────
MONEY_CATEGORIES: dict[str, str] = {
    "direct_confirmed_spend": "прямой подтверждённый расход",
    "potentially_excludable_spend": "потенциально исключаемый расход",
    "cpa_reduction_same_budget": "снижение CPA при том же бюджете",
    "equivalent_additional_conversions": "эквивалент дополнительных конверсий",
}
MONEY_CATEGORY_VALUES: frozenset[str] = frozenset(MONEY_CATEGORIES)

# Формат нового ID реестра: буква блока (D/A/T/C/S) + двузначный номер.
# Старые числовые legacy_id ("0.1", "2.2"…) сюда не подходят намеренно —
# в analyze они не участвуют (см. CLAUDE.md, «Схема ID проверок»).
CHECK_ID_FORMAT = re.compile(r"^[DATCS]\d{2}$")

STATUS_VALUES: frozenset[str] = frozenset({
    "подтверждена",
    "частично подтверждена",
    "не подтверждена",
    "данных недостаточно",
})

# HIGH/MED/LOW — стандартные потолки (данные + источник, см. degradation.py).
# client-HIGH — факт со слов клиента; не подчиняется потолку источника
# (marketing-diagnostics-methodology-v2.md §10.3).
CLIENT_CONFIDENCE = "client-HIGH"
CONFIDENCE_VALUES: frozenset[str] = frozenset({"HIGH", "MED", "LOW", CLIENT_CONFIDENCE})

MAX_FINDINGS_PER_RUN = 12


class FindingValidationError(ValueError):
    """Находка или пакет находок нарушает структурный контракт слоя analyze."""


def is_valid_check_id_format(check_id: str) -> bool:
    """Формат ID нового реестра: буква блока + двузначный номер (напр. A07, S27)."""
    return bool(CHECK_ID_FORMAT.match(check_id or ""))


def known_check_ids(methodology: dict[str, Any]) -> frozenset[str]:
    """Множество ID из реестра `config/methodology.yaml` (уже распарсенного)."""
    return frozenset(
        c.get("id") for c in (methodology.get("checks") or []) if c.get("id")
    )


@dataclass
class Finding:
    """Единая карточка находки (каталог v2 §12) — типизированный аналог YAML.

    Поля называются по-английски (принцип 8 CLAUDE.md — идентификаторы кода на
    английском), но их СОДЕРЖИМОЕ (тексты находки) — на русском.
    """

    check_id: str
    name: str
    status: str
    confidence: str
    significant: bool
    period: str
    data_source: str
    evidence: str
    what_is_distorted: str
    recommended_action: str
    how_to_measure: str
    what_cannot_be_concluded: str
    segment: str | None = None
    control_metric: str | None = None
    money_category: str | None = None
    money_amount_rub: float | None = None
    money_not_assessable: bool = False
    assumptions: list[str] = field(default_factory=list)
    source_check_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Порядок ключей — как в карточке каталога v2 §12 (читаемый YAML/JSON на выходе).
FINDING_FIELD_ORDER: tuple[str, ...] = (
    "check_id", "name", "status", "confidence", "significant", "period",
    "segment", "data_source", "evidence", "control_metric",
    "what_is_distorted", "money_category", "money_amount_rub",
    "money_not_assessable", "assumptions", "recommended_action",
    "how_to_measure", "what_cannot_be_concluded", "source_check_ids",
)


def finding_to_ordered_dict(finding: Finding) -> dict[str, Any]:
    """Тот же словарь, что `finding.to_dict()`, но в порядке карточки каталога v2."""
    data = finding.to_dict()
    return {key: data[key] for key in FINDING_FIELD_ORDER}


def validate_finding(
    finding: Finding,
    *,
    known_ids: frozenset[str] | None = None,
    confidence_cap: str | None = None,
) -> list[str]:
    """Вернуть список нарушений контракта находки (пустой список — находка валидна).

    Ничего не бросает — решение о том, что делать с нарушениями (отбросить
    находку, остановить прогон), принимает вызывающий код.

    ``confidence_cap`` — потолок источника проверки из
    ``degradation_report.checks[*].confidence_cap`` (второй потолок; первый —
    потолок выборки — уже применён к числу на уровне compute, см. поле
    ``confidence`` самой находки). ``client-HIGH`` этому потолку не подчиняется.
    """
    errors: list[str] = []

    if not is_valid_check_id_format(finding.check_id):
        errors.append(
            f"check_id={finding.check_id!r} не соответствует формату нового реестра "
            "(буква блока D/A/T/C/S + двузначный номер, напр. A07)"
        )
    elif known_ids is not None and finding.check_id not in known_ids:
        errors.append(
            f"check_id={finding.check_id!r} отсутствует в реестре config/methodology.yaml"
        )

    if finding.status not in STATUS_VALUES:
        errors.append(
            f"status={finding.status!r} не входит в допустимые значения {sorted(STATUS_VALUES)}"
        )

    if finding.confidence not in CONFIDENCE_VALUES:
        errors.append(
            f"confidence={finding.confidence!r} не входит в допустимые значения "
            f"{sorted(CONFIDENCE_VALUES)}"
        )

    if not finding.significant:
        errors.append(
            "significant=false запрещено: незначимая находка (p >= significance_alpha "
            "либо выборка < min_sample_visits) не публикуется ни при каком confidence "
            "(каталог v2, п.14; methodology-v2.md §10.4)"
        )

    if (
        confidence_cap
        and finding.confidence in CONFIDENCE_VALUES
        and finding.confidence != CLIENT_CONFIDENCE
        and degradation_mod.min_confidence(finding.confidence, confidence_cap) != finding.confidence
    ):
        errors.append(
            f"confidence={finding.confidence!r} превышает потолок источника "
            f"confidence_cap={confidence_cap!r} — LLM может только понижать уверенность"
        )

    if finding.money_category is not None and finding.money_category not in MONEY_CATEGORY_VALUES:
        errors.append(
            f"money_category={finding.money_category!r} не входит в 4 категории каталога v2 "
            f"(правило 15): {sorted(MONEY_CATEGORY_VALUES)}"
        )

    if finding.money_category is not None and finding.money_not_assessable:
        errors.append(
            "money_category задана вместе с money_not_assessable=true — поля взаимоисключающие "
            '(находка либо несёт ровно одну денежную категорию, либо помечена «в ₽ не оценить»)'
        )

    if finding.money_amount_rub is not None and finding.money_category is None:
        errors.append(
            "money_amount_rub задан без money_category — денежная сумма обязана нести категорию"
        )

    for field_name in (
        "name", "period", "data_source", "evidence", "what_is_distorted",
        "recommended_action", "how_to_measure", "what_cannot_be_concluded",
    ):
        if not (getattr(finding, field_name) or "").strip():
            errors.append(f"поле {field_name!r} не может быть пустым")

    return errors


def validate_findings_batch(
    findings: list[Finding],
    *,
    methodology: dict[str, Any] | None = None,
    confidence_caps: dict[str, str] | None = None,
) -> list[str]:
    """Провалидировать пакет находок целиком: лимит находок за прогон + каждую находку."""
    errors: list[str] = []

    if len(findings) > MAX_FINDINGS_PER_RUN:
        errors.append(
            f"находок в пакете {len(findings)} — максимум {MAX_FINDINGS_PER_RUN} за один "
            "прогон analyze"
        )

    ids = known_check_ids(methodology) if methodology is not None else None
    caps = confidence_caps or {}

    for finding in findings:
        cap = caps.get(finding.check_id)
        for err in validate_finding(finding, known_ids=ids, confidence_cap=cap):
            errors.append(f"{finding.check_id}: {err}")

    return errors
