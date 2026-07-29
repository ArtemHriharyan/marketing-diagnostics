"""Блок 4 — SEO и органический спрос (каталог v2 §9, задача 5bA: S01–S10).

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §9):
    S01  брендовый и небрендовый органический трафик смешаны   [seo_queries]
    S02  запросы на позициях 4–10 недополучают клики            [seo_queries]
    S03  запросы на позициях 11–20 не дожаты до первой страницы [seo_queries]
    S04  CTR аномально низкий для текущей позиции                [seo_queries]
    S05  отдельные страницы теряют клики и позиции               [seo_queries]
    S06  сезонность ошибочно принимается за рост/падение SEO    [seo_queries] (+wordstat)
    S07  коммерческий спрос без релевантной посадочной           [wordstat, seo_queries] (+site_crawl)
    S08  страница не соответствует намерению запроса             [seo_queries, visits] (+site_crawl)
    S09  несколько страниц конкурируют по одному кластеру        [seo_queries]
    S10  по запросу ранжируется не та страница                   [seo_queries] (+visits)

Контракт:
    Читает   — data/canonical/{seo_queries,visits,site_pages}.parquet,
               data/metrics/degradation_report.json (confidence_cap на проверку).
               config клиента НЕ читается: is_brand уже посчитан в transform
               (build_canonical.is_brand_query, config.brand_terms применены
               там), здесь используется готовая колонка seo_queries.is_brand.
    Пишет    — data/metrics/{s01..s10}.csv/.json. БЕЗ LLM.

S11–S27 не реализуются этой задачей (см. промт 5bA) — не путать с
config/methodology.yaml, где они уже зарегистрированы для будущих задач.

── S11–S27 не реализуются (см. промт задачи 5bA) ────────────────────────────
requires/optional этих ID уже есть в methodology.yaml (регистр общий на весь
блок 4), но диспетчер run() ниже гейтит только S01-S10 — S11-S27 остаются
"not_implemented" до отдельной задачи, тот же прецедент, что C01-C12 (5G) vs
C13-C25 (5H) в block3.py.

── Структурные разрывы (НЕ устраняются здесь — вне allowed_files) ───────────

1. wordstat: src/extract/wordstat.py объявляет CANONICAL_TABLES=["wordstat"],
   но build_canonical.py НЕ строит data/canonical/wordstat.parquet ("схема не
   задана" — см. docstring transform-модуля) — тот же класс разрыва, что CrUX
   в block3.py (canonical_tables заявлен в манифесте экстрактора, физического
   parquet нет). S07 (requires=[wordstat, seo_queries]) поэтому ВСЕГДА пишет
   unavailable в текущем состоянии пайплайна — придумывать проверку без данных
   нельзя (CLAUDE.md, протокол микрозадач п.5), тот же принцип, что A07/A16/A25
   в block1.py. S06 (optional=[wordstat]) остаётся runnable по seo_queries
   одному, но каждая строка отдельно помечает wordstat_available=false — без
   Wordstat нельзя утверждать, что падение вызвано сезонностью, а не SEO
   (каталог §11, "Что Claude не должен утверждать", п.9) — итоговый вердикт
   S06 остаётся LOW (гипотеза), даже при большом объёме данных seo_queries.

2. month-гранулярность seo_queries: build_seo_queries_gsc агрегирует по
   (query, page, device, month) — по одной строке на месяц, как и нужно для
   S05/S06 ("сравнить месяцы"). Но later build_canonical.py делает
   `drop_duplicates(subset=["query","page","source","device"], keep="first")`
   БЕЗ "month" в ключе (см. build_canonical.py:2016-2018) — это дедуп-ключ
   слоя transform, не в allowed_files этой задачи, менять нельзя. Практическое
   следствие: на части выгрузок seo_queries может нести только один month на
   комбинацию (query,page,device), даже если сырьё имело несколько месяцев.
   S05/S06 здесь написаны так, чтобы КОРРЕКТНО работать при любом фактическом
   числе различимых месяцев — если для страницы доступен только один месяц,
   тренд не строится (запись "insufficient_month_history", не имитируется).

3. "Целевой URL" (S10, "Сопоставить целевой URL, фактический URL и конверсию"):
   в схеме клиента (config.yaml) и в canonical-слое нет поля/таблицы, явно
   сопоставляющей кластер запросов с "правильной" посадочной страницей —
   такой конфигурации не существует (вне allowed_files этой задачи — не
   выдумываем поле). Единственный доступный прокси — конверсия: "актуальный
   URL" = страница, которая ФАКТИЧЕСКИ ранжируется лучше всех (минимальная
   средневзвешенная позиция) среди страниц, конкурирующих за один запрос
   (см. S09); "целевой" в смысле "какая страница дала бы лучший результат
   пользователю" заменяется страницей с лучшей органической вовлечённостью
   среди тех же кандидатов. Если это разные страницы — кандидат находки.
   Явно помечено `target_url_from_config: false` в каждой строке, чтобы не
   выдать эвристику за конфигурационный факт.

4. S08 ("Классифицировать intent и сравнить с типом ранжируемой страницы"):
   site_pages (см. block3.py SCHEMAS) не хранит классификацию intent/типа
   страницы — только url/http_status/redirect_chain/final_url/canonical_url/
   robots_directive/in_sitemap/title/description/h1/crawled_at/js_content_diff.
   Автоматическая классификация intent не существует в каноническом слое —
   не придумывается здесь. Автоматическая часть S08 — органическая
   вовлечённость по (page, device) как прокси "получил ли пользователь то,
   что ожидал" (тот же принцип, что C12 в block3.py — "zero_engagement" как
   косвенный сигнал непонятного/нерелевантного предложения). site_pages.title/
   description/h1 подключаются ТОЛЬКО как читаемый контекст для аналитика
   (без автоматического вердикта), если таблица доступна — тот же приём, что
   manual-наблюдения в block3.py переносятся "как есть".

── S08–S10: обязательный device-разрез (задача 5bA, промт) ──────────────────
Для S08/S09/S10 колонка seo_queries.device участвует в отдельном разрезе:
строки с device="unknown" (источник не даёт device-разбивку — Вебмастер
всегда, GSC при старом раздельном экспорте, см. build_canonical.py) не
попадают в findings с разрезом "by_device", но участвуют в основном
(device-агностическом) агрегате наравне с остальными строками — не
исключаются из проверки целиком, только из под-разреза по устройству
(прямое требование промта задачи, тот же принцип, что «единая методология
не выбрасывает частично неполные строки, если их ещё можно использовать
где-то ещё» — см. CLAUDE.md принцип 4, «управляемая деградация»). Для
S01-S07 (не требуют device по промту этой задачи) device-разрез не строится.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow.parquet as pq

from . import common
from ..pipeline import degradation as degradation_mod

# ── Пороги-эвристики (каталог не даёт точных чисел — тот же принцип, что
# block0/1/2/3.py: обоснование у каждой константы) ───────────────────────────

# S01: минимум суммарных показов, чтобы вообще судить о брендовом/небрендовом
# миксе (иначе шум на единичных показах); доля брендового спроса, начиная с
# которой микс считается "брендово-тяжёлым" (тот же принцип и число, что
# _T05_HIGH_BRAND_SHARE в block2.py — тот же смысловой порог, независимая
# константа, т.к. блоки compute не делят пороги через common.py).
_S01_MIN_SHOWS_FOR_CHECK = 50
_S01_HIGH_BRAND_SHARE = 0.5

# S02/S03: минимум суммарных показов (query, page), чтобы считать объём
# материальным, а не шумом единичных показов.
_MIN_SHOWS_FOR_OPPORTUNITY = 20

# S02 — "недополучают клики" (позиции 4-10, уже на первой странице).
_S02_POSITION_MIN, _S02_POSITION_MAX = 4.0, 10.0

# S03 — "strike zone" (позиции 11-20, легаси 5.1).
_S03_POSITION_MIN, _S03_POSITION_MAX = 11.0, 20.0

# S04: диапазоны позиций для сравнения CTR внутри бакета (каталог требует
# "диапазон позиций, устройств и типов запросов" — device здесь сознательно
# не участвует, см. докстринг модуля, "S08-S10: обязательный device-разрез").
# Второе измерение сравнения — is_brand (бренд/небренд, "типы запросов").
_S04_POSITION_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("top_1_3", 1.0, 3.0),
    ("page1_4_10", 4.0, 10.0),
    ("strike_zone_11_20", 11.0, 20.0),
    ("page2_plus_21", 21.0, None),
)
_S04_MIN_SHOWS_FOR_ROW = 20        # мин. показов у самого запроса для сравнения
_S04_MIN_QUERIES_FOR_MEDIAN = 5    # мин. число query-групп в бакете для медианы
_S04_CTR_LOW_RATIO = 0.5           # тот же коэффициент, что _A20_CTR_LOW_RATIO в block1.py

# S05: минимум суммарных показов за ранний период, чтобы сравнение было
# материальным; во сколько раз клики позднего периода должны просесть
# относительно раннего, чтобы считать это падением (не шумом).
_S05_MIN_SHOWS_FOR_TREND = 50
_S05_DECLINE_CLICK_RATIO = 0.7

# S06: минимум месяцев истории, чтобы вообще строить помесячный тренд;
# во сколько раз месяц должен отклониться от медианы месячных показов, чтобы
# считаться всплеском/провалом (тот же принцип, что _T09_SPIKE_RATIO/
# _T09_DROP_RATIO в block2.py).
_S06_MIN_MONTHS_FOR_TREND = 3
_S06_SPIKE_RATIO = 2.0
_S06_DROP_RATIO = 0.5

# S08: минимум показов кластера и минимум органических визитов на (page,
# device), чтобы сравнение было материальным; доля визитов без единого
# целевого действия, начиная с которой предложение страницы считается
# кандидатом на несоответствие намерению (выше порога C12 в block3.py —
# 0.85 — т.к. органический трафик по своей природе разнороднее платного).
_S08_MIN_SHOWS_FOR_CHECK = 20
_S08_MIN_ORGANIC_VISITS_FOR_CHECK = 20
_S08_HIGH_ZERO_ENGAGEMENT_SHARE = 0.9

# S09: минимум суммарных показов по запросу (по всем конкурирующим страницам),
# чтобы считать пересечение материальным, не шумом на единичных показах.
_S09_MIN_SHOWS_FOR_COMPETITION = 20

# S10: минимум органических визитов на странице-кандидате, чтобы её
# вовлечённость вообще сравнивать; минимальный разрыв (в п.п. вовлечённости),
# начиная с которого альтернативная страница считается "явно лучше".
_S10_MIN_ORGANIC_VISITS_FOR_COMPARISON = 20
_S10_ENGAGEMENT_GAP_PP = 0.05


# ── Общие хелперы (дублируют паттерн block0/1/2/3.py — блоки compute не
# делят приватные хелперы через common.py, см. CLAUDE.md принцип 2) ─────────
def _table_nonempty(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return pq.ParquetFile(path).metadata.num_rows > 0
    except OSError:
        return False


def _confidence_caps(paths: Any) -> dict[str, str]:
    """{check_id: confidence_cap} из уже записанного degradation_report.json."""
    report = common.load_degradation(paths)
    return {
        c.get("check_id"): c.get("confidence_cap", "HIGH")
        for c in (report.get("checks") or [])
        if c.get("check_id")
    }


def _cap(confidence: str, confidence_cap: str) -> str:
    """Прижать confidence к потолку проверки (compute капает вниз, не поднимает)."""
    return degradation_mod.min_confidence(confidence, confidence_cap)


def _sample_confidence(sample_size: int, min_sample_visits: int) -> str:
    """HIGH при визит-уровневой выборке >= порога, иначе MED (тот же принцип,

    что block0/1/2/3.py). Используется только там, где measurement реально
    визит-уровневый (S08 — органическая вовлечённость по visits), остальные
    S-проверки этого модуля — отчётные агрегаты seo_queries (report-уровень,
    как A02-A11 в block1.py), поэтому капаются на MED напрямую без выборки.
    """
    return "HIGH" if sample_size >= min_sample_visits else "MED"


def _write_unavailable(metrics_dir: Path, check_id: str, reason: str) -> None:
    """Явная запись «проверка недоступна» вместо молчаливого пропуска."""
    common.write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


def _median(values: list[float]) -> float | None:
    """Медиана отсортированного списка (не изменяет исходный список)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _url_path(url: str | None) -> str:
    """URL/путь -> нормализованный путь (тот же принцип, что
    normalize_entry_page/build_canonical._url_path в block3.py): без домена/
    query/фрагмента, нижний регистр, без хвостового slash (кроме корня "/").
    seo_queries.page бывает полным URL (GSC) или путём (Вебмастер) —
    нормализация делает оба сопоставимыми с visits.entry_page.
    """
    path = urlsplit(url or "").path or "/"
    path = path.lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _position_bucket(position: float | None) -> str | None:
    if position is None:
        return None
    for name, low, high in _S04_POSITION_BUCKETS:
        if position >= low and (high is None or position <= high):
            return name
    return None


