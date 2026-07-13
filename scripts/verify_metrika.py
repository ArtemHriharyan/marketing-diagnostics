#!/usr/bin/env python
"""Сверка Logs API против Reporting API Яндекс.Метрики.

Зачем: Logs API (визит-уровень) и Reporting API (агрегаты) считают одно и то же
двумя путями. Если они расходятся — где-то потеря/дубли выгрузки либо
семплирование агрегатов.

Что делает:
  1. Читает data/raw/metrika_logs/*.csv.gz, считает по месяцам:
       - visits       = число уникальных ym:s:visitID;
       - goal_reaches = СУММАРНЫЕ срабатывания form_submit-целей — все
                        вхождения id из config.goals.form_submit_goal_ids в
                        ym:s:goalsID, включая повторы в рамках одного визита
                        (если один и тот же id встретился в массиве дважды —
                        считаем два срабатывания).
  2. Читает data/raw/metrika_reports/goals_by_month.json (визиты ym:s:visits и
     достижения ym:s:goal<id>reaches по месяцам) — reaches там уже "суммарные
     срабатывания" в терминах Reports API, так что сравнение симметрично:
     reaches Logs против reaches Reports (а не "визиты с целью" против
     "reaches", как считалось раньше — это было сравнение разных сущностей).
  3. Печатает таблицу: месяц | визиты logs | визиты reports | Δ% | reaches logs |
     reaches reports | Δ% (с меткой статуса на каждую дельту).
  4. Пороги на |Δ| — одинаковые для визитов и целей: <2% OK, 2–5% WARN,
     >5% FAIL. При любом FAIL — ненулевой код возврата. Это сигнал реального
     расхождения выгрузок (потеря/дубли/семплирование), а НЕ переотработки
     целей — переотработка (reaches намного больше уникальных визитов с
     целью) — нормальное явление и сама по себе verdict не портит.

goal_inflation_preview — информационная оценка коэффициента переотработки
(reaches / уникальные визиты с целью), посчитанная ЦЕЛИКОМ по Logs (обе
величины с одной стороны, без сопоставления с Reports). НЕ влияет на verdict
и код возврата — это лишь предпросмотр. Официальный расчёт переотработки —
compute/block0.py, проверка 0.1, по canonical visits.parquet
(form_submit_count / form_submit).

Запуск:
    python scripts/verify_metrika.py <client>
Также вызывается автоматически в конце стадии extract для источника metrika
(src/pipeline/orchestrator.py::run_extract).

LLM здесь не вызывается: чистая детерминированная сверка.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Пороги на модуль относительной дельты, в процентах. Одинаковые для визитов
# и для reaches-против-reaches — это сравнения одной природы (обе стороны
# считают одну и ту же сущность, просто разными API).
OK_MAX = 2.0     # |Δ| < 2%      -> OK
WARN_MAX = 5.0   # 2% <= |Δ| <= 5% -> WARN;  |Δ| > 5% -> FAIL

LOGS_DIR = "metrika_logs"
REPORTS_DIR = "metrika_reports"
GOALS_BY_MONTH = "goals_by_month.json"
OUTPUT_NAME = "reconciliation.json"

# Имена полей Logs API, по которым ищем колонки (порядок в файле не хардкодим).
COL_VISIT_ID = "ym:s:visitID"
COL_DATETIME = "ym:s:dateTime"
COL_GOALS_ID = "ym:s:goalsID"

_STATUS_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}


# ── Утилиты дельты/статуса ─────────────────────────────────────────────────
def pct_delta(logs: float, reports: float) -> float:
    """Модуль относительной дельты logs к reports (база — reports), в %.

    reports==0: 0% если и logs==0, иначе бесконечность (гарантированный FAIL).
    """
    if reports == 0:
        return 0.0 if logs == 0 else float("inf")
    return abs(logs - reports) / reports * 100.0


def status_of(delta_pct: float) -> str:
    if delta_pct < OK_MAX:
        return "OK"
    if delta_pct <= WARN_MAX:
        return "WARN"
    return "FAIL"


def _worst(statuses: list[str]) -> str:
    worst = "OK"
    for s in statuses:
        if _STATUS_RANK[s] > _STATUS_RANK[worst]:
            worst = s
    return worst


# ── Чтение Logs API (визит-уровень) ────────────────────────────────────────
def load_logs_monthly(raw_dir: Path, form_submit_ids: set[int]) -> dict[str, dict[str, int]]:
    """Свести визиты и срабатывания form_submit-целей по месяцам из csv.gz Logs API.

    Возвращает {"YYYY-MM": {"visits": N, "goal_reaches": R, "goal_visits": V}}:
      - visits       — уникальные ym:s:visitID месяца;
      - goal_reaches — СУММАРНЫЕ срабатывания form_submit-целей (каждое
                       вхождение id в ym:s:goalsID считается отдельно, дубли
                       внутри визита не схлопываются) — идёт в сверку с
                       reaches Reports API;
      - goal_visits  — уникальные визиты с хотя бы одним срабатыванием;
                       используется ТОЛЬКО для goal_inflation_preview, в
                       саму сверку (verdict) не входит.
    """
    logs_dir = Path(raw_dir) / LOGS_DIR
    visits: dict[str, set[str]] = {}
    goal_reaches: dict[str, int] = {}
    goal_visit_ids: dict[str, set[str]] = {}

    for gz_path in sorted(logs_dir.glob("*.csv.gz")):
        with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            try:
                i_id = header.index(COL_VISIT_ID)
                i_dt = header.index(COL_DATETIME)
                i_goals = header.index(COL_GOALS_ID)
            except ValueError as exc:
                raise SystemExit(f"verify_metrika: в {gz_path.name} нет ожидаемой колонки: {exc}")

            for line in fh:
                if not line.strip():
                    continue
                row = line.rstrip("\n").split("\t")
                if len(row) <= max(i_id, i_dt, i_goals):
                    continue
                visit_id = row[i_id]
                month = row[i_dt][:7]  # "2025-04-20 15:54:16" -> "2025-04"
                visits.setdefault(month, set()).add(visit_id)

                if form_submit_ids:
                    hits = _row_goal_hit_count(row[i_goals], form_submit_ids)
                    if hits:
                        goal_reaches[month] = goal_reaches.get(month, 0) + hits
                        goal_visit_ids.setdefault(month, set()).add(visit_id)

    out: dict[str, dict[str, int]] = {}
    for month, ids in visits.items():
        out[month] = {
            "visits": len(ids),
            "goal_reaches": goal_reaches.get(month, 0),
            "goal_visits": len(goal_visit_ids.get(month, set())),
        }
    return out


def _row_goal_hit_count(goals_cell: str, form_submit_ids: set[int]) -> int:
    """Число вхождений form_submit-целей в ym:s:goalsID (JSON-массив id).

    Считает КАЖДОЕ вхождение отдельно (с дублями) — это и есть "суммарные
    срабатывания" на стороне Logs, сопоставимые с reaches Reports API.
    """
    cell = (goals_cell or "").strip()
    if not cell or cell == "[]":
        return 0
    try:
        reached = json.loads(cell)
    except (ValueError, TypeError):
        return 0
    count = 0
    for gid in reached:
        try:
            if int(gid) in form_submit_ids:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


# ── Чтение Reporting API (агрегаты) ────────────────────────────────────────
def load_reports_monthly(raw_dir: Path, form_submit_ids: set[int]) -> dict[str, dict[str, Any]]:
    """Свести визиты и суммарные reaches form_submit-целей по месяцам.

    Структура файла: список записей {month, date1, data:<stat>, goal_ids:[...]}.
    В stat.totals: [visits, reaches(goal_ids[0]), reaches(goal_ids[1]), ...].
    Цели одного месяца могут быть разбиты на несколько батчей (лимит метрик).
    """
    path = Path(raw_dir) / REPORTS_DIR / GOALS_BY_MONTH
    entries = json.loads(path.read_text(encoding="utf-8"))

    visits: dict[str, float] = {}
    goal_reaches: dict[str, float] = {}
    sampled: dict[str, bool] = {}

    for entry in entries:
        month = str(entry.get("month", ""))[:7]
        data = entry.get("data") or {}
        totals = data.get("totals") or []
        if not totals:
            continue
        # visits берём из любого батча месяца — метрика ym:s:visits одинакова.
        visits[month] = float(totals[0])
        sampled[month] = sampled.get(month, False) or bool(data.get("sampled"))

        goal_ids = entry.get("goal_ids") or []
        for idx, gid in enumerate(goal_ids):
            if int(gid) in form_submit_ids:
                # totals[0] — визиты, поэтому reaches цели i лежит в totals[i+1].
                reaches = float(totals[idx + 1]) if idx + 1 < len(totals) else 0.0
                goal_reaches[month] = goal_reaches.get(month, 0.0) + reaches

    out: dict[str, dict[str, Any]] = {}
    for month, v in visits.items():
        out[month] = {
            "visits": v,
            "goal_reaches": goal_reaches.get(month, 0.0),
            "sampled": sampled.get(month, False),
        }
    return out


# ── Сверка ─────────────────────────────────────────────────────────────────
def reconcile(raw_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Собрать полный отчёт сверки Logs↔Reports (визиты и reaches-против-reaches)."""
    raw_dir = Path(raw_dir)
    form_submit_ids = {
        int(g) for g in ((config.get("goals") or {}).get("form_submit_goal_ids") or [])
    }

    logs = load_logs_monthly(raw_dir, form_submit_ids)
    reports = load_reports_monthly(raw_dir, form_submit_ids)

    months = sorted(set(logs) & set(reports))
    logs_only = sorted(set(logs) - set(reports))
    reports_only = sorted(set(reports) - set(logs))

    rows: list[dict[str, Any]] = []
    statuses: list[str] = []
    for month in months:
        lv, lr = logs[month]["visits"], logs[month]["goal_reaches"]
        rv, rr = reports[month]["visits"], reports[month]["goal_reaches"]
        vd, gd = pct_delta(lv, rv), pct_delta(lr, rr)
        vs, gs = status_of(vd), status_of(gd)
        statuses += [vs, gs]

        goal_visits_logs = logs[month]["goal_visits"]
        ratio = (lr / goal_visits_logs) if goal_visits_logs else None

        rows.append({
            "month": month,
            "visits_logs": int(lv),
            "visits_reports": int(rv),
            "visits_delta_pct": _round(vd),
            "visits_status": vs,
            "goal_reaches_logs": int(lr),
            "goal_reaches_reports": int(rr),
            "goal_reaches_delta_pct": _round(gd),
            "goal_reaches_status": gs,
            "sampled": bool(reports[month].get("sampled")),
            # Информационно: НЕ участвует в verdict/exit_code (см. докстринг
            # модуля). Официальный расчёт — compute/block0.py, проверка 0.1.
            "goal_inflation_preview": {
                "reaches_logs": int(lr),
                "visits_with_goal_logs": goal_visits_logs,
                "ratio": round(ratio, 3) if ratio is not None else None,
            },
        })

    verdict = _worst(statuses) if statuses else "OK"
    counts = {s: statuses.count(s) for s in ("OK", "WARN", "FAIL")}

    notes: list[str] = []
    if not form_submit_ids:
        notes.append("config.goals.form_submit_goal_ids пуст — колонки целей нулевые; "
                     "заполни id целей отправки формы для осмысленной сверки.")
    if logs_only:
        notes.append(f"месяцы только в Logs (нет в Reports): {', '.join(logs_only)}")
    if reports_only:
        notes.append(f"месяцы только в Reports (нет в Logs — выгрузка логов неполна?): "
                     f"{', '.join(reports_only)}")
    if any(r["sampled"] for r in rows):
        notes.append("часть агрегатов Reports семплирована — расхождения возможны штатно.")
    notes.append(
        "цели сверяются reaches-против-reaches (суммарные срабатывания с обеих сторон, "
        "включая повторы в рамках визита); FAIL здесь означает реальное расхождение "
        "выгрузок. goal_inflation_preview — информационная оценка переотработки по "
        "одним Logs, в verdict не входит; итоговый расчёт — compute/block0.py, "
        "проверка 0.1."
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "form_submit_goal_ids": sorted(form_submit_ids),
        "thresholds_pct": {"ok_below": OK_MAX, "warn_below_or_eq": WARN_MAX},
        "verdict": verdict,
        "counts": counts,
        "months": rows,
        "notes": notes,
    }


