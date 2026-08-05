"""Блок M — денежная рамка и приоритизация (каталог v2 правило 15;
marketing-diagnostics-methodology-v2.md §8; задача 5I).

Не пересчитывает бизнес-метрики заново — эти формулы принадлежат block1.py
(A) и block3.py (C). Здесь только СБОРКА уже посчитанных чисел из
data/metrics/{aXX,cXX}.json в четыре денежные категории каталога (правило 15):

    direct_confirmed_spend            — прямой подтверждённый расход
    potentially_excludable_spend      — потенциально исключаемый расход
    cpa_reduction_same_budget         — снижение CPA при том же бюджете
    equivalent_additional_conversions — эквивалент дополнительных конверсий

Категории НЕ смешиваются: подытог считается отдельно на каждую (см.
_MONEY_CATEGORY_TOTAL в run()), общего "грандтотала" по всем четырём нет —
это разные типы денег, складывать их в одну цифру запрещено методологией.

Источники величин:
    - "Главные величины" (main quantities) — плоские суммы уже посчитанного
      cost_normalized_rub/wasted_spend_rub из отдельных A-проверок, взятые
      как есть за строками с явным "проблемным" флагом (см. _FLAT_RULES).
    - CPA/CPC-outlier проверки (A05/A11/A12/A13/A14/A19) уже сами считают
      cost/net_conversions(или clicks)/бенчмарк (медиана или целевой сегмент)
      в одной строке — money_frame лишь переводит уже посчитанное сравнение
      в ₽ (excess = cost - volume*benchmark), новой бизнес-математики не
      вводит (см. _BENCHMARK_RULES).
    - Сценарии (эквивалент доп. конверсий) реализованы только для C06
      (доходимость формы, легаси 1.1, флагманская витринная находка) —
      разрыв доходимости сегмента относительно сайта в целом, помноженный на
      объём сегмента, даёт "недополученные конверсии"; перевод в ₽ — через
      сквозной CPA A04 (сумма cost_normalized_rub / сумма net_conversions по
      всем кампаниям). Остальные 24 C-проверки не переводятся в ₽ этой
      задачей — у них нет уже посчитанного числового разрыва, который можно
      было бы перевести в деньги без придумывания новой формулы (что
      протокол микрозадач CLAUDE.md запрещает при отсутствии источника
      истины на конкретную формулу).
    - Каждый сценарий помечается scenario=True и несёт scenario_label
      "сценарий, не прогноз" (money_frame.csv) — в findings_registry.csv
      это же читается из "Допущения".
    - confidence каждой находки/сценария = min(confidence найденных строк,
      confidence_cap соответствующей проверки из degradation_report) —
      assumptions наследуют этот же потолок, отдельного своего не имеют.

SEO (S-блок): на момент этой задачи src/compute/block4.py (S) не
реализован — data/metrics/s??.json не существует ни для одного клиента.
money_frame проверяет это явно (см. _seo_ready) и, если ни один s??.json не
несёт данных, добавляет отдельную запись-оговорку "SEO не учтён: источник
не готов" — рамка никогда не выглядит молча полной.

Уровень ключа атрибуции (задача 7G): отдельная строка `kind="attribution"`
в money_frame.json — L0/L1/L2/L_UNKNOWN, посчитанные по фактической
заполненности колонок canonical (crm/visits) и фактическому JOIN, вместе с
доказательством по каждой проверенной колонке. Report это поле только читает.

findings_registry.csv — skeleton единой карточки находки (каталог v2 §12):
плоские деньги/подытоги в regestry не попадают (kind != category_item и
!= scenario пропускается), для находок и сценариев заполняются только
колонки, которые compute-слой действительно знает (ID, название из
methodology.yaml, уверенность, сегмент, источник, что искажается, денежная
категория, оценка в ₽, допущения) — нарративные колонки (Статус,
Доказательство, Рекомендуемое действие, Как измерить, Что нельзя заключить)
остаются пустыми до слоя analyze (единственного слоя с LLM — не этой
задачи, "Не реализуй LLM-приоритизацию").

БЕЗ вызовов LLM (принцип 3 CLAUDE.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import common
from ..pipeline import degradation as degradation_mod
from ..pipeline import orchestrator as orchestrator_mod


# ── Денежные категории (каталог v2, правило 15) ─────────────────────────────
DIRECT_CONFIRMED_SPEND = "direct_confirmed_spend"
POTENTIALLY_EXCLUDABLE_SPEND = "potentially_excludable_spend"
CPA_REDUCTION_SAME_BUDGET = "cpa_reduction_same_budget"
EQUIVALENT_ADDITIONAL_CONVERSIONS = "equivalent_additional_conversions"

MONEY_CATEGORIES: dict[str, str] = {
    DIRECT_CONFIRMED_SPEND: "прямой подтверждённый расход",
    POTENTIALLY_EXCLUDABLE_SPEND: "потенциально исключаемый расход",
    CPA_REDUCTION_SAME_BUDGET: "снижение CPA при том же бюджете",
    EQUIVALENT_ADDITIONAL_CONVERSIONS: "эквивалент дополнительных конверсий",
}

SCENARIO_LABEL = "сценарий, не прогноз"
SEO_NOT_READY_NOTE = "SEO не учтён: источник не готов"


# ── Уровень ключа атрибуции (задача 7G) ─────────────────────────────────────
# Считается из фактической canonical-схемы клиента, не из документации и не
# из константы: L2 — есть рабочий ключ склейки CRM с визитами (client_id/
# yclid/gclid либо контакт+таймстамп), дающий фактический JOIN выше порога
# успешности; L1 — есть заполненное поле источника/utm; L0 — CRM-таблица есть,
# ни одно из условий не выполнено; L_UNKNOWN — CRM-источника нет вообще.
# Пороги и имена колонок — только в config/defaults.yaml (принцип 1).
ATTRIBUTION_L0 = "L0"
ATTRIBUTION_L1 = "L1"
ATTRIBUTION_L2 = "L2"
ATTRIBUTION_L_UNKNOWN = "L_UNKNOWN"

# Различаем постоянное ограничение источника и ещё не посчитанную величину.
STATUS_AVAILABLE = "available"
STATUS_NOT_COMPUTABLE = "not_computable"      # источник принципиально не несёт ключа
STATUS_NOT_COMPUTED_YET = "not_computed_yet"  # источника нет — величина не считалась

NO_REPEAT_KEY_REASON = "нет ключа склейки повторных обращений"
NO_CRM_SOURCE_REASON = "CRM-источника нет: ключ склейки повторных обращений не проверялся"


# ── Плоские "главные величины" — сумма уже посчитанного поля, взятая только
# со строк с явным проблемным флагом (никакой новой математики) ─────────────
_FLAT_RULES: dict[str, dict[str, Any]] = {
    "A04": {
        "flag": "zero_conversion_campaign",
        "amount": "cost_normalized_rub",
        "category": POTENTIALLY_EXCLUDABLE_SPEND,
        "segment": lambda r: r.get("campaign_name") or r.get("campaign_id"),
        "note": "Кампания тратит бюджет, чистых конверсий нет",
    },
    "A09": {
        "flag": "no_net_conversions",
        "amount": "cost_normalized_rub",
        "category": POTENTIALLY_EXCLUDABLE_SPEND,
        "segment": lambda r: r.get("query"),
        "note": "Расход по поисковому запросу без чистых конверсий",
        "finding": "query_spend_vs_conversions",
    },
    "A10": {
        "flag": "missing_negative_keyword_candidate",
        "amount": "wasted_spend_rub",
        "category": POTENTIALLY_EXCLUDABLE_SPEND,
        "segment": lambda r: r.get("query"),
        "note": "Повторяющийся расход без конверсий — кандидат в минус-слова",
    },
    "A17": {
        "flag": "possible_cannibalization",
        "amount": "cost_normalized_rub",
        "category": POTENTIALLY_EXCLUDABLE_SPEND,
        "segment": lambda r: r.get("query"),
        "note": "Платный клик по бренду при уже видимой органике",
        "finding": "brand_query_paid_vs_organic",
    },
    "A06": {
        "flag": "budget_misallocated",
        "amount": "cost_normalized_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("campaign_name") or r.get("campaign_id"),
        "note": "Доля бюджета кампании не соответствует доле чистых конверсий",
    },
}

# ── CPA/CPC-outlier проверки: excess = cost - volume*benchmark, только на
# строках, где сама проверка уже отметила сегмент как устойчиво хуже ────────
_BENCHMARK_RULES: dict[str, dict[str, Any]] = {
    "A05": {
        "flag": "cpa_persistently_worse", "cost": "cost_normalized_rub",
        "volume": "net_conversions", "benchmark": "median_cpa_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("campaign_name") or r.get("campaign_id"),
        "note": "CPA кампании устойчиво хуже медианы сопоставимых кампаний",
    },
    "A11": {
        "flag": "match_type_dilutes_semantics", "cost": "cost_normalized_rub",
        "volume": "net_conversions", "benchmark": "keyword_cpa_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("match_type"),
        "note": "Тип соответствия размывает семантику относительно точной фразы",
    },
    "A12": {
        "flag": "off_target_geo_worse", "cost": "cost_normalized_rub",
        "volume": "net_conversions", "benchmark": "target_region_cpa_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("location_of_presence_name"),
        "note": "CPA нецелевого региона кратно хуже целевого",
        "finding": "region_detail",
    },
    "A13": {
        "flag": "weekday_persistently_worse", "cost": "cost_normalized_rub",
        "volume": "net_conversions", "benchmark": "median_weekday_cpa_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("weekday"),
        "note": "CPA дня недели устойчиво хуже медианы",
        "finding": "weekday_economics",
    },
    "A14": {
        "flag": "device_cpa_persistently_worse", "cost": "cost_normalized_rub",
        "volume": "net_conversions", "benchmark": "median_device_cpa_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("device"),
        "note": "CPA устройства устойчиво хуже медианы",
        "finding": "cpa_by_device",
    },
    "A19": {
        "flag": "cpc_anomalously_high", "cost": "cost_normalized_rub",
        "volume": "clicks", "benchmark": "median_cpc_rub",
        "category": CPA_REDUCTION_SAME_BUDGET,
        "segment": lambda r: r.get("query"),
        "note": "CPC фразы аномально выше медианы",
    },
}


# ── Чтение уже записанных артефактов A/C ────────────────────────────────────
def _read_artifact(metrics_dir: Path, check_id: str) -> list[dict[str, Any]] | None:
    """Прочитать data/metrics/<check_id>.json; отсутствие/битый файл -> None."""
    path = metrics_dir / f"{check_id.lower()}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


def _confidence_caps(paths: Any) -> dict[str, str]:
    """{check_id: confidence_cap} из уже записанного degradation_report.json."""
    report = common.load_degradation(paths)
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (report.get("checks") or [])
        if c.get("check_id")
    }


def _check_names(methodology: dict[str, Any]) -> dict[str, str]:
    return {
        c.get("id"): c.get("name", "")
        for c in (methodology.get("checks") or [])
        if c.get("id")
    }


def _seo_ready(metrics_dir: Path) -> bool:
    """Есть ли хотя бы один непустой s??.json (S01..S27)."""
    for path in sorted(metrics_dir.glob("s[0-9][0-9].json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data and not (len(data) == 1 and data[0].get("status") == "unavailable"):
            return True
    return False


# ── Сборка одной денежной находки ───────────────────────────────────────────
def _money_item(
    *, check_id: str, category: str, amount_rub: float | None,
    confidence: str, confidence_cap: str, segment: Any, description: str,
    source_check_ids: list[str], scenario: bool = False,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "money_category": category,
        "amount_rub": amount_rub,
        "unit": "RUB",
        "confidence": degradation_mod.min_confidence(confidence or "LOW", confidence_cap or "HIGH"),
        "confidence_cap": confidence_cap,
        "segment": segment,
        "description": description,
        "source_check_ids": source_check_ids,
        "scenario": scenario,
        "assumptions": assumptions or [],
        "caveats": [],
    }


def _iter_rule_rows(rows: list[dict[str, Any]], rule: dict[str, Any]):
    finding = rule.get("finding")
    for row in rows:
        if row.get("status") == "unavailable":
            continue
        if finding is not None and row.get("finding") != finding:
            continue
        if not row.get(rule["flag"]):
            continue
        yield row


def _flat_amount_items(
    check_id: str, rows: list[dict[str, Any]], rule: dict[str, Any], confidence_cap: str,
) -> list[dict[str, Any]]:
    items = []
    for row in _iter_rule_rows(rows, rule):
        amount = row.get(rule["amount"])
        if amount is None:
            continue
        items.append(_money_item(
            check_id=check_id, category=rule["category"], amount_rub=amount,
            confidence=row.get("confidence", "LOW"), confidence_cap=confidence_cap,
            segment=rule["segment"](row), description=rule["note"],
            source_check_ids=[check_id],
        ))
    return items


def _benchmark_amount_items(
    check_id: str, rows: list[dict[str, Any]], rule: dict[str, Any], confidence_cap: str,
) -> list[dict[str, Any]]:
    items = []
    for row in _iter_rule_rows(rows, rule):
        cost = row.get(rule["cost"])
        volume = row.get(rule["volume"])
        benchmark = row.get(rule["benchmark"])
        if cost is None or volume is None or benchmark is None:
            continue
        excess = round(cost - volume * benchmark, 2)
        if excess <= 0:
            continue
        items.append(_money_item(
            check_id=check_id, category=rule["category"], amount_rub=excess,
            confidence=row.get("confidence", "LOW"), confidence_cap=confidence_cap,
            segment=rule["segment"](row),
            description=f"{rule['note']} (excess над бенчмарком {benchmark} ₽)",
            source_check_ids=[check_id],
        ))
    return items


def _a18_items(rows: list[dict[str, Any]], confidence_cap: str) -> list[dict[str, Any]]:
    """A18 — пересечение кампаний за один запрос: вложенный список campaigns[],
    сумма их cost_normalized_rub валидна только если ни одна не null."""
    items = []
    for row in rows:
        if row.get("status") == "unavailable":
            continue
        campaigns = row.get("campaigns") or []
        costs = [c.get("cost_normalized_rub") for c in campaigns]
        if not campaigns or any(c is None for c in costs):
            continue
        total = round(sum(costs), 2)
        if total <= 0:
            continue
        items.append(_money_item(
            check_id="A18", category=CPA_REDUCTION_SAME_BUDGET, amount_rub=total,
            confidence=row.get("confidence", "LOW"), confidence_cap=confidence_cap,
            segment=row.get("query"),
            description="Несколько кампаний конкурируют за один и тот же запрос",
            source_check_ids=["A18"],
        ))
    return items


def _blended_cpa_from_a04(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Сквозной CPA по всем кампаниям A04 — база для перевода C-сценариев в ₽."""
    if not rows or (len(rows) == 1 and rows[0].get("status") == "unavailable"):
        return None
    total_cost = 0.0
    total_conv = 0
    seen = False
    for row in rows:
        cost = row.get("cost_normalized_rub")
        conv = row.get("net_conversions")
        if cost is None or conv is None:
            continue
        total_cost += cost
        total_conv += conv
        seen = True
    if not seen or total_conv <= 0:
        return None
    return {
        "total_spend_rub": round(total_cost, 2),
        "total_net_conversions": total_conv,
        "blended_cpa_rub": round(total_cost / total_conv, 2),
    }


