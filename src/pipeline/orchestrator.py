"""Логика этапов конвейера и гейтов.

Оркестратор ничего не знает про конкретный API — он лишь координирует слои и
следит за инвариантами: неизменяемость чужих слоёв, гейт перед report,
управляемая деградация. Тяжёлую работу делают модули extract/transform/compute/
analyze/report; здесь — только каркас вызовов и общие утилиты (пути, логи,
загрузка конфигов).

LLM вызывается только внутри слоя analyze; сам оркестратор его не трогает.
"""

from __future__ import annotations

import sys
from datetime import datetime
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
        print(message)
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
    log("intake: структура конфига валидна (ping токенов — TODO в extract).")
    return True


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
                log(f"extract[{mod_name}]: ИСТОЧНИК НЕДОСТУПЕН — {exc} (код {exc.exit_code})")
                unavailable.append(mod_name)
            except NotImplementedError:
                log(f"extract[{mod_name}]: экстрактор ещё не реализован — пропуск")
                skipped.append(mod_name)

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

    built = build_canonical.build(paths, config, defaults)
    if built:
        log(f"transform: построено {len(built)} таблиц -> {', '.join(built)}")
    else:
        log("transform: нет сырья ни для одной канонической таблицы (см. data/raw/manifest.json).")


def run_compute(paths: ClientPaths, log: StageLogger) -> None:
    """canonical -> data/metrics/ + degradation_report.json.

    Считаются только проверки, чьи requires удовлетворены. Непокрытые уходят в
    degradation_report (см. src.pipeline.degradation).
    """
    import json

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
    log("compute: расчёт метрик (block0..block6) — заглушка, ещё не реализован.")


def run_analyze(paths: ClientPaths, log: StageLogger) -> None:
    """metrics + inputs/ -> findings/draft/*.yaml. Единственный слой с LLM."""
    paths.findings_draft.mkdir(parents=True, exist_ok=True)
    log("analyze: заглушка — src/analyze/draft_findings.py не реализован.")


def run_report(paths: ClientPaths, log: StageLogger) -> bool:
    """findings/approved/ + degradation_report -> report/. Защищён гейтом."""
    if not approved_findings_present(paths):
        log(report_gate_message(paths))
        return False
    paths.report.mkdir(parents=True, exist_ok=True)
    log("report: заглушка — src/report/build_report.py не реализован.")
    return True
