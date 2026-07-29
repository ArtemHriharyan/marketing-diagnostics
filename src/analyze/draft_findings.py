"""Генерация черновиков находок из метрик и качественных входов.

Контракт:
    Читает   — data/metrics/* (артефакты compute), data/metrics/degradation_report.json,
               inputs/client_answers.yaml, inputs/webvisor_findings.yaml,
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
    build_input_pack()   — собирает всё, что модель получит на вход: метрики,
                            качественные inputs, деградацию (оба потолка
                            уверенности), контекст клиента, реестр check_id.
    build_system_prompt() — текст системного промта с запретами модели (см.
                            docstring функции).

Задача 6B подключает сам вызов модели (единственное место в пайплайне,
где это разрешено — принцип 3 CLAUDE.md):
    _call_llm()           — один структурированный вызов (output_config.format)
                            поверх input_pack; предсказуемый токен-бюджет
                            (LLM_MAX_TOKENS), без повторной генерации после
                            валидного ответа (ретраи — только транспортные,
                            через timeout/max_retries самого SDK).
    draft()               — собирает пакет, пишет его как аудиторский артефакт
                            (INPUT_PACK_ARTIFACT_NAME), вызывает модель и
                            записывает прошедшие schemas.validate_finding
                            находки как findings/draft/F-<блок>-<nn>.yaml
                            (не больше schemas.MAX_FINDINGS_PER_RUN).

    Модель и ключ API берутся из project env (anthropic.Anthropic() по
    умолчанию читает ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN из process env),
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
_METRICS_EXCLUDE_STEMS = frozenset({"degradation_report", "metrics_summary"})

INPUT_PACK_ARTIFACT_NAME = "_analyze_input_pack.json"


# ── Сбор входного пакета ────────────────────────────────────────────────────
def _load_metrics(paths: Any) -> dict[str, Any]:
    """Прочитать все data/metrics/*.json (кроме служебных) как {имя: содержимое}.

    Отсутствующий каталог/битый файл -> соответствующий ключ просто не появится
    (принцип 4: пайплайн не падает от отсутствия источника).
    """
    metrics_dir = Path(paths.metrics)
    if not metrics_dir.exists():
        return {}
    result: dict[str, Any] = {}
    for p in sorted(metrics_dir.glob("*.json")):
        if p.stem in _METRICS_EXCLUDE_STEMS:
            continue
        try:
            result[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return result


def _load_inputs(paths: Any) -> dict[str, Any]:
    """Прочитать все inputs/*.yaml клиента как {имя_файла_без_расширения: данные}."""
    inputs_dir = Path(paths.inputs)
    result: dict[str, Any] = {}
    if not inputs_dir.exists():
        return result
    for p in sorted(inputs_dir.glob("*.yaml")):
        with p.open("r", encoding="utf-8") as fh:
            result[p.stem] = yaml.safe_load(fh) or {}
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
    """Собрать всё, что уходит модели: metrics + inputs + degradation + контекст.

    Пакет должен быть JSON-сериализуем целиком (это и есть будущее тело запроса
    к API) — используются только примитивы, списки и словари, никаких
    объектов Path/datetime и т.п.
    """
    defaults = defaults or {}
    degradation_report = _load_degradation_report(paths)

    return {
        "client_context": _client_context(config),
        "methodology_check_names": {
            c.get("id"): c.get("name")
            for c in (methodology.get("checks") or [])
            if c.get("id")
        },
        "known_check_ids": sorted(schemas.known_check_ids(methodology)),
        "metrics": _load_metrics(paths),
        "inputs": _load_inputs(paths),
        "degradation": {
            "runnable_check_ids": degradation_report.get("runnable_check_ids") or [],
            "skipped": degradation_report.get("skipped") or [],
            "checks": degradation_report.get("checks") or [],
            "counts": degradation_report.get("counts") or {},
        },
        # Два потолка уверенности (оба обязаны соблюдаться, берётся меньший):
        #   1) потолок выборки  — уже применён к каждому числу на уровне compute
        #      (см. поле "confidence" внутри "metrics" выше); правило воспроизведено
        #      здесь как параметры, не как готовое значение — LLM само не считает.
        #   2) потолок источника — per-check confidence_cap из degradation_report.
        "confidence_ceilings": {
            "sample_size_rule": {
                "min_sample_visits": defaults.get("min_sample_visits"),
                "significance_alpha": defaults.get("significance_alpha"),
                "note": (
                    "Потолок №1 (выборка): HIGH только на визит-уровне при выборке >= "
                    "min_sample_visits и статистически значимой разнице (p < "
                    "significance_alpha); уже применён к полю confidence внутри metrics — "
                    "не пересчитывать заново."
                ),
            },
            "source_cap_by_check": _confidence_caps(degradation_report),
        },
        "money_categories": dict(schemas.MONEY_CATEGORIES),
        "max_findings_per_run": schemas.MAX_FINDINGS_PER_RUN,
        "currency_round": defaults.get("currency_round", 0),
    }


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
      5. Не больше max_findings_per_run находок за прогон.
      6. Без обвинений конкретных людей/менеджеров/подрядчика — только
         проверяемый факт в данных и рекомендация.
      7. confidence не выше меньшего из двух потолков (см.
         confidence_ceilings во входном пакете); исключение — client-HIGH.
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
        "1. Использовать любые числа, которых нет во входном пакете (metrics/inputs/"
        "degradation). Не досчитывать, не оценивать на глаз, не подставлять цифры "
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
        f"5. Формулировать больше {schemas.MAX_FINDINGS_PER_RUN} находок за один прогон — "
        "если кандидатов больше, оставь самые денежно значимые и весомые, остальные не "
        "публикуй в этом прогоне.\n"
        "6. Обвинять конкретных людей, менеджеров, отдел продаж или подрядчика. Находка "
        "описывает проверяемый факт в данных и рекомендацию по нему, а не действия людей "
        "(каталог v2, §1 «не включено», §11 «что нельзя утверждать»).\n"
        "7. Повышать confidence выше любого из двух потолков (см. confidence_ceilings "
        "входного пакета): потолок выборки (уже применён к числу в metrics) и потолок "
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
# Модель и ключ — из project env (anthropic.Anthropic() по умолчанию читает
# ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN из process env), НЕ из
# clients/<name>/.env — см. докстринг модуля.
LLM_MODEL_ENV_VAR = "ANALYZE_LLM_MODEL"
DEFAULT_LLM_MODEL = "claude-opus-4-8"
LLM_MAX_TOKENS = 8000          # предсказуемый бюджет одного структурированного вызова
LLM_TIMEOUT_SECONDS = 180.0
LLM_MAX_RETRIES = 2            # ретраи транспортного уровня SDK (сеть/429/5xx)

_FINDING_FIELD_NAMES = frozenset(f.name for f in dataclasses.fields(schemas.Finding))


def _resolve_llm_model() -> str:
    return os.environ.get(LLM_MODEL_ENV_VAR) or DEFAULT_LLM_MODEL


def _finding_item_schema() -> dict[str, Any]:
    """JSON Schema одной находки для output_config.format.

    Форма зеркалит schemas.Finding. Допустимые значения status/confidence/
    money_category намеренно не заданы через enum здесь — их проверяет
    schemas.validate_finding после разбора ответа (structured outputs плохо
    сочетают nullable-поля с enum; смысловая проверка и так нужна отдельно).
    """
    return {
        "type": "object",
        "properties": {
            "check_id": {"type": "string"},
            "name": {"type": "string"},
            "status": {"type": "string"},
            "confidence": {"type": "string"},
            "significant": {"type": "boolean"},
            "period": {"type": "string"},
            "segment": {"type": ["string", "null"]},
            "data_source": {"type": "string"},
            "evidence": {"type": "string"},
            "control_metric": {"type": ["string", "null"]},
            "what_is_distorted": {"type": "string"},
            "money_category": {"type": ["string", "null"]},
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
            "data_source", "evidence", "what_is_distorted", "money_not_assessable",
            "assumptions", "recommended_action", "how_to_measure",
            "what_cannot_be_concluded", "source_check_ids",
        ],
        "additionalProperties": False,
    }


def _findings_response_schema() -> dict[str, Any]:
    """Схема ответа целиком: {"findings": [...]} — один структурированный вызов."""
    return {
        "type": "object",
        "properties": {"findings": {"type": "array", "items": _finding_item_schema()}},
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
    умолчанию создаётся anthropic.Anthropic() (ключ/модель — из project env,
    см. докстринг модуля).
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic(timeout=LLM_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES)

    response = client.messages.create(
        model=_resolve_llm_model(),
        max_tokens=LLM_MAX_TOKENS,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                "Входной пакет (metrics/inputs/degradation/контекст клиента) в "
                "формате JSON:\n\n" + json.dumps(input_pack, ensure_ascii=False)
            ),
        }],
        output_config={"format": {"type": "json_schema", "schema": _findings_response_schema()}},
    )

    text = next(block.text for block in response.content if getattr(block, "type", None) == "text")
    return json.loads(text)


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
    с "_", чтобы не перепутать с находкой) — то же тело запроса, что ушло
    модели, для сверки/отладки. Затем вызывает модель один раз (см. _call_llm)
    и для каждой находки в ответе проверяет структурную форму
    (schemas.validate_finding) и evidence (validate_findings_mod.
    validate_finding_evidence — задача 6C: числа находки обязаны реально
    присутствовать в data/metrics, confidence не выше compute-уровня).
    Прошедшие обе проверки находки пишутся как
    findings/draft/F-<блок>-<nn>.yaml (не больше schemas.MAX_FINDINGS_PER_RUN;
    лишние в ответе модели отбрасываются, а не докидываются в отчёт).
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
        json.dumps({**pack, "system_prompt": system_prompt}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written = [out_path.name]

    response_data = _call_llm(system_prompt, pack, client=client)
    raw_findings = (response_data.get("findings") or [])[: schemas.MAX_FINDINGS_PER_RUN]

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
            metrics=pack["metrics"],
            inputs=pack["inputs"],
            degradation_report=degradation_report,
        )
        if errors:
            rejected_count += 1
            _write_rejected(Path(paths.findings_draft), rejected_count, item, errors)
            continue
        valid_findings.append(finding)

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