def _c06_scenarios(
    rows: list[dict[str, Any]], blended: dict[str, Any] | None, confidence_cap: str,
) -> list[dict[str, Any]]:
    """Сценарий "эквивалент доп. конверсий" для сегментов C06 хуже сайта в целом."""
    summary = next((r for r in rows if r.get("finding") == "funnel_summary"), None)
    if summary is None or summary.get("open_to_submit_rate") is None:
        return []
    overall_rate = summary["open_to_submit_rate"]
    overall_confidence = summary.get("confidence", "LOW")

    items = []
    for row in rows:
        if row.get("finding") != "funnel_by_segment":
            continue
        if row.get("confidence") == "LOW":
            continue  # ниже min_sample_visits — уже отфильтровано самим C06
        rate = row.get("open_to_submit_rate")
        opens = row.get("form_open_visits")
        if rate is None or opens is None or rate >= overall_rate:
            continue
        additional_conversions = opens * (overall_rate - rate)
        if additional_conversions <= 0:
            continue

        confidence = degradation_mod.min_confidence(row.get("confidence", "LOW"), overall_confidence)
        assumptions = [
            f"бенчмарк — общая доходимость формы по сайту ({round(overall_rate, 4)})",
            f"оценочно {round(additional_conversions, 1)} доп. конверсий при выравнивании "
            "сегмента до общего уровня",
        ]
        amount_rub = None
        source_check_ids = ["C06"]
        if blended is not None:
            amount_rub = round(additional_conversions * blended["blended_cpa_rub"], 2)
            assumptions.append(
                f"денежный эквивалент по сквозному CPA A04 ({blended['blended_cpa_rub']} ₽/конверсия)"
            )
            source_check_ids.append("A04")
        else:
            assumptions.append("сквозной CPA недоступен (A04) — оценка в ₽ невозможна")

        items.append(_money_item(
            check_id="C06", category=EQUIVALENT_ADDITIONAL_CONVERSIONS, amount_rub=amount_rub,
            confidence=confidence, confidence_cap=confidence_cap,
            segment=f"{row.get('segment_dimension')}={row.get('segment_value')}",
            description=(
                f"Доходимость формы в сегменте ниже общей по сайту "
                f"({round(rate, 4)} против {round(overall_rate, 4)})"
            ),
            source_check_ids=source_check_ids, scenario=True, assumptions=assumptions,
        ))
    return items


