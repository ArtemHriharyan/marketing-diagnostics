"""Логика этапов конвейера и гейтов.

Оркестратор ничего не знает про конкретный API — он лишь координирует слои и
следит за инвариантами: неизменяемость чужих слоёв, гейт перед report,
управляемая деградация. Тяжёлую работу делают модули extract/transform/compute/
analyze/report; здесь — только каркас вызовов и общие утилиты (пути, логи,
загрузка конфигов).

LLM вызывается только внутри слоя analyze; сам оркестратор его не трогает.
"""

from __future__ import annotations

import calendar
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import degradation as degradation_mod
from . import manifest as manifest_mod


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
CLIENTS_DIR = REPO_ROOT / "clients"

STAGES = ("intake", "extract", "transform", "compute", "analyze", "report")


# ── Пути клиента ───────────────────────────────────────────────────────────
class ClientPaths:
    """Каноничные пути одного клиента. Единая точка правды о раскладке каталогов."""

    def __init__(self, client: str) -> None:
        self.client = client
        self.root = CLIENTS_DIR / client
        self.config_file = self.root / "config.yaml"
        self.env_file = self.root / ".env"
        self.inputs = self.root / "inputs"
        self.data = self.root / "data"
        self.raw = self.data / "raw"
        self.canonical = self.data / "canonical"
        self.metrics = self.data / "metrics"
        self.findings_draft = self.root / "findings" / "draft"
        self.findings_approved = self.root / "findings" / "approved"
        self.report = self.root / "report"
        self.logs = self.root / "logs"

    def exists(self) -> bool:
        return self.config_file.exists()


# ── Загрузка конфигов ──────────────────────────────────────────────────────
def load_yaml(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_defaults() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "defaults.yaml")


def load_methodology() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "methodology.yaml")


def load_client_config(paths: ClientPaths) -> dict[str, Any]:
    return load_yaml(paths.config_file)


# ── Логирование этапа ──────────────────────────────────────────────────────
class StageLogger:
    """Двойной вывод: в консоль и в clients/<name>/logs/<stage>_<ts>.log.

    Использование:
        with StageLogger(paths, "intake") as log:
            log("сообщение")
    """

    def __init__(self, paths: ClientPaths, stage: str) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        paths.logs.mkdir(parents=True, exist_ok=True)
        self.path = paths.logs / f"{stage}_{ts}.log"
        self._fh = None

    def __enter__(self) -> "StageLogger":
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def __call__(self, message: str = "") -> None:
        encoding = sys.stdout.encoding or "utf-8"
        print(message.encode(encoding, errors="replace").decode(encoding))
        if self._fh:
            self._fh.write(message + "\n")
            self._fh.flush()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh:
            self._fh.close()


# ── Гейт перед report ──────────────────────────────────────────────────────
def approved_findings_present(paths: ClientPaths) -> bool:
    """True, если в findings/approved/ есть хотя бы один *.yaml."""
    if not paths.findings_approved.exists():
        return False
    return any(paths.findings_approved.glob("*.yaml"))


def report_gate_message(paths: ClientPaths) -> str:
    """Инструкция аналитику, когда гейт перед report закрыт."""
    return (
        "ГЕЙТ: findings/approved/ пуст — этап report запускать нельзя.\n"
        f"  1. Проверь черновики находок в: {paths.findings_draft}\n"
        f"  2. Утверждённые перенеси в:      {paths.findings_approved}\n"
        "  3. Повтори: python run.py "
        f"{paths.client} --stage report"
    )


