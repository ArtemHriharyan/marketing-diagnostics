"""Блок 3 — CRO, сайт и воронка до обращения (каталог v2 §8, C01–C25).

Задача 5G реализовала C01–C12 (скорость/техника, форма open->submit,
качественные причины отвала первого порядка). Задача 5H (текущая) добавляет
C13–C25 (поздние условия, доверие, CTA, навигация/поиск, попапы,
browser/OS-специфика, корзина/бронирование, наличие, контент без пути к
деньгам) — C01–C12 при этом не переписываются.

Проверки (config/methodology.yaml, catalog-proveryaemyh-marketingovyh-ugroz-v2.md §8):
    C01  медленная загрузка на мобильных                    [crux] (+visits)
    C02  отдельные шаблоны значительно медленнее среднего     [crux] (+visits)
    C03  JS-ошибки ломают форму/калькулятор/фильтр/корзину    [site_crawl] (+webvisor_findings)
    C04  реклама/поиск ведут на 404/5xx/недоступные страницы  [visits] (+site_crawl)
    C05  лишние редиректы и цепочки переходов                 [visits] (+site_crawl)
    C06  большой отвал между открытием и отправкой формы      [visits]
    C07  форма содержит лишние обязательные поля              [visits] (+webvisor_findings)
    C08  маски/валидация/CAPTCHA блокируют отправку           [site_crawl] (+webvisor_findings)
    C09  мобильные элементы неудобны                          [visits] (+webvisor_findings)
    C10  нет понятного подтверждения успешной отправки        [visits] (+webvisor_findings)
    C11  submit фиксируется, но данные фактически не доставлены [site_crawl]
    C12  на первом экране непонятно, что предлагает компания  [visits] (+webvisor_findings)
    C13  цена/условия/следующий шаг раскрываются поздно       [visits] (+webvisor_findings)
    C14  недостаточно доверия (гарантии/реквизиты/отзывы)     [site_crawl] (+webvisor_findings)
    C15  основной CTA незаметен/неоднозначен                  [visits] (+site_crawl)
    C16  слишком много конкурирующих действий на странице     [visits]
    C17  нет альтернативы основной форме (звонок/мессенджер)  [site_crawl]
    C18  навигация/категории/фильтры/поиск не помогают        [visits]
    C19  внутренний поиск часто даёт ноль результатов         [visits]
    C20  попап/чат/cookie-баннер перекрывает контент и CTA    [visits] (+webvisor_findings)
    C21  проблема в конкретном браузере/ОС/разрешении         [visits]
    C22  корзина/запись/бронирование теряют на конкретном шаге [visits]
    C23  платёжный/бронирующий модуль выдаёт ошибки           [site_crawl] (+visits)
    C24  реклама/SEO ведут на отсутствующий товар/услугу      [visits] (+site_crawl)
    C25  информационные страницы не ведут к коммерции          [visits]

Контракт:
    Читает   — data/canonical/{visits,site_pages}.parquet, data/raw/crux/crux.json
               (НАПРЯМУЮ, не через canonical — у CrUX нет канонической таблицы,
               см. src/extract/crux.py: "canonical_tables: []"), inputs/{manual_cwv,
               manual_form_tests,webvisor_findings,client_answers}.yaml, data/metrics/
               d01.json (только C10 — сверка с переотработкой цели form_submit, тот же
               прецедент, что A03 в block1.py читает d01/d03), data/metrics/
               degradation_report.json (confidence_cap на проверку).
    Пишет    — data/metrics/{c01..c25}.csv/.json. БЕЗ LLM.

Не реализует: A01/A03 уже считают легаси-метрику "платный трафик vs весь сайт"
(1.2) в block1.py — здесь она НЕ пересчитывается (см. CLAUDE.md принцип 2 и
docstring block1.py — один источник правды на одну цифру).

── Известные структурные разрывы (НЕ устраняются здесь — вне allowed_files) ──

1. CrUX (C01/C02) не даёт канонической таблицы вовсе (`crux.py:
   CANONICAL_TABLES = []`) — `available_tables_from_manifest` (degradation.py)
   собирает `available` только из `canonical_tables` записей манифеста, значит
   requires=[crux] НИКОГДА не станет "runnable" через автоматический механизм
   деградации в текущем состоянии extract-слоя. Тот же класс разрыва, что
   задокументирован для D02/D03/goals в задаче 4I-goals-canonical
   (docs/implementation_status.md) — там extract тоже не регистрирует нужную
   таблицу в CANONICAL_TABLES, и это сознательно не чинится вне allowed_files
   своей задачи. Здесь аналогично: src/extract/crux.py, src/pipeline/
   degradation.py, config/methodology.yaml не входят в allowed_files задачи 5G.
   Тесты (как и в test_block1.py/test_block2.py) конструируют `runnable_ids`
   явным множеством, не полагаясь на реальную деградацию.

2. `site_crawl.py: CANONICAL_TABLES = ["pages"]`, а фактическое имя канонической
   таблицы (SCHEMAS, build_canonical.py) — `site_pages`. Тот же класс разрыва:
   requires=[site_crawl] (C03/C08/C11) не станет "runnable" автоматически, пока
   имена не будут выровнены отдельной задачей с extract в allowed_files. Здесь
   используется `common.load_canonical()`, который читает файлы `data/canonical/
   *.parquet` напрямую с диска (а не манифест) — поэтому "site_pages" в
   `canonical` появляется корректно, как только transform реально построил
   таблицу, независимо от разрыва (1)/(2) в деградации.

3. `inputs/manual_form_tests.yaml` НЕ упомянут в requires/optional НИ ОДНОЙ
   проверки C01–C12 в config/methodology.yaml — при этом marketing-diagnostics-
   methodology-v2.md §6 прямо называет C03, C08–C11 "требуют ручного
   тестирования... заносить в inputs/manual_form_tests.yaml, не пытаться
   закрыть скриптом". Расхождение между прозой (методология v2, приоритет c)
   и машинным реестром (methodology.yaml, приоритет d, CLAUDE.md п.5) не
   устраняется — оба файла вне allowed_files. Разрешение здесь: requires/
   optional из methodology.yaml управляют ТОЛЬКО диспетчеризацией (какой
   check_id должен быть runnable), а не тем, какие файлы блок вправе прочитать
   — блок читает inputs/manual_form_tests.yaml напрямую для C03/C08/C11 (и как
   необязательное обогащение C10), тот же приём, что T06/T09 в block2.py читают
   inputs/client_answers.yaml напрямую. Задача 5H (C13–C25) расширяет тот же
   приём дальше: inputs/manual_form_tests.yaml (его собственный докстринг прямо
   говорит "Используется в проверках C01–C25") читается напрямую также для
   C14/C17/C23 (полностью ручные, по аналогии с C03/C08/C11) и как fallback
   для C15/C16/C18/C25 (см. разрыв 9); inputs/client_answers.yaml читается
   напрямую для C13/C24 — ни один из этих ID не несёт client_answers в
   requires/optional методологии, но тот же принцип "requires управляет только
   диспетчеризацией" применяется к нему так же, как уже применялся к
   manual_form_tests.yaml здесь и к client_answers.yaml в block2.py.

4. C09/C10 в прозе методологии v2 §6 попадают в один список с C03/C08/C11
   ("не автоматизируются в принципе"), но их type_default в methodology.yaml —
   "A+B" (не чистое "B"), а requires — [visits] (не site_crawl). Реализовано
   по машинному реестру: автоматическая часть (device-разрез конверсии для
   C09, сигнал повторного срабатывания цели для C10) + необязательное ручное
   обогащение (inputs/webvisor_findings.yaml, inputs/manual_form_tests.yaml),
   без гейта на ручной источник.

5. Каталог описывает C06 как воронку "open -> start -> submit". Промежуточный
   признак "начал заполнять форму" НЕ существует в goal_flags()/config.goals
   (src/transform/build_canonical.py: только form_open/form_submit/call_click/
   messenger_click) — extract/transform вне allowed_files этой задачи. Считается
   двухступенчатая воронка open->submit (соответствует легаси 1.1 в точности),
   что явно помечено полем stage_start_available=false в артефакте.

6. C05: site_pages не хранит per-редиректный query string, только конечный
   final_url — сохранность UTM-параметров через цепочку редиректов проверить
   нельзя (только факт наличия и длину цепочки). Помечено полем
   utm_preservation_verifiable=false.

── Структурные разрывы задачи 5H (C13–C25) — тот же класс, что 1–6 выше ──────

7. Внутренний поиск по сайту (C18, C19) НЕ выгружается ни одним модулем
   src/extract/ и не имеет канонической таблицы вовсе (grep по репозиторию —
   ни "site_search", ни "internal_search", ни отчёт Метрики "Поиск по сайту"
   нигде не упоминаются). C19 (type_default="A", requires=[visits],
   optional=[]) объявлен методологией как ПОЛНОСТЬЮ автоматический — при этом
   реальных данных для него нет и взяться неоткуда без отдельной задачи с
   extract/transform в allowed_files. Решение здесь: C19 всегда пишется как
   unavailable с явной причиной, а НЕ имитируется по visits/site_pages
   (CLAUDE.md, протокол микрозадач п.5: "не симулировать по косвенным
   данным"). C18 (A+B, requires=[visits]) тем же разрывом лишён авто-части —
   у него есть валидный B-фолбэк через inputs/manual_form_tests.yaml (разрыв 9).

8. Пошаговая воронка корзины/записи/бронирования (C22, type_default="A",
   requires=[visits]) невосстановима: goal_flags()/config.goals
   (src/transform/build_canonical.py) знает только 4 плоские группы целей
   (form_open/form_submit/call_click/messenger_click) без промежуточных шагов
   — тот же класс ограничения, что уже задокументирован для C06 (разрыв 5
   выше), только здесь степень серьёзнее: у C06 хотя бы есть двухступенчатая
   open->submit воронка как приближение легаси 1.1, а для корзины/бронирования
   в config.goals нет вообще отдельной группы "cart_step"/"booking_step" —
   приближать нечем. C22 всегда пишется как unavailable.

9. CTA-элементы, вторичные (конкурирующие) элементы страницы, попапы/чат/
   cookie-баннеры, признаки наличия товара/услуги и классификация страниц на
   контентные/коммерческие — ни одна из этих сущностей не хранится в
   канонической схеме (`site_pages`: url/http_status/redirect_chain/final_url/
   canonical_url/robots_directive/in_sitemap/title/description/h1/crawled_at/
   js_content_diff — ни поля разметки кнопок, ни категории страницы; `visits`:
   только 4 группы целей, browser/os/device/screen, без кликов по конкретным
   элементам). Затрагивает C15, C16, C18, C20, C25 (все A+B/A, requires=
   [visits], без содержательной авто-части в текущей схеме). Решение здесь —
   не имитировать: единственный источник вывода для C15/C16/C18/C25 —
   inputs/manual_form_tests.yaml (общий ручной аудит, см. разрыв 3), с явным
   `automatic_component: "unavailable"` и `limitation` в каждой строке, чтобы
   A+B-проверка без авто-сигнала не выглядела "забытой", а честно называла
   структурную причину. C20 использует inputs/webvisor_findings.yaml (его
   optional по methodology.yaml) тем же принципом; unavailable-причина C20
   отдельно указывает аналитику свериться с device-конверсией C09, не
   пересчитывая те же числа под другим check_id (см. C09/C21 ниже, разрыв 10).

10. C21 ("проблема в конкретном браузере/ОС/разрешении") — единственная
    проверка среди C13–C25 с содержательной авто-частью: `visits` хранит
    browser/os/screen_resolution (backfill-патч, см. _BACKFILL_COLUMNS) и
    form_submit, поэтому конверсия по сегменту сравнима с базовым (самым
    массовым) значением того же измерения. Дименшн device сознательно
    ИСКЛЮЧЁН из C21 (хотя каталог перечисляет device в списке измерений) —
    он уже полностью посчитан в C09 под собственной причинной рамкой
    ("мобильные элементы неудобны"); пересчитывать те же числа под C21 с
    другой рамкой ("технический баг в конкретном сегменте") нарушило бы
    "один источник правды на одну цифру" (CLAUDE.md принцип 2, тот же
    прецедент, что A01/A03 vs C06 для легаси 1.2). Аналитик обязан
    сопоставить оба вывода вручную на этапе analyze, а не читать их как два
    независимых подтверждения.

── Ручные наблюдения — контракт честности (CLAUDE.md, прямое требование промта) ──
Патерны/выводы из inputs/manual_cwv.yaml, inputs/manual_form_tests.yaml,
inputs/webvisor_findings.yaml переносятся в метрики КАК ЕСТЬ, без переклассификации
и без вычисления автоматического булевого вердикта "подтверждено". confidence
паттернов всегда прижимается к MED (или ниже — не выше); выводы (conclusions)
могут нести собственный confidence, но не выше MED. Это НЕ "автоматически
доказанный факт" — окончательное суждение о применимости к конкретному ID
угрозы остаётся за аналитиком/analyze (см. CLAUDE.md, «Уверенность находок»).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow.parquet as pq
from scipy import stats

from . import common
from ..pipeline import degradation as degradation_mod

# ── Официальные пороги Google Core Web Vitals (web.dev/vitals) — общеизвестный
# отраслевой стандарт, НЕ изобретён для этой задачи. INP заменил FID в марте
# 2024 как третья метрика "Core" триады; FCP — дополнительная метрика оттуда
# же. Значения в мс, кроме CLS (безразмерная величина, отдельная шкала).
_CWV_THRESHOLDS_MS: dict[str, tuple[float, float]] = {
    "largest_contentful_paint": (2500, 4000),
    "interaction_to_next_paint": (200, 500),
    "first_contentful_paint": (1800, 3000),
}
_CWV_CLS_THRESHOLDS: tuple[float, float] = (0.1, 0.25)

# C02: во сколько раз p75 ключевого URL должен быть хуже origin-агрегата,
# чтобы шаблон считался "значительно медленнее" (каталог не даёт числа —
# эвристика, тот же принцип, что и коэффициенты-outlier'ы в block1.py).
_C02_TEMPLATE_SLOWER_RATIO = 1.3

# C04: с какого HTTP-статуса посадочная считается "недоступной".
_C04_BAD_STATUS_MIN = 400

# C05: один хоп (напр. http->https, без-www->www) — норма, не "лишний";
# цепочкой ("лишние редиректы и цепочки переходов", множественное число в
# формулировке угрозы) считается от двух хопов.
_C05_MIN_CHAIN_HOPS_FOR_FINDING = 2

# C09: минимум визитов в сегменте устройства, чтобы вообще сравнивать
# конверсию (тот же принцип материальности, что T01/T08 в block2.py).
_C09_MIN_VISITS_FOR_COMPARISON = 30

# C12: минимум визитов на посадочную, чтобы судить о "непонятном оффере";
# доля визитов без единого целевого действия, начиная с которой первый экран
# считается кандидатом на непонятный оффер (эвристика, каталог числа не даёт).
_C12_MIN_VISITS_FOR_CHECK = 30
_C12_HIGH_ZERO_ENGAGEMENT_SHARE = 0.85

# C21: измерения технической сегментации (device сознательно исключён — см.
# докстринг модуля, разрыв 10, число уже полностью принадлежит C09) и минимум
# визитов в сегменте/базовом значении, чтобы вообще сравнивать конверсию (тот
# же принцип материальности, что _C09_MIN_VISITS_FOR_COMPARISON).
_C21_SEGMENT_DIMENSIONS: tuple[str, ...] = ("browser", "os", "screen_resolution")
_C21_MIN_VISITS_FOR_COMPARISON = 30


# ── Общие хелперы (дублируют паттерн block0/1/2.py — блоки compute не делят
# приватные хелперы через common.py, см. CLAUDE.md принцип 2) ───────────────
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
    """HIGH при визит-уровневой выборке >= порога, иначе MED."""
    return "HIGH" if sample_size >= min_sample_visits else "MED"


def _write_unavailable(metrics_dir: Path, check_id: str, reason: str) -> None:
    """Явная запись «проверка недоступна» вместо молчаливого пропуска."""
    common.write_metric_artifact(
        metrics_dir,
        check_id.lower(),
        [{"check_id": check_id, "status": "unavailable", "reason": reason}],
    )


def _two_proportion_p_value(count1: int, n1: int, count2: int, n2: int) -> float | None:
    """Двусторонний z-тест разницы двух долей (значимость сравнивается снаружи)."""
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = count1 / n1, count2 / n2
    p_pool = (count1 + count2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p1 - p2) / se
    return float(2 * stats.norm.sf(abs(z)))


def _rate_metric(name: str, value: float | None) -> str | None:
    """good | needs_improvement | poor по официальным порогам CWV, или None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if name == "cumulative_layout_shift":
        good, needs = _CWV_CLS_THRESHOLDS
    else:
        bounds = _CWV_THRESHOLDS_MS.get(name)
        if bounds is None:
            return None
        good, needs = bounds
    if value <= good:
        return "good"
    if value <= needs:
        return "needs_improvement"
    return "poor"