# ── findings_registry.csv skeleton (каталог v2 §12, единая карточка) ───────
_CARD_FIELDS: tuple[str, ...] = (
    "ID угрозы", "Название", "Статус", "Уверенность", "Период",
    "Затронутый сегмент", "Источник данных", "Доказательство",
    "Контрольная метрика или второй источник", "Что именно искажается",
    "Денежная категория", "Оценка в рублях или «в ₽ не оценить»",
    "Допущения", "Рекомендуемое действие",
    "Как измерить результат после изменения",
    "Что нельзя заключить из этих данных",
)


def _findings_registry_rows(
    money_rows: list[dict[str, Any]], names: dict[str, str],
) -> list[dict[str, Any]]:
    out = []
    for row in money_rows:
        if row.get("kind") not in ("category_item", "scenario"):
            continue
        check_id = row["check_id"]
        amount = row.get("amount_rub")
        assumptions = list(row.get("assumptions") or [])
        if row.get("scenario"):
            assumptions = [SCENARIO_LABEL, *assumptions]
        out.append({field: "" for field in _CARD_FIELDS} | {
            "ID угрозы": check_id,
            "Название": names.get(check_id, ""),
            "Уверенность": row.get("confidence") or "",
            "Затронутый сегмент": row.get("segment") or "",
            "Источник данных": ", ".join(row.get("source_check_ids") or [check_id]),
            "Что именно искажается": row.get("description") or "",
            "Денежная категория": MONEY_CATEGORIES.get(row.get("money_category"), ""),
            "Оценка в рублях или «в ₽ не оценить»": (
                amount if amount is not None else "в ₽ не оценить"
            ),
            "Допущения": "; ".join(assumptions),
            "Что нельзя заключить из этих данных": "; ".join(row.get("caveats") or []),
        })
    return out