def _round(x: float) -> Any:
    return "inf" if x == float("inf") else round(x, 2)


# ── Вывод ──────────────────────────────────────────────────────────────────
def format_table(report: dict[str, Any]) -> str:
    """Человекочитаемая таблица сверки."""
    head = (f"{'месяц':<9}| {'виз.logs':>9} | {'виз.rep':>9} | {'Δ виз':>12} | "
            f"{'reach.logs':>10} | {'reach.rep':>10} | {'Δ reach':>12}")
    lines = [head, "-" * len(head)]
    for r in report["months"]:
        lines.append(
            f"{r['month']:<9}| {r['visits_logs']:>9} | {r['visits_reports']:>9} | "
            f"{_fmt_delta(r['visits_delta_pct'], r['visits_status']):>12} | "
            f"{r['goal_reaches_logs']:>10} | {r['goal_reaches_reports']:>10} | "
            f"{_fmt_delta(r['goal_reaches_delta_pct'], r['goal_reaches_status']):>12}"
        )
    c = report["counts"]
    lines.append("-" * len(head))
    lines.append(f"ИТОГ: {report['verdict']}  (OK {c['OK']}, WARN {c['WARN']}, FAIL {c['FAIL']})")
    for note in report.get("notes", []):
        lines.append(f"  • {note}")
    return "\n".join(lines)


