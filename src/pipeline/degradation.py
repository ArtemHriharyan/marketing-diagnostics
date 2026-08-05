"""Карта деградации: какие проверки методологии выполнимы при наличных данных.

Контракт:
    Читает   — реестр проверок (config/methodology.yaml, уже распарсенный)
               и множество доступных канонических таблиц. Множество берётся из
               manifest.json слоя raw (какие источники выгружены) либо напрямую
               из перечня существующих parquet-файлов в data/canonical/.
    Пишет    — ничего сам; возвращает структуры, которые слой compute сохраняет
               в data/metrics/degradation_report.json.

Второй список (невыполнимые проверки) в неизменном виде идёт в отчёт как раздел
«Что не удалось проверить».

Помимо runnable/skipped отчёт несёт детальную запись по каждой проверке
(``checks``), контракт которой обязан читать слой analyze:

    {
      "check_id": "...",
      "runnable": bool,
      "requires": ["...", ...],
      "type_effective": "A"|"B"|"Q"|"A+B"|"A+Q"|"permanent_LOW",
      "source_modes": {"<requires-таблица>": "api"|"manual", ...},
      "confidence_cap": "HIGH" | "MED" | "LOW",
      "reason_if_not_runnable": "..." | None
    }

Правило «LLM не может повышать confidence» получает второй потолок:
``confidence_cap`` из manual-источников, поверх исходного из compute. Третий,
опциональный потолок — ``check.confidence_cap_downgraded`` в methodology.yaml:
применяется, только если тем же прогоном сработал ``type_downgrade_if`` этой
проверки (см. A24 — потеря "A"-компоненты типа при недоступности структурных
полей объявления означает потерю автоматической проверяемости).

LLM здесь не вызывается: чистая детерминированная логика (принцип 3).
"""

from __future__ import annotations

from typing import Any, Iterable


# Человекочитаемые названия источников для формулировки причины.
# Ключ — имя канонической таблицы, значение — как назвать его в отчёте.
_SOURCE_LABELS: dict[str, str] = {
    "visits": "визиты Метрики",
    "costs": "расходы",
    "direct_queries": "поисковые запросы Директа (Директ не подключён)",
    "seo_queries": "поисковые запросы SEO (Вебмастер/GSC не подключены)",
    "wordstat": "спрос Wordstat",
    "crux": "полевые CWV (CrUX не подключён)",
    "site_crawl": "обход сайта (ручная техническая проверка)",
    "crm": "выгрузка CRM",
    "client_answers": "анкета клиента (inputs/client_answers.yaml)",
    "manual_form_tests": "ручное тестирование форм (inputs/manual_form_tests.yaml)",
    "webvisor_findings": "наблюдения из Вебвизора (inputs/webvisor_findings.yaml)",
    "campaign_strategies": "стратегии кампаний Директа (Директ не подключён)",
    "campaign_status": "статусы кампаний Директа (Директ не подключён)",
    "degradation_report": "отчёт о деградации",
}


def _label(table: str) -> str:
    """Вернуть человекочитаемое имя источника для причины недоступности."""
    return _SOURCE_LABELS.get(table, table)


# ── Зависимости окружения (requirements.txt) ────────────────────────────────
# Отсутствие пакета — такая же управляемая деградация, как отсутствие источника
# (принцип 4): затронутый блок пропускается, соседние считаются. Карта нужна,
# чтобы сказать об этом на intake (первая секунда прогона), а не на импорте
# блока в середине compute. ``module`` — имя импорта, ``package`` — имя в
# requirements.txt, ``affects`` — что именно перестаёт работать.
DEPENDENCIES: tuple[dict[str, Any], ...] = (
    {"module": "pandas", "package": "pandas",
     "affects": ("transform", "compute")},
    {"module": "pyarrow", "package": "pyarrow",
     "affects": ("transform", "compute")},
    {"module": "duckdb", "package": "duckdb",
     "affects": ("compute: все блоки",)},
    {"module": "yaml", "package": "pyyaml",
     "affects": ("все стадии",)},
    {"module": "dotenv", "package": "python-dotenv",
     "affects": ("extract",)},
    {"module": "requests", "package": "requests",
     "affects": ("extract",)},
    {"module": "scipy", "package": "scipy",
     "affects": ("compute: block1",)},
    {"module": "playwright", "package": "playwright",
     "affects": ("extract: site_crawl",)},
    {"module": "tapi_yandex_metrika", "package": "tapi-yandex-metrika",
     "affects": ("extract: metrika",)},
    {"module": "openai", "package": "openai",
     "affects": ("analyze",)},
)


