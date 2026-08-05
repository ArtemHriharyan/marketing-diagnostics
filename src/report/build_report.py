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

Задача 7E: явный контракт доступа к посчитанной экономике
(``load_report_economics``) — report читает ``cost_summary.json``,
``acquisition_economics.json`` и ``money_frame.json`` строго по карте
``config/report_economics_map.yaml`` («строка отчёта -> адрес ключа») и
НИЧЕГО не вычисляет: ни делений, ни сумм (принцип 2 CLAUDE.md,
methodology-v2 §8 — блок M только собирает уже посчитанное). Правила:
    * ключ отсутствует/значение не определено -> строка остаётся со
      статусом «не посчитано» и причиной (из ``degradation_report`` по
      объявленным в карте ``degradation_check_ids``, иначе — адрес
      отсутствующего ключа). Никогда не 0 и никогда не пропуск строки.
    * файл metrics отсутствует целиком -> строки помечаются «экономика не
      посчитана», сборка отчёта не падает.
    * ``attribution_level`` (L0|L1|L2|L_UNKNOWN) читается из metrics по
      адресу карты, а не хардкодится; compute это поле пока не пишет
      (docs/audit_econ.md §2, §4) -> фиксируется ``L_UNKNOWN`` с причиной,
      источник поля закрывает задача 7G.
    * ``cost_per_web_conversion`` (доступна на любом уровне) и
      ``cost_per_deal_by_source`` (только L1/L2) — два независимых поля.
      На L0 второе несёт «недоступно: источник сделки не фиксируется в
      CRM» и никогда не наследует значение первого.
Рендер экономической секции отчёта в эту задачу не входит (задача 7F).

Задача 7F: секция «Экономика привлечения» — вторая по порядку, сразу после
страницы вердикта и до карточек находок. Рендерится ВСЕГДА: это базовая
рамка отчёта, а не находка, поэтому гейт непустого ``findings/approved/``
на неё не влияет. Секция не берёт ни одного числа мимо ``report_economics``
(``load_report_economics``) и ничего не считает сама. Решения, не заданные
явно источниками истины (задокументировано, не угадано молча):
    * Разница между числом записей CRM и числом веб-конверсий (пункт 2)
      НЕ печатается отдельной цифрой: слой report не вычисляет (принцип 2
      CLAUDE.md), а такой цифры нет в ``report_economics``. Вместо вычета
      печатается объяснение, почему числа не совпадают и почему их нельзя
      приводить к одному — каталог v2 §11.10: расхождение Метрики и CRM
      лишь ограничивает выводы уровнем веб-конверсии.
    * Разделение моделей ``cost_per_web_conversion`` между пунктами 3 и 4
      идёт по полю ``basis`` самой модели, а не по её ``id`` (id задаёт
      клиентский конфиг — принцип 1 CLAUDE.md): ``tracked_proxy`` —
      веб-конверсии (таблица пункта 4), ``actual``/``estimate`` — модель на
      CRM-запись (общая стоимость клиента, пункт 3). Смешение запрещено
      каталогом v2 §11.3 (CPA веб-конверсии ≠ стоимость продажи).
    * База НДС печатается по каждой статье расхода из ``money_basis``
      файла ``cost_summary.json``: отдельного признака НДС на статью
      compute не отдаёт (``src/compute/cost_summary.py`` — одна база на
      весь файл), поэтому у каждой строки печатается именно она, а не
      выдуманный признак на статью (каталог v2 §11.7 — расход нельзя
      сравнивать без проверки НДС, значит база должна быть видна на каждой
      строке).
    * Слова CAC/ROI/LTV/ROMI/«прибыль» в секции не употребляются: на L0
      сведение расхода с выручкой по источнику невозможно, а выручка ≠
      прибыль (каталог v2 §11.3, §11.4, §11.11).
    * Пункт 5 на L0 — текстовый абзац без единого числа: что именно нельзя
      посчитать, из-за какого отсутствующего поля (причина из
      ``cost_per_deal_by_source.reason_code``) и что внедрить, чтобы стало
      можно (docs/audit_econ.md §2 — заполняемое поле источника в CRM либо
      ключ для матчинга с визитами).

Задача 7H: класс утечки внутренних строк в клиентский документ закрыт на
уровне механизма, а не вычиткой текста. Три изменения контракта:
    * ``attribution_level`` читается по фактическому адресу — строка
      ``kind="attribution"`` файла ``money_frame.json``
      (src/compute/money_frame.py, ``_attribution_row``), вместе с
      ``attribution_evidence`` и ``unique_customers_available``. Пометка
      ``source_status: not_emitted_by_compute`` с него снята: compute поле
      отдаёт. Доказательство протянуто в контракт, но в клиентский текст не
      выводится — это имена колонок canonical и доли заполненности.
    * ``validate_economics_map`` при загрузке требует от каждого адреса
      карты одного из двух: либо он разрешается в присутствующем файле
      metrics, либо помечен ``source_status: not_emitted_by_compute``.
      Третьего не дано — неразрешимый непомеченный адрес роняет сборку с
      указанием строки карты. Отсутствующий целиком файл проверку не
      проваливает (управляемая деградация, принцип 4 CLAUDE.md), а
      непопадание фильтра ``where`` в элемент списка — факт данных
      клиента, а не ошибка адреса (см. ``_address_structure_error``).
    * Причина «не посчитано» рендерится ТОЛЬКО фразой из закрытого словаря
      ``client_reason_phrases`` карты, по коду ``reason_code`` строки
      контракта (``_client_reason``); внутренняя строка ``reason`` остаётся
      в контракте для диагностики и в клиентский текст не попадает. Кода
      нет в словаре -> «данных источника недостаточно». Поэтому в секции
      нечем напечатать адрес ключа, имя файла, id проверки или id задачи.
Плюс два продуктовых уточнения, следующих из ``unique_customers``:
    * Пункт 3 называется по факту склейки повторных обращений: её нет ->
      «стоимость одной сделки»/«одного обращения» по фактической единице
      записи CRM и без слова «клиент» (склеивать записи не с чем, значит
      величина не про клиента); есть -> «стоимость клиента» плюс отдельная
      строка про повторные обращения.
    * ``not_computable`` (источник принципиально не несёт признака) и
      ``not_computed_yet`` (признак есть, величина не считалась) — разные
      клиентские тексты: первый говорит, что внедрить, второй — что
      внедрять нечего (``client_limitation_phrases``, ``_limitation_lines``).
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

# Задача 7E: контракт доступа к посчитанной экономике ─────────────────────
ECONOMICS_MAP_FILENAME = "report_economics_map.yaml"
ECONOMICS_STATUS_OK = "посчитано"
ECONOMICS_STATUS_MISSING = "не посчитано"
ECONOMICS_STATUS_UNAVAILABLE = "недоступно"
ECONOMICS_SECTION_NOT_COMPUTED = "экономика не посчитана"
ATTRIBUTION_LEVEL_UNKNOWN = "L_UNKNOWN"