# ── Уровень ключа атрибуции: детерминированный расчёт по canonical ──────────
def _attribution_config(defaults: dict[str, Any]) -> dict[str, Any] | None:
    """Секция ``attribution`` из config/defaults.yaml; её отсутствие -> None.

    Без порогов и списков колонок уровень не считается вовсе: подставлять их
    в код запрещено (принцип 1), а писать уровень без доказательства — нельзя.
    """
    config = (defaults or {}).get("attribution")
    return config if isinstance(config, dict) else None


def _table_columns(con: Any, table: str) -> set[str]:
    try:
        return {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}
    except Exception:  # noqa: BLE001 - отсутствие optional canonical таблицы
        return set()


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _non_empty_sql(alias: str, column: str, placeholders: list[str]) -> str:
    """SQL-условие «значение фактически заполнено».

    NULL, пустая строка и значения-заглушки (``unknown``, ``не определено``…)
    заполненностью не считаются — иначе колонка, которую transform забил
    ``"unknown"`` на 100% строк, выглядела бы рабочим ключом.
    """
    expr = f"lower(trim(CAST({alias}.{_quote(column)} AS VARCHAR)))"
    condition = f"{alias}.{_quote(column)} IS NOT NULL AND {expr} <> ''"
    if placeholders:
        values = ", ".join(_sql_literal(p) for p in placeholders)
        condition += f" AND {expr} NOT IN ({values})"
    return condition