def check_dependencies(
    dependencies: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Проверить импортируемость объявленных зависимостей, НЕ импортируя их.

    ``importlib.util.find_spec`` смотрит только наличие модуля в путях поиска —
    код зависимости не выполняется, поэтому проверка дёшева и безопасна на
    intake. Возвращает по записи на зависимость:
    ``{module, package, present, affects}``. Порядок — как в ``DEPENDENCIES``.
    """
    import importlib.util

    rows: list[dict[str, Any]] = []
    for dep in (DEPENDENCIES if dependencies is None else dependencies):
        module = str(dep.get("module"))
        try:
            present = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            # Битый/отсутствующий родительский пакет — тот же случай «нет».
            present = False
        rows.append(
            {
                "module": module,
                "package": dep.get("package") or module,
                "present": present,
                "affects": list(dep.get("affects") or []),
            }
        )
    return rows


def missing_dependencies(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить из вывода ``check_dependencies`` только отсутствующие."""
    return [row for row in rows if not row.get("present")]


def format_dependency_table(rows: Iterable[dict[str, Any]]) -> str:
    """Отформатировать таблицу «зависимость -> есть/нет -> затронутые блоки»."""
    rows = list(rows)
    lines = [f"{'зависимость':<24}{'есть':<8}затронуто", "-" * 60]
    for row in rows:
        mark = "да" if row.get("present") else "НЕТ"
        affects = ", ".join(row.get("affects") or []) or "-"
        lines.append(f"{str(row.get('module')):<24}{mark:<8}{affects}")
    return "\n".join(lines)


def missing_dependency_reason(module: str) -> str:
    """Единая формулировка причины пропуска блока из-за зависимости."""
    return f"отсутствует зависимость: {module}"


def register_missing_dependencies(
    report: dict[str, Any],
    missing: dict[str, str],
) -> dict[str, Any]:
    """Внести блоки, не импортировавшиеся из-за отсутствующей зависимости.

    Пишет два места отчёта:
      * ``missing_dependencies`` — машиночитаемый список ``{block, module,
        reason}`` для аудита прогона;
      * ``skipped`` — та же запись в формате раздела «Что не удалось проверить»,
        чтобы ограничение прогона попало в отчёт явно, а не потерялось в логе.

    ``counts`` не трогается: это счётчики реестра проверок, а не блоков (тот же
    приём, что у report._report_limitations с T09). Повторный вызов
    идемпотентен — записи дедуплицируются по имени блока.
    """
    if not missing:
        return report

    entries = report.setdefault("missing_dependencies", [])
    known_blocks = {e.get("block") for e in entries if isinstance(e, dict)}
    skipped = report.setdefault("skipped", [])
    known_ids = {s.get("id") for s in skipped if isinstance(s, dict)}

    for block, module in sorted(missing.items()):
        reason = missing_dependency_reason(module)
        if block not in known_blocks:
            entries.append({"block": block, "module": module, "reason": reason})
        skipped_id = f"блок {block}"
        if skipped_id not in known_ids:
            skipped.append(
                {
                    "id": skipped_id,
                    "block": None,
                    "name": f"блок {block} не выполнен",
                    "requires": [],
                    "missing": [module],
                    "reason": reason,
                    "degrades_to": None,
                }
            )
    return report


def register_artifact_states(
    report: dict[str, Any],
    stale: Iterable[dict[str, Any]] = (),
    unregistered: Iterable[str] = (),
) -> dict[str, Any]:
    """Внести в отчёт артефакты metrics, не принадлежащие текущему прогону (STATE-1).

    Два состояния, оба — ограничение прогона, оба обязаны быть видимы, а не
    молча пропущены (в этом и была задача: candidates раньше подхватывал такой
    файл глобом наравне со свежим):

      * ``stale``        — артефакт зарегистрирован прошлым прогоном (чужой
        run_id) либо не зарегистрирован вовсе, а файл на месте: его содержание
        относится к другому входному состоянию;
      * ``unregistered`` — файл лежит в data/metrics/, но реестр входов о нём
        не знает: его записал не слой compute.

    Файлы не удаляются: снести чужой артефакт — потерять улику для разбора
    прогона. ``counts`` не трогается — это счётчики реестра проверок, а не
    файлов (тот же приём, что у register_missing_dependencies).
    """
    stale_entries = [dict(entry) for entry in stale]
    unregistered_names = sorted(set(unregistered))
    if not stale_entries and not unregistered_names:
        return report

    artifacts = report.setdefault("artifact_states", {})
    if stale_entries:
        artifacts["stale"] = sorted(stale_entries, key=lambda e: str(e.get("artifact")))
    if unregistered_names:
        artifacts["unregistered"] = unregistered_names
    return report


# Режим (api|manual) каждой канонической таблицы.
#   * ``_API_TABLES``      — всегда api (машинная выгрузка систем).
#   * ``_MANUAL_TABLES``   — всегда manual (ручной ввод/обход/анкета): их данные
#                            собирает аналитик, поэтому находки капаются до MED.
#   * ``seo_queries``      — режим берётся из config.sources (webmaster/gsc):
#                            manual, если ХОТЯ БЫ один из них заявлен mode=manual.
_API_TABLES: frozenset[str] = frozenset(
    {"visits", "costs", "direct_queries", "campaign_strategies", "campaign_status",
     "wordstat", "crux", "degradation_report"}
)
_MANUAL_TABLES: frozenset[str] = frozenset(
    {"site_crawl", "webvisor_findings", "client_answers", "manual_form_tests",
     "crm", "manual_serp"}
)
# Какие ключи config.sources питают seo_queries (единственная таблица с
# переключаемым режимом). Порядок не важен — достаточно одного manual.
_SEO_SOURCE_KEYS: tuple[str, ...] = ("webmaster", "gsc")

# Порядок уверенности: HIGH > MED > LOW. Используется для min().
_CONFIDENCE_ORDER = {"HIGH": 3, "MED": 2, "LOW": 1}


def min_confidence(a: str, b: str) -> str:
    """Вернуть меньший из двух уровней уверенности (HIGH > MED > LOW)."""
    return a if _CONFIDENCE_ORDER.get(a, 0) <= _CONFIDENCE_ORDER.get(b, 0) else b


def table_source_modes(config: dict[str, Any] | None) -> dict[str, str]:
    """По config.sources вернуть режим каждой канонической таблицы: api|manual.

    Всегда-api и всегда-manual таблицы фиксированы; ``seo_queries`` переключается
    по config (manual, если webmaster или gsc заявлены ``mode: manual``). Таблицы,
    не перечисленные здесь, потребитель трактует как ``api``.
    """
    sources = ((config or {}).get("sources") or {})
    modes: dict[str, str] = {t: "api" for t in _API_TABLES}
    modes.update({t: "manual" for t in _MANUAL_TABLES})

    seo_mode = "api"
    for key in _SEO_SOURCE_KEYS:
        spec = sources.get(key) or {}
        if isinstance(spec, dict) and spec.get("mode") == "manual":
            seo_mode = "manual"
            break
    modes["seo_queries"] = seo_mode
    return modes


def collect_manifest_flags(manifest: dict[str, Any] | None) -> dict[str, bool]:
    """Собрать булевы флаги выгрузки из manifest.json для правил понижения типа.

    Флаги берутся из трёх мест (позже перечисленное перекрывает раннее):
        manifest["flags"]                     — явный словарь флагов;
        булевы поля верхнего уровня manifest;
        булевы поля каждой записи sources[*]  — напр. у direct флаг
            ``campaign_report_has_lost_impression_share``.
    Отсутствие манифеста -> пустой словарь (все флаги трактуются как false).
    """
    flags: dict[str, bool] = {}
    if not manifest:
        return flags
    for key, value in manifest.items():
        if isinstance(value, bool):
            flags[key] = value
    for entry in (manifest.get("sources") or {}).values():
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if isinstance(value, bool):
                flags[key] = value
    explicit = manifest.get("flags")
    if isinstance(explicit, dict):
        for key, value in explicit.items():
            if isinstance(value, bool):
                flags[key] = value
    return flags


def _eval_downgrade(expr: str | None, flags: dict[str, bool]) -> bool:
    """Оценить условие понижения типа вида ``<flag> == false|true``.

    Никакого eval: разбираем единственный поддерживаемый шаблон. ``<flag>`` —
    булев флаг из manifest.json (см. ``collect_manifest_flags``); отсутствующий
    флаг трактуется как ``false``. Нераспознанное условие -> False (тип не
    понижается).
    """
    if not expr or not isinstance(expr, str):
        return False
    parts = expr.split("==")
    if len(parts) != 2:
        return False
    flag = parts[0].strip()
    rhs = parts[1].strip().lower()
    if not flag or rhs not in ("true", "false"):
        return False
    actual = bool(flags.get(flag, False))
    expected = rhs == "true"
    return actual == expected


def evaluate_check(
    check: dict[str, Any],
    available: set[str],
    source_modes: dict[str, str],
    manual_cap: str = "MED",
    flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Обогатить одну проверку по контракту карты деградации.

    Возвращает ``{check_id, runnable, requires, type_effective, source_modes,
    confidence_cap, reason_if_not_runnable}``.

    ``source_modes`` — режим каждого требования (requires). ``confidence_cap`` =
    "MED", если ХОТЯ БЫ одно из requires закрыто источником mode=manual, иначе
    "HIGH". ``type_effective`` = ``type_downgraded`` при истинном
    ``type_downgrade_if`` (условие читает флаги манифеста), иначе ``type_default``.
    Особое значение ``type_downgraded=permanent_LOW`` действует безусловно и
    навсегда ограничивает ``confidence_cap`` уровнем LOW.
    """
    required = list(check.get("requires") or [])
    missing = [t for t in required if t not in available]
    runnable = not missing

    # Режимы требований (именно requires — по ним считается потолок уверенности).
    modes = {t: source_modes.get(t, "api") for t in required}

    # Потолок уверенности: хотя бы один manual среди requires -> manual_cap.
    confidence_cap = "HIGH"
    if any(mode == "manual" for mode in modes.values()):
        confidence_cap = min_confidence(confidence_cap, manual_cap)

    # Эффективный тип с учётом постоянного ограничения и пост-хок понижения.
    type_effective = check.get("type_default", "A")
    if check.get("type_downgraded") == "permanent_LOW":
        type_effective = "permanent_LOW"
        confidence_cap = min_confidence(confidence_cap, "LOW")
    elif _eval_downgrade(check.get("type_downgrade_if"), flags or {}):
        type_effective = check.get("type_downgraded") or type_effective
        # Опциональный доп. потолок confidence_cap для тех проверок, у которых
        # понижение типа означает потерю автоматической проверяемости (не все
        # type_downgrade_if это подразумевают — поле явно опциональное, см.
        # config/methodology.yaml).
        extra_cap = check.get("confidence_cap_downgraded")
        if extra_cap:
            confidence_cap = min_confidence(confidence_cap, extra_cap)

    reason = None
    if not runnable:
        reason = "нет источника: " + "; ".join(_label(t) for t in missing)

    return {
        "check_id": check.get("id"),
        "runnable": runnable,
        "requires": required,
        "type_effective": type_effective,
        "source_modes": modes,
        "confidence_cap": confidence_cap,
        "reason_if_not_runnable": reason,
    }


def available_tables_from_manifest(manifest: dict[str, Any] | None) -> set[str]:
    """Собрать множество доступных канонических таблиц из manifest.json.

    manifest ожидается в формате, который пишет src.pipeline.manifest:
        {"sources": {"<source>": {"canonical_tables": [...], ...}, ...}}
    Пустой или отсутствующий манифест -> пустое множество (штатная деградация,
    принцип 4: пайплайн не падает).
    """
    if not manifest:
        return set()

    tables: set[str] = set()
    for entry in (manifest.get("sources") or {}).values():
        for table in entry.get("canonical_tables", []) or []:
            tables.add(table)
    # Некоторые таблицы приходят не из API, а из inputs/ клиента.
    for table in manifest.get("input_tables", []) or []:
        tables.add(table)
    return tables


def split_checks(
    checks: Iterable[dict[str, Any]],
    available: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Разбить реестр проверок на выполнимые и невыполнимые.

    Проверка выполнима, если все её ``requires`` присутствуют в ``available``.
    ``optional`` на выполнимость не влияет — лишь обогащает результат.

    Возвращает кортеж (runnable, skipped):
        runnable — список записей проверок как есть;
        skipped  — список словарей вида
             {"id", "block", "name", "requires": [...], "missing": [...], "reason": "...",
             "degrades_to": <id|None>}
    Порядок сохраняется, что делает раздел «Что не удалось проверить»
    детерминированным.
    """
    available_set = set(available)
    runnable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for check in checks:
        required = list(check.get("requires") or [])
        missing = [t for t in required if t not in available_set]
        if not missing:
            runnable.append(check)
            continue

        reason = "нет источника: " + "; ".join(_label(t) for t in missing)
        skipped.append(
            {
                "id": check.get("id"),
                "block": check.get("block"),
                "name": check.get("name"),
                "requires": required,
                "missing": missing,
                "reason": reason,
                "degrades_to": check.get("degrades_to"),
            }
        )

    return runnable, skipped


def build_degradation_report(
    methodology: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    available: Iterable[str] | None = None,
    config: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать полный отчёт о деградации для data/metrics/degradation_report.json.

    Источник доступных таблиц: явный ``available`` (приоритет) либо вывод из
    ``manifest``. Реестр проверок берётся из ``methodology['checks']``.

    ``config`` (клиентский) задаёт режимы источников (api|manual) для расчёта
    ``confidence_cap`` и ``source_modes`` по каждой проверке. ``defaults`` даёт
    ``manual_source_confidence_cap`` (по умолчанию "MED"). ``manifest`` даёт
    булевы флаги для правил понижения типа (``type_downgrade_if``). Все три
    опциональны — без них проверки трактуются как api, флаги как false, потолок
    остаётся HIGH.
    """
    checks = methodology.get("checks") or []
    if available is None:
        available = available_tables_from_manifest(manifest)

    available_set = set(available)
    runnable, skipped = split_checks(checks, available_set)

    modes = table_source_modes(config)
    manual_cap = (defaults or {}).get("manual_source_confidence_cap", "MED")
    flags = collect_manifest_flags(manifest)
    detailed = [
        evaluate_check(c, available_set, modes, manual_cap, flags) for c in checks
    ]

    return {
        # STATE-1: заполняется orchestrator.run_compute сразу после сборки
        # отчёта (compute_run_id считается от него же). Ключ объявлен здесь,
        # чтобы отчёт имел одну форму и до, и после назначения прогону id.
        "run_id": None,
        "available_tables": sorted(available_set),
        "runnable_check_ids": [c.get("id") for c in runnable],
        "skipped": skipped,
        "checks": detailed,
        # Состояние окружения на момент прогона: отсутствующая зависимость —
        # такое же ограничение прогона, как отсутствующий источник, и должна
        # быть видна в отчёте, а не только в логе (см. check_dependencies).
        "dependencies": {"missing": missing_dependencies(check_dependencies())},
        "counts": {
            "total": len(checks),
            "runnable": len(runnable),
            "skipped": len(skipped),
        },
    }
