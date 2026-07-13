"""Экстрактор: Яндекс.Метрика Reports API (агрегаты для сверки).

Контракт:
    Читает   — config.sources.metrika (counter_id), METRIKA_TOKEN, окно дат.
    Пишет    — data/raw/metrika_reports/ (агрегированные срезы) + manifest.json.
               Служит контрольной суммой к Logs API.
    Деградация — опционален; без него теряется быстрая сверка, но не сами визиты.
    LLM      — не используется.

Что выгружаем:
    1. goals_list.json          — список целей счётчика (Management API). Нужен,
                                  чтобы знать, какие ym:s:goal<id>reaches спрашивать.
    2. goals_by_month.json      — визиты + достижения по каждой цели по месяцам.
                                  Сверочная таблица для расчёта переотработки целей
                                  на агрегатах (проверка 0.1 на стороне Reports).
    3. sources_by_month.json    — визиты по источникам трафика по месяцам
                                  (сверка с last-significant из Logs API).

Все срезы — как отдал Stat API (raw JSON), помесячно. Парсинг/склейка — transform.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import _common as C

SCRIPT_VERSION = "0.2.0"
SOURCE = "metrika_reports"
CANONICAL_TABLES = ["visits"]

MANAGEMENT_BASE = "https://api-metrika.yandex.net/management/v1/counter"
STAT_URL = "https://api-metrika.yandex.net/stat/v1/data"

# Stat API допускает максимум 20 метрик на запрос. Цели спрашиваем метрикой
# ym:s:goal<id>reaches, поэтому режем на батчи так, чтобы visits + батч целей
# укладывались в лимит.
STAT_MAX_METRICS = 20
GOAL_BATCH = STAT_MAX_METRICS - 1  # 19 целей + ym:s:visits = 20


def _auth_headers(token: str) -> dict[str, str]:
    """Заголовок авторизации Метрики. Токен нигде не логируется."""
    return {"Authorization": f"OAuth {token}"}


def ping(config: dict[str, Any], env: dict[str, str]) -> bool:
    """Лёгкая проверка живости METRIKA_TOKEN через список целей счётчика."""
    import requests

    metrika = (config.get("sources") or {}).get("metrika") or {}
    counter_id = metrika.get("counter_id")
    if not counter_id:
        return False
    try:
        token = C.get_token(env, "METRIKA_TOKEN", SOURCE)
    except C.AuthError:
        return False

    session = requests.Session()
    try:
        resp = C.http_request(
            session, "GET", f"{MANAGEMENT_BASE}/{counter_id}/goals",
            source=SOURCE, headers=_auth_headers(token), timeout=30,
        )
        return getattr(resp, "status_code", 500) < 400
    except C.SourceUnavailable:
        return False


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    session: Any = None,
    defaults: dict[str, Any] | None = None,
    today: Any = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Выгрузить агрегаты для сверки в data/raw/metrika_reports/."""
    import requests

    log = log or (lambda _msg: None)
    session = session or requests.Session()

    metrika = (config.get("sources") or {}).get("metrika") or {}
    counter_id = metrika.get("counter_id")
    if not counter_id:
        raise C.SourceUnavailable(SOURCE, "не задан sources.metrika.counter_id в config.yaml")

    token = C.get_token(env, "METRIKA_TOKEN", SOURCE)
    headers = _auth_headers(token)

    date_from, date_to = C.resolve_window(config, defaults, today=today)
    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    log(f"{SOURCE}: окно {C.fmt(date_from)}..{C.fmt(date_to)}, счётчик {counter_id}")

    # 1. Список целей счётчика.
    goals = _fetch_goals(session, counter_id, headers)
    _dump(out_dir / "goals_list.json", goals)
    goal_ids = [g.get("id") for g in goals if g.get("id") is not None]
    log(f"{SOURCE}: целей у счётчика — {len(goal_ids)}")

    # 2. Визиты + достижения по целям, помесячно. Цели режем на батчи по 19
    #    (лимит Stat API — 20 метрик на запрос вместе с ym:s:visits).
    goals_by_month: list[dict[str, Any]] = []
    for start in range(0, len(goal_ids), GOAL_BATCH):
        batch = goal_ids[start:start + GOAL_BATCH]
        metrics = ["ym:s:visits"] + [f"ym:s:goal{gid}reaches" for gid in batch]
        part = _stat_by_month(
            session, counter_id, headers, date_from, date_to,
            metrics=metrics, dimensions=[],
        )
        for entry in part:
            entry["goal_ids"] = batch     # какие цели в этом батче (для transform)
        goals_by_month.extend(part)
    _dump(out_dir / "goals_by_month.json", goals_by_month)

    # 3. Источники трафика, помесячно.
    sources_by_month = _stat_by_month(
        session, counter_id, headers, date_from, date_to,
        metrics=["ym:s:visits"], dimensions=["ym:s:lastsignTrafficSource"],
    )
    _dump(out_dir / "sources_by_month.json", sources_by_month)

    rows = (
        len(goals)
        + sum(len(m["data"].get("data", [])) for m in goals_by_month)
        + sum(len(m["data"].get("data", [])) for m in sources_by_month)
    )
    manifest = _record_manifest(paths, date_from, date_to, rows)
    log(f"{SOURCE}: готово — {rows} строк агрегатов")

    return {
        "source": SOURCE,
        "rows": rows,
        "goals": len(goals),
        "date_from": C.fmt(date_from),
        "date_to": C.fmt(date_to),
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
    }


# ── Шаги Reports/Management API ────────────────────────────────────────────
def _fetch_goals(session, counter_id, headers) -> list[dict[str, Any]]:
    """Список целей счётчика (Management API)."""
    resp = C.http_request(
        session, "GET", f"{MANAGEMENT_BASE}/{counter_id}/goals",
        source=SOURCE, headers=headers, timeout=30,
    )
    C.ensure_ok(resp, SOURCE, "goals list")
    return resp.json().get("goals") or []


def _stat_by_month(
    session, counter_id, headers, date_from, date_to, *, metrics, dimensions
) -> list[dict[str, Any]]:
    """Пройтись Stat API по каждому месяцу окна и собрать сырые ответы."""
    out: list[dict[str, Any]] = []
    for chunk_from, chunk_to in C.month_chunks(date_from, date_to):
        params = {
            "ids": counter_id,
            "date1": C.fmt(chunk_from),
            "date2": C.fmt(chunk_to),
            "metrics": ",".join(metrics),
            "limit": 100000,
            "accuracy": "full",
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        resp = C.http_request(
            session, "GET", STAT_URL,
            source=SOURCE, headers=headers, params=params, timeout=120,
        )
        C.ensure_ok(resp, SOURCE, "stat data")
        out.append({
            "month": C.fmt(chunk_from),
            "date1": C.fmt(chunk_from),
            "date2": C.fmt(chunk_to),
            "data": resp.json(),
        })
    return out


def _dump(path: Path, obj: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _record_manifest(paths, date_from, date_to, rows) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from=C.fmt(date_from), date_to=C.fmt(date_to),
        rows=rows, script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
    )