def _sql_literal(text: Any) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _fill_evidence(
    con: Any, *, role: str, column: str, columns: set[str], rows: int,
    placeholders: list[str], threshold: float,
) -> dict[str, Any]:
    """Доказательство по одной колонке: имя + фактическая доля непустых значений."""
    evidence: dict[str, Any] = {
        "role": role, "table": "crm", "column": column,
        "present": column in columns, "rows": rows,
        "non_empty": 0, "non_empty_share": 0.0,
        "threshold": threshold, "passes": False,
    }
    if column not in columns or rows <= 0:
        return evidence
    condition = _non_empty_sql("c", column, placeholders)
    non_empty = int(
        con.execute(f"SELECT COUNT(*) FROM crm c WHERE {condition}").fetchone()[0]
    )
    evidence["non_empty"] = non_empty
    evidence["non_empty_share"] = round(non_empty / rows, 4)
    evidence["passes"] = evidence["non_empty_share"] >= threshold
    return evidence


def _join_evidence(
    con: Any, *, role: str, pair: dict[str, Any], crm_columns: set[str],
    visits_columns: set[str], visits_present: bool, rows: int,
    placeholders: list[str], fill_threshold: float, join_threshold: float,
) -> dict[str, Any]:
    """Доказательство по ключу склейки: заполненность + фактический JOIN с visits."""
    crm_column = str(pair.get("crm_column") or "")
    visits_column = str(pair.get("visits_column") or "")
    evidence = _fill_evidence(
        con, role=role, column=crm_column, columns=crm_columns, rows=rows,
        placeholders=placeholders, threshold=fill_threshold,
    )
    evidence["joined_with"] = f"visits.{visits_column}"
    evidence["join_possible"] = bool(
        visits_present and visits_column in visits_columns and evidence["present"]
    )
    evidence["join_matched"] = 0
    evidence["join_success_rate"] = 0.0
    evidence["join_threshold"] = join_threshold
    evidence["join_passes"] = False
    if not evidence["join_possible"] or rows <= 0 or not evidence["passes"]:
        return evidence

    condition = _non_empty_sql("c", crm_column, placeholders)
    matched = int(con.execute(
        f"SELECT COUNT(*) FROM crm c WHERE {condition} AND EXISTS ("
        f"SELECT 1 FROM visits v WHERE CAST(v.{_quote(visits_column)} AS VARCHAR) "
        f"= CAST(c.{_quote(crm_column)} AS VARCHAR))"
    ).fetchone()[0])
    evidence["join_matched"] = matched
    evidence["join_success_rate"] = round(matched / rows, 4)
    evidence["join_passes"] = evidence["join_success_rate"] >= join_threshold
    return evidence