# Задача 7H: карта адресов проверяется при загрузке ───────────────────────
MAP_SOURCE_STATUS_NOT_EMITTED = "not_emitted_by_compute"

# Коды причин «не посчитано». В клиентский текст попадает не код и не
# внутренняя строка, а фраза из закрытого словаря карты
# (``client_reason_phrases``) — см. ``_client_reason``.
REASON_FILE_MISSING = "source_file_missing"
REASON_KEY_MISSING = "key_missing"
REASON_VALUE_NULL = "value_not_computed"
REASON_CHECK_SKIPPED = "check_skipped"
REASON_MODEL_NOT_COMPUTED = "model_not_computed"
REASON_DEAL_SOURCE_NOT_RECORDED = "deal_source_not_recorded"
REASON_ATTRIBUTION_UNKNOWN = "attribution_level_unknown"
CLIENT_REASON_FALLBACK = "данных источника недостаточно"

# Статусы величины, требующей внедрения (``not_computable``) против
# временно не посчитанной (``not_computed_yet``) — src/compute/money_frame.py.
STATUS_NOT_COMPUTABLE = "not_computable"
STATUS_NOT_COMPUTED_YET = "not_computed_yet"
STATUS_AVAILABLE = "available"


class EconomicsMapError(RuntimeError):
    """Адрес карты экономики не разрешается и не помечен как не отдаваемый.

    Осознанно роняет сборку отчёта: молчаливое «не посчитано» на опечатке в
    адресе неотличимо для клиента от реального отсутствия данных.
    """

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

# Задача 7F: таблицы приложения под секцию экономики + сноски [4]/[5]/[6].
ECONOMICS_SPEND_CSV = "economics_spend.csv"
ECONOMICS_RESULT_CSV = "economics_result.csv"
ECONOMICS_WEB_CONVERSION_CSV = "economics_cost_per_web_conversion.csv"
FOOTNOTE_ECONOMICS_SPEND = "[4]"
FOOTNOTE_ECONOMICS_RESULT = "[5]"
FOOTNOTE_ECONOMICS_WEB_CONVERSION = "[6]"

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
_NON_PUBLISHABLE_STATUSES = frozenset({"unavailable", "unavailable_for_cause"})
_DIAGNOSTIC_CONTEXT_MARKERS = frozenset({"channel_anomaly_context"})
_T09_CAUSE_LIMITATION = {
    "id": "T09",
    "block": 2,
    "reason": "аномалия наблюдается, причина не установлена",
}


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


def _is_publishable_finding(data: dict[str, Any]) -> bool:
    """Не публиковать сохранённые ограничения и диагностический контекст как finding."""
    if data.get("status") in _NON_PUBLISHABLE_STATUSES:
        return False
    if data.get("finding") in _DIAGNOSTIC_CONTEXT_MARKERS:
        return False
    return not (data.get("check_id") == "T09" and data.get("causal_claim") is False)


def _contains_status(payload: Any, status: str) -> bool:
    if isinstance(payload, dict):
        return payload.get("status") == status or any(
            _contains_status(value, status) for value in payload.values()
        )
    if isinstance(payload, list):
        return any(_contains_status(value, status) for value in payload)
    return False


def _report_limitations(skipped: list[dict[str, Any]], t09_metric: Any) -> list[dict[str, Any]]:
    """Дополнить реестровые пропуски запретом T09 на причинный вывод."""
    limitations = list(skipped)
    if _contains_status(t09_metric, "unavailable_for_cause") and not any(
        item.get("id") == "T09" for item in limitations
    ):
        limitations.append(dict(_T09_CAUSE_LIMITATION))
    return limitations


def load_approved_findings(findings_approved_dir: Path) -> list[dict[str, Any]]:
    """Загрузить утверждённые находки как словари, отсортированные по имени файла."""
    directory = Path(findings_approved_dir)
    if not directory.exists():
        return []
    findings: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        data = _load_yaml(path)
        if data and _is_publishable_finding(data):
            findings.append(data)
    return findings


def load_glossary(config_dir: Path | None = None) -> list[dict[str, str]]:
    """Загрузить термины config/report_glossary.yaml (список {term, definition})."""
    directory = Path(config_dir) if config_dir is not None else CONFIG_DIR
    data = _load_yaml(directory / GLOSSARY_FILENAME)
    return list(data.get("terms") or [])


# ── Экономика: контракт доступа к посчитанным величинам (задача 7E) ──────
def load_economics_map(config_dir: Path | None = None) -> dict[str, Any]:
    """Загрузить карту «строка отчёта -> ключ в файле data/metrics/»."""
    directory = Path(config_dir) if config_dir is not None else CONFIG_DIR
    return _load_yaml(directory / ECONOMICS_MAP_FILENAME)


def _load_json_document(path: Path) -> Any:
    """Прочитать JSON как есть (dict или list); отсутствие файла -> None."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _path_text(path: list[Any]) -> str:
    """Человекочитаемый адрес ключа для поля ``source`` строки контракта."""
    steps: list[str] = []
    for step in path:
        if isinstance(step, dict):
            where = step.get("where") or {}
            steps.append(f"[{where.get('field')}={where.get('equals')}]")
        else:
            steps.append(str(step))
    return ".".join(steps) if steps else "<весь документ>"


def _resolve_map_path(document: Any, path: list[Any]) -> tuple[bool, Any]:
    """Пройти по адресу карты. Возвращает (ключ найден, значение).

    Шаг-строка — ключ словаря; шаг ``{where: {field, equals}}`` — выбор
    элемента списка по значению поля (выбор, не вычисление); пустой путь —
    документ целиком.
    """
    current = document
    for step in path:
        if isinstance(step, dict):
            where = step.get("where") or {}
            if not isinstance(current, list):
                return False, None
            match = next(
                (
                    item
                    for item in current
                    if isinstance(item, dict) and item.get(where.get("field")) == where.get("equals")
                ),
                None,
            )
            if match is None:
                return False, None
            current = match
            continue
        if not isinstance(current, dict) or step not in current:
            return False, None
        current = current[step]
    return True, current


# ── Валидация адресов карты (задача 7H) ──────────────────────────────────
# Верхнеуровневые секции карты и их адресные ключи. Порядок фиксирован —
# он же порядок сообщений об ошибке.
_MAP_ADDRESS_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("attribution_level", ("path", "evidence_path")),
    ("unique_customers", ("path", "status_path")),
    ("cost_per_web_conversion", ("path",)),
    ("cost_per_deal_by_source", ("path_when_available",)),
)


def _map_line_numbers(config_dir: Path | None = None) -> dict[str, int]:
    """Номера строк карты: верхнеуровневые секции и `- id:` каждой строки.

    Нужны только для сообщения об ошибке адреса — чтобы правку делали в
    карте, а не искали её по коду.
    """
    directory = Path(config_dir) if config_dir is not None else CONFIG_DIR
    path = directory / ECONOMICS_MAP_FILENAME
    if not path.exists():
        return {}
    lines: dict[str, int] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if raw[:1].isalpha() and ":" in raw:
            lines.setdefault(raw.split(":", 1)[0].strip(), number)
        elif stripped.startswith("- id:"):
            lines.setdefault(stripped.split(":", 1)[1].strip(), number)
    return lines


def _iter_map_addresses(economics_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Все адреса карты одним списком: (где объявлен, файл, путь, пометка).

    ``item_fields`` сюда не входят: это поля отдельного элемента списка, у
    каждой модели свой набор заполненных, и их отсутствие уже отражается в
    ``missing_fields`` элемента, а не в адресе карты.
    """
    addresses: list[dict[str, Any]] = []
    for section, keys in _MAP_ADDRESS_KEYS:
        spec = economics_map.get(section) or {}
        for key in keys:
            if key not in spec:
                continue
            addresses.append({
                "location": section,
                "line_key": section,
                "file": spec.get("file"),
                "path": list(spec.get(key) or []),
                "source_status": spec.get("source_status"),
            })
    for row_map in economics_map.get("rows") or []:
        addresses.append({
            "location": f"rows/{row_map.get('id')}",
            "line_key": row_map.get("id"),
            "file": row_map.get("file"),
            "path": list(row_map.get("path") or []),
            "source_status": row_map.get("source_status"),
        })
    return addresses