def _aggregate_query_page(con: Any, where_sql: str = "") -> list[dict[str, Any]]:
    """seo_queries -> [{query, page, shows, clicks, position, is_brand}, ...]

    Группировка по (query, page) БЕЗ device/source/month (device-разрез не
    требуется для S01-S07, см. докстринг модуля) — суммарные показы/клики,
    средневзвешенная по показам позиция (только по строкам с известной
    позицией — тот же принцип взвешивания, что build_seo_queries_gsc в
    transform), is_brand как OR (запрос брендовый, если хоть одна строка
    группы размечена так — is_brand считается по тексту запроса в transform,
    поэтому в пределах одной группы (query, page) он всегда одинаков).
    """
    sql = (
        "SELECT query, page, "
        "SUM(total_shows) AS shows, SUM(total_clicks) AS clicks, "
        "SUM(avg_show_position * total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS pos_w, "
        "SUM(total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS shows_pos, "
        "bool_or(is_brand) AS is_brand "
        "FROM seo_queries "
        f"{where_sql} "
        "GROUP BY query, page"
    )
    rows = con.execute(sql).fetchall()
    out: list[dict[str, Any]] = []
    for query, page, shows, clicks, pos_w, shows_pos, is_brand in rows:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        shows_pos = int(shows_pos or 0)
        position = (pos_w / shows_pos) if (pos_w is not None and shows_pos > 0) else None
        out.append({
            "query": query, "page": page, "shows": shows, "clicks": clicks,
            "position": position, "is_brand": bool(is_brand),
        })
    return out