def compute_attribution(paths: Any, defaults: dict[str, Any]) -> dict[str, Any] | None:
    """Уровень ключа атрибуции + доказательство по каждой проверенной колонке.

    Возвращает None только если секция ``attribution`` не задана в defaults —
    тогда уровень не пишется вовсе (уровень без доказательства запрещён).
    """
    config = _attribution_config(defaults)
    if config is None:
        return None

    placeholders = [str(v).strip().lower() for v in (config.get("placeholder_values") or [])]
    fill_threshold = float(config.get("min_join_key_fill_rate", 1.0))
    join_threshold = float(config.get("min_join_success_rate", 1.0))
    source_threshold = float(config.get("min_source_fill_rate", 1.0))
    repeat_threshold = float(config.get("min_repeat_key_fill_rate", 1.0))

    canonical = common.load_canonical(paths)
    crm_present = "crm" in canonical
    evidence: list[dict[str, Any]] = [
        {"role": "table", "table": "crm", "present": crm_present}
    ]
    if not crm_present:
        return {
            "level": ATTRIBUTION_L_UNKNOWN,
            "evidence": evidence,
            "unique_customers_available": False,
            "unique_customers_status": STATUS_NOT_COMPUTED_YET,
            "unique_customers_reason": NO_CRM_SOURCE_REASON,
        }

    visits_present = "visits" in canonical
    con = common.open_duckdb(paths)
    try:
        crm_columns = _table_columns(con, "crm")
        visits_columns = _table_columns(con, "visits") if visits_present else set()
        rows = int(con.execute("SELECT COUNT(*) FROM crm").fetchone()[0])
        evidence[0]["rows"] = rows
        evidence.append({"role": "table", "table": "visits", "present": visits_present})

        join_evidence = [
            _join_evidence(
                con, role="join_key", pair=pair, crm_columns=crm_columns,
                visits_columns=visits_columns, visits_present=visits_present, rows=rows,
                placeholders=placeholders, fill_threshold=fill_threshold,
                join_threshold=join_threshold,
            )
            for pair in (config.get("join_keys") or [])
        ]
        contact_evidence = [
            _join_evidence(
                con, role="contact_key", pair=pair, crm_columns=crm_columns,
                visits_columns=visits_columns, visits_present=visits_present, rows=rows,
                placeholders=placeholders, fill_threshold=fill_threshold,
                join_threshold=join_threshold,
            )
            for pair in (config.get("contact_keys") or [])
        ]
        timestamp_evidence = [
            _fill_evidence(
                con, role="timestamp", column=str(column), columns=crm_columns, rows=rows,
                placeholders=placeholders, threshold=fill_threshold,
            )
            for column in (config.get("timestamp_columns") or [])
        ]
        source_evidence = [
            _fill_evidence(
                con, role="source", column=str(column), columns=crm_columns, rows=rows,
                placeholders=placeholders, threshold=source_threshold,
            )
            for column in (config.get("source_columns") or [])
        ]
        repeat_evidence = [
            _fill_evidence(
                con, role="repeat_key", column=str(column), columns=crm_columns, rows=rows,
                placeholders=placeholders, threshold=repeat_threshold,
            )
            for column in (config.get("repeat_columns") or [])
        ]
    finally:
        con.close()

    evidence.extend(
        join_evidence + contact_evidence + timestamp_evidence
        + source_evidence + repeat_evidence
    )

    has_timestamp = any(e["passes"] for e in timestamp_evidence)
    l2 = any(e["join_passes"] for e in join_evidence) or (
        has_timestamp and any(e["join_passes"] for e in contact_evidence)
    )
    if l2:
        level = ATTRIBUTION_L2
    elif any(e["passes"] for e in source_evidence):
        level = ATTRIBUTION_L1
    else:
        level = ATTRIBUTION_L0

    repeat_key_available = (
        any(e["passes"] for e in repeat_evidence)
        or any(e["passes"] for e in contact_evidence)
    )
    return {
        "level": level,
        "evidence": evidence,
        "unique_customers_available": repeat_key_available,
        "unique_customers_status": (
            STATUS_AVAILABLE if repeat_key_available else STATUS_NOT_COMPUTABLE
        ),
        "unique_customers_reason": None if repeat_key_available else NO_REPEAT_KEY_REASON,
    }


