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

Задача 6A — детерминированная оболочка БЕЗ вызова API Anthropic:
    build_input_pack()   — собирает всё, что модель получит на вход: метрики,
                            качественные inputs, деградацию (оба потолка
                            уверенности), контекст клиента, реестр check_id.
    build_system_prompt() — текст системного промта с запретами модели (см.
                            docstring функции).
    draft()               — на этом этапе только собирает и валидирует пакет,
                            пишет его как аудиторский артефакт в findings/draft/
                            (НЕ находка — сама генерация находок LLM подключается
                            отдельной задачей, когда появится вызов API).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..pipeline import orchestrator as orchestrator_mod
from . import schemas

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
    import yaml

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


# ── Точка входа слоя (контракт: см. докстринг модуля) ──────────────────────
def draft(paths: Any, config: dict[str, Any], methodology: dict[str, Any]) -> list[str]:
    """Собрать и провалидировать входной пакет для модели; API пока не вызывается.

    Задача 6A — только детерминированная оболочка: собрать input pack, построить
    системный промт, записать их как аудиторский артефакт в findings/draft/ (имя
    начинается с "_", чтобы не перепутать с настоящей находкой — находки-карточки
    появятся отдельной задачей вместе с подключением вызова модели).

    Возвращает список записанных имён файлов (сейчас — ровно один аудиторский
    артефакт, не находка).
    """
    paths.findings_draft.mkdir(parents=True, exist_ok=True)
    defaults = orchestrator_mod.load_defaults()

    pack = build_input_pack(paths, config, methodology, defaults)
    pack["system_prompt"] = build_system_prompt(defaults)

    out_path = Path(paths.findings_draft) / INPUT_PACK_ARTIFACT_NAME
    out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    return [out_path.name]
