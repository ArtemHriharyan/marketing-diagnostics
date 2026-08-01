"""Генерация черновиков находок из метрик и качественных входов.

Контракт:
    Читает   — analysis_candidates.json и компактные metrics-артефакты,
               degradation_report.json, заполненные inputs/*.yaml,
               config/methodology.yaml (названия проверок для привязки находок).
    Пишет    — findings/draft/*.yaml. Каждая находка: id проверки, формулировка
               (русский), опора на числа, уровень уверенности
               (client-HIGH / MED / …), рекомендация.
    LLM      — ДА, только здесь. Модель формулирует текст находки поверх уже
               посчитанных детерминированных чисел; сами числа не выдумываются.
    Гейт     — вывод идёт в draft/, НЕ в approved/. Перенос — ручной, аналитиком.

    ПОТОЛКИ (обязательны к соблюдению; LLM их только понижает, не повышает):
      * degradation_report.checks[*].confidence_cap — верхняя граница уверенности
        находки по этой проверке (MED, если задействован manual-источник). Это
        второй потолок поверх исходного из compute: итоговая уверенность =
        min(compute-уверенность, confidence_cap).
      * degradation_report.checks[*].type_effective — тип находки (A|B|Q) уже
        после пост-хок понижения; analyze берёт его как есть, не «повышает».

Задача 6A дала детерминированную оболочку (без вызова API Anthropic):
    build_input_pack()   — собирает всё, что модель получит на вход: P06-кандидаты,
                            компактный контекст, inputs, деградацию (оба потолка
                            уверенности), контекст клиента, реестр check_id.
    build_system_prompt() — текст системного промта с запретами модели (см.
                            docstring функции).

Задача 6B подключает сам вызов модели (единственное место в пайплайне,
где это разрешено — принцип 3 CLAUDE.md):
    _call_llm()           — один структурированный вызов (text.format)
                            поверх input_pack; предсказуемый токен-бюджет
                            (LLM_MAX_TOKENS), без повторной генерации после
                            валидного ответа (ретраи — только транспортные,
                            через timeout/max_retries самого SDK).
    draft()               — собирает пакет, пишет его как аудиторский артефакт
                            (INPUT_PACK_ARTIFACT_NAME), вызывает модель и
                            записывает прошедшие schemas.validate_finding
                            находки как findings/draft/F-<блок>-<nn>.yaml
                            (не больше schemas.MAX_FINDINGS_PER_RUN).

    Модель, ключ API и base URL берутся из project env,
    а НЕ из clients/<name>/.env — секреты клиента (принцип 6 CLAUDE.md)
    относятся к источникам данных, а не к самому пайплайну.

    Глубокая программная проверка evidence находок (что цифры в evidence
    реально соответствуют metrics/degradation) — отдельная задача 6C; здесь
    только структурная валидация схемы (schemas.validate_finding).
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import yaml

from ..pipeline import orchestrator as orchestrator_mod
from . import schemas
from . import validate_findings as validate_findings_mod

# Служебные артефакты data/metrics/, которые не являются результатом
# конкретной проверки D/A/T/C/S и не входят в пакет "metrics" как есть —
# degradation разбирается отдельно (см. _load_degradation_report), summary
# не несёт бизнес-чисел (см. src/compute/common.py: build_metrics_summary)
# и в input pack не нужен.
_NON_FINDING_STATUSES = frozenset({"unavailable", "unavailable_for_cause"})
_DIAGNOSTIC_CONTEXT_MARKERS = frozenset({"channel_anomaly_context"})
_COMPACT_CONTEXT_ARTIFACTS = (
    "funnels", "cost_summary", "acquisition_economics", "seasonality",
)

INPUT_PACK_ARTIFACT_NAME = "_analyze_input_pack.json"
INPUT_PACK_BYTE_CAP = 100_000


# ── Сбор входного пакета ────────────────────────────────────────────────────
def _load_json_artifact(paths: Any, stem: str) -> Any:
    """Безопасно прочитать один явно разрешённый компактный metrics-артефакт."""
    path = Path(paths.metrics) / f"{stem}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_source_metrics(paths: Any, check_ids: set[str]) -> dict[str, Any]:
    """Прочитать после LLM только check-файлы, нужные для evidence-сверки."""
    result: dict[str, Any] = {}
    for check_id in sorted(check_ids):
        if not schemas.is_valid_check_id_format(check_id):
            continue
        payload = _load_json_artifact(paths, check_id.lower())
        if payload is not None:
            result[check_id.lower()] = payload
    return result


def _prune_empty(value: Any) -> Any:
    """Удалить только незаполненные качественные inputs, сохранив 0 и false."""
    if isinstance(value, dict):
        result = {key: _prune_empty(item) for key, item in value.items()}
        return {key: item for key, item in result.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        result = [_prune_empty(item) for item in value]
        return [item for item in result if item not in (None, "", [], {})]
    return value


def _row_dict(columns: list[str], row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if isinstance(row, list):
        return {
            column: row[index] if index < len(row) else None
            for index, column in enumerate(columns)
        }
    return {}


def _candidate_rows(payload: Any) -> tuple[list[str], list[Any], list[int]]:
    """Вернуть columnar rows P06 и индексы строк-кандидатов."""
    if not isinstance(payload, dict):
        return [], [], []
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not isinstance(columns, list) or not all(isinstance(item, str) for item in columns):
        return [], [], []
    if not isinstance(rows, list):
        return columns, [], []
    indexes: list[int] = []
    for index, row in enumerate(rows):
        decoded = _row_dict(columns, row)
        if decoded.get("row_role") == "candidate" or decoded.get("candidate") is True:
            indexes.append(index)
    return columns, rows, indexes


def _is_unavailable_row(row: dict[str, Any]) -> bool:
    return (
        row.get("status") in _NON_FINDING_STATUSES
        or row.get("finding") in _DIAGNOSTIC_CONTEXT_MARKERS
    )


def _candidate_ref(row: dict[str, Any], index: int) -> dict[str, Any]:
    result = {"row_index": index}
    for key in ("check_id", "artifact", "source_artifact", "row_ref"):
        if row.get(key) not in (None, ""):
            result[key] = row[key]
    return result


def _has_money_signal(value: Any, key: str = "") -> bool:
    markers = ("money", "cost", "spend", "revenue", "rub")
    if isinstance(value, dict):
        return any(_has_money_signal(item, str(item_key)) for item_key, item in value.items())
    if isinstance(value, list):
        return any(_has_money_signal(item, key) for item in value)
    return any(marker in key.lower() for marker in markers) and value not in (
        None, "", 0, 0.0, False,
    )


def _candidate_priority(
    row: dict[str, Any], index: int, *, coverage_anchor: bool = False
) -> tuple[int, int, int, int, int]:
    """Приоритет byte-cap: деньги, значимость, confidence, стабильный порядок."""
    money = int(bool(row.get("money_category")) or _has_money_signal(row))
    significant = int(row.get("significant") is True)
    confidence = {"HIGH": 3, "MED": 2, "LOW": 1}.get(str(row.get("confidence")), 0)
    return money, significant, confidence, int(coverage_anchor), -index


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _set_pack_size(pack: dict[str, Any]) -> int:
    """Записать устойчивый размер самого передаваемого JSON-пакета."""
    audit = pack.setdefault("audit", {})
    for _ in range(4):
        size = _json_size(pack)
        if audit.get("input_pack_bytes") == size:
            break
        audit["input_pack_bytes"] = size
    return _json_size(pack)


def _refresh_coverage(pack: dict[str, Any], *, detected: int, included: int) -> None:
    coverage = pack["coverage"]
    coverage["candidates_detected"] = detected
    coverage["candidates_included"] = included
    coverage["candidates_excluded"] = detected - included
    check_ids = []
    columns, rows, indexes = _candidate_rows(pack.get("analysis_candidates"))
    for index in indexes:
        check_id = _row_dict(columns, rows[index]).get("check_id")
        if check_id:
            check_ids.append(check_id)
    coverage["included_check_ids"] = sorted(set(check_ids))
    coverage["included_blocks"] = sorted({check_id[0] for check_id in check_ids if check_id})


def _apply_byte_cap(pack: dict[str, Any], byte_cap: int) -> None:
    """Детерминированно исключить низкоприоритетных кандидатов после сборки пакета."""
    candidates = pack.get("analysis_candidates") or {}
    pack["audit"]["byte_cap_exceeded"] = False
    columns, rows, indexes = _candidate_rows(candidates)
    decoded_all = {index: _row_dict(columns, row) for index, row in enumerate(rows)}
    decoded = {index: decoded_all[index] for index in indexes}
    included = set(indexes)
    for index in [index for index in indexes if _is_unavailable_row(decoded[index])]:
        included.discard(index)
        pack["excluded_candidates"].append({
            **_candidate_ref(decoded[index], index), "reason": "unavailable_row",
        })

    unavailable_rows = {
        index for index, row in decoded_all.items() if _is_unavailable_row(row)
    }

    def rebuild_rows() -> None:
        candidates["rows"] = [
            row for index, row in enumerate(rows)
            if index not in unavailable_rows and (index not in indexes or index in included)
        ]

    rebuild_rows()
    _refresh_coverage(pack, detected=len(indexes), included=len(included))
    _set_pack_size(pack)
    anchors: set[int] = set()
    by_block: dict[str, list[int]] = {}
    for index in included:
        check_id = str(decoded[index].get("check_id") or "")
        by_block.setdefault(check_id[:1], []).append(index)
    for block_indexes in by_block.values():
        anchors.add(max(block_indexes, key=lambda index: _candidate_priority(decoded[index], index)))
    removal_order = sorted(
        included,
        key=lambda index: _candidate_priority(
            decoded[index], index, coverage_anchor=index in anchors
        ),
    )
    for index in removal_order:
        if pack["audit"]["input_pack_bytes"] <= byte_cap:
            break
        included.remove(index)
        pack["excluded_candidates"].append({
            **_candidate_ref(decoded[index], index), "reason": "byte_cap",
        })
        rebuild_rows()
        _refresh_coverage(pack, detected=len(indexes), included=len(included))
        _set_pack_size(pack)
    pack["audit"]["byte_cap_exceeded"] = _set_pack_size(pack) > byte_cap
    _set_pack_size(pack)


def _compact_degradation(report: dict[str, Any]) -> dict[str, Any]:
    columns = ["check_id", "runnable", "type_effective", "confidence_cap", "reason"]
    rows = [
        [
            check.get("check_id"), check.get("runnable"), check.get("type_effective"),
            check.get("confidence_cap"), check.get("reason_if_not_runnable"),
        ]
        for check in report.get("checks") or []
    ]
    return {"columns": columns, "rows": rows, "counts": report.get("counts") or {}}


def _load_inputs(paths: Any) -> dict[str, Any]:
    """Прочитать все inputs/*.yaml клиента как {имя_файла_без_расширения: данные}."""
    inputs_dir = Path(paths.inputs)
    result: dict[str, Any] = {}
    if not inputs_dir.exists():
        return result
    for p in sorted(inputs_dir.glob("*.yaml")):
        with p.open("r", encoding="utf-8") as fh:
            value = _prune_empty(yaml.safe_load(fh) or {})
            if value not in (None, "", [], {}):
                result[p.stem] = value
    return result


def _load_degradation_report(paths: Any) -> dict[str, Any]:
    """Прочитать data/metrics/degradation_report.json; отсутствие -> пустой отчёт."""
    path = Path(paths.metrics) / "degradation_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _confidence_caps(degradation_report: dict[str, Any]) -> dict[str, str]:
    """{check_id: confidence_cap} — потолок №2 (источник), см. schemas.validate_finding."""
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (degradation_report.get("checks") or [])
        if c.get("check_id")
    }


def _client_context(config: dict[str, Any]) -> dict[str, Any]:
    """Контекст клиента (ниша/гео/бренд/окно анализа) — не бизнес-числа, а рамка отчёта."""
    config = config or {}
    client = config.get("client") or {}
    return {
        "name": client.get("name"),
        "niche": client.get("niche"),
        "geo": client.get("geo"),
        "brand_terms": config.get("brand_terms") or [],
        "data_window": config.get("data_window") or {},
    }


def build_input_pack(
    paths: Any,
    config: dict[str, Any],
    methodology: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать компактный пакет P06-кандидатов, контекста и ограничений.

    Пакет должен быть JSON-сериализуем целиком (это и есть будущее тело запроса
    к API) — используются только примитивы, списки и словари, никаких
    объектов Path/datetime и т.п.
    """
    defaults = defaults or {}
    degradation_report = _load_degradation_report(paths)
    analysis_candidates = _load_json_artifact(paths, "analysis_candidates") or {
        "columns": [], "rows": [], "coverage": {}
    }
    compact_context = {
        stem: payload
        for stem in _COMPACT_CONTEXT_ARTIFACTS
        if (payload := _load_json_artifact(paths, stem)) is not None
    }
    byte_cap = int(defaults.get("analyze_input_pack_byte_cap") or INPUT_PACK_BYTE_CAP)
    pack = {
        "client_context": _client_context(config),
        "check_names": {
            c.get("id"): c.get("name")
            for c in (methodology.get("checks") or [])
            if c.get("id")
        },
        "known_check_ids": sorted(schemas.known_check_ids(methodology)),
        "analysis_candidates": analysis_candidates,
        "compact_context": compact_context,
        "inputs": _load_inputs(paths),
        "degradation": _compact_degradation(degradation_report),
        "constraints": {
            "sample_size_rule": {
                "min_sample_visits": defaults.get("min_sample_visits"),
                "significance_alpha": defaults.get("significance_alpha"),
            },
            "source_cap_by_check": _confidence_caps(degradation_report),
            "money_categories": dict(schemas.MONEY_CATEGORIES),
            "max_findings_per_run": schemas.MAX_FINDINGS_PER_RUN,
            "currency_round": defaults.get("currency_round", 0),
        },
        "coverage": {"source": analysis_candidates.get("coverage") or {}},
        "excluded_candidates": [],
        "audit": {"byte_cap": byte_cap},
    }
    _apply_byte_cap(pack, byte_cap)
    return pack


# ── Системный промт ─────────────────────────────────────────────────────────
def build_system_prompt(defaults: dict[str, Any] | None = None) -> str:
    """Текст системного промта будущего вызова модели (сам вызов — вне этой задачи).

    Запреты (обязательны, независимо от формулировки конкретной находки):
      1. Числа только из входного пакета — никаких досчётов, оценок «на глаз»
         или цифр из общих знаний о рынке/отрасли.
      2. significant=false запрещено: незначимая разница не становится находкой
         ни при каком confidence (см. sample_size_rule во входном пакете).
      3. Процентные пункты (п.п.) и проценты (%) не путать: разница долей —
         это п.п., а не «рост на N%».
      4. Денежные категории (4 шт., см. money_categories) не смешивать в одну
         сумму — у находки ровно одна категория либо money_not_assessable=true.
      5. Не больше max_findings_per_run находок после объединения сегментов.
      6. Без обвинений конкретных людей/менеджеров/подрядчика — только
         проверяемый факт в данных и рекомендация.
      7. confidence не выше меньшего из двух потолков (см.
         constraints во входном пакете); исключение — client-HIGH.
      8. check_id — только из known_check_ids входного пакета (новый реестр
         D/A/T/C/S), не из legacy-нумерации.
    """
    defaults = defaults or {}
    min_sample = defaults.get("min_sample_visits", 500)
    alpha = defaults.get("significance_alpha", 0.05)
    money_categories_text = "; ".join(
        f"{key} — {label}" for key, label in schemas.MONEY_CATEGORIES.items()
    )

    return (
        "Ты формулируешь черновики находок маркетинговой диагностики поверх уже "
        "посчитанных детерминированных чисел из входного пакета. Тебе запрещено:\n"
        "1. Использовать любые числа, которых нет во входном пакете (analysis_candidates/"
        "compact_context/inputs/degradation). Не досчитывать, не оценивать на глаз, "
        "не подставлять цифры "
        "из общих знаний о рынке или отрасли.\n"
        f"2. Публиковать находку с significant=false: различие с p >= {alpha} "
        f"(significance_alpha) или выборка < {min_sample} визитов (min_sample_visits) — "
        "не находка ни при каком confidence, как бы эффектно ни выглядело число.\n"
        "3. Путать процентные пункты (п.п.) и проценты (%): разница долей 12% и 15% — "
        "это 3 п.п., а не «рост на 25%». Проверяй формулировку каждой числовой разницы "
        "отдельно, до того как её записать.\n"
        "4. Смешивать денежные категории в одну сумму. Каждая денежная оценка относится "
        f"ровно к одной из четырёх категорий ({money_categories_text}) либо помечена "
        "money_not_assessable=true («в ₽ не оценить»). Складывать суммы из разных "
        "категорий в общий итог запрещено (каталог v2, правило 15).\n"
        f"5. После объединения повторяющихся сегментов одной проблемы формулировать больше "
        f"{schemas.MAX_FINDINGS_PER_RUN} находок за один прогон. Лимит применяется только "
        "после объединения.\n"
        "6. Обвинять конкретных людей, менеджеров, отдел продаж или подрядчика. Находка "
        "описывает проверяемый факт в данных и рекомендацию по нему, а не действия людей "
        "(каталог v2, §1 «не включено», §11 «что нельзя утверждать»).\n"
        "7. Повышать confidence выше любого из двух потолков (см. constraints "
        "входного пакета): потолок выборки (уже применён к кандидату) и потолок "
        "источника (confidence_cap проверки в degradation). Итоговая confidence — минимум "
        "из обоих. Исключение — client-HIGH: факт со слов клиента не подчиняется потолку "
        "источника.\n"
        "8. Придумывать check_id. Используй только ID из known_check_ids входного пакета "
        "(буква блока D/A/T/C/S + номер, напр. A07) — старые числовые ID (0.1, 2.2…) в "
        "этом слое не используются.\n\n"
        "Каждая находка заполняется по единой карточке (каталог v2, §12) и обязана нести "
        "money_category (или money_not_assessable=true, если сумму в рублях оценить нельзя)."
    )


# ── Вызов модели (задача 6B; единственное место в пайплайне — принцип 3) ────
# Модель, ключ и base URL — из project env, НЕ из
# clients/<name>/.env — см. докстринг модуля.
LLM_MODEL_ENV_VAR = "ANALYZE_LLM_MODEL"
DEFAULT_LLM_MODEL = "gpt-5.6-terra"
LLM_BASE_URL_ENV_VAR = "ANALYZE_LLM_BASE_URL"
DEFAULT_LLM_BASE_URL = "https://api.proxyapi.ru/openai/v1"
PROXYAPI_API_KEY_ENV_VAR = "PROXYAPI_API_KEY"
LLM_MAX_TOKENS = 8000          # предсказуемый бюджет одного структурированного вызова
LLM_TIMEOUT_SECONDS = 180.0
LLM_MAX_RETRIES = 2            # ретраи транспортного уровня SDK (сеть/429/5xx)

_FINDING_FIELD_NAMES = frozenset(f.name for f in dataclasses.fields(schemas.Finding))


def _resolve_llm_model() -> str:
    return os.environ.get(LLM_MODEL_ENV_VAR) or DEFAULT_LLM_MODEL


def _resolve_llm_base_url() -> str:
    return os.environ.get(LLM_BASE_URL_ENV_VAR) or DEFAULT_LLM_BASE_URL


def _finding_item_schema(known_check_ids: list[str] | None = None) -> dict[str, Any]:
    """JSON Schema одной находки для text.format.

    Форма зеркалит schemas.Finding. Закрытые множества заданы здесь, чтобы
    неверные значения не могли пройти structured output.
    """
    return {
        "type": "object",
        "properties": {
            "check_id": {
                "type": "string",
                **({"enum": known_check_ids} if known_check_ids else {}),
            },
            "name": {"type": "string"},
            "status": {"type": "string", "enum": sorted(schemas.STATUS_VALUES)},
            "confidence": {"type": "string", "enum": sorted(schemas.CONFIDENCE_VALUES)},
            "significant": {"type": "boolean"},
            "period": {"type": "string"},
            "segment": {"type": ["string", "null"]},
            "data_source": {"type": "string"},
            "evidence": {"type": "string"},
            "control_metric": {"type": ["string", "null"]},
            "what_is_distorted": {"type": "string"},
            "money_category": {
                "type": ["string", "null"],
                "enum": [None, *sorted(schemas.MONEY_CATEGORY_VALUES)],
            },
            "money_amount_rub": {"type": ["number", "null"]},
            "money_not_assessable": {"type": "boolean"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {"type": "string"},
            "how_to_measure": {"type": "string"},
            "what_cannot_be_concluded": {"type": "string"},
            "source_check_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "check_id", "name", "status", "confidence", "significant", "period",
            "segment", "data_source", "evidence", "control_metric", "what_is_distorted",
            "money_category", "money_amount_rub", "money_not_assessable", "assumptions",
            "recommended_action", "how_to_measure", "what_cannot_be_concluded",
            "source_check_ids",
        ],
        "additionalProperties": False,
    }


def _findings_response_schema(known_check_ids: list[str] | None = None) -> dict[str, Any]:
    """Схема ответа целиком: {"findings": [...]} — один структурированный вызов."""
    return {
        "type": "object",
        "properties": {
            "findings": {"type": "array", "items": _finding_item_schema(known_check_ids)}
        },
        "required": ["findings"],
        "additionalProperties": False,
    }


def _call_llm(
    system_prompt: str, input_pack: dict[str, Any], *, client: Any = None
) -> dict[str, Any]:
    """Один структурированный вызов модели поверх input_pack. Возвращает {"findings": [...]}.

    Ретраи (``timeout``/``max_retries``) — только на транспортном уровне SDK
    (сетевые сбои, 429, 5xx). Если модель уже вернула валидный (парсящийся)
    ответ, повторный вызов не делается — дальнейшая фильтрация находок
    происходит локально в draft().

    ``client`` — точка подмены для тестов (см. tests/test_analyze*.py); по
    умолчанию создаётся openai.OpenAI() с ключом только из project env.
    """
    if client is None:
        api_key = os.environ.get(PROXYAPI_API_KEY_ENV_VAR)
        if not api_key:
            raise RuntimeError(
                "PROXYAPI_API_KEY не задан в project environment; "
                "analyze не читает clients/<name>/.env"
            )

        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=_resolve_llm_base_url(),
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )

    response = client.responses.create(
        model=_resolve_llm_model(),
        max_output_tokens=LLM_MAX_TOKENS,
        instructions=system_prompt,
        input=[{
            "role": "user",
            "content": (
                "Компактный входной пакет (кандидаты/контекст/ограничения) в "
                "формате JSON:\n\n" + json.dumps(
                    input_pack, ensure_ascii=False, separators=(",", ":")
                )
            ),
        }],
        text={"format": {
            "type": "json_schema",
            "name": "analyze_findings",
            "strict": True,
            "schema": _findings_response_schema(input_pack.get("known_check_ids") or []),
        }},
    )

    return json.loads(response.output_text)


def _finding_from_dict(item: Any) -> schemas.Finding | None:
    """Собрать Finding из одного элемента ответа модели; некорректная форма -> None.

    Только структурная сборка (нужные ключи, dataclass принял значения) —
    смысловую проверку полей делает schemas.validate_finding в draft().
    """
    if not isinstance(item, dict):
        return None
    kwargs = {k: v for k, v in item.items() if k in _FINDING_FIELD_NAMES}
    try:
        return schemas.Finding(**kwargs)
    except TypeError:
        return None


def _finding_filenames(findings: list[schemas.Finding]) -> list[str]:
    """F-<блок>-<nn>.yaml — nn последовательный внутри блока для этого прогона."""
    counters: dict[str, int] = {}
    names: list[str] = []
    for finding in findings:
        block = (finding.check_id[:1] if finding.check_id else "X").upper()
        counters[block] = counters.get(block, 0) + 1
        names.append(f"F-{block}-{counters[block]:02d}.yaml")
    return names


def _group_findings(findings: list[schemas.Finding]) -> list[schemas.Finding]:
    """Объединить повторяющиеся сегменты одной проблемы до применения лимита."""
    grouped: dict[tuple[Any, ...], schemas.Finding] = {}
    for finding in findings:
        key = (
            finding.check_id,
            finding.name,
            finding.status,
            finding.confidence,
            finding.period,
            finding.data_source,
            finding.what_is_distorted,
            finding.money_category,
            finding.money_amount_rub,
            finding.money_not_assessable,
            finding.recommended_action,
        )
        current = grouped.get(key)
        if current is None:
            grouped[key] = dataclasses.replace(
                finding,
                assumptions=list(finding.assumptions),
                source_check_ids=list(finding.source_check_ids),
            )
            continue
        for field_name in ("segment", "evidence", "control_metric"):
            values = [getattr(current, field_name), getattr(finding, field_name)]
            unique = list(dict.fromkeys(value for value in values if value))
            setattr(current, field_name, "; ".join(unique) if unique else None)
        current.assumptions = list(dict.fromkeys([*current.assumptions, *finding.assumptions]))
        current.source_check_ids = list(dict.fromkeys([
            *current.source_check_ids, *finding.source_check_ids
        ]))
    return list(grouped.values())


REJECTED_DIRNAME = "rejected"


def _write_rejected(findings_draft_dir: Path, index: int, raw_item: Any, reasons: list[str]) -> str:
    """Записать один отклонённый ответ модели в findings/draft/rejected/R-<nn>.yaml.

    ``raw_item`` — необработанный элемент ответа модели (как есть, до сборки
    в schemas.Finding, чтобы причина отказа была видна вместе с исходным
    текстом даже для структурно сломанных ответов). ``reasons`` — машинные
    причины отказа (schemas.validate_finding + validate_findings_mod.
    validate_finding_evidence), не меньше одной.
    """
    rejected_dir = findings_draft_dir / REJECTED_DIRNAME
    rejected_dir.mkdir(parents=True, exist_ok=True)
    filename = f"R-{index:02d}.yaml"
    payload = {"reasons": reasons, "finding": raw_item}
    (rejected_dir / filename).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return filename


# ── Точка входа слоя (контракт: см. докстринг модуля) ──────────────────────
def draft(
    paths: Any,
    config: dict[str, Any],
    methodology: dict[str, Any],
    *,
    client: Any = None,
) -> list[str]:
    """Собрать входной пакет, вызвать модель и записать находки в findings/draft/.

    Всегда пишет аудиторский артефакт (INPUT_PACK_ARTIFACT_NAME, имя начинается
    с "_", чтобы не перепутать с находкой) — пакет и системный промт для
    сверки/отладки. Затем вызывает модель один раз (см. _call_llm)
    и для каждой находки в ответе проверяет структурную форму
    (schemas.validate_finding) и evidence (validate_findings_mod.
    validate_finding_evidence — задача 6C: числа находки обязаны реально
    присутствовать в data/metrics, confidence не выше compute-уровня).
    Прошедшие обе проверки находки пишутся как
    findings/draft/F-<блок>-<nn>.yaml (повторы одной проблемы сначала
    объединяются, затем применяется schemas.MAX_FINDINGS_PER_RUN).
    Не прошедшие — в findings/draft/rejected/R-<nn>.yaml с машинной причиной
    (для ручного разбора аналитиком, не для повторной генерации).

    ``client`` — точка подмены для тестов, см. _call_llm.

    Возвращает список записанных имён файлов в findings/draft/ (не в
    rejected/): аудиторский артефакт + карточки прошедших валидацию находок.
    """
    paths.findings_draft.mkdir(parents=True, exist_ok=True)
    defaults = orchestrator_mod.load_defaults()

    pack = build_input_pack(paths, config, methodology, defaults)
    system_prompt = build_system_prompt(defaults)

    out_path = Path(paths.findings_draft) / INPUT_PACK_ARTIFACT_NAME
    out_path.write_text(
        json.dumps(
            {**pack, "system_prompt": system_prompt},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    written = [out_path.name]

    response_data = _call_llm(system_prompt, pack, client=client)
    raw_findings = response_data.get("findings") or []
    returned_check_ids = {
        item.get("check_id")
        for item in raw_findings
        if isinstance(item, dict) and isinstance(item.get("check_id"), str)
    }
    source_metrics = _load_source_metrics(paths, returned_check_ids)

    known_ids = schemas.known_check_ids(methodology)
    degradation_report = _load_degradation_report(paths)
    confidence_caps = _confidence_caps(degradation_report)

    valid_findings: list[schemas.Finding] = []
    rejected_count = 0
    for item in raw_findings:
        finding = _finding_from_dict(item)
        if finding is None:
            rejected_count += 1
            _write_rejected(
                Path(paths.findings_draft), rejected_count, item,
                ["ответ модели не собирается в schemas.Finding (не совпадают поля)"],
            )
            continue

        cap = confidence_caps.get(finding.check_id)
        errors = schemas.validate_finding(finding, known_ids=known_ids, confidence_cap=cap)
        errors += validate_findings_mod.validate_finding_evidence(
            finding,
            metrics=source_metrics,
            inputs={
                **pack["inputs"],
                "analysis_candidates": pack["analysis_candidates"],
                "compact_context": pack["compact_context"],
            },
            degradation_report=degradation_report,
        )
        if errors:
            rejected_count += 1
            _write_rejected(Path(paths.findings_draft), rejected_count, item, errors)
            continue
        valid_findings.append(finding)

    valid_findings = _group_findings(valid_findings)[: schemas.MAX_FINDINGS_PER_RUN]
    for finding, filename in zip(valid_findings, _finding_filenames(valid_findings)):
        finding_path = Path(paths.findings_draft) / filename
        finding_path.write_text(
            yaml.safe_dump(
                schemas.finding_to_ordered_dict(finding),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        written.append(filename)

    return written