def _organic_visits_by_page(con: Any) -> dict[str, tuple[int, int]]:
    """{normalized_path: (organic_visits, engaged_visits)} по всем устройствам.

    engaged = хотя бы одна из 4 целевых групп визит-уровня (тот же признак
    вовлечённости, что C12/C10 в block3.py: form_open/form_submit/
    call_click/messenger_click).
    """
    rows = con.execute(
        "SELECT entry_page, COUNT(*), "
        "COUNT(*) FILTER (WHERE form_open OR form_submit OR call_click OR messenger_click) "
        "FROM visits WHERE source_group = 'organic' GROUP BY entry_page"
    ).fetchall()
    out: dict[str, tuple[int, int]] = {}
    for entry_page, total, engaged in rows:
        path = _url_path(entry_page)
        prev_total, prev_engaged = out.get(path, (0, 0))
        out[path] = (prev_total + int(total or 0), prev_engaged + int(engaged or 0))
    return out


def _organic_visits_by_page_device(con: Any) -> dict[tuple[str, str], tuple[int, int]]:
    """{(normalized_path, device): (organic_visits, engaged_visits)}."""
    rows = con.execute(
        "SELECT entry_page, device, COUNT(*), "
        "COUNT(*) FILTER (WHERE form_open OR form_submit OR call_click OR messenger_click) "
        "FROM visits WHERE source_group = 'organic' GROUP BY entry_page, device"
    ).fetchall()
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for entry_page, device, total, engaged in rows:
        key = (_url_path(entry_page), device)
        prev_total, prev_engaged = out.get(key, (0, 0))
        out[key] = (prev_total + int(total or 0), prev_engaged + int(engaged or 0))
    return out