def _fmt_delta(delta: Any, status: str) -> str:
    val = "inf%" if delta == "inf" else f"{delta}%"
    return f"{val} {status}"


def write_report(raw_dir: Path, report: dict[str, Any]) -> Path:
    """Записать reconciliation.json в data/raw/ (перезапись — идемпотентно)."""
    out = Path(raw_dir) / OUTPUT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return out


def exit_code(report: dict[str, Any]) -> int:
    """Ненулевой код только при FAIL (WARN не роняет) — реальное расхождение

    выгрузок (визиты и/или reaches), а не переотработка целей.
    """
    return 1 if report["verdict"] == "FAIL" else 0


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    try:  # на Windows-консоли включаем UTF-8, чтобы не спотыкаться на кириллице
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import argparse

    parser = argparse.ArgumentParser(
        prog="verify_metrika.py",
        description="Сверка Logs API против Reporting API Яндекс.Метрики.",
    )
    parser.add_argument("client", help="имя клиента (каталог clients/<client>/)")
    args = parser.parse_args(argv)

    # Импорт оркестратора только в CLI-ветке (ядро сверки от него не зависит).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.pipeline import orchestrator as orch

    paths = orch.ClientPaths(args.client)
    if not paths.exists():
        print(f"Клиент '{args.client}' не найден: нет {paths.config_file}", file=sys.stderr)
        return 2

    config = orch.load_client_config(paths)
    report = reconcile(paths.raw, config)
    print(format_table(report))
    out = write_report(paths.raw, report)
    print(f"\nreconciliation.json -> {out}")
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