# ── Ручные inputs/*.yaml — честный транспорт, без переклассификации ─────────
def _yaml_populated(doc: dict[str, Any] | None, meta_keys: tuple[str, ...]) -> bool:
    """Хотя бы один meta.<key> непуст -> аналитик реально заполнял файл (не шаблон)."""
    if not doc:
        return False
    meta = doc.get("meta") or {}
    for key in meta_keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            return True
    return False


def _manual_confidence(raw_conf: Any, cap_level: str) -> str:
    conf = raw_conf if raw_conf in ("HIGH", "MED", "LOW") else cap_level
    return degradation_mod.min_confidence(conf, cap_level)


def _manual_pattern_rows(
    check_id: str, doc: dict[str, Any] | None, confidence_cap: str, cap_level: str,
) -> list[dict[str, Any]]:
    """patterns ручного inputs/*.yaml как есть — без вывода "подтверждено"."""
    rows: list[dict[str, Any]] = []
    for pattern in (doc or {}).get("patterns") or []:
        if not isinstance(pattern, dict):
            continue
        row = {"check_id": check_id, "finding": "manual_pattern", **pattern}
        row["confidence"] = _cap(cap_level, confidence_cap)
        rows.append(row)
    return rows


def _manual_conclusions_rows(
    check_id: str, doc: dict[str, Any] | None, confidence_cap: str, cap_level: str,
) -> list[dict[str, Any]]:
    """conclusions ручного inputs/*.yaml — собственный confidence, не выше cap_level."""
    rows: list[dict[str, Any]] = []
    for conclusion in (doc or {}).get("conclusions") or []:
        if not isinstance(conclusion, dict):
            continue
        row = {"check_id": check_id, "finding": "manual_conclusion", **conclusion}
        row["confidence"] = _cap(_manual_confidence(conclusion.get("confidence"), cap_level), confidence_cap)
        rows.append(row)
    return rows