def _attribution_row(attribution: dict[str, Any]) -> dict[str, Any]:
    """Строка money_frame.json с уровнем и его доказательством (только добавление)."""
    level = attribution["level"]
    return {
        "check_id": "M", "kind": "attribution", "money_category": None,
        "amount_rub": None, "unit": None,
        "metric": "attribution_level", "value": level,
        "attribution_level": level,
        "attribution_evidence": attribution["evidence"],
        "unique_customers_available": attribution["unique_customers_available"],
        "unique_customers_status": attribution["unique_customers_status"],
        "unique_customers_reason": attribution["unique_customers_reason"],
        "confidence": None, "confidence_cap": None, "segment": None,
        "description": (
            f"Уровень ключа атрибуции {level} — посчитан по фактической "
            "заполненности колонок canonical (см. attribution_evidence)"
        ),
        "source_check_ids": [], "scenario": False,
    }


# ── Точка входа блока (контракт common.dispatch_blocks) ────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Собрать money_frame.csv/json + findings_registry.csv/json из A/C."""
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    defaults = defaults or {}
    currency_round = int(defaults.get("currency_round", 0))

    methodology = orchestrator_mod.load_methodology()
    names = _check_names(methodology)
    confidence_caps = _confidence_caps(paths)

    items: list[dict[str, Any]] = []

    for check_id, rule in _FLAT_RULES.items():
        rows = _read_artifact(metrics_dir, check_id)
        if rows:
            items.extend(_flat_amount_items(check_id, rows, rule, confidence_caps.get(check_id, "HIGH")))

    for check_id, rule in _BENCHMARK_RULES.items():
        rows = _read_artifact(metrics_dir, check_id)
        if rows:
            items.extend(_benchmark_amount_items(check_id, rows, rule, confidence_caps.get(check_id, "HIGH")))

    a18_rows = _read_artifact(metrics_dir, "A18")
    if a18_rows:
        items.extend(_a18_items(a18_rows, confidence_caps.get("A18", "HIGH")))

    a04_rows = _read_artifact(metrics_dir, "A04")
    blended = _blended_cpa_from_a04(a04_rows)

    c06_rows = _read_artifact(metrics_dir, "C06")
    if c06_rows:
        items.extend(_c06_scenarios(c06_rows, blended, confidence_caps.get("C06", "HIGH")))

    for item in items:
        if item["amount_rub"] is not None:
            item["amount_rub"] = round(item["amount_rub"], currency_round)

    rows_out: list[dict[str, Any]] = []

    if blended is not None:
        cap = confidence_caps.get("A04", "MED")
        rows_out.append({
            "check_id": "M", "kind": "quantity", "money_category": None,
            "amount_rub": round(blended["total_spend_rub"], currency_round), "unit": "RUB",
            "metric": "total_ad_spend_rub_a04",
            "confidence": degradation_mod.min_confidence("MED", cap), "confidence_cap": cap,
            "segment": None,
            "description": "Суммарный расход по кампаниям (A04), НДС-нормализован",
            "source_check_ids": ["A04"], "scenario": False,
        })
        rows_out.append({
            "check_id": "M", "kind": "quantity", "money_category": None,
            "amount_rub": None, "unit": "RUB/conversion",
            "metric": "blended_cpa_rub", "value": blended["blended_cpa_rub"],
            "confidence": degradation_mod.min_confidence("MED", cap), "confidence_cap": cap,
            "segment": None,
            "description": "Сквозной CPA по всем кампаниям A04 — база сценариев эквивалента конверсий",
            "source_check_ids": ["A04"], "scenario": False,
        })

    for item in items:
        row = dict(item)
        row["kind"] = "scenario" if item.get("scenario") else "category_item"
        row["scenario_label"] = SCENARIO_LABEL if item.get("scenario") else None
        rows_out.append(row)

    # Уровень ключа атрибуции — факт из данных, а не константа. Пишется только
    # вместе с доказательством (см. compute_attribution); без секции
    # attribution в defaults не пишется вовсе.
    attribution = compute_attribution(paths, defaults)
    if attribution is not None:
        rows_out.append(_attribution_row(attribution))

    seo_ready = _seo_ready(metrics_dir)
    if not seo_ready:
        rows_out.append({
            "check_id": "S", "kind": "caveat", "money_category": None, "amount_rub": None,
            "confidence": None, "confidence_cap": None, "segment": None,
            "description": SEO_NOT_READY_NOTE, "source_check_ids": [], "scenario": False,
        })

    # Подытог на каждую денежную категорию отдельно — категории не смешиваются.
    for category in MONEY_CATEGORIES:
        matching = [
            r for r in rows_out
            if r.get("kind") in ("category_item", "scenario") and r.get("money_category") == category
        ]
        if not matching:
            continue
        total = sum(r["amount_rub"] for r in matching if r.get("amount_rub") is not None)
        rows_out.append({
            "check_id": "M", "kind": "category_total", "money_category": category,
            "amount_rub": round(total, currency_round), "confidence": None, "confidence_cap": None,
            "segment": None,
            "description": f"Подытог категории «{MONEY_CATEGORIES[category]}» ({len(matching)} находок)",
            "source_check_ids": [], "scenario": False,
        })

    for row in rows_out:
        conf, cap = row.get("confidence"), row.get("confidence_cap")
        if conf and cap:
            common.assert_confidence_within_cap(conf, cap)

    common.write_metric_artifact(metrics_dir, "money_frame", rows_out)

    registry_rows = _findings_registry_rows(rows_out, names)
    common.write_metric_artifact(metrics_dir, "findings_registry", registry_rows)

    return ["money_frame", "findings_registry"]