def _address_structure_error(document: Any, path: list[Any]) -> str | None:
    """Несовпадение структуры по адресу; ``None`` — адрес состоятелен.

    Непопадание фильтра ``where`` ошибкой не считается: отсутствие записи
    такого вида — факт данных клиента, а не опечатка в адресе.
    """
    current = document
    for step in path:
        if isinstance(step, dict):
            where = step.get("where") or {}
            if not isinstance(current, list):
                return f"ожидался список для выбора по полю {where.get('field')!r}"
            match = next(
                (
                    item
                    for item in current
                    if isinstance(item, dict) and item.get(where.get("field")) == where.get("equals")
                ),
                None,
            )
            if match is None:
                return None
            current = match
            continue
        if not isinstance(current, dict):
            return f"шаг {step!r}: контейнер не является словарём"
        if step not in current:
            return f"ключ {step!r} отсутствует"
        current = current[step]
    return None


def validate_economics_map(
    economics_map: dict[str, Any],
    documents: dict[str, dict[str, Any]],
    config_dir: Path | None = None,
) -> None:
    """Каждый адрес карты либо разрешается, либо помечен. Третьего нет.

    Отсутствующий целиком файл metrics проверку не проваливает — это
    управляемая деградация (принцип 4 CLAUDE.md), а не ошибка адреса.
    """
    lines = _map_line_numbers(config_dir)
    for address in _iter_map_addresses(economics_map):
        if address["source_status"] == MAP_SOURCE_STATUS_NOT_EMITTED:
            continue
        file_key = address["file"]
        state = documents.get(file_key)
        location = address["location"]
        line = lines.get(address["line_key"])
        where = f"{ECONOMICS_MAP_FILENAME}:{line}" if line else ECONOMICS_MAP_FILENAME
        if state is None:
            raise EconomicsMapError(
                f"{where} ({location}): файл карты {file_key!r} не объявлен в разделе files"
            )
        if not state.get("available"):
            continue
        problem = _address_structure_error(state.get("document"), address["path"])
        if problem is not None:
            raise EconomicsMapError(
                f"{where} ({location}): адрес "
                f"{state.get('filename')}:{_path_text(address['path'])} не разрешается "
                f"({problem}) и не помечен source_status: {MAP_SOURCE_STATUS_NOT_EMITTED}"
            )


def _client_reason(economics: dict[str, Any], code: str | None) -> str:
    """Клиентская формулировка причины — только из закрытого словаря карты.

    Кода нет в словаре -> нейтральная фраза. Внутренняя строка причины
    (адрес ключа, имя файла, id проверки) в клиентский текст не попадает
    никогда: сюда передаётся код, а не текст.
    """
    phrases = economics.get("client_reason_phrases") or {}
    default = phrases.get("default") or CLIENT_REASON_FALLBACK
    if not code:
        return default
    return phrases.get(code) or default