# ── CrUX (C01/C02) — читаем data/raw/crux/crux.json НАПРЯМУЮ (нет canonical) ─
def _read_crux_raw(paths: Any) -> dict[str, Any] | None:
    path = Path(paths.raw) / "crux" / "crux.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _mobile_visit_context(canonical: dict[str, Path], paths: Any) -> dict[str, Any] | None:
    """Контекст (не гейт): доля мобильных визитов и их form_submit_rate."""
    if "visits" not in canonical or not _table_nonempty(canonical["visits"]):
        return None
    con = common.open_duckdb(paths)
    try:
        total, mobile, mobile_submit, total_submit = con.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE device = 'mobile'), "
            "COUNT(*) FILTER (WHERE device = 'mobile' AND form_submit), "
            "COUNT(*) FILTER (WHERE form_submit) FROM visits"
        ).fetchone()
    finally:
        con.close()
    total = int(total or 0)
    mobile = int(mobile or 0)
    if total == 0:
        return None
    return {
        "total_visits": total,
        "mobile_visit_share": round(mobile / total, 4),
        "mobile_form_submit_rate": round((mobile_submit or 0) / mobile, 4) if mobile else None,
        "overall_form_submit_rate": round((total_submit or 0) / total, 4),
    }


# ── C01 — медленная загрузка на мобильных ────────────────────────────────────
def _run_c01(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    manual_cap_enabled = bool(defaults.get("crux_min_field_data", True))
    raw = _read_crux_raw(paths)
    context = _mobile_visit_context(canonical, paths)

    if raw and raw.get("cwv_field_data_available"):
        rows: list[dict[str, Any]] = []
        for record in raw.get("records") or []:
            if not record.get("field_data_available"):
                continue
            p75 = record.get("p75") or {}
            ratings = {f"{name}_rating": _rate_metric(name, value) for name, value in p75.items()}
            rows.append({
                "check_id": "C01",
                "finding": "field_cwv",
                "target_type": record.get("target_type"),
                "target": record.get("target"),
                **p75,
                **ratings,
                "any_metric_poor": any(v == "poor" for v in ratings.values()),
                "device_specific": False,
                "device_specific_note": (
                    "CrUX-запрос не фильтрует по formFactor (см. src/extract/crux.py) "
                    "— p75 агрегирован по всем устройствам, не только мобильным."
                ),
                "source": "crux_field",
                "confidence": _cap("MED", confidence_cap),
            })
        if context:
            rows.append({
                "check_id": "C01", "finding": "mobile_visit_context", **context,
                "confidence": _cap("MED", confidence_cap),
            })
        common.write_metric_artifact(metrics_dir, "c01", rows, confidence_cap=confidence_cap)
        return

    inputs = common.load_inputs(paths)
    manual = inputs.get("manual_cwv")
    if _yaml_populated(manual, ("tested_at",)):
        cap_level = "MED" if manual_cap_enabled else "LOW"
        rows = []
        for pattern in (manual or {}).get("patterns") or []:
            if not isinstance(pattern, dict):
                continue
            ratings = {
                "lcp_rating": _rate_metric("largest_contentful_paint", pattern.get("lcp_ms")),
                "cls_rating": _rate_metric("cumulative_layout_shift", pattern.get("cls")),
                "inp_rating": _rate_metric("interaction_to_next_paint", pattern.get("inp_ms")),
            }
            rows.append({
                "check_id": "C01", "finding": "manual_lab_cwv",
                "device": (manual.get("meta") or {}).get("device"),
                **pattern, **ratings,
                "field_data_available": False,
                "source": "manual_lab",
                "confidence": _cap(cap_level, confidence_cap),
            })
        rows.extend(_manual_conclusions_rows("C01", manual, confidence_cap, cap_level))
        if context:
            rows.append({
                "check_id": "C01", "finding": "mobile_visit_context", **context,
                "confidence": _cap("MED", confidence_cap),
            })
        common.write_metric_artifact(metrics_dir, "c01", rows, confidence_cap=confidence_cap)
        return

    _write_unavailable(
        metrics_dir, "C01",
        "нет ни полевых данных CrUX (data/raw/crux/crux.json отсутствует или "
        "cwv_field_data_available=false), ни ручного лабораторного замера "
        "(inputs/manual_cwv.yaml не заполнен — meta.tested_at пуст)",
    )


# ── C02 — отдельные шаблоны значительно медленнее среднего ──────────────────
def _run_c02(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    manual_cap_enabled = bool(defaults.get("crux_min_field_data", True))
    raw = _read_crux_raw(paths)

    if raw and raw.get("cwv_field_data_available"):
        records = [r for r in (raw.get("records") or []) if r.get("field_data_available")]
        origin_record = next((r for r in records if r.get("target_type") == "origin"), None)
        url_records = [r for r in records if r.get("target_type") == "url"]

        if origin_record is None or not url_records:
            common.write_metric_artifact(metrics_dir, "c02", [], confidence_cap=confidence_cap)
            return

        origin_p75 = origin_record.get("p75") or {}
        rows: list[dict[str, Any]] = []
        for record in url_records:
            p75 = record.get("p75") or {}
            comparisons: dict[str, Any] = {}
            slower_any = False
            for name, value in p75.items():
                baseline = origin_p75.get(name)
                if baseline is None or baseline <= 0:
                    continue
                ratio = value / baseline
                comparisons[f"{name}_ratio_to_origin"] = round(ratio, 3)
                if ratio >= _C02_TEMPLATE_SLOWER_RATIO:
                    slower_any = True
            rows.append({
                "check_id": "C02",
                "finding": "template_vs_origin",
                "target": record.get("target"),
                **p75,
                **comparisons,
                "origin_p75": origin_p75,
                "slower_ratio_threshold": _C02_TEMPLATE_SLOWER_RATIO,
                "template_significantly_slower": slower_any,
                "source": "crux_field",
                "confidence": _cap("MED", confidence_cap),
            })
        common.write_metric_artifact(metrics_dir, "c02", rows, confidence_cap=confidence_cap)
        return

    inputs = common.load_inputs(paths)
    manual = inputs.get("manual_cwv")
    if _yaml_populated(manual, ("tested_at",)):
        cap_level = "MED" if manual_cap_enabled else "LOW"
        rows = []
        for pattern in (manual or {}).get("patterns") or []:
            if not isinstance(pattern, dict):
                continue
            ratings = {
                "lcp_rating": _rate_metric("largest_contentful_paint", pattern.get("lcp_ms")),
                "cls_rating": _rate_metric("cumulative_layout_shift", pattern.get("cls")),
                "inp_rating": _rate_metric("interaction_to_next_paint", pattern.get("inp_ms")),
            }
            rows.append({
                "check_id": "C02", "finding": "manual_lab_cwv_by_url",
                **pattern, **ratings,
                "source": "manual_lab",
                "confidence": _cap(cap_level, confidence_cap),
            })
        rows.extend(_manual_conclusions_rows("C02", manual, confidence_cap, cap_level))
        common.write_metric_artifact(metrics_dir, "c02", rows, confidence_cap=confidence_cap)
        return

    _write_unavailable(
        metrics_dir, "C02",
        "нет ни полевых данных CrUX по нескольким URL (data/raw/crux/crux.json "
        "отсутствует, cwv_field_data_available=false или нет проверенных key_urls), "
        "ни ручного лабораторного замера (inputs/manual_cwv.yaml не заполнен)",
    )


# ── C03/C08/C11 — полностью ручные проверки (каталог Источник=B) ───────────
def _run_manual_only_check(
    check_id: str, paths: Any, confidence_cap: str, metrics_dir: Path,
) -> None:
    """methodology v2 §6: "не автоматизируются в принципе — заносить в

    inputs/manual_form_tests.yaml, не пытаться закрыть скриптом". site_pages
    (site_crawl) — инфраструктурная предпосылка (аналитик тестировал формы в
    рамках того же обхода сайта), не источник самих находок C03/C08/C11 (см.
    докстринг модуля, разрыв 3).
    """
    inputs = common.load_inputs(paths)
    doc = inputs.get("manual_form_tests")
    if not _yaml_populated(doc, ("tested_at",)):
        _write_unavailable(
            metrics_dir, check_id,
            "inputs/manual_form_tests.yaml не заполнен (meta.tested_at пуст) — "
            "проверка не автоматизируется, требует ручного тестирования форм",
        )
        return

    rows = _manual_pattern_rows(check_id, doc, confidence_cap, "MED")
    rows.extend(_manual_conclusions_rows(check_id, doc, confidence_cap, "MED"))
    common.write_metric_artifact(metrics_dir, check_id.lower(), rows, confidence_cap=confidence_cap)


# ── site_pages — путь-нормализованный индекс (для C04/C05) ─────────────────
def _url_path(url: str | None) -> str:
    """Тот же принцип нормализации, что normalize_entry_page (build_canonical.py):

    без домена/query/фрагмента, нижний регистр, без хвостового slash (кроме "/").
    """
    path = urlsplit(url or "").path or "/"
    path = path.lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _load_site_pages(canonical: dict[str, Path], paths: Any) -> dict[str, dict[str, Any]]:
    """{нормализованный_путь: {http_status, redirect_hops, final_url}} из site_pages.

    Первая строка на путь побеждает при коллизии (напр. http/https после
    normalize_url остаются разными полными url, но одним путём) — тот же
    принцип "первая строка побеждает", что dedupe_site_pages использует при
    дедупе по полному url.
    """
    if "site_pages" not in canonical or not _table_nonempty(canonical["site_pages"]):
        return {}
    con = common.open_duckdb(paths)
    try:
        rows = con.execute(
            "SELECT url, http_status, redirect_chain, final_url FROM site_pages"
        ).fetchall()
    finally:
        con.close()

    out: dict[str, dict[str, Any]] = {}
    for url, http_status, redirect_chain, final_url in rows:
        path = _url_path(url)
        if path in out:
            continue
        chain_len = 0
        if redirect_chain:
            try:
                chain_len = len(json.loads(redirect_chain))
            except (TypeError, ValueError):
                chain_len = 0
        out[path] = {
            "http_status": http_status,
            "redirect_hops": chain_len,
            "final_url": final_url,
        }
    return out


# ── C04 — реклама/поиск ведут на 404/5xx/недоступные страницы ──────────────
def _run_c04(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "C04",
            "site_pages недоступна (site_crawl не выполнен) — HTTP-статусы "
            "посадочных страниц проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        by_entry = con.execute(
            "SELECT entry_page, COUNT(*), COUNT(*) FILTER (WHERE is_ad) "
            "FROM visits GROUP BY entry_page"
        ).fetchall()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for entry_page, visit_count, ad_visit_count in by_entry:
        info = site_pages.get(entry_page)
        status = info["http_status"] if info else None
        crawled = info is not None
        broken = bool(crawled and status is not None and status >= _C04_BAD_STATUS_MIN)
        rows.append({
            "check_id": "C04",
            "finding": "landing_status",
            "entry_page": entry_page,
            "visit_count": int(visit_count or 0),
            "ad_visit_count": int(ad_visit_count or 0),
            "http_status": status,
            "crawled": crawled,
            "bad_status_threshold": _C04_BAD_STATUS_MIN,
            "broken_landing": broken,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "c04", rows, confidence_cap=confidence_cap)


# ── C05 — лишние редиректы и цепочки переходов ──────────────────────────────
def _run_c05(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "C05",
            "site_pages недоступна (site_crawl не выполнен) — цепочки "
            "редиректов проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        by_entry = con.execute(
            "SELECT entry_page, COUNT(*) FROM visits GROUP BY entry_page"
        ).fetchall()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for entry_page, visit_count in by_entry:
        info = site_pages.get(entry_page)
        if info is None or info["redirect_hops"] < 1:
            continue
        hops = info["redirect_hops"]
        rows.append({
            "check_id": "C05",
            "finding": "redirect_chain",
            "entry_page": entry_page,
            "visit_count": int(visit_count or 0),
            "redirect_hops": hops,
            "final_url": info["final_url"],
            "min_hops_for_finding": _C05_MIN_CHAIN_HOPS_FOR_FINDING,
            "excessive_redirect_chain": hops >= _C05_MIN_CHAIN_HOPS_FOR_FINDING,
            "utm_preservation_verifiable": False,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "c05", rows, confidence_cap=confidence_cap)


# ── C06 — большой отвал между открытием и отправкой формы (легаси 1.1) ─────
_C06_SEGMENT_DIMENSIONS: tuple[str, ...] = ("device", "source_group")


def _run_c06(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        total_open, total_submit = con.execute(
            "SELECT COUNT(*) FILTER (WHERE form_open), "
            "COUNT(*) FILTER (WHERE form_open AND form_submit) FROM visits"
        ).fetchone()
        segment_data = {
            dim: con.execute(
                f'SELECT "{dim}", COUNT(*) FILTER (WHERE form_open), '
                f'COUNT(*) FILTER (WHERE form_open AND form_submit) '
                f'FROM visits GROUP BY "{dim}"'
            ).fetchall()
            for dim in _C06_SEGMENT_DIMENSIONS
        }
    finally:
        con.close()

    total_open = int(total_open or 0)
    total_submit = int(total_submit or 0)
    completion_rate = (total_submit / total_open) if total_open else None

    rows: list[dict[str, Any]] = [{
        "check_id": "C06",
        "finding": "funnel_summary",
        "form_open_visits": total_open,
        "form_submit_visits": total_submit,
        "open_to_submit_rate": round(completion_rate, 4) if completion_rate is not None else None,
        "stage_start_available": False,
        "limitation": (
            "Каталог описывает воронку open->start->submit; промежуточный признак "
            "'начал заполнять форму' отсутствует в goal_flags()/config.goals "
            "(src/transform/build_canonical.py) — структурное ограничение вне "
            "allowed_files этой задачи. Считается двухступенчатая воронка "
            "open->submit (соответствует легаси 1.1)."
        ),
        "confidence": _cap(
            _sample_confidence(total_open, min_sample) if total_open > 0 else "LOW",
            confidence_cap,
        ),
    }]

    for dim, seg_rows in segment_data.items():
        for seg_value, seg_open, seg_submit in seg_rows:
            seg_open = int(seg_open or 0)
            seg_submit = int(seg_submit or 0)
            seg_rate = (seg_submit / seg_open) if seg_open else None
            rows.append({
                "check_id": "C06",
                "finding": "funnel_by_segment",
                "segment_dimension": dim,
                "segment_value": seg_value,
                "form_open_visits": seg_open,
                "form_submit_visits": seg_submit,
                "open_to_submit_rate": round(seg_rate, 4) if seg_rate is not None else None,
                "confidence": _cap(
                    _sample_confidence(seg_open, min_sample) if seg_open > 0 else "LOW",
                    confidence_cap,
                ),
            })

    common.write_metric_artifact(metrics_dir, "c06", rows, confidence_cap=confidence_cap)


# ── C07 — форма содержит лишние обязательные поля ───────────────────────────
def _run_c07(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        total_open, total_submit = con.execute(
            "SELECT COUNT(*) FILTER (WHERE form_open), "
            "COUNT(*) FILTER (WHERE form_open AND form_submit) FROM visits"
        ).fetchone()
    finally:
        con.close()

    total_open = int(total_open or 0)
    total_submit = int(total_submit or 0)
    abandonment_rate = (1 - total_submit / total_open) if total_open else None

    rows: list[dict[str, Any]] = [{
        "check_id": "C07",
        "finding": "form_abandonment_context",
        "form_open_visits": total_open,
        "form_submit_visits": total_submit,
        "abandonment_rate": round(abandonment_rate, 4) if abandonment_rate is not None else None,
        "field_level_granularity_available": False,
        "limitation": (
            "visits не содержит пофлеьного разреза (какое поле формы вызвало "
            "отвал) — автоматическая часть даёт только общий open->submit отвал "
            "как контекст; конкретные лишние поля определяются вручную через "
            "inputs/webvisor_findings.yaml."
        ),
        "confidence": _cap(
            _sample_confidence(total_open, min_sample) if total_open > 0 else "LOW",
            confidence_cap,
        ),
    }]

    inputs = common.load_inputs(paths)
    webvisor = inputs.get("webvisor_findings")
    if _yaml_populated(webvisor, ("date", "sessions_reviewed")):
        rows.extend(_manual_pattern_rows("C07", webvisor, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C07", webvisor, confidence_cap, "MED"))

    common.write_metric_artifact(metrics_dir, "c07", rows, confidence_cap=confidence_cap)


# ── C09 — мобильные элементы неудобны (device-разрез конверсии) ────────────
def _run_c09(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))
    alpha = float(defaults.get("significance_alpha", 0.05))

    con = common.open_duckdb(paths)
    try:
        by_device = con.execute(
            "SELECT device, COUNT(*), COUNT(*) FILTER (WHERE form_submit) "
            "FROM visits GROUP BY device"
        ).fetchall()
    finally:
        con.close()

    stats_by_device = {device: (int(cnt or 0), int(sub or 0)) for device, cnt, sub in by_device}
    desktop_cnt, desktop_sub = stats_by_device.get("desktop", (0, 0))
    desktop_rate = (desktop_sub / desktop_cnt) if desktop_cnt else None

    rows: list[dict[str, Any]] = []
    for device, (cnt, sub) in sorted(stats_by_device.items()):
        rate = (sub / cnt) if cnt else None
        p_value = None
        underperforms = False
        if (
            device != "desktop"
            and desktop_cnt >= _C09_MIN_VISITS_FOR_COMPARISON
            and cnt >= _C09_MIN_VISITS_FOR_COMPARISON
        ):
            p_value = _two_proportion_p_value(desktop_sub, desktop_cnt, sub, cnt)
            underperforms = bool(
                p_value is not None and p_value < alpha
                and rate is not None and desktop_rate is not None and rate < desktop_rate
            )
        rows.append({
            "check_id": "C09",
            "finding": "device_conversion",
            "device": device,
            "visit_count": cnt,
            "form_submit_count": sub,
            "form_submit_rate": round(rate, 4) if rate is not None else None,
            "desktop_form_submit_rate": round(desktop_rate, 4) if desktop_rate is not None else None,
            "p_value": round(p_value, 6) if p_value is not None else None,
            "significance_alpha": alpha,
            "min_visits_for_comparison": _C09_MIN_VISITS_FOR_COMPARISON,
            "device_underperforms_desktop": underperforms,
            "confidence": _cap(
                _sample_confidence(cnt, min_sample) if cnt > 0 else "LOW", confidence_cap,
            ),
        })

    inputs = common.load_inputs(paths)
    webvisor = inputs.get("webvisor_findings")
    if _yaml_populated(webvisor, ("date", "sessions_reviewed")):
        rows.extend(_manual_pattern_rows("C09", webvisor, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C09", webvisor, confidence_cap, "MED"))

    common.write_metric_artifact(metrics_dir, "c09", rows, confidence_cap=confidence_cap)


# ── C10 — нет понятного подтверждения успешной отправки ─────────────────────
def _run_c10(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    """Автоматический сигнал (повторное срабатывание цели form_submit за визит)

    сверяется с D01.overtrigger (block0.py) — переотработка целей уже
    подтверждена на реальных данных Pognali как СИСТЕМНЫЙ артефакт двойного
    счёта (docs/implementation_status.md, goal-flags-overtrigger-symmetry-check:
    87.9% визитов-хитов form_submit дают >1 срабатывание), а НЕ обязательно
    повторной физической отправкой формы пользователем. При overtrigger=true
    сигнал явно помечается как confounded и не поднимается выше LOW —
    содержательный вывод C10 в этом случае несёт только ручная часть
    (inputs/manual_form_tests.yaml), тот же приём, что A03 в block1.py сверяется
    с d01/d03 прежде чем делать вывод.
    """
    min_sample = int(defaults.get("min_sample_visits", 500))

    d01_path = Path(paths.metrics) / "d01.json"
    overtrigger = False
    if d01_path.exists():
        with d01_path.open("r", encoding="utf-8") as fh:
            d01_rows = json.load(fh)
        entry = next((r for r in d01_rows if r.get("goal_group") == "form_submit"), None)
        if entry is not None:
            overtrigger = bool(entry.get("overtrigger"))

    con = common.open_duckdb(paths)
    try:
        total_submit, repeat_submit = con.execute(
            "SELECT COUNT(*) FILTER (WHERE form_submit), "
            "COUNT(*) FILTER (WHERE form_submit AND form_submit_count >= 2) FROM visits"
        ).fetchone()
    finally:
        con.close()

    total_submit = int(total_submit or 0)
    repeat_submit = int(repeat_submit or 0)
    repeat_share = (repeat_submit / total_submit) if total_submit else None

    caveat = (
        "Повторное срабатывание цели form_submit в рамках визита часто вызвано "
        "дублированием goal_id в настройке целей (см. D01, block0.py), а НЕ "
        "повторной физической отправкой формы пользователем — при "
        "confounded_by_goal_overtrigger=true этот сигнал не считать "
        "самостоятельным подтверждением проблемы C10, только контекстом."
        if overtrigger else
        "D01 не отметил переотработку form_submit на этом прогоне — сигнал "
        "повторной отправки не искажён известным паттерном дублирования целей."
    )

    rows: list[dict[str, Any]] = [{
        "check_id": "C10",
        "finding": "repeat_submit_signal",
        "form_submit_visits": total_submit,
        "repeat_submit_visits": repeat_submit,
        "repeat_submit_share": round(repeat_share, 4) if repeat_share is not None else None,
        "confounded_by_goal_overtrigger": overtrigger,
        "caveat": caveat,
        "confidence": _cap(
            "LOW" if overtrigger else (
                _sample_confidence(total_submit, min_sample) if total_submit > 0 else "LOW"
            ),
            confidence_cap,
        ),
    }]

    inputs = common.load_inputs(paths)
    manual = inputs.get("manual_form_tests")
    if _yaml_populated(manual, ("tested_at",)):
        rows.extend(_manual_pattern_rows("C10", manual, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C10", manual, confidence_cap, "MED"))

    common.write_metric_artifact(metrics_dir, "c10", rows, confidence_cap=confidence_cap)


# ── C12 — на первом экране непонятно, что предлагает компания ──────────────
def _run_c12(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))

    con = common.open_duckdb(paths)
    try:
        by_entry = con.execute(
            "SELECT entry_page, COUNT(*), "
            "COUNT(*) FILTER (WHERE NOT form_open AND NOT call_click AND NOT messenger_click) "
            "FROM visits GROUP BY entry_page"
        ).fetchall()
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for entry_page, cnt, zero_engagement in by_entry:
        cnt = int(cnt or 0)
        zero_engagement = int(zero_engagement or 0)
        share = (zero_engagement / cnt) if cnt else None
        candidate = bool(
            cnt >= _C12_MIN_VISITS_FOR_CHECK
            and share is not None
            and share >= _C12_HIGH_ZERO_ENGAGEMENT_SHARE
        )
        rows.append({
            "check_id": "C12",
            "finding": "entry_page_zero_engagement",
            "entry_page": entry_page,
            "visit_count": cnt,
            "zero_engagement_visits": zero_engagement,
            "zero_engagement_share": round(share, 4) if share is not None else None,
            "min_visits_threshold": _C12_MIN_VISITS_FOR_CHECK,
            "high_zero_engagement_threshold": _C12_HIGH_ZERO_ENGAGEMENT_SHARE,
            "unclear_first_screen_candidate": candidate,
            "confidence": _cap(
                _sample_confidence(cnt, min_sample) if cnt > 0 else "LOW", confidence_cap,
            ),
        })

    inputs = common.load_inputs(paths)
    webvisor = inputs.get("webvisor_findings")
    if _yaml_populated(webvisor, ("date", "sessions_reviewed")):
        rows.extend(_manual_pattern_rows("C12", webvisor, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C12", webvisor, confidence_cap, "MED"))

    common.write_metric_artifact(metrics_dir, "c12", rows, confidence_cap=confidence_cap)


# ═══════════════════════ Задача 5H: C13–C25 ═════════════════════════════════
# ── C13 — цена/условия/следующий шаг раскрываются поздно ───────────────────
def _run_c13(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Единственный содержательный сигнал — client_facts из inputs/client_answers.yaml

    (site_and_form.price_shown_before_submit/deposit, ответы на вопросы
    установочного созвона, см. docstring client_answers.yaml) — client-HIGH,
    без потолка источника (CLAUDE.md, «Уверенность находок»). `visits` сам по
    себе не несёт события «пользователь увидел цену/условие» — момент
    раскрытия из одних визитов не восстановим (тот же класс разрыва, что
    описан для C15/C16/C18/C25 в докстринге модуля, разрыв 9 — только у C13
    есть выход через client_answers, поэтому в список разрыва 9 он не входит).
    """
    inputs = common.load_inputs(paths)
    client = inputs.get("client_answers") or {}
    site_and_form = client.get("site_and_form") or {}
    deposit = site_and_form.get("deposit") or {}

    rows: list[dict[str, Any]] = []
    price_shown = site_and_form.get("price_shown_before_submit")
    if price_shown is not None:
        rows.append({
            "check_id": "C13", "finding": "client_fact_price_disclosure",
            "price_shown_before_submit": bool(price_shown),
            "source": "client_answers", "confidence": "client-HIGH",
        })
    if deposit.get("exists") is not None:
        rows.append({
            "check_id": "C13", "finding": "client_fact_deposit",
            "deposit_exists": bool(deposit.get("exists")),
            "deposit_amount_rub": deposit.get("amount_rub"),
            "source": "client_answers", "confidence": "client-HIGH",
        })

    webvisor = inputs.get("webvisor_findings")
    if _yaml_populated(webvisor, ("date", "sessions_reviewed")):
        rows.extend(_manual_pattern_rows("C13", webvisor, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C13", webvisor, confidence_cap, "MED"))

    if not rows:
        _write_unavailable(
            metrics_dir, "C13",
            "нет ни ответа клиента (inputs/client_answers.yaml: "
            "site_and_form.price_shown_before_submit/deposit не заполнены), ни "
            "наблюдений Вебвизора (inputs/webvisor_findings.yaml) — момент "
            "раскрытия цены/условий не восстановим из одних visits (нет "
            "события просмотра цены в канонической схеме)",
        )
        return

    common.write_metric_artifact(metrics_dir, "c13", rows, confidence_cap=confidence_cap)


# ── C20 — попап/чат/cookie-баннер перекрывает контент и CTA ────────────────
def _run_c20(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Нет данных о наложении элементов в канонической схеме (см. докстринг

    модуля, разрыв 9) — единственный источник вывода inputs/webvisor_findings.yaml
    (optional по methodology.yaml). Device-конверсия (потенциальный косвенный
    сигнал "мобильный сегмент теряет конверсию") уже полностью посчитана в C09
    под своей причинной рамкой — здесь НЕ пересчитывается (разрыв 10), только
    упоминается как пойнтер для аналитика.
    """
    inputs = common.load_inputs(paths)
    webvisor = inputs.get("webvisor_findings")
    if not _yaml_populated(webvisor, ("date", "sessions_reviewed")):
        _write_unavailable(
            metrics_dir, "C20",
            "inputs/webvisor_findings.yaml не заполнен (meta.date/"
            "sessions_reviewed пусты) — попап/чат/cookie-баннер не "
            "детектируются автоматически, в канонической схеме нет данных о "
            "наложении элементов; при заполнении сверить с device-конверсией "
            "C09 вручную (не пересчитывается повторно под C20)",
        )
        return

    rows = _manual_pattern_rows("C20", webvisor, confidence_cap, "MED")
    rows.extend(_manual_conclusions_rows("C20", webvisor, confidence_cap, "MED"))
    common.write_metric_artifact(metrics_dir, "c20", rows, confidence_cap=confidence_cap)


# ── C21 — проблема в конкретном браузере/ОС/разрешении ──────────────────────
def _run_c21(paths: Any, defaults: dict[str, Any], confidence_cap: str, metrics_dir: Path) -> None:
    """Конверсия по browser/os/screen_resolution против самого массового

    значения того же измерения (баланс — как в C09, но измерения другие;
    device сознательно не входит, см. докстринг модуля, разрыв 10).
    """
    min_sample = int(defaults.get("min_sample_visits", 500))
    alpha = float(defaults.get("significance_alpha", 0.05))

    con = common.open_duckdb(paths)
    try:
        segment_data = {
            dim: con.execute(
                f'SELECT COALESCE("{dim}", \'unknown\') AS seg, COUNT(*), '
                f'COUNT(*) FILTER (WHERE form_submit) FROM visits GROUP BY seg'
            ).fetchall()
            for dim in _C21_SEGMENT_DIMENSIONS
        }
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    for dim, seg_rows in segment_data.items():
        stats_by_value = {value: (int(cnt or 0), int(sub or 0)) for value, cnt, sub in seg_rows}
        if not stats_by_value:
            continue
        baseline_value = max(stats_by_value, key=lambda v: stats_by_value[v][0])
        baseline_cnt, baseline_sub = stats_by_value[baseline_value]
        baseline_rate = (baseline_sub / baseline_cnt) if baseline_cnt else None

        for value, (cnt, sub) in sorted(stats_by_value.items()):
            rate = (sub / cnt) if cnt else None
            p_value = None
            underperforms = False
            if (
                value != baseline_value
                and baseline_cnt >= _C21_MIN_VISITS_FOR_COMPARISON
                and cnt >= _C21_MIN_VISITS_FOR_COMPARISON
            ):
                p_value = _two_proportion_p_value(baseline_sub, baseline_cnt, sub, cnt)
                underperforms = bool(
                    p_value is not None and p_value < alpha
                    and rate is not None and baseline_rate is not None and rate < baseline_rate
                )
            rows.append({
                "check_id": "C21",
                "finding": "segment_conversion",
                "segment_dimension": dim,
                "segment_value": value,
                "is_baseline": value == baseline_value,
                "baseline_value": baseline_value,
                "visit_count": cnt,
                "form_submit_count": sub,
                "form_submit_rate": round(rate, 4) if rate is not None else None,
                "baseline_form_submit_rate": round(baseline_rate, 4) if baseline_rate is not None else None,
                "p_value": round(p_value, 6) if p_value is not None else None,
                "significance_alpha": alpha,
                "min_visits_for_comparison": _C21_MIN_VISITS_FOR_COMPARISON,
                "segment_underperforms_baseline": underperforms,
                "confidence": _cap(
                    _sample_confidence(cnt, min_sample) if cnt > 0 else "LOW", confidence_cap,
                ),
            })

    common.write_metric_artifact(metrics_dir, "c21", rows, confidence_cap=confidence_cap)


# ── C24 — реклама/SEO ведут на отсутствующий товар/недоступную услугу ──────
def _run_c24(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """client_facts (Q04 capacity_limits) — единственный источник: site_pages

    не хранит наличие/доступность (нет поля stock/availability в схеме, см.
    докстринг модуля, разрыв 9), а C04 отдельно уже покрывает чисто битые
    (4xx/5xx) посадочные — здесь нужен именно случай "страница отвечает 200,
    но услуга/товар недоступны", который без клиентского факта не восстановить.
    """
    inputs = common.load_inputs(paths)
    client = inputs.get("client_answers") or {}
    capacity_limits = client.get("capacity_limits") or []

    rows: list[dict[str, Any]] = []
    for limit in capacity_limits:
        if not isinstance(limit, dict):
            continue
        text = (limit.get("limit") or "").strip()
        if not text:
            continue
        rows.append({
            "check_id": "C24", "finding": "client_fact_capacity_limit",
            "limit": text, "period": limit.get("period"),
            "source": "client_answers", "confidence": "client-HIGH",
        })

    if not rows:
        _write_unavailable(
            metrics_dir, "C24",
            "inputs/client_answers.yaml: capacity_limits (Q04) не заполнен — "
            "наличие/доступность товара или услуги не выгружается ни в "
            "site_pages (нет поля stock/availability в канонической схеме), "
            "ни в visits; C04 отдельно уже покрывает чисто битые (4xx/5xx) "
            "посадочные, здесь нужен именно случай \"страница отвечает 200, "
            "но услуга недоступна\", который без клиентского факта не "
            "восстановить",
        )
        return

    common.write_metric_artifact(metrics_dir, "c24", rows, confidence_cap=confidence_cap)


# ── C14 — недостаточно доверия (гарантии/реквизиты/кейсы/отзывы) ───────────
# Тип B (полностью ручная, как C03/C08/C11), плюс optional webvisor_findings
# по methodology.yaml — единственная из manual-only проверок 5H с обогащением.
def _run_c14(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    inputs = common.load_inputs(paths)
    manual = inputs.get("manual_form_tests")
    webvisor = inputs.get("webvisor_findings")
    manual_ok = _yaml_populated(manual, ("tested_at",))
    webvisor_ok = _yaml_populated(webvisor, ("date", "sessions_reviewed"))

    if not manual_ok and not webvisor_ok:
        _write_unavailable(
            metrics_dir, "C14",
            "ни inputs/manual_form_tests.yaml (meta.tested_at пуст), ни "
            "inputs/webvisor_findings.yaml (meta.date/sessions_reviewed пусты) "
            "не заполнены — элементы доверия (гарантии, реквизиты, кейсы, "
            "отзывы, процесс) не автоматизируются, site_pages не хранит их "
            "присутствие/расположение (см. докстринг модуля, разрыв 9)",
        )
        return

    rows: list[dict[str, Any]] = []
    if manual_ok:
        rows.extend(_manual_pattern_rows("C14", manual, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C14", manual, confidence_cap, "MED"))
    if webvisor_ok:
        rows.extend(_manual_pattern_rows("C14", webvisor, confidence_cap, "MED"))
        rows.extend(_manual_conclusions_rows("C14", webvisor, confidence_cap, "MED"))
    common.write_metric_artifact(metrics_dir, "c14", rows, confidence_cap=confidence_cap)


# ── C15/C16/C18/C25 — A+B без применимой авто-части (см. разрыв 9) ─────────
def _run_manual_form_tests_fallback(
    check_id: str, paths: Any, confidence_cap: str, metrics_dir: Path, gap_note: str,
) -> None:
    """A+B-проверка, чья автоматическая половина структурно недоступна в

    текущей канонической схеме (см. докстринг модуля, разрыв 9) — единственный
    источник вывода inputs/manual_form_tests.yaml (общий ручной аудит C01-C25,
    см. его собственный докстринг). ``gap_note`` объясняет отсутствие авто-части
    и в reason unavailable, и в limitation каждой ручной строки — проверка не
    выглядит "забытой", а честно называет структурную причину.
    """
    inputs = common.load_inputs(paths)
    doc = inputs.get("manual_form_tests")
    if not _yaml_populated(doc, ("tested_at",)):
        _write_unavailable(
            metrics_dir, check_id,
            f"inputs/manual_form_tests.yaml не заполнен (meta.tested_at пуст); "
            f"{gap_note}",
        )
        return

    rows = _manual_pattern_rows(check_id, doc, confidence_cap, "MED")
    rows.extend(_manual_conclusions_rows(check_id, doc, confidence_cap, "MED"))
    for row in rows:
        row["automatic_component"] = "unavailable"
        row["limitation"] = gap_note
    common.write_metric_artifact(metrics_dir, check_id.lower(), rows, confidence_cap=confidence_cap)


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить C01–C25 из числа доступных; вернуть имена записанных артефактов."""
    canonical = common.load_canonical(paths)
    caps = _confidence_caps(paths)
    metrics_dir = Path(paths.metrics)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[str] = []

    # C01/C02: crux не даёт canonical-таблицы (см. докстринг, разрыв 1) — гейт
    # только по runnable_ids, без условия на canonical (тот же приём, что T06
    # в block2.py для client_answers).
    if "C01" in runnable_ids:
        _run_c01(paths, defaults, canonical, caps.get("C01", "HIGH"), metrics_dir)
        artifacts.append("c01")

    if "C02" in runnable_ids:
        _run_c02(paths, defaults, canonical, caps.get("C02", "HIGH"), metrics_dir)
        artifacts.append("c02")

    if "C03" in runnable_ids and "site_pages" in canonical:
        _run_manual_only_check("C03", paths, caps.get("C03", "HIGH"), metrics_dir)
        artifacts.append("c03")

    if "C04" in runnable_ids and "visits" in canonical:
        _run_c04(paths, canonical, caps.get("C04", "HIGH"), metrics_dir)
        artifacts.append("c04")

    if "C05" in runnable_ids and "visits" in canonical:
        _run_c05(paths, canonical, caps.get("C05", "HIGH"), metrics_dir)
        artifacts.append("c05")

    if "C06" in runnable_ids and "visits" in canonical:
        _run_c06(paths, defaults, caps.get("C06", "HIGH"), metrics_dir)
        artifacts.append("c06")

    if "C07" in runnable_ids and "visits" in canonical:
        _run_c07(paths, defaults, caps.get("C07", "HIGH"), metrics_dir)
        artifacts.append("c07")

    if "C08" in runnable_ids and "site_pages" in canonical:
        _run_manual_only_check("C08", paths, caps.get("C08", "HIGH"), metrics_dir)
        artifacts.append("c08")

    if "C09" in runnable_ids and "visits" in canonical:
        _run_c09(paths, defaults, caps.get("C09", "HIGH"), metrics_dir)
        artifacts.append("c09")

    if "C10" in runnable_ids and "visits" in canonical:
        _run_c10(paths, defaults, caps.get("C10", "HIGH"), metrics_dir)
        artifacts.append("c10")

    if "C11" in runnable_ids and "site_pages" in canonical:
        _run_manual_only_check("C11", paths, caps.get("C11", "HIGH"), metrics_dir)
        artifacts.append("c11")

    if "C12" in runnable_ids and "visits" in canonical:
        _run_c12(paths, defaults, caps.get("C12", "HIGH"), metrics_dir)
        artifacts.append("c12")

    # ── Задача 5H: C13–C25 ───────────────────────────────────────────────────
    if "C13" in runnable_ids and "visits" in canonical:
        _run_c13(paths, caps.get("C13", "HIGH"), metrics_dir)
        artifacts.append("c13")

    if "C14" in runnable_ids and "site_pages" in canonical:
        _run_c14(paths, caps.get("C14", "HIGH"), metrics_dir)
        artifacts.append("c14")

    if "C15" in runnable_ids and "visits" in canonical:
        _run_manual_form_tests_fallback(
            "C15", paths, caps.get("C15", "HIGH"), metrics_dir,
            "нет данных о видимости/CTR CTA-элементов в канонической схеме "
            "(site_pages не хранит разметку кнопок, visits не трекает клики "
            "по элементам вне 4 групп целей) — см. докстринг модуля, разрыв 9",
        )
        artifacts.append("c15")

    if "C16" in runnable_ids and "visits" in canonical:
        _run_manual_form_tests_fallback(
            "C16", paths, caps.get("C16", "HIGH"), metrics_dir,
            "нет данных о кликах по вторичным (конкурирующим) элементам "
            "страницы — visits трекает только 4 группы целей (форма/звонок/"
            "мессенджер) — см. докстринг модуля, разрыв 9",
        )
        artifacts.append("c16")

    if "C17" in runnable_ids and "site_pages" in canonical:
        _run_manual_only_check("C17", paths, caps.get("C17", "HIGH"), metrics_dir)
        artifacts.append("c17")

    if "C18" in runnable_ids and "visits" in canonical:
        _run_manual_form_tests_fallback(
            "C18", paths, caps.get("C18", "HIGH"), metrics_dir,
            "внутренний поиск/фильтры не выгружаются ни одним источником в "
            "pipeline — тот же класс разрыва, что и C19 (см. докстринг "
            "модуля, разрыв 7)",
        )
        artifacts.append("c18")

    if "C19" in runnable_ids and "visits" in canonical:
        _write_unavailable(
            metrics_dir, "C19",
            "внутренний поиск по сайту не выгружается ни одним источником в "
            "pipeline (нет модуля extract для отчёта \"Поиск по сайту\"/аналога "
            "search-query лога, нет канонической таблицы) — частоту нулевой "
            "выдачи посчитать нечем; не симулируется по косвенным данным "
            "(CLAUDE.md, протокол микрозадач п.5; см. докстринг модуля, разрыв 7)",
        )
        artifacts.append("c19")

    if "C20" in runnable_ids and "visits" in canonical:
        _run_c20(paths, caps.get("C20", "HIGH"), metrics_dir)
        artifacts.append("c20")

    if "C21" in runnable_ids and "visits" in canonical:
        _run_c21(paths, defaults, caps.get("C21", "HIGH"), metrics_dir)
        artifacts.append("c21")

    if "C22" in runnable_ids and "visits" in canonical:
        _write_unavailable(
            metrics_dir, "C22",
            "пошаговая воронка корзины/записи/бронирования не восстановима: "
            "goal_flags()/config.goals (src/transform/build_canonical.py) знает "
            "только 4 плоские группы целей (form_open/form_submit/call_click/"
            "messenger_click) без пошагового признака — тот же структурный "
            "разрыв, что и в C06 (см. докстринг модуля, разрывы 5 и 8), только "
            "здесь без даже двухступенчатого приближения: отдельной группы "
            "\"cart_step\"/\"booking_step\" в config.goals нет вовсе",
        )
        artifacts.append("c22")

    if "C23" in runnable_ids and "site_pages" in canonical:
        _run_manual_only_check("C23", paths, caps.get("C23", "HIGH"), metrics_dir)
        artifacts.append("c23")

    if "C24" in runnable_ids and "visits" in canonical:
        _run_c24(paths, caps.get("C24", "HIGH"), metrics_dir)
        artifacts.append("c24")

    if "C25" in runnable_ids and "visits" in canonical:
        _run_manual_form_tests_fallback(
            "C25", paths, caps.get("C25", "HIGH"), metrics_dir,
            "нет классификации страниц на контентные/коммерческие ни в "
            "config, ни в канонической схеме (site_pages не хранит категорию "
            "страницы) — см. докстринг модуля, разрыв 9",
        )
        artifacts.append("c25")

    return artifacts