def _load_site_titles(canonical: dict[str, Path], paths: Any) -> dict[str, dict[str, Any]]:
    """{normalized_path: {title, h1}} из site_pages — только читаемый контекст,

    без автоматического вердикта (см. докстринг модуля, разрыв 4). Первая
    строка на путь побеждает при коллизии (тот же принцип, что
    block3._load_site_pages).
    """
    if "site_pages" not in canonical or not _table_nonempty(canonical["site_pages"]):
        return {}
    con = common.open_duckdb(paths)
    try:
        rows = con.execute("SELECT url, title, h1 FROM site_pages").fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    for url, title, h1 in rows:
        path = _url_path(url)
        if path in out:
            continue
        out[path] = {"title": title, "h1": h1}
    return out


# ── S01 — брендовый и небрендовый органический трафик смешаны (легаси 5.2) ──
def _run_s01(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        by_brand = con.execute(
            "SELECT is_brand, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY is_brand"
        ).fetchall()
        by_source = con.execute(
            "SELECT source, is_brand, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY source, is_brand"
        ).fetchall()
    finally:
        con.close()

    brand_shows = brand_clicks = other_shows = other_clicks = 0
    for is_brand, shows, clicks in by_brand:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        if is_brand:
            brand_shows += shows
            brand_clicks += clicks
        else:
            other_shows += shows
            other_clicks += clicks
    total_shows = brand_shows + other_shows
    brand_share = (brand_shows / total_shows) if total_shows else None

    rows: list[dict[str, Any]] = [{
        "check_id": "S01",
        "finding": "brand_nonbrand_mix",
        "brand_shows": brand_shows,
        "non_brand_shows": other_shows,
        "total_shows": total_shows,
        "brand_share_of_shows": round(brand_share, 4) if brand_share is not None else None,
        "brand_clicks": brand_clicks,
        "non_brand_clicks": other_clicks,
        "min_shows_threshold": _S01_MIN_SHOWS_FOR_CHECK,
        "high_brand_share_threshold": _S01_HIGH_BRAND_SHARE,
        "organic_demand_mix_brand_heavy": bool(
            total_shows >= _S01_MIN_SHOWS_FOR_CHECK
            and brand_share is not None
            and brand_share >= _S01_HIGH_BRAND_SHARE
        ),
        "confidence": _cap("MED", confidence_cap),
    }]

    for source, is_brand, shows, clicks in by_source:
        rows.append({
            "check_id": "S01",
            "finding": "by_source",
            "source": source,
            "is_brand": bool(is_brand),
            "total_shows": int(shows or 0),
            "total_clicks": int(clicks or 0),
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "s01", rows, confidence_cap=confidence_cap)


# ── S02 — запросы на позициях 4-10 недополучают клики ───────────────────────
def _run_s02(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        groups = _aggregate_query_page(con, "WHERE NOT is_brand")
    finally:
        con.close()

    candidates = [
        g for g in groups
        if g["shows"] >= _MIN_SHOWS_FOR_OPPORTUNITY
        and g["position"] is not None
        and _S02_POSITION_MIN <= g["position"] <= _S02_POSITION_MAX
    ]

    rows: list[dict[str, Any]] = [{
        "check_id": "S02",
        "finding": "summary",
        "candidate_count": len(candidates),
        "total_shows": sum(g["shows"] for g in candidates),
        "total_clicks": sum(g["clicks"] for g in candidates),
        "position_band": [_S02_POSITION_MIN, _S02_POSITION_MAX],
        "min_shows_threshold": _MIN_SHOWS_FOR_OPPORTUNITY,
        "confidence": _cap("MED", confidence_cap),
    }]

    for g in sorted(candidates, key=lambda x: -x["shows"]):
        ctr = (g["clicks"] / g["shows"]) if g["shows"] else None
        rows.append({
            "check_id": "S02",
            "finding": "position_4_10_opportunity",
            "query": g["query"],
            "page": g["page"],
            "total_shows": g["shows"],
            "total_clicks": g["clicks"],
            "avg_position": round(g["position"], 2),
            "ctr": round(ctr, 4) if ctr is not None else None,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "s02", rows, confidence_cap=confidence_cap)


# ── S03 — запросы на позициях 11-20 (strike zone, легаси 5.1) ──────────────
def _run_s03(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        groups = _aggregate_query_page(con, "WHERE NOT is_brand")
    finally:
        con.close()

    candidates = [
        g for g in groups
        if g["shows"] >= _MIN_SHOWS_FOR_OPPORTUNITY
        and g["position"] is not None
        and _S03_POSITION_MIN <= g["position"] <= _S03_POSITION_MAX
    ]

    rows: list[dict[str, Any]] = [{
        "check_id": "S03",
        "finding": "summary",
        "candidate_count": len(candidates),
        "total_shows": sum(g["shows"] for g in candidates),
        "total_clicks": sum(g["clicks"] for g in candidates),
        "position_band": [_S03_POSITION_MIN, _S03_POSITION_MAX],
        "min_shows_threshold": _MIN_SHOWS_FOR_OPPORTUNITY,
        "confidence": _cap("MED", confidence_cap),
    }]

    for g in sorted(candidates, key=lambda x: -x["shows"]):
        ctr = (g["clicks"] / g["shows"]) if g["shows"] else None
        rows.append({
            "check_id": "S03",
            "finding": "strike_zone_11_20",
            "query": g["query"],
            "page": g["page"],
            "total_shows": g["shows"],
            "total_clicks": g["clicks"],
            "avg_position": round(g["position"], 2),
            "ctr": round(ctr, 4) if ctr is not None else None,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "s03", rows, confidence_cap=confidence_cap)


# ── S04 — CTR аномально низкий для текущей позиции (легаси 5.3) ────────────
def _run_s04(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        groups = _aggregate_query_page(con)
    finally:
        con.close()

    by_bucket: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for g in groups:
        if g["shows"] < _S04_MIN_SHOWS_FOR_ROW or g["position"] is None:
            continue
        bucket = _position_bucket(g["position"])
        if bucket is None:
            continue
        by_bucket.setdefault((bucket, g["is_brand"]), []).append(g)

    rows: list[dict[str, Any]] = []
    anomaly_count = 0
    for (bucket, is_brand), items in sorted(by_bucket.items(), key=lambda kv: kv[0]):
        if len(items) < _S04_MIN_QUERIES_FOR_MEDIAN:
            continue
        ctrs = [item["clicks"] / item["shows"] for item in items if item["shows"] > 0]
        median_ctr = _median(ctrs)
        if median_ctr is None or median_ctr <= 0:
            continue
        for item in items:
            ctr = item["clicks"] / item["shows"] if item["shows"] else None
            ratio = (ctr / median_ctr) if ctr is not None else None
            anomalous = ratio is not None and ratio <= _S04_CTR_LOW_RATIO
            if anomalous:
                anomaly_count += 1
            rows.append({
                "check_id": "S04",
                "finding": "ctr_vs_bucket_median",
                "query": item["query"],
                "page": item["page"],
                "position_bucket": bucket,
                "is_brand": item["is_brand"],
                "total_shows": item["shows"],
                "total_clicks": item["clicks"],
                "avg_position": round(item["position"], 2),
                "ctr": round(ctr, 4) if ctr is not None else None,
                "bucket_median_ctr": round(median_ctr, 4),
                "ctr_to_bucket_median_ratio": round(ratio, 3) if ratio is not None else None,
                "low_ctr_ratio_threshold": _S04_CTR_LOW_RATIO,
                "ctr_anomalously_low": bool(anomalous),
                "confidence": _cap("MED", confidence_cap),
            })

    rows.insert(0, {
        "check_id": "S04",
        "finding": "summary",
        "buckets_evaluated": len({(b, br) for (b, br), items in by_bucket.items()
                                   if len(items) >= _S04_MIN_QUERIES_FOR_MEDIAN}),
        "rows_evaluated": len(rows),
        "anomaly_count": anomaly_count,
        "min_shows_per_row_threshold": _S04_MIN_SHOWS_FOR_ROW,
        "min_queries_for_median_threshold": _S04_MIN_QUERIES_FOR_MEDIAN,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s04", rows, confidence_cap=confidence_cap)


# ── S05 — отдельные страницы теряют клики и позиции ─────────────────────────
def _run_s05(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        by_page_month = con.execute(
            "SELECT page, month, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY page, month"
        ).fetchall()
    finally:
        con.close()

    by_page: dict[str, dict[str, tuple[int, int]]] = {}
    for page, month, shows, clicks in by_page_month:
        by_page.setdefault(page, {})[month] = (int(shows or 0), int(clicks or 0))

    rows: list[dict[str, Any]] = []
    insufficient_history_count = 0
    declining_count = 0
    for page, months_map in sorted(by_page.items()):
        months = sorted(months_map)
        if len(months) < 2:
            insufficient_history_count += 1
            continue
        # len(months) >= 2 гарантировано проверкой выше -> mid всегда >= 1.
        mid = len(months) // 2
        early_months, late_months = months[:mid], months[mid:]
        early_shows = sum(months_map[m][0] for m in early_months)
        early_clicks = sum(months_map[m][1] for m in early_months)
        late_shows = sum(months_map[m][0] for m in late_months)
        late_clicks = sum(months_map[m][1] for m in late_months)
        if early_shows < _S05_MIN_SHOWS_FOR_TREND:
            continue
        click_ratio = (late_clicks / early_clicks) if early_clicks > 0 else None
        declining = click_ratio is not None and click_ratio <= _S05_DECLINE_CLICK_RATIO
        if declining:
            declining_count += 1
        rows.append({
            "check_id": "S05",
            "finding": "page_trend",
            "page": page,
            "months_available": months,
            "early_period_months": early_months,
            "late_period_months": late_months,
            "early_shows": early_shows,
            "early_clicks": early_clicks,
            "late_shows": late_shows,
            "late_clicks": late_clicks,
            "click_ratio_late_to_early": round(click_ratio, 3) if click_ratio is not None else None,
            "decline_ratio_threshold": _S05_DECLINE_CLICK_RATIO,
            "min_shows_threshold": _S05_MIN_SHOWS_FOR_TREND,
            "page_declining": bool(declining),
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S05",
        "finding": "summary",
        "pages_evaluated": len(rows),
        "pages_declining": declining_count,
        "pages_with_insufficient_month_history": insufficient_history_count,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s05", rows, confidence_cap=confidence_cap)


# ── S06 — сезонность vs падение/рост SEO (легаси 5.5) ───────────────────────
def _run_s06(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        by_month = con.execute(
            "SELECT month, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY month ORDER BY month"
        ).fetchall()
    finally:
        con.close()

    wordstat_available = "wordstat" in canonical and _table_nonempty(canonical["wordstat"])

    months = [(m, int(s or 0), int(c or 0)) for m, s, c in by_month]
    rows: list[dict[str, Any]] = [{
        "check_id": "S06",
        "finding": "monthly_shows_trend",
        "months": [{"month": m, "total_shows": s, "total_clicks": c} for m, s, c in months],
        "months_count": len(months),
        "min_months_for_trend": _S06_MIN_MONTHS_FOR_TREND,
        "confidence": _cap("MED", confidence_cap),
    }]

    if len(months) >= _S06_MIN_MONTHS_FOR_TREND:
        shows_series = [s for _, s, _ in months]
        med = _median(shows_series)
        for m, s, c in months:
            ratio = (s / med) if med else None
            is_spike = ratio is not None and ratio >= _S06_SPIKE_RATIO
            is_drop = ratio is not None and ratio <= _S06_DROP_RATIO
            if not (is_spike or is_drop):
                continue
            rows.append({
                "check_id": "S06",
                "finding": "monthly_shows_anomaly",
                "month": m,
                "total_shows": s,
                "total_clicks": c,
                "baseline_median_shows": round(med, 2) if med is not None else None,
                "ratio_to_baseline": round(ratio, 3) if ratio is not None else None,
                "anomaly_type": "spike" if is_spike else "drop",
                "spike_ratio_threshold": _S06_SPIKE_RATIO,
                "drop_ratio_threshold": _S06_DROP_RATIO,
                "confidence": _cap("MED", confidence_cap),
            })

    rows.append({
        "check_id": "S06",
        "finding": "seasonality_reconciliation",
        "wordstat_available": wordstat_available,
        "limitation": (
            "wordstat.parquet не строится в canonical-слое в текущем состоянии "
            "transform (src/extract/wordstat.py объявляет canonical_tables, но "
            "build_canonical.py эту таблицу не собирает — расширение схемы вне "
            "allowed_files этой задачи). Без сопоставления с Wordstat нельзя "
            "утверждать, что колебания показов вызваны сезонностью, а не "
            "реальной SEO-проблемой (каталог §11, «Что Claude не должен "
            "утверждать», п.9) — вердикт остаётся гипотезой."
        ) if not wordstat_available else (
            "wordstat доступен в canonical, но у extract/wordstat.py нет "
            "задокументированной схемы столбцов — сопоставление не "
            "реализовано в этой задаче, не выдумываем поля."
        ),
        "verdict": "cannot_determine_without_wordstat",
        "confidence": _cap("LOW", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s06", rows, confidence_cap=confidence_cap)


# ── S07 — коммерческий спрос без релевантной посадочной ─────────────────────
def _run_s07(canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """requires=[wordstat, seo_queries] — wordstat структурно недоступен (см.

    докстринг модуля, разрыв 1), поэтому проверка всегда пишет unavailable в
    текущем состоянии пайплайна, независимо от confidence_cap/runnable_ids
    (тот же прецедент, что A07/A16/A25 в block1.py).
    """
    if "wordstat" in canonical and _table_nonempty(canonical["wordstat"]):
        _write_unavailable(
            metrics_dir, "S07",
            "wordstat доступен в canonical, но у extract/wordstat.py нет "
            "задокументированной схемы столбцов (see docstring transform) — "
            "сопоставление спроса с картой страниц не реализовано в этой "
            "задаче, не выдумываем поля",
        )
        return
    _write_unavailable(
        metrics_dir, "S07",
        "wordstat.parquet не строится в canonical-слое (src/extract/wordstat.py "
        "объявляет canonical_tables=['wordstat'], но build_canonical.py не "
        "собирает эту таблицу — 'схема не задана', расширение вне allowed_files "
        "этой задачи) — сопоставить коммерческий спрос с картой страниц нечем",
    )


# ── S08 — страница не соответствует намерению запроса ───────────────────────
def _run_s08(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))
    con = common.open_duckdb(paths)
    try:
        overall = con.execute(
            "SELECT page, SUM(total_shows), SUM(total_clicks) FROM seo_queries GROUP BY page"
        ).fetchall()
        by_device = con.execute(
            "SELECT page, device, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "WHERE device != 'unknown' GROUP BY page, device"
        ).fetchall()
        organic_by_page = _organic_visits_by_page(con)
        organic_by_page_device = _organic_visits_by_page_device(con)
    finally:
        con.close()

    site_titles = _load_site_titles(canonical, paths)

    rows: list[dict[str, Any]] = []
    candidate_count = 0
    for page, shows, clicks in overall:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        if shows < _S08_MIN_SHOWS_FOR_CHECK:
            continue
        path = _url_path(page)
        organic_visits, engaged = organic_by_page.get(path, (0, 0))
        if organic_visits < _S08_MIN_ORGANIC_VISITS_FOR_CHECK:
            continue
        zero_engagement_share = 1 - (engaged / organic_visits)
        candidate = zero_engagement_share >= _S08_HIGH_ZERO_ENGAGEMENT_SHARE
        if candidate:
            candidate_count += 1
        context = site_titles.get(path, {})
        rows.append({
            "check_id": "S08",
            "finding": "page_intent_mismatch_overall",
            "page": page,
            "total_shows": shows,
            "total_clicks": clicks,
            "organic_visits": organic_visits,
            "organic_engaged_visits": engaged,
            "zero_engagement_share": round(zero_engagement_share, 4),
            "high_zero_engagement_threshold": _S08_HIGH_ZERO_ENGAGEMENT_SHARE,
            "intent_mismatch_candidate": bool(candidate),
            "intent_classification_available": False,
            "page_title": context.get("title"),
            "page_h1": context.get("h1"),
            "confidence": _cap(_sample_confidence(organic_visits, min_sample), confidence_cap),
        })

    for page, device, shows, clicks in by_device:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        if shows < _S08_MIN_SHOWS_FOR_CHECK:
            continue
        path = _url_path(page)
        organic_visits, engaged = organic_by_page_device.get((path, device), (0, 0))
        if organic_visits < _S08_MIN_ORGANIC_VISITS_FOR_CHECK:
            continue
        zero_engagement_share = 1 - (engaged / organic_visits)
        candidate = zero_engagement_share >= _S08_HIGH_ZERO_ENGAGEMENT_SHARE
        rows.append({
            "check_id": "S08",
            "finding": "page_intent_mismatch_by_device",
            "page": page,
            "device": device,
            "total_shows": shows,
            "total_clicks": clicks,
            "organic_visits": organic_visits,
            "organic_engaged_visits": engaged,
            "zero_engagement_share": round(zero_engagement_share, 4),
            "high_zero_engagement_threshold": _S08_HIGH_ZERO_ENGAGEMENT_SHARE,
            "intent_mismatch_candidate": bool(candidate),
            "confidence": _cap(_sample_confidence(organic_visits, min_sample), confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S08",
        "finding": "summary",
        "pages_evaluated": sum(1 for r in rows if r["finding"] == "page_intent_mismatch_overall"),
        "intent_mismatch_candidates": candidate_count,
        "min_shows_threshold": _S08_MIN_SHOWS_FOR_CHECK,
        "min_organic_visits_threshold": _S08_MIN_ORGANIC_VISITS_FOR_CHECK,
        "intent_classification_available": False,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s08", rows, confidence_cap=confidence_cap)


# ── S09 — несколько страниц конкурируют по одному кластеру ─────────────────
def _run_s09(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        overall = con.execute(
            "SELECT query, page, SUM(total_shows) FROM seo_queries GROUP BY query, page"
        ).fetchall()
        by_device = con.execute(
            "SELECT query, device, page, SUM(total_shows) FROM seo_queries "
            "WHERE device != 'unknown' GROUP BY query, device, page"
        ).fetchall()
    finally:
        con.close()

    pages_by_query: dict[str, dict[str, int]] = {}
    for query, page, shows in overall:
        path = _url_path(page)
        bucket = pages_by_query.setdefault(query, {})
        bucket[path] = bucket.get(path, 0) + int(shows or 0)

    rows: list[dict[str, Any]] = []
    competing_count = 0
    for query, page_shows in sorted(pages_by_query.items()):
        total_shows = sum(page_shows.values())
        if total_shows < _S09_MIN_SHOWS_FOR_COMPETITION or len(page_shows) < 2:
            continue
        competing_count += 1
        rows.append({
            "check_id": "S09",
            "finding": "query_page_overlap_overall",
            "query": query,
            "competing_page_count": len(page_shows),
            "total_shows": total_shows,
            "pages": [
                {"page": p, "total_shows": s}
                for p, s in sorted(page_shows.items(), key=lambda kv: -kv[1])
            ],
            "min_shows_threshold": _S09_MIN_SHOWS_FOR_COMPETITION,
            "confidence": _cap("MED", confidence_cap),
        })

    pages_by_query_device: dict[tuple[str, str], dict[str, int]] = {}
    for query, device, page, shows in by_device:
        path = _url_path(page)
        bucket = pages_by_query_device.setdefault((query, device), {})
        bucket[path] = bucket.get(path, 0) + int(shows or 0)

    for (query, device), page_shows in sorted(pages_by_query_device.items()):
        total_shows = sum(page_shows.values())
        if total_shows < _S09_MIN_SHOWS_FOR_COMPETITION or len(page_shows) < 2:
            continue
        rows.append({
            "check_id": "S09",
            "finding": "query_page_overlap_by_device",
            "query": query,
            "device": device,
            "competing_page_count": len(page_shows),
            "total_shows": total_shows,
            "pages": [
                {"page": p, "total_shows": s}
                for p, s in sorted(page_shows.items(), key=lambda kv: -kv[1])
            ],
            "min_shows_threshold": _S09_MIN_SHOWS_FOR_COMPETITION,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S09",
        "finding": "summary",
        "queries_with_competing_pages": competing_count,
        "min_shows_threshold": _S09_MIN_SHOWS_FOR_COMPETITION,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s09", rows, confidence_cap=confidence_cap)


# ── S10 — по запросу ранжируется не та страница ─────────────────────────────
def _wrong_page_candidate(
    query: str,
    page_shows: dict[str, int],
    page_position: dict[str, float],
    engagement: dict[str, tuple[int, int]],
) -> dict[str, Any] | None:
    """Сравнить "фактически лучше ранжируемую" страницу с "лучше конвертирующей"

    среди кандидатов на один запрос (см. докстринг модуля, разрыв 3 — нет
    конфигурационного "целевого URL", вовлечённость используется как прокси).
    """
    ranked = [(p, page_position[p]) for p in page_shows if p in page_position]
    if not ranked:
        return None
    ranking_leader = min(ranked, key=lambda pp: pp[1])[0]

    comparable = [
        (p, engagement[p][1] / engagement[p][0])
        for p in page_shows
        if p in engagement and engagement[p][0] >= _S10_MIN_ORGANIC_VISITS_FOR_COMPARISON
    ]
    if not comparable:
        return None
    conversion_leader, conversion_leader_rate = max(comparable, key=lambda pr: pr[1])

    if conversion_leader == ranking_leader:
        return None
    ranking_leader_rate = None
    if ranking_leader in engagement and engagement[ranking_leader][0] >= _S10_MIN_ORGANIC_VISITS_FOR_COMPARISON:
        organic_visits, engaged = engagement[ranking_leader]
        ranking_leader_rate = engaged / organic_visits
    if ranking_leader_rate is not None and (conversion_leader_rate - ranking_leader_rate) < _S10_ENGAGEMENT_GAP_PP:
        return None

    return {
        "ranking_leader_page": ranking_leader,
        "ranking_leader_position": round(page_position[ranking_leader], 2),
        "ranking_leader_engagement_rate": (
            round(ranking_leader_rate, 4) if ranking_leader_rate is not None else None
        ),
        "better_converting_page": conversion_leader,
        "better_converting_page_engagement_rate": round(conversion_leader_rate, 4),
        "engagement_gap_pp_threshold": _S10_ENGAGEMENT_GAP_PP,
    }


def _run_s10(paths: Any, has_visits: bool, confidence_cap: str, metrics_dir: Path) -> None:
    """visits — optional по methodology.yaml (S10.optional=[visits]): без него

    сравнение "ранжируется/конвертирует лучше" невозможно (нет вовлечённости),
    но проверка остаётся runnable по одному seo_queries — просто не находит
    кандидатов (см. summary.visits_available=false), а не падает и не пишет
    unavailable целиком (visits — optional, а не requires).
    """
    con = common.open_duckdb(paths)
    try:
        overall = con.execute(
            "SELECT query, page, "
            "SUM(total_shows) AS shows, "
            "SUM(avg_show_position * total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS pos_w, "
            "SUM(total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS shows_pos "
            "FROM seo_queries GROUP BY query, page"
        ).fetchall()
        organic_by_page = _organic_visits_by_page(con) if has_visits else {}
    finally:
        con.close()

    shows_by_query: dict[str, dict[str, int]] = {}
    position_by_query: dict[str, dict[str, float]] = {}
    for query, page, shows, pos_w, shows_pos in overall:
        path = _url_path(page)
        shows = int(shows or 0)
        shows_bucket = shows_by_query.setdefault(query, {})
        shows_bucket[path] = shows_bucket.get(path, 0) + shows
        if pos_w is not None and shows_pos:
            position_by_query.setdefault(query, {})[path] = pos_w / shows_pos

    rows: list[dict[str, Any]] = []
    candidate_count = 0
    for query, page_shows in sorted(shows_by_query.items()):
        total_shows = sum(page_shows.values())
        if total_shows < _S09_MIN_SHOWS_FOR_COMPETITION or len(page_shows) < 2:
            continue
        candidate = _wrong_page_candidate(
            query, page_shows, position_by_query.get(query, {}), organic_by_page,
        )
        if candidate is None:
            continue
        candidate_count += 1
        rows.append({
            "check_id": "S10",
            "finding": "wrong_page_ranking_candidate",
            "query": query,
            "total_shows": total_shows,
            "target_url_from_config": False,
            **candidate,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S10",
        "finding": "summary",
        "wrong_page_ranking_candidates": candidate_count,
        "min_shows_threshold": _S09_MIN_SHOWS_FOR_COMPETITION,
        "min_organic_visits_threshold": _S10_MIN_ORGANIC_VISITS_FOR_COMPARISON,
        "target_url_from_config": False,
        "visits_available": has_visits,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s10", rows, confidence_cap=confidence_cap)


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить S01-S10 из числа доступных; вернуть имена записанных артефактов.

    S11-S27 не реализуются этой задачей (см. докстринг модуля).
    """
    canonical = common.load_canonical(paths)
    caps = _confidence_caps(paths)
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[str] = []
    has_seo = "seo_queries" in canonical and _table_nonempty(canonical["seo_queries"])
    has_visits = "visits" in canonical and _table_nonempty(canonical["visits"])

    if "S01" in runnable_ids and has_seo:
        _run_s01(paths, caps.get("S01", "HIGH"), metrics_dir)
        artifacts.append("s01")

    if "S02" in runnable_ids and has_seo:
        _run_s02(paths, caps.get("S02", "HIGH"), metrics_dir)
        artifacts.append("s02")

    if "S03" in runnable_ids and has_seo:
        _run_s03(paths, caps.get("S03", "HIGH"), metrics_dir)
        artifacts.append("s03")

    if "S04" in runnable_ids and has_seo:
        _run_s04(paths, caps.get("S04", "HIGH"), metrics_dir)
        artifacts.append("s04")

    if "S05" in runnable_ids and has_seo:
        _run_s05(paths, caps.get("S05", "HIGH"), metrics_dir)
        artifacts.append("s05")

    if "S06" in runnable_ids and has_seo:
        _run_s06(paths, canonical, caps.get("S06", "HIGH"), metrics_dir)
        artifacts.append("s06")

    if "S07" in runnable_ids and has_seo:
        _run_s07(canonical, caps.get("S07", "HIGH"), metrics_dir)
        artifacts.append("s07")

    if "S08" in runnable_ids and has_seo and has_visits:
        _run_s08(paths, defaults, canonical, caps.get("S08", "HIGH"), metrics_dir)
        artifacts.append("s08")

    if "S09" in runnable_ids and has_seo:
        _run_s09(paths, caps.get("S09", "HIGH"), metrics_dir)
        artifacts.append("s09")

    if "S10" in runnable_ids and has_seo:
        _run_s10(paths, has_visits, caps.get("S10", "HIGH"), metrics_dir)
        artifacts.append("s10")

    return artifacts