def _economics_documents(metrics_dir: Path, economics_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Прочитать все файлы карты; отсутствующий файл не роняет сборку."""
    documents: dict[str, dict[str, Any]] = {}
    for key, spec in (economics_map.get("files") or {}).items():
        filename = (spec or {}).get("filename") or f"{key}.json"
        # joinpath, а не оператор `/`: в этой части модуля нет ни одного
        # арифметического оператора — контракт ничего не вычисляет (7E).
        document = _load_json_document(Path(metrics_dir).joinpath(filename))
        documents[key] = {
            "filename": filename,
            "label": (spec or {}).get("label") or key,
            "available": document is not None,
            "document": document,
        }
    return documents


def _degradation_reason(degradation: dict[str, Any] | None, check_ids: list[str]) -> str | None:
    """Причина из degradation_report по объявленным в карте id проверок."""
    if not check_ids:
        return None
    wanted = set(check_ids)
    reasons = [
        f"{item.get('id')}: {item.get('reason', '')}"
        for item in ((degradation or {}).get("skipped") or [])
        if item.get("id") in wanted
    ]
    return "; ".join(reasons) if reasons else None


def _economics_row(
    row_map: dict[str, Any],
    documents: dict[str, dict[str, Any]],
    degradation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Одна строка контракта: значение берётся по адресу карты как есть.

    Ни отсутствие файла, ни отсутствие ключа не превращаются в 0 и не
    выбрасывают строку: строка остаётся со статусом «не посчитано»/
    «экономика не посчитана» и причиной.
    """
    doc_state = documents.get(row_map.get("file")) or {}
    filename = doc_state.get("filename") or row_map.get("file")
    path = list(row_map.get("path") or [])
    address = _path_text(path)
    declared_checks = list(row_map.get("degradation_check_ids") or [])

    entry: dict[str, Any] = {
        "id": row_map.get("id"),
        "label": row_map.get("label"),
        "group": row_map.get("group"),
        "unit": row_map.get("unit"),
        "source": f"{filename}:{address}",
        "value": None,
        "available": False,
        "status": ECONOMICS_STATUS_MISSING,
        "reason": None,
        "reason_code": None,
    }
    skipped_reason = _degradation_reason(degradation, declared_checks)

    if not doc_state.get("available"):
        entry["status"] = ECONOMICS_SECTION_NOT_COMPUTED
        entry["reason"] = skipped_reason or f"файл {filename} отсутствует в data/metrics/"
        entry["reason_code"] = REASON_CHECK_SKIPPED if skipped_reason else REASON_FILE_MISSING
        return entry

    found, value = _resolve_map_path(doc_state.get("document"), path)
    if not found:
        entry["reason"] = skipped_reason or f"ключ {address} отсутствует в {filename}"
        entry["reason_code"] = REASON_CHECK_SKIPPED if skipped_reason else REASON_KEY_MISSING
        return entry
    if value is None:
        entry["reason"] = skipped_reason or (
            f"значение ключа {address} не определено в {filename}"
        )
        entry["reason_code"] = REASON_CHECK_SKIPPED if skipped_reason else REASON_VALUE_NULL
        return entry

    entry["value"] = value
    entry["available"] = True
    entry["status"] = ECONOMICS_STATUS_OK
    return entry


def _attribution_level(
    economics_map: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    """Уровень ключа атрибуции: только из metrics, иначе fallback карты.

    Хардкод уровня запрещён: значение читается по адресу карты — строка
    ``kind="attribution"`` файла ``money_frame.json``, которую compute
    пишет вместе с доказательством по каждой проверенной колонке
    (``attribution_evidence``, src/compute/money_frame.py). Доказательство
    протягивается в контракт тем же адресом и в клиентский текст не
    выводится: это имена колонок и доли заполненности.
    """
    spec = economics_map.get("attribution_level") or {}
    fallback = spec.get("fallback") or ATTRIBUTION_LEVEL_UNKNOWN
    allowed = list(spec.get("allowed") or [])
    doc_state = documents.get(spec.get("file")) or {}
    filename = doc_state.get("filename") or spec.get("file")
    path = list(spec.get("path") or [])
    address = _path_text(path)

    source: dict[str, Any] = {
        "source": f"{filename}:{address}",
        "resolved": False,
        "reason": None,
        "evidence": None,
    }

    if not doc_state.get("available"):
        source["reason"] = f"файл {filename} отсутствует в data/metrics/"
        return fallback, source

    document = doc_state.get("document")
    evidence_found, evidence = _resolve_map_path(document, list(spec.get("evidence_path") or []))
    if evidence_found:
        source["evidence"] = evidence

    found, value = _resolve_map_path(document, path)
    if not found:
        source["reason"] = (
            f"строка уровня атрибуции отсутствует в {filename}: уровень пишется "
            "только вместе с доказательством, в этом прогоне он не посчитан"
        )
        return fallback, source
    if value not in allowed:
        source["reason"] = f"значение {value!r} вне допустимого набора {allowed}"
        return fallback, source

    source["resolved"] = True
    return value, source


def _unique_customers(
    economics_map: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Доступна ли склейка повторных обращений в клиентов (задача 7H).

    Читается тем же адресом, что и уровень атрибуции. ``status``
    различает постоянное ограничение источника (``not_computable``) и
    временно не посчитанную величину (``not_computed_yet``) — у них
    разный клиентский текст (пункт 3 секции).
    """
    spec = economics_map.get("unique_customers") or {}
    doc_state = documents.get(spec.get("file")) or {}
    filename = doc_state.get("filename") or spec.get("file")
    path = list(spec.get("path") or [])
    fallback_status = spec.get("fallback_status") or STATUS_NOT_COMPUTED_YET

    result: dict[str, Any] = {
        "label": spec.get("label"),
        "source": f"{filename}:{_path_text(path)}",
        "available": False,
        "resolved": False,
        "status": fallback_status,
    }

    if not doc_state.get("available"):
        return result

    document = doc_state.get("document")
    found, value = _resolve_map_path(document, path)
    if not found or value is None:
        return result

    status_found, status = _resolve_map_path(document, list(spec.get("status_path") or []))
    result["resolved"] = True
    result["available"] = bool(value)
    result["status"] = status if status_found and status else (
        STATUS_AVAILABLE if value else fallback_status
    )
    return result


def _cost_per_web_conversion(
    economics_map: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Стоимость веб-конверсии по моделям привлечения — величина, доступная
    на любом уровне атрибуции (источник сделки для неё не нужен). Никогда не
    подставляется вместо ``cost_per_deal_by_source``.
    """
    spec = economics_map.get("cost_per_web_conversion") or {}
    doc_state = documents.get(spec.get("file")) or {}
    filename = doc_state.get("filename") or spec.get("file")
    path = list(spec.get("path") or [])
    address = _path_text(path)
    item_fields = spec.get("item_fields") or {}

    result: dict[str, Any] = {
        "label": spec.get("label"),
        "source": f"{filename}:{address}",
        "available": False,
        "status": ECONOMICS_STATUS_MISSING,
        "reason": None,
        "reason_code": None,
        "items": [],
    }

    if not doc_state.get("available"):
        result["status"] = ECONOMICS_SECTION_NOT_COMPUTED
        result["reason"] = f"файл {filename} отсутствует в data/metrics/"
        result["reason_code"] = REASON_FILE_MISSING
        return result

    found, models = _resolve_map_path(doc_state.get("document"), path)
    if not found or not isinstance(models, list):
        result["reason"] = f"ключ {address} отсутствует в {filename}"
        result["reason_code"] = REASON_KEY_MISSING
        return result

    for model in models:
        item: dict[str, Any] = {}
        missing_fields: list[str] = []
        for field, field_path in item_fields.items():
            field_found, field_value = _resolve_map_path(model, list(field_path or []))
            if not field_found or field_value is None:
                missing_fields.append(field)
                item[field] = None
                continue
            item[field] = field_value
        item["missing_fields"] = missing_fields
        result["items"].append(item)

    result["available"] = True
    result["status"] = ECONOMICS_STATUS_OK
    return result


def _cost_per_deal_by_source(
    economics_map: dict[str, Any],
    documents: dict[str, dict[str, Any]],
    attribution_level: str,
) -> dict[str, Any]:
    """Стоимость сделки по источникам — только при L1/L2.

    На L0 величина не «не посчитана», а принципиально недоступна: источник
    сделки не фиксируется в CRM. Значение стоимости веб-конверсии сюда не
    наследуется ни при каких условиях.
    """
    spec = economics_map.get("cost_per_deal_by_source") or {}
    required = list(spec.get("requires_attribution_level") or [])
    doc_state = documents.get(spec.get("file")) or {}
    filename = doc_state.get("filename") or spec.get("file")
    path = list(spec.get("path_when_available") or [])
    address = _path_text(path)

    result: dict[str, Any] = {
        "label": spec.get("label"),
        "source": f"{filename}:{address}",
        "attribution_level": attribution_level,
        "requires_attribution_level": required,
        "available": False,
        "value": None,
        "status": ECONOMICS_STATUS_UNAVAILABLE,
        "reason": None,
        "reason_code": None,
    }

    if attribution_level not in required:
        unknown = attribution_level == ATTRIBUTION_LEVEL_UNKNOWN
        result["reason"] = (
            spec.get("unavailable_reason_unknown") if unknown
            else spec.get("unavailable_reason_l0")
        )
        result["reason_code"] = (
            REASON_ATTRIBUTION_UNKNOWN if unknown else REASON_DEAL_SOURCE_NOT_RECORDED
        )
        return result

    result["status"] = ECONOMICS_STATUS_MISSING
    if not doc_state.get("available"):
        result["status"] = ECONOMICS_SECTION_NOT_COMPUTED
        result["reason"] = f"файл {filename} отсутствует в data/metrics/"
        result["reason_code"] = REASON_FILE_MISSING
        return result

    found, value = _resolve_map_path(doc_state.get("document"), path)
    if not found or value is None:
        result["reason"] = f"ключ {address} отсутствует в {filename}"
        result["reason_code"] = REASON_KEY_MISSING
        return result

    result["value"] = value
    result["available"] = True
    result["status"] = ECONOMICS_STATUS_OK
    return result


def load_report_economics(
    metrics_dir: Path,
    degradation: dict[str, Any] | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Собрать объект ``report_economics`` строго по карте (задача 7E).

    Только чтение уже посчитанных величин: report не считает ни делений,
    ни сумм (принцип 2 CLAUDE.md, methodology-v2 §8). Отсутствующий файл
    metrics не роняет сборку — соответствующие строки помечаются
    «экономика не посчитана».
    """
    economics_map = load_economics_map(config_dir)
    documents = _economics_documents(Path(metrics_dir), economics_map)
    # Задача 7H: неразрешимый непомеченный адрес — ошибка карты, а не
    # «не посчитано». Проверяется до сборки строк, чтобы отчёт вообще не
    # собрался с молча пустым показателем.
    validate_economics_map(economics_map, documents, config_dir)

    rows = [
        _economics_row(row_map, documents, degradation)
        for row_map in (economics_map.get("rows") or [])
    ]
    level, level_source = _attribution_level(economics_map, documents)

    files_available = [state.get("available") for state in documents.values()]
    if not any(files_available):
        status, section_note = "not_computed", ECONOMICS_SECTION_NOT_COMPUTED
    elif all(files_available) and all(row["available"] for row in rows):
        status, section_note = "ok", None
    else:
        status, section_note = "partial", None

    return {
        "status": status,
        "section_note": section_note,
        "attribution_level": level,
        "attribution_level_source": level_source,
        "unique_customers": _unique_customers(economics_map, documents),
        "client_reason_phrases": dict(economics_map.get("client_reason_phrases") or {}),
        "client_limitation_phrases": dict(economics_map.get("client_limitation_phrases") or {}),
        "sources": {
            key: {
                "filename": state.get("filename"),
                "label": state.get("label"),
                "available": state.get("available"),
            }
            for key, state in documents.items()
        },
        "rows": rows,
        "cost_per_web_conversion": _cost_per_web_conversion(economics_map, documents),
        "cost_per_deal_by_source": _cost_per_deal_by_source(economics_map, documents, level),
    }


# ── Секция «Экономика привлечения» (задача 7F) ───────────────────────────
ECONOMICS_SECTION_TITLE = "## Экономика привлечения"

# База денег из cost_summary.json / acquisition_economics.json. Расшифровка
# читаемая, но не переопределяющая смысл: compute отдаёт конечные суммы как
# фактически уплачены, НДС повторно не добавляется и не вычитается
# (src/compute/cost_summary.py, докстринг модуля).
_MONEY_BASIS_LABELS = {
    "gross_final_rub": "конечная фактически уплаченная сумма, НДС повторно не начисляется и не вычитается",
}

# Формулировки уровня атрибуции — общие для любого клиента (принцип 1
# CLAUDE.md), берутся по коду из report_economics["attribution_level"].
_ATTRIBUTION_LEVEL_TEXT = {
    "L0": (
        "источник сделки в CRM не фиксируется — расход и результат сводятся "
        "только по всем каналам сразу"
    ),
    "L1": (
        "источник сделки в CRM фиксируется — расход и результат сводятся "
        "в разрезе источника"
    ),
    "L2": (
        "источник и кампания сделки в CRM фиксируются — расход и результат "
        "сводятся в разрезе кампании"
    ),
    ATTRIBUTION_LEVEL_UNKNOWN: (
        "уровень атрибуции не определён — сведение расхода с результатом "
        "по источнику не подтверждено данными"
    ),
}

_CRM_RECORD_UNIT_NAMES = {
    "paid_booking": "оплаченная бронь",
    "lead": "обращение",
    "opportunity": "потенциальная сделка",
    "unknown": "единица записи не определена",
}
_CRM_RECORD_UNIT_NAME_DEFAULT = "единица записи не определена"

# Задача 7H, пункт 3. Без склейки повторных обращений величина называется
# по тому, что фактически лежит в строках CRM, и слово «клиент» в пункте не
# употребляется: одна и та же запись может быть повторной, а склеить её не
# с чем. Единица записи не определена -> «обращение»: это более слабое
# утверждение, чем «сделка» (не заявляет ни оплаты, ни доведения до сделки).
_DEAL_UNIT_TITLES = {
    "paid_booking": "стоимость одной сделки",
    "opportunity": "стоимость одной сделки",
    "lead": "стоимость одного обращения",
}
_DEAL_UNIT_TITLE_DEFAULT = "стоимость одного обращения"
_DEAL_UNIT_PLURAL = {
    "paid_booking": "сделкам",
    "opportunity": "сделкам",
    "lead": "обращениям",
}
_DEAL_UNIT_PLURAL_DEFAULT = "обращениям"
_CUSTOMER_TITLE = "стоимость клиента"

# Разделение моделей между пунктом 3 и пунктом 4 — по basis, не по id
# (см. докстринг модуля, задача 7F).
_WEB_CONVERSION_BASES = frozenset({"tracked_proxy"})
_CRM_RECORD_BASES = frozenset({"actual", "estimate"})


def _econ_row_by_id(economics: dict[str, Any], row_id: str) -> dict[str, Any]:
    """Строка контракта экономики по её id; отсутствие -> пустая строка."""
    for row in economics.get("rows") or []:
        if row.get("id") == row_id:
            return row
    return {"id": row_id, "value": None, "available": False, "reason": None}


def _econ_not_computed(economics: dict[str, Any], row: dict[str, Any]) -> str:
    """«Не посчитано» + причина ТОЛЬКО фразой из закрытого словаря (7H)."""
    return f"{ECONOMICS_STATUS_MISSING}: {_client_reason(economics, row.get('reason_code'))}"


def _money_basis_text(value: Any) -> str:
    """Пометка базы НДС: расшифровка кода; неизвестный код не печатается.

    Печатать сам код нельзя — это внутренний идентификатор, а не
    клиентская формулировка (задача 7H).
    """
    if not value:
        return "база суммы не указана"
    return _MONEY_BASIS_LABELS.get(value, "база суммы не расшифрована")


def _format_count(value: Any) -> str:
    """Счётное значение как есть: целое — с разделителем разрядов, иначе str()."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return str(value)


def _column_index(columns: Any, name: str) -> int | None:
    if isinstance(columns, list) and name in columns:
        return columns.index(name)
    return None


def _web_conversion_items(economics: dict[str, Any]) -> list[dict[str, Any]]:
    return list((economics.get("cost_per_web_conversion") or {}).get("items") or [])


def _items_by_basis(economics: dict[str, Any], bases: frozenset[str]) -> list[dict[str, Any]]:
    return [item for item in _web_conversion_items(economics) if item.get("basis") in bases]


def _build_economics_intro(economics: dict[str, Any]) -> list[str]:
    level = economics.get("attribution_level") or ATTRIBUTION_LEVEL_UNKNOWN
    level_text = _ATTRIBUTION_LEVEL_TEXT.get(level, _ATTRIBUTION_LEVEL_TEXT[ATTRIBUTION_LEVEL_UNKNOWN])
    lines = [ECONOMICS_SECTION_TITLE, ""]
    lines.append(f"_Уровень атрибуции: {level} — {level_text}._")
    lines.append("")
    lines.append(
        "_Раздел собран из уже посчитанных величин и приводится в каждом отчёте "
        "независимо от состава утверждённых находок._"
    )
    lines.append("")
    return lines


def _build_economics_spend(economics: dict[str, Any], currency_round: int) -> list[str]:
    """Пункт 1: расход построчно на статью, с базой НДС у каждой строки."""
    lines = [f"### Полный расход за период {FOOTNOTE_ECONOMICS_SPEND}", ""]

    basis_row = _econ_row_by_id(economics, "spend_money_basis")
    basis_text = _money_basis_text(basis_row.get("value"))

    columns_row = _econ_row_by_id(economics, "spend_by_component_columns")
    rows_row = _econ_row_by_id(economics, "spend_by_component_rows")
    total_row = _econ_row_by_id(economics, "spend_total_rub")

    id_index = _column_index(columns_row.get("value"), "component_id")
    amount_index = _column_index(columns_row.get("value"), "amount_rub")
    channel_index = _column_index(columns_row.get("value"), "channel")
    kind_index = _column_index(columns_row.get("value"), "kind")

    spend_rows = rows_row.get("value")
    if not isinstance(spend_rows, list) or id_index is None or amount_index is None:
        lines.append(f"Расход по статьям — {_econ_not_computed(economics, rows_row)}.")
    else:
        for row in spend_rows:
            if not isinstance(row, list) or len(row) <= max(id_index, amount_index):
                continue
            parts = []
            if kind_index is not None and len(row) > kind_index:
                parts.append(str(row[kind_index]))
            if channel_index is not None and len(row) > channel_index:
                parts.append(f"канал {row[channel_index]}")
            meta = f" ({', '.join(parts)})" if parts else ""
            amount = format_rub(row[amount_index], currency_round)
            lines.append(
                f"- **{row[id_index]}**{meta}: {amount} {FOOTNOTE_ECONOMICS_SPEND} "
                f"— база НДС: {basis_text}"
            )

    if total_row.get("available"):
        lines.append("")
        lines.append(
            f"**Итого расход за период:** {format_rub(total_row.get('value'), currency_round)} "
            f"{FOOTNOTE_ECONOMICS_SPEND} — база НДС: {basis_text}"
        )
    else:
        lines.append("")
        lines.append(f"**Итого расход за период:** {_econ_not_computed(economics, total_row)}.")
    lines.append("")
    return lines


def _build_economics_result(economics: dict[str, Any]) -> list[str]:
    """Пункт 2: результат периода — CRM и веб-конверсии ДВУМЯ числами."""
    lines = [f"### Результат периода {FOOTNOTE_ECONOMICS_RESULT}", ""]

    count_row = _econ_row_by_id(economics, "crm_record_count")
    unit_row = _econ_row_by_id(economics, "crm_record_unit")
    unit_text = _CRM_RECORD_UNIT_NAMES.get(
        unit_row.get("value"), _CRM_RECORD_UNIT_NAME_DEFAULT
    )

    if count_row.get("available"):
        unit_part = f" (единица записи CRM: {unit_text})" if unit_row.get("available") else ""
        lines.append(
            f"- **Сделки и клиенты по данным CRM:** {_format_count(count_row.get('value'))} "
            f"{FOOTNOTE_ECONOMICS_RESULT}{unit_part}"
        )
    else:
        lines.append(
            f"- **Сделки и клиенты по данным CRM:** {_econ_not_computed(economics, count_row)}"
        )

    tracked = [
        item for item in _items_by_basis(economics, _WEB_CONVERSION_BASES)
        if item.get("denominator_value") is not None
    ]
    if tracked:
        for item in tracked:
            # id модели задаёт клиентский конфиг — это внутренний
            # идентификатор, в клиентский текст он не выводится (7H).
            name = item.get("result_name") or "модель привлечения"
            lines.append(
                f"- **Веб-конверсии с сайта:** {_format_count(item.get('denominator_value'))} "
                f"{FOOTNOTE_ECONOMICS_RESULT} (модель: {name})"
            )
    else:
        lines.append(
            f"- **Веб-конверсии с сайта:** {ECONOMICS_STATUS_MISSING}: "
            "модель веб-конверсии не отдала число засчитанных на сайте достижений цели."
        )

    lines.append("")
    lines.append(
        "Это два разных числа, и сводить их к одному нельзя. Записи CRM приходят "
        "из всех каналов сразу — включая звонки, повторные обращения и заявки мимо "
        "сайта. Веб-конверсия — засчитанное на сайте достижение цели в рамках визита; "
        "стала ли она сделкой, из этих данных не видно. Разница между числами не "
        "приводится отдельной цифрой: слой отчёта ничего не вычисляет, а вычитание "
        "сравнивало бы разные единицы учёта. Само расхождение — не ошибка и не потеря: "
        "оно ограничивает выводы уровнем веб-конверсии (см. пункт «Стоимость сделки "
        "по источникам»)."
    )
    lines.append("")
    return lines


def _limitation_lines(economics: dict[str, Any], status: str | None) -> list[str]:
    """Ограничение величины: постоянное против временно отсутствующего (7H).

    ``not_computable`` — источник принципиально не несёт нужного признака,
    поэтому печатается, что именно надо внедрить. ``not_computed_yet`` —
    внедрять нечего, величина просто не считалась. Тексты разные и берутся
    из закрытого словаря карты.
    """
    phrases = economics.get("client_limitation_phrases") or {}
    entry = phrases.get(status) or phrases.get("default") or {}
    lines: list[str] = []
    for text in (entry.get("nature"), entry.get("remedy")):
        if not text:
            continue
        if lines:
            lines.append("")  # отдельными абзацами: ограничение и что внедрить
        lines.append(text)
    return lines


def _build_economics_total_cost(economics: dict[str, Any], currency_round: int) -> list[str]:
    """Пункт 3: одна цифра по всем каналам сразу.

    Заголовок и формулировки зависят от факта склейки повторных обращений
    (``unique_customers.available``, задача 7H): без склейки величина —
    стоимость одной сделки или одного обращения (что фактически лежит в
    строках CRM), и слово «клиент» в пункте не употребляется; со склейкой —
    стоимость клиента плюс отдельная строка про повторные обращения.
    """
    unique = economics.get("unique_customers") or {}
    by_customer = bool(unique.get("available"))
    unit_value = _econ_row_by_id(economics, "crm_record_unit").get("value")
    title = _CUSTOMER_TITLE if by_customer else _DEAL_UNIT_TITLES.get(
        unit_value, _DEAL_UNIT_TITLE_DEFAULT
    )
    plural = "клиентам" if by_customer else _DEAL_UNIT_PLURAL.get(
        unit_value, _DEAL_UNIT_PLURAL_DEFAULT
    )

    lines = [f"### Общая {title} {FOOTNOTE_ECONOMICS_RESULT}", ""]

    candidates = [
        item for item in _items_by_basis(economics, _CRM_RECORD_BASES)
        if item.get("value_rub") is not None
    ]
    if not candidates:
        web = economics.get("cost_per_web_conversion") or {}
        code = web.get("reason_code") or REASON_MODEL_NOT_COMPUTED
        lines.append(f"{ECONOMICS_STATUS_MISSING}: {_client_reason(economics, code)}.")
        lines.append("")
        return lines

    item = candidates[0]
    lines.append(
        f"**{format_rub(item.get('value_rub'), currency_round)}** "
        f"{FOOTNOTE_ECONOMICS_RESULT} — по всем каналам сразу: весь учтённый расход "
        f"периода отнесён ко всем {plural} периода, без разделения по каналам "
        "и кампаниям."
    )
    if item.get("basis") == "estimate":
        lines.append("")
        lines.append(
            "_Знаменатель — оценка по доле, заданной в настройках, а не подсчёт "
            "обращений с сайта; величина показывает порядок, а не точное значение._"
        )

    lines.append("")
    if by_customer:
        lines.append(
            "**Повторные обращения:** записи одного и того же обратившегося "
            "склеиваются между собой, поэтому величина выше — стоимость именно "
            "клиента, а не отдельного обращения. Сколько записей приходится на "
            "повторные, видно из сопоставления числа записей и числа клиентов в "
            f"пункте «Результат периода» {FOOTNOTE_ECONOMICS_RESULT}."
        )
    else:
        lines.extend(_limitation_lines(economics, unique.get("status")))
    lines.append("")
    return lines


def _build_economics_web_conversion(economics: dict[str, Any], currency_round: int) -> list[str]:
    """Пункт 4: таблица «стоимость веб-конверсии» — заголовок буквально такой."""
    lines = [f"### Стоимость веб-конверсии {FOOTNOTE_ECONOMICS_WEB_CONVERSION}", ""]

    tracked = _items_by_basis(economics, _WEB_CONVERSION_BASES)
    if not tracked:
        web = economics.get("cost_per_web_conversion") or {}
        code = web.get("reason_code") or REASON_MODEL_NOT_COMPUTED
        lines.append(f"{ECONOMICS_STATUS_MISSING}: {_client_reason(economics, code)}.")
        lines.append("")
        return lines

    lines.append(
        "| источник / кампания | стоимость веб-конверсии | веб-конверсий за период | учтённый расход |"
    )
    lines.append("|---|---|---|---|")
    for item in tracked:
        name = item.get("result_name") or "модель привлечения"
        value = (
            f"{format_rub(item.get('value_rub'), currency_round)} {FOOTNOTE_ECONOMICS_WEB_CONVERSION}"
            if item.get("value_rub") is not None
            else ECONOMICS_STATUS_MISSING
        )
        count = (
            f"{_format_count(item.get('denominator_value'))} {FOOTNOTE_ECONOMICS_WEB_CONVERSION}"
            if item.get("denominator_value") is not None
            else ECONOMICS_STATUS_MISSING
        )
        spend = (
            f"{format_rub(item.get('numerator_amount_rub'), currency_round)} "
            f"{FOOTNOTE_ECONOMICS_WEB_CONVERSION}"
            if item.get("numerator_amount_rub") is not None
            else ECONOMICS_STATUS_MISSING
        )
        lines.append(f"| {name} | {value} | {count} | {spend} |")
    lines.append("")
    lines.append(
        "_В таблице — стоимость веб-конверсии: расход, отнесённый к засчитанным "
        "на сайте достижениям цели. Это не стоимость обращения, не стоимость "
        "заявки и не стоимость клиента._"
    )
    lines.append("")
    return lines


def _build_economics_cost_per_deal(economics: dict[str, Any]) -> list[str]:
    """Пункт 5: на L0 — абзац без единого числа, не таблица."""
    lines = ["### Стоимость сделки по источникам", ""]

    deal = economics.get("cost_per_deal_by_source") or {}
    level = economics.get("attribution_level") or ATTRIBUTION_LEVEL_UNKNOWN

    if deal.get("available"):
        lines.append(
            f"Величина посчитана и приведена в приложении к разделу "
            f"{FOOTNOTE_ECONOMICS_RESULT}."
        )
        lines.append("")
        return lines

    if level == "L0":
        cause = _client_reason(economics, deal.get("reason_code"))
        lines.append(
            f"Стоимость сделки в разрезе источника и кампании на текущем уровне "
            f"атрибуции не считается — {cause}. Расход известен по каждому каналу, "
            "но у сделки в CRM нет поля, которое связывало бы её с каналом, "
            "кампанией или визитом на сайте: любое разнесение расхода по источникам "
            "было бы догадкой, а не расчётом."
        )
        lines.append("")
        lines.append(
            "Чтобы величина стала считаемой, нужно внедрить одно из двух: "
            "заполняемое поле источника (или utm-меток) в карточке сделки CRM, "
            "проставляемое в момент её создания, либо передачу ключа визита — "
            "идентификатора клиента, click ID или телефона в открытом виде — "
            "с сайта в CRM, чтобы сделки можно было сопоставлять с визитами. "
            "До этого сведение расхода с результатом остаётся на уровне "
            "веб-конверсии."
        )
        lines.append("")
        return lines

    cause = _client_reason(economics, deal.get("reason_code"))
    lines.append(
        f"Стоимость сделки в разрезе источника не приводится: {cause}. Пока источник "
        "сделки не подтверждён данными CRM, разнесение расхода по источникам "
        "не выполняется."
    )
    lines.append("")
    return lines


def _build_economics_section(economics: dict[str, Any], currency_round: int) -> str:
    """Секция «Экономика привлечения» — вторая в отчёте, всегда рендерится."""
    lines: list[str] = []
    lines.extend(_build_economics_intro(economics))
    lines.extend(_build_economics_spend(economics, currency_round))
    lines.extend(_build_economics_result(economics))
    lines.extend(_build_economics_total_cost(economics, currency_round))
    lines.extend(_build_economics_web_conversion(economics, currency_round))
    lines.extend(_build_economics_cost_per_deal(economics))
    return "\n".join(lines)


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


def _build_economics_tables(report_dir: Path, economics: dict[str, Any]) -> None:
    """CSV-приложения секции экономики (задача 7F) — сноски [4]/[5]/[6].

    Пишутся всегда, даже пустыми (только заголовок): сноски на них ссылаются
    безусловно, как и в задаче 7D. Значения переносятся как есть, без
    форматирования и пересчёта.
    """
    tables_dir = report_dir / APPENDIX_TABLES_DIRNAME

    columns = _econ_row_by_id(economics, "spend_by_component_columns").get("value")
    spend_rows = _econ_row_by_id(economics, "spend_by_component_rows").get("value")
    money_basis = _econ_row_by_id(economics, "spend_money_basis").get("value") or ""
    header = tuple(columns) if isinstance(columns, list) else ("component_id", "amount_rub")
    _write_csv(
        tables_dir / ECONOMICS_SPEND_CSV,
        (*header, "money_basis"),
        [
            (*row, money_basis)
            for row in (spend_rows if isinstance(spend_rows, list) else [])
            if isinstance(row, list)
        ],
    )

    deal = economics.get("cost_per_deal_by_source") or {}
    result_rows: list[tuple[Any, ...]] = [
        (row_id, row.get("label"), row.get("value"), row.get("status"), row.get("source"))
        for row_id, row in (
            (item, _econ_row_by_id(economics, item))
            for item in ("spend_total_rub", "crm_record_unit", "crm_record_count")
        )
    ]
    result_rows.extend(
        (item.get("id"), item.get("result_name"), item.get("value_rub"), item.get("status"),
         (economics.get("cost_per_web_conversion") or {}).get("source"))
        for item in _items_by_basis(economics, _CRM_RECORD_BASES)
    )
    result_rows.append(
        ("cost_per_deal_by_source", deal.get("label"), deal.get("value"), deal.get("status"),
         deal.get("source"))
    )
    _write_csv(
        tables_dir / ECONOMICS_RESULT_CSV,
        ("id", "label", "value", "status", "source"),
        result_rows,
    )

    _write_csv(
        tables_dir / ECONOMICS_WEB_CONVERSION_CSV,
        ("id", "result_name", "basis", "value_rub", "unit", "denominator_value",
         "numerator_amount_rub", "status"),
        [
            (
                item.get("id"), item.get("result_name"), item.get("basis"),
                item.get("value_rub"), item.get("unit"), item.get("denominator_value"),
                item.get("numerator_amount_rub"), item.get("status"),
            )
            for item in _items_by_basis(economics, _WEB_CONVERSION_BASES)
        ],
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
    lines.append(
        f"{FOOTNOTE_ECONOMICS_SPEND}: `{APPENDIX_TABLES_DIRNAME}/{ECONOMICS_SPEND_CSV}` — "
        "расход по статьям за период с базой денег каждой статьи."
    )
    lines.append(
        f"{FOOTNOTE_ECONOMICS_RESULT}: `{APPENDIX_TABLES_DIRNAME}/{ECONOMICS_RESULT_CSV}` — "
        "результат периода (записи CRM), итог расхода и стоимость на запись CRM."
    )
    lines.append(
        f"{FOOTNOTE_ECONOMICS_WEB_CONVERSION}: "
        f"`{APPENDIX_TABLES_DIRNAME}/{ECONOMICS_WEB_CONVERSION_CSV}` — стоимость "
        "веб-конверсии по моделям привлечения с числителем и знаменателем каждой."
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
    report_limitations = _report_limitations(
        degradation.get("skipped") or [], _load_json(metrics_dir / "t09.json")
    )
    glossary = load_glossary()

    # Задача 7E: контракт экономики собирается здесь, чтобы отчёт получал
    # уже посчитанные величины по явной карте. Задача 7F: секция рендерится
    # из этого объекта и только из него.
    report_economics = load_report_economics(metrics_dir, degradation)

    currency_round = int(defaults.get("currency_round") or 0)

    shown_findings, appendix_findings = split_findings_for_report(findings)
    main_candidate_count = len(findings) - sum(1 for f in findings if _is_low_confidence(f))

    lines: list[str] = []
    lines.extend(_build_header(config))
    lines.append(_build_verdict_section(findings, degradation, metrics_summary, currency_round))
    # Задача 7F: секция экономики — вторая, сразу после вердикта и до карточек
    # находок; рендерится всегда, гейт непустого approved на неё не влияет.
    lines.append(_build_economics_section(report_economics, currency_round))
    lines.append(_build_summary_section(findings, degradation, metrics_summary))
    lines.append(_build_action_plan_section(findings))
    lines.append(
        _build_findings_section(shown_findings, main_candidate_count, bool(findings), currency_round)
    )
    lines.append(_build_skipped_section(report_limitations))
    lines.append(_build_appendix_section(appendix_findings, degradation, currency_round))
    lines.append(_build_footnotes_section())
    lines.append(_build_glossary_section(glossary))

    report_dir = Path(paths.report)
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / REPORT_FILENAME
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    _build_appendix_tables(
        report_dir, appendix_findings, report_limitations, currency_round
    )
    _build_economics_tables(report_dir, report_economics)

    agenda_text = build_oral_review_agenda(findings, config, currency_round)
    (report_dir / ORAL_REVIEW_AGENDA_FILENAME).write_text(
        agenda_text.rstrip() + "\n", encoding="utf-8"
    )

    return str(out_path)
