#!/usr/bin/env python
"""CLI-оркестратор пайплайна диагностики маркетинга.

Использование:
    python run.py <client> --stage <intake|extract|transform|compute|analyze|report|all>

Этапы (подробности — в CLAUDE.md):
    intake     валидирует config.yaml и .env, пингует заявленные API,
               печатает таблицу «источник -> доступен/нет».
    extract    выгрузка источников из config в data/raw/ + manifest.json.
    transform  raw -> data/canonical/*.parquet.
    compute    canonical -> data/metrics/ + degradation_report.json.
    analyze    metrics + inputs/ -> findings/draft/*.yaml (единственный слой с LLM).
    report     findings/approved/ + degradation_report -> report/.
               ГЕЙТ: запрещён при пустом findings/approved/.
    all        всё подряд с остановкой на гейтах.

Каждый этап пишет лог в clients/<name>/logs/<stage>_<timestamp>.log.
Повторный запуск этапа идемпотентен: перезапись своего слоя целиком допустима,
чужих слоёв — нет.
"""

from __future__ import annotations

import argparse
import sys

from src.pipeline import orchestrator as orch


def _stage_intake(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "intake") as log:
        return orch.run_intake(paths, log)


def _stage_extract(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "extract") as log:
        orch.run_extract(paths, log)
    return True


def _stage_transform(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "transform") as log:
        orch.run_transform(paths, log)
    return True


def _stage_compute(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "compute") as log:
        orch.run_compute(paths, log)
    return True


def _stage_analyze(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "analyze") as log:
        orch.run_analyze(paths, log)
    return True


def _stage_report(paths: orch.ClientPaths) -> bool:
    with orch.StageLogger(paths, "report") as log:
        return orch.run_report(paths, log)


# Порядок исполнения для стадии all и диспетчеризация одиночных стадий.
_RUNNERS = {
    "intake": _stage_intake,
    "extract": _stage_extract,
    "transform": _stage_transform,
    "compute": _stage_compute,
    "analyze": _stage_analyze,
    "report": _stage_report,
}


def run_all(paths: orch.ClientPaths) -> int:
    """Последовательный прогон всех этапов с остановкой на первом провале/гейте."""
    for stage in orch.STAGES:
        print(f"\n=== stage: {stage} ===")
        ok = _RUNNERS[stage](paths)
        if not ok:
            print(f"\nОстановка на этапе '{stage}' (провал валидации или гейт).")
            return 1
    print("\nВсе этапы завершены.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Пайплайн диагностики маркетинга для малого бизнеса.",
    )
    parser.add_argument("client", help="имя клиента (каталог clients/<client>/)")
    parser.add_argument(
        "--stage",
        required=True,
        choices=(*orch.STAGES, "all"),
        help="этап конвейера",
    )
    args = parser.parse_args(argv)

    paths = orch.ClientPaths(args.client)
    if not paths.exists():
        print(
            f"Клиент '{args.client}' не найден: нет {paths.config_file}\n"
            "Скопируйте clients/_template в clients/<client> и заполните config.yaml.",
            file=sys.stderr,
        )
        return 2

    if args.stage == "all":
        return run_all(paths)

    ok = _RUNNERS[args.stage](paths)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