# ── Вспомогательные функции для работы с датами окна ──────────────────────
def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(d: date, n: int) -> date:
    """Прибавить n месяцев к дате (n может быть отрицательным). День обрезается до конца месяца."""
    total = d.year * 12 + d.month - 1 + n
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _compute_compare_window(
    primary: dict[str, str],
    compare_cfg: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not compare_cfg or not compare_cfg.get("enabled"):
        return None
    offset = int(compare_cfg.get("offset_months") or 12)
    d_from = date.fromisoformat(primary["date_from"])
    d_to = date.fromisoformat(primary["date_to"])
    return {
        "date_from": _add_months(d_from, -offset).isoformat(),
        "date_to": _add_months(d_to, -offset).isoformat(),
    }


def _resolve_data_window(
    data_window: dict[str, Any] | None,
    compare_cfg: dict[str, Any] | None,
    log: Any,
    _today: date | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, list[str]]:
    """Разобрать и валидировать секции data_window + compare_previous_period.

    Возвращает (primary_window, compare_window, current_month_is_partial, errors).
    Непустой errors -> intake должен завершиться с ошибкой.
    _today используется только в тестах для фиксации «сегодня».
    """
    errors: list[str] = []

    if not data_window:
        return None, None, False, []

    today = _today or date.today()

    # ── Обратная совместимость: старый формат data_window.months ─────────────
    if "months" in data_window and "mode" not in data_window:
        log(
            "ПРЕДУПРЕЖДЕНИЕ: устаревший формат data_window (поле months), "
            "см. миграцию в CLAUDE.md. Интерпретируется как mode: months_back."
        )
        months_back = int(data_window["months"])
        prev_last = today.replace(day=1) - timedelta(days=1)
        d_to = _last_day_of_month(prev_last.year, prev_last.month)
        d_from = _add_months(d_to.replace(day=1), -(months_back - 1))
        primary = {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()}
        return primary, _compute_compare_window(primary, compare_cfg), False, []

    mode = str(data_window.get("mode") or "").strip()

    # ── Нет mode, нет months — старый flat-формат без строгой валидации ──────
    if not mode:
        log(
            "ПРЕДУПРЕЖДЕНИЕ: data_window не содержит поля mode — "
            "валидация окна пропущена."
        )
        df = data_window.get("date_from")
        dt = data_window.get("date_to")
        if df and dt:
            return {"date_from": str(df), "date_to": str(dt)}, None, False, []
        return None, None, False, []

    # ── mode: months_back ────────────────────────────────────────────────────
    if mode == "months_back":
        months_back = int(data_window.get("months_back") or 12)
        prev_last = today.replace(day=1) - timedelta(days=1)
        d_to = _last_day_of_month(prev_last.year, prev_last.month)
        d_from = _add_months(d_to.replace(day=1), -(months_back - 1))
        primary = {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()}
        return primary, _compute_compare_window(primary, compare_cfg), False, []

    # ── mode: explicit ───────────────────────────────────────────────────────
    if mode != "explicit":
        errors.append(
            f"data_window.mode: неизвестный режим {mode!r}. "
            "Допустимые значения: explicit, months_back."
        )
        return None, None, False, errors

    date_from_str = data_window.get("date_from")
    date_to_str = data_window.get("date_to")

    if not date_from_str:
        errors.append("data_window.date_from обязателен при mode: explicit")
        return None, None, False, errors
    if not date_to_str:
        errors.append("data_window.date_to обязателен при mode: explicit")
        return None, None, False, errors

    try:
        d_from = date.fromisoformat(str(date_from_str))
    except ValueError:
        errors.append(f"date_from — невалидная дата: {date_from_str!r}")
        return None, None, False, errors

    if d_from.day != 1:
        errors.append(
            f"date_from должен быть первым числом месяца, получено: {date_from_str}"
        )
        return None, None, False, errors

    partial = False
    if str(date_to_str).lower() == "today":
        d_to = today
        partial = True
    else:
        try:
            d_to = date.fromisoformat(str(date_to_str))
        except ValueError:
            errors.append(f"date_to — невалидная дата: {date_to_str!r}")
            return None, None, False, errors

        last_day = _last_day_of_month(d_to.year, d_to.month)
        if d_to != last_day:
            errors.append(
                f'date_to должен быть последним днём месяца или строкой "today", '
                f"получено: {date_to_str}"
            )
            return None, None, False, errors

    primary = {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()}
    return primary, _compute_compare_window(primary, compare_cfg), partial, []


# ── Этапы (каркас; тяжёлая логика — в слоях) ───────────────────────────────
def run_intake(paths: ClientPaths, log: StageLogger) -> bool:
    """Валидация config.yaml и .env, лёгкий ping заявленных API.

    Реальные пинги выполняют модули extract (у каждого — функция проверки
    живости токена). Здесь — валидация структуры конфига и печать таблицы
    «источник -> доступен/нет». Возвращает True, если конфиг корректен.
    """
    if not paths.exists():
        log(f"Не найден config.yaml клиента: {paths.config_file}")
        return False

    config = load_client_config(paths)
    sources = config.get("sources", {}) or {}

    log(f"Клиент: {config.get('client', {}).get('name') or paths.client}")
    log("")
    log(f"{'источник':<14}{'заявлен':<10}{'доступен':<10}")
    log("-" * 34)

    for name, spec in sources.items():
        enabled = bool((spec or {}).get("enabled"))
        # TODO(extract): вызвать лёгкий ping соответствующего модуля extract.
        # Пока источник считается доступным только по факту enabled=true;
        # фактическую живость токена подставят экстракторы.
        available = "?" if enabled else "-"
        log(f"{name:<14}{('да' if enabled else 'нет'):<10}{available:<10}")

    log("")

    # ── Валидация data_window ────────────────────────────────────────────────
    data_window = config.get("data_window") or {}
    compare_cfg = config.get("compare_previous_period") or {}
    primary_window, compare_window, partial, errors = _resolve_data_window(
        data_window, compare_cfg, log
    )

    if errors:
        for err in errors:
            log(f"ОШИБКА (data_window): {err}")
        log("intake: завершён с ошибкой — пайплайн не запущен.")
        return False

    if primary_window:
        global_fields: dict[str, Any] = {"primary_window": primary_window}
        if compare_window:
            global_fields["compare_window"] = compare_window
        if partial:
            global_fields["current_month_is_partial"] = True
        manifest_mod.update_global(paths.raw, **global_fields)

    log("intake: структура конфига валидна (ping токенов — TODO в extract).")
    return True


# Соответствие имени YAML в inputs/ клиента -> канонической таблице,
# которую он закрывает в manifest["input_tables"] (читает
# src.pipeline.degradation.available_tables_from_manifest). Расширено с
# client_answers на manual_form_tests в FIX-input-tables-manifest-gate
# (расширенная версия, см. docs/implementation_status.md) — requires:
# [manual_form_tests] несёт C03/C08/C11/C17/C23 (config/methodology.yaml).
# webvisor_findings НЕ добавлен: ни одна проверка не ссылается на него через
# requires (только через optional, которое на runnable не влияет) — токен
# добавляется в эту карту только когда/если появится такой requires.
# crm/manual_serp не добавляются: по AUDIT-input-tables-blast-radius это
# мёртвые записи справочника деградации, ни один requires/optional на них не
# ссылается.
INPUT_TABLE_FILES: dict[str, str] = {
    "client_answers": "client_answers.yaml",
    "manual_form_tests": "manual_form_tests.yaml",
}


def _detect_input_tables(paths: "ClientPaths") -> list[str]:
    """Определить, какие input-таблицы клиента реально заполнены.

    Таблица считается доступной, если соответствующий файл в inputs/
    существует и парсится YAML в непустое значение — отсутствующий файл или
    пустой YAML (```None``` после ``safe_load``) не считаются. Результат
    идёт в manifest["input_tables"], который читает
    ``degradation.available_tables_from_manifest`` при сборке карты
    деградации: без этого D06/D07/T06 (``requires: [client_answers, ...]``)
    и C03/C08/C11/C17/C23 (``requires: [manual_form_tests]``) структурно
    никогда не становятся runnable, даже когда анкета/ручные тесты форм
    заполнены.

    ``paths.inputs`` опционален (минимальные дублёры ClientPaths в тестах
    других экстракторов его не объявляют) — без него функция просто ничего
    не находит, как при отсутствующем каталоге.
    """
    inputs_dir = getattr(paths, "inputs", None)
    if inputs_dir is None:
        return []
    detected: list[str] = []
    for table, filename in INPUT_TABLE_FILES.items():
        path = inputs_dir / filename
        if not path.exists():
            continue
        try:
            content = load_yaml(path)
        except Exception:
            continue
        if content:
            detected.append(table)
    return detected


# Карта: ключ источника в config.sources -> модули-экстракторы src/extract/.
# У Метрики два экстрактора на один источник: сырьё визитов (Logs API) и
# агрегаты для сверки (Reports API).
EXTRACTORS: dict[str, list[str]] = {
    "metrika": ["metrika_reports", "metrika_logs"],
    "direct": ["direct"],
    "wordstat": ["wordstat"],
    "crux": ["crux"],
    "crm_csv": ["crm_import"],
}

# Источники с переключаемым режимом api|manual (см. патч про source_mode):
# выбор модуля <source>_<mode> делается по config.sources.<source>.mode.
# Дефолт — manual (сейчас у GSC/Вебмастера нет API-доступа). Выходной контракт
# сырья у обоих режимов одинаков, поэтому переключение не трогает transform.
MODE_DISPATCH = ("gsc", "webmaster")


def _modules_for_source(source: str, spec: dict[str, Any] | None) -> list[str]:
    """Список модулей-экстракторов для источника с учётом режима api|manual."""
    if source in MODE_DISPATCH:
        mode = str((spec or {}).get("mode") or "manual").strip().lower()
        suffix = "api" if mode == "api" else "manual"
        return [f"{source}_{suffix}"]
    return EXTRACTORS.get(source, [])


def _call_extract(module: Any, config: dict[str, Any], env: dict[str, str],
                  paths: "ClientPaths", log: "StageLogger",
                  defaults: dict[str, Any]) -> dict[str, Any]:
    """Вызвать module.extract, передав опциональные kwargs только если он их принимает.

    Реализованные экстракторы принимают log/defaults; заглушки — нет (у них
    сигнатура (config, env, paths) и они падают NotImplementedError). Фильтрация
    по сигнатуре позволяет вызывать и те, и другие единообразно.
    """
    import inspect

    params = inspect.signature(module.extract).parameters
    accepts_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    kwargs: dict[str, Any] = {}
    for key, value in (("log", log), ("defaults", defaults)):
        if accepts_kw or key in params:
            kwargs[key] = value
    return module.extract(config, env, paths, **kwargs)


def run_extract(paths: ClientPaths, log: StageLogger) -> None:
    """Выгрузка сырых данных заявленных источников в data/raw/<source>/.

    Диспетчеризует по config.sources на модули src/extract/. Каждый модуль пишет
    свой подкаталог и обновляет manifest.json (идемпотентно — перезапись своего
    слоя целиком допустима). Пайплайн не падает от недоступности источника
    (принцип 4): AuthError/SourceUnavailable логируются как «источник недоступен»
    и не прерывают остальные источники; нереализованные экстракторы пропускаются.
    """
    import importlib

    from ..extract import _common as extract_common

    paths.raw.mkdir(parents=True, exist_ok=True)
    config = load_client_config(paths)
    defaults = load_defaults()
    env = extract_common.load_env(paths.env_file)  # токены НЕ логируются
    sources = config.get("sources", {}) or {}

    input_tables = _detect_input_tables(paths)
    manifest_mod.update_global(paths.raw, input_tables=input_tables)
    if input_tables:
        log(f"extract[inputs]: заполнены -> {', '.join(input_tables)}")

    extracted, unavailable, skipped = [], [], []
    for source, spec in sources.items():
        if not (spec or {}).get("enabled"):
            continue
        for mod_name in _modules_for_source(source, spec):
            module = importlib.import_module(f"src.extract.{mod_name}")
            try:
                log(f"extract[{mod_name}]: старт")
                result = _call_extract(module, config, env, paths, log, defaults)
                rows = result.get("rows", 0)
                log(f"extract[{mod_name}]: готово — {rows} строк -> data/raw/{result.get('source', mod_name)}/")
                extracted.append(mod_name)
            except extract_common.SourceUnavailable as exc:
                # AuthError — частный случай; сообщение уже человекочитаемое.
                log(
                    f"extract[{mod_name}]: ИСТОЧНИК НЕДОСТУПЕН — {exc} "
                    f"(внутренний код оркестратора {exc.exit_code}, "
                    f"не код ошибки из текста выше)"
                )
                unavailable.append(mod_name)
            except NotImplementedError:
                log(f"extract[{mod_name}]: экстрактор ещё не реализован — пропуск")
                skipped.append(mod_name)

    # site_crawl — опциональный; вызывается при наличии crawl.base_url (принцип 4)
    if (config.get("crawl") or {}).get("base_url"):
        _sc = "site_crawl"
        _sc_module = importlib.import_module(f"src.extract.{_sc}")
        try:
            log(f"extract[{_sc}]: старт")
            _sc_result = _call_extract(_sc_module, config, env, paths, log, defaults)
            _sc_rows = _sc_result.get("rows", 0)
            log(f"extract[{_sc}]: готово — {_sc_rows} строк -> data/raw/{_sc_result.get('source', _sc)}/")
            extracted.append(_sc)
        except extract_common.SourceUnavailable as exc:
            log(
                f"extract[{_sc}]: ИСТОЧНИК НЕДОСТУПЕН — {exc} "
                f"(внутренний код оркестратора {exc.exit_code}, "
                f"не код ошибки из текста выше)"
            )
            unavailable.append(_sc)
        except NotImplementedError:
            log(f"extract[{_sc}]: экстрактор ещё не реализован — пропуск")
            skipped.append(_sc)

    log("")
    log(f"extract: выгружено {len(extracted)}, недоступно {len(unavailable)}, "
        f"пропущено {len(skipped)}.")
    if extracted:
        log(f"  выгружено:  {', '.join(extracted)}")
    if unavailable:
        log(f"  недоступно: {', '.join(unavailable)}")

    # Авто-сверка Logs↔Reports, если выгружены оба источника Метрики.
    if {"metrika_logs", "metrika_reports"} <= set(extracted):
        _run_metrika_reconciliation(paths, config, log)


def _run_metrika_reconciliation(paths: ClientPaths, config: dict[str, Any],
                                log: "StageLogger") -> None:
    """Сверка Logs API против Reporting API в конце extract (не роняет стадию).

    Экстракция уже успешна и идемпотентна; сверка — QA-артефакт. Поэтому FAIL
    громко логируется и пишется в reconciliation.json, но саму стадию extract не
    прерывает (принцип 4). Ненулевой код возврата даёт отдельный CLI-запуск
    scripts/verify_metrika.py для CI/ручной проверки.
    """
    from scripts import verify_metrika as vm

    try:
        report = vm.reconcile(paths.raw, config)
    except Exception as exc:  # сверка не должна ронять успешную выгрузку
        log(f"verify_metrika: сверка не выполнена ({type(exc).__name__}: {exc})")
        return

    log("")
    log("=== сверка Logs ↔ Reports (verify_metrika) ===")
    log(vm.format_table(report))
    out = vm.write_report(paths.raw, report)
    log(f"reconciliation.json -> {out}")
    if report["verdict"] == "FAIL":
        log("verify_metrika: ВНИМАНИЕ — расхождение >5% (см. reconciliation.json). "
            "Для CI/ручной проверки: python scripts/verify_metrika.py <client> (код != 0).")


def run_transform(paths: ClientPaths, log: StageLogger) -> None:
    """raw -> data/canonical/*.parquet (детерминированно, без LLM)."""
    from ..transform import build_canonical

    paths.canonical.mkdir(parents=True, exist_ok=True)
    config = load_client_config(paths)
    defaults = load_defaults()

    client_answers_path = paths.inputs / "client_answers.yaml"
    client_answers = load_yaml(client_answers_path) if client_answers_path.exists() else {}
    built = build_canonical.build(paths, config, defaults, client_answers=client_answers)
    if built:
        log(f"transform: построено {len(built)} таблиц -> {', '.join(built)}")
    else:
        log("transform: нет сырья ни для одной канонической таблицы (см. data/raw/manifest.json).")


def run_compute(paths: ClientPaths, log: StageLogger) -> None:
    """canonical -> data/metrics/ + degradation_report.json.

    Считаются только проверки, чьи requires удовлетворены. Непокрытые уходят в
    degradation_report (см. src.pipeline.degradation). Диспетчеризация по
    блокам (block0..block6) и запись metrics_summary — см. src.compute.common;
    сами блоки пока не реализуют бизнес-проверки D/A/T/C/S (заглушки).
    """
    import json

    from ..compute import common as compute_common

    paths.metrics.mkdir(parents=True, exist_ok=True)
    methodology = load_methodology()
    manifest = manifest_mod.load_manifest(paths.raw)
    config = load_client_config(paths)
    defaults = load_defaults()
    report = degradation_mod.build_degradation_report(
        methodology, manifest=manifest, config=config, defaults=defaults
    )

    out = paths.metrics / "degradation_report.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    counts = report["counts"]
    log(
        f"compute: выполнимо {counts['runnable']}/{counts['total']} проверок, "
        f"пропущено {counts['skipped']}. degradation_report -> {out}"
    )

    dispatch_result = compute_common.dispatch_blocks(paths, defaults, report)
    summary = compute_common.build_metrics_summary(report, dispatch_result)
    summary_path = compute_common.write_json_atomic(
        paths.metrics / "metrics_summary.json", summary
    )

    log("compute: расчёт метрик по блокам:")
    for name, status in dispatch_result["block_status"].items():
        log(f"  {name}: {status}")
    log(f"compute: metrics_summary -> {summary_path}")


def run_analyze(paths: ClientPaths, log: StageLogger) -> None:
    """metrics + inputs/ -> findings/draft/*.yaml. Единственный слой с LLM.

    Перед вызовом src.analyze.draft_findings.draft() findings/draft/
    перезаписывается целиком (принцип 2 — свой слой можно перезаписывать
    полностью): имена файлов находок нумеруются заново внутри каждого
    прогона (F-<блок>-<nn>.yaml), поэтому без очистки старые файлы
    предыдущего прогона (например, более многочисленного) могли бы
    остаться рядом с новыми — повторный запуск не был бы идемпотентен.
    findings/approved/ этот стейдж не трогает и не создаёт — гейт перед
    report (report_gate_message) остаётся под ручным контролем аналитика.
    """
    import shutil

    from ..analyze import draft_findings

    if paths.findings_draft.exists():
        shutil.rmtree(paths.findings_draft)
    paths.findings_draft.mkdir(parents=True, exist_ok=True)

    config = load_client_config(paths)
    methodology = load_methodology()

    written = draft_findings.draft(paths, config, methodology)
    finding_files = [
        name for name in written if name != draft_findings.INPUT_PACK_ARTIFACT_NAME
    ]

    log(f"analyze: черновиков находок записано {len(finding_files)} -> {paths.findings_draft}")
    if finding_files:
        log(f"  {', '.join(finding_files)}")
    log("")
    log(
        "ГЕЙТ ПЕРЕД REPORT: черновики в findings/draft/ — не факт для отчёта, "
        "нужна ручная проверка аналитика.\n"
        f"  1. Проверь черновики находок в: {paths.findings_draft}\n"
        f"  2. Утверждённые вручную перенеси в: {paths.findings_approved}\n"
        "  3. Затем запусти: python run.py "
        f"{paths.client} --stage report"
    )


def run_report(paths: ClientPaths, log: StageLogger) -> bool:
    """findings/approved/ + degradation_report -> report/. Защищён гейтом."""
    if not approved_findings_present(paths):
        log(report_gate_message(paths))
        return False

    from ..report import build_report

    paths.report.mkdir(parents=True, exist_ok=True)
    config = load_client_config(paths)
    defaults = load_defaults()

    try:
        out_path = build_report.build(paths, config, defaults)
    except Exception as exc:
        log(f"report: ОШИБКА сборки отчёта — {type(exc).__name__}: {exc}")
        raise

    log(f"report: собран -> {out_path}")
    return True
