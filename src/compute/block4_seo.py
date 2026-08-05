"""Блок 4 — SEO и органический спрос (каталог v2 §9, задачи 5bA: S01–S10,
5bB: S11–S20 — технический SEO и производительность, 5bC: S21–S27 — кросс-
системные, коммерческие и структурные проверки, завершает блок 4).

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
    S11  важные страницы закрыты robots/noindex                  [seo_queries] (+site_crawl)
    S12  canonical указывает на неверную страницу                [seo_queries] (+site_crawl)
    S13  sitemap неполный/устаревший/с ошибочными URL            [seo_queries] (+site_crawl)
    S14  органический трафик ведёт на 404/удалённые страницы     [seo_queries] (+site_crawl)
    S15  цепочки и массовые редиректы размывают сигнал           [site_crawl] (+seo_queries)
    S16  индекс раздут дублями/параметрами/тонкими страницами    [seo_queries] (+site_crawl)
    S17  title/description/H1 отсутствуют/дублируются/не по спросу [seo_queries] (+site_crawl)
    S18  важные страницы имеют мало внутренних ссылок/сироты     [site_crawl]
    S19  архитектура требует слишком много кликов до коммерции   [site_crawl] (+visits)
    S20  мобильная производительность и CWV ухудшают конверсию   [seo_queries, crux] (+visits)
    S21  Яндекс и Google показывают противоположную картину       [seo_queries]
    S22  контент получает органику, не переводит в коммерцию      [seo_queries, visits]
    S23  органические посадочные конвертируют хуже сопоставимых   [seo_queries, visits]
    S24  высококонверсионные SEO-страницы теряют видимость        [seo_queries, visits]
    S25  сниппет не использует структурированные данные/SERP      [seo_queries] (+site_crawl)
    S26  геоспрос не покрыт отдельными релевантными страницами    [wordstat, seo_queries] (+site_crawl)
    S27  JS-контент/ссылки недоступны поисковому роботу            [seo_queries] (+site_crawl)

Контракт:
    Читает   — data/canonical/{seo_queries,visits,site_pages,site_link_graph}.parquet,
               data/raw/crux/crux.json (НАПРЯМУЮ, не через canonical — у CrUX
               нет канонической таблицы, тот же приём, что C01/C02 в block3.py),
               inputs/manual_cwv.yaml (S20, тот же приём, что C01 при отсутствии
               полевых данных CrUX), data/metrics/degradation_report.json
               (confidence_cap на проверку).
               config клиента НЕ читается: is_brand уже посчитан в transform
               (build_canonical.is_brand_query, config.brand_terms применены
               там), здесь используется готовая колонка seo_queries.is_brand.
    Пишет    — data/metrics/{s01..s27}.csv/.json. БЕЗ LLM.

── Структурные разрывы задачи 5bA (S01-S10, НЕ устраняются здесь — вне allowed_files) ───────────

1. wordstat (ИСТОРИЯ, закрыто задачами FIX-wordstat-canonical +
   FIX-block4-seo-wordstat-consumption, уточнено FIX-s07-site-pages-join):
   src/transform/build_canonical.py строит data/canonical/wordstat.parquet
   (LEFT JOIN wordstat_weekly + wordstat_core_queries по normalized_phrase).
   S07 (requires=[wordstat, seo_queries], optional=[site_crawl]) сопоставляет
   кластеры спроса (scope=='gap-specific', т.е. коммерческий спрос за
   вычетом junk/general) с картой страниц ДВУМЯ независимыми сигналами
   (AUDIT-s07-s26-formula-match, 2026-07-29 — query-only сопоставление не
   соответствует формуле каталога §9 строка 263, "Сопоставить кластеры
   Wordstat/GSC с картой страниц", источник — "Wordstat + GSC + сайт"):
   has_matching_query — normalize(phrase) буквально совпадает с каким-то
   query из seo_queries (старая логика, единственная до этого фикса);
   has_matching_page — на какой-то странице canonical["site_pages"] все
   слова фразы встречаются в title/h1/URL-пути (простое текстовое
   пересечение множеств слов, см. _phrase_matches_site_page — каталог не
   даёт и не требует более сложной формулы). Находка — материальный кластер
   без совпадения ни по одному из двух сигналов. Без canonical["wordstat"]
   ИЛИ без canonical["site_pages"] проверка пишет unavailable (данных нет —
   не придумываем, CLAUDE.md протокол микрозадач п.5); до FIX-s07-site-
   pages-join отсутствие site_pages не проверялось вовсе. S26 (requires=
   [wordstat, seo_queries]) сознательно НЕ получил site_pages-сопоставление
   этим фиксом — та же query-only логика, что и раньше
   (geo_dimension_available=false); каталог требует для S26 ещё и позиции/
   зону обслуживания — отдельная задача вне scope FIX-s07-site-pages-join.
   S06 (optional=[wordstat]) сверяет месяцы-аномалии показов
   seo_queries с недельным спросом Wordstat (фразы purpose="seasonality" —
   единственные, которые extract специально отбирает для отслеживания
   сезонной кривой, см. src/extract/wordstat.py:_merge_seasonality_candidates):
   при wordstat_available=True и наличии данных за аномальный месяц итоговая
   confidence поднимается до MED (реальная сверка, не гипотеза — каталог §11,
   "Что Claude не должен утверждать", п.9 требует именно ПРОВЕРКИ сезонности,
   а не констатации невозможности); без Wordstat вердикт остаётся прежним
   (LOW, cannot_determine_without_wordstat).

2. month-гранулярность seo_queries: build_seo_queries_gsc агрегирует по
   (query, page, device, month), а финальный transform-дедуп также включает
   month. Поэтому месячные ряды не схлопываются до page-level агрегатов S09
   и S24.

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

── Структурные разрывы задачи 5bB (S11-S20, НЕ устраняются здесь — вне allowed_files) ──

5. **Частичное покрытие краулера:** site_crawl.py обходит ограниченный
   список URL (`top_n_each_source` по умолчанию 20 на источник — топ по
   расходу/трафику из C/D/E, см. data-export-spec-v2.md §G1, ред. 2:
   "частичное покрытие по построению — НЕ повод для произвольно короткого
   списка"). Каждая проверка S11-S19, читающая site_pages, несёт в summary
   `crawl_coverage_caveat` и `crawled_url_count`, чтобы находки не читались
   как утверждение о сайте целиком — только об обойдённых URL.

6. **S11 — "требует недоступного рендеринга" реализован частично:** каталог
   (источник истины a, catalog-proveryaemyh-marketingovyh-ugroz-v2.md)
   описывает S11 как robots/noindex ИЛИ недоступный рендеринг. data-export-
   spec-v2.md §G1 (источник истины b, контракт полей) явно относит
   `js_content_diff` (сырой HTML vs отрендеренный) только к S27 ("Сырой HTML
   vs отрендеренный... — S27"), не к S11 — а S27 вне скоупа задачи 5bB.
   Расхождение между (a) и (b) (CLAUDE.md, протокол микрозадач п.5) разрешено
   в пользу (b): здесь реализован только компонент robots/noindex;
   `js_rendering_component_implemented: false` явно помечает каждую строку и
   summary S11.

7. **CrUX (S20)** — тот же структурный разрыв, что задокументирован в
   block3.py (докстринг, разрыв 1): `crux.py: CANONICAL_TABLES = []` ->
   requires=[crux] никогда не станет "runnable" через автоматическую
   деградацию. Блок читает `data/raw/crux/crux.json` напрямую, минуя
   canonical/degradation (тот же приём, что C01/C02); тесты конструируют
   `runnable_ids` явным множеством (тот же прецедент, что test_block1/2/3.py).
   CrUX-запрос не фильтрует по formFactor (тот же прецедент, что C01) — при
   наличии полевых данных p75 агрегирован по всем устройствам, не только
   мобильным, несмотря на формулировку S20 "мобильная производительность";
   единственный источник действительно device-specific CWV —
   `inputs/manual_cwv.yaml` (`meta.device`). При пустом CrUX
   (`cwv_field_data_available` ложно или файл отсутствует) — единственный
   путь дальше, с обязательным MED-потолком (задача 5bB, промт: "CrUX empty
   -> только manual lab data с MED cap").

8. **S18/S19 (`site_link_graph`)** — тот же структурный разрыв класса,
   что 1/2 в докстринге block3.py: `site_crawl.py` пишет `link_graph.parquet`
   только "если BFS даёт рёбра" (докстринг extract-модуля) — при отсутствии
   рёбер (BFS не выполнялся или не нашёл внутренних ссылок) обе проверки
   пишут unavailable с явной причиной, а не имитируют глубину/входящие ссылки
   по косвенным данным.

9. **S19** ("слишком много кликов до коммерческой страницы") — `site_pages`/
   `site_link_graph` не хранят признак "коммерческая страница" (тот же класс
   ограничения, что разрыв 4 выше про S08, — "нет поля в канонической схеме,
   не выдумываем"). Реализовано по всем URL графа глубже порога
   `_S19_DEEP_THRESHOLD`; `commercial_classification_available: false` в
   summary — приоритизация среди них по коммерческой значимости остаётся за
   аналитиком.

── Структурные разрывы задачи 5bC (S21-S27, завершает блок 4) ──────────────

10. **S21** ("Яндекс и Google показывают противоположную картину") — сравнение
    строится по `seo_queries.source` (`gsc`|`webmaster`), агрегированному по
    странице (не по (query, page): Вебмастер отдаёт `page` только с 3B-patch,
    а `_url_path` нормализует оба представления URL к одному ключу — тот же
    приём, что _organic_visits_by_page). Вебмастер (`popular-queries`) — это
    снимок за всё окно выгрузки (один `month` на строку, см. docstring
    build_seo_queries_webmaster в transform), а не помесячный ряд, как GSC —
    поэтому сравнение "система A растёт, система B падает" по месяцам
    невозможно без искажения (разные единицы времени); реализовано
    сравнение агрегированной позиции и CTR каждой системы за всё окно —
    расхождение по позиции/CTR при материальном объёме показов в обеих
    системах, не расхождение трендов.

11. **S22** ("контент получает органику, не переводит в коммерческий раздел")
    — та же структурная нехватка, что разрыв 4 (S08): в canonical-слое нет
    классификации "информационная/коммерческая страница", а `visits` не несёт
    последовательность страниц сессии (только `entry_page`) — сравнить "вход
    на инфо-страницу -> переход в коммерческий раздел" буквально нечем.
    Автоматическая часть здесь по necessity пересекается с S08 (тот же прокси
    "нулевая вовлечённость при материальном органическом трафике"), но S22
    считает это на уровне ВСЕГО сайта — доля кликов органики, оседающая на
    страницах без единой вовлечённости (`dead_end_click_share`), а не на
    уровне отдельной страницы, как S08. `page_classification_available: false`
    в каждой строке и summary.

12. **S25** ("сниппет не использует структурированные данные и элементы
    выдачи") — ни в одной канонической таблице (`site_pages`: см. разрыв 4)
    нет поля структурированных данных/типа сниппета — каталог сам относит
    финальную проверку к "ручной SERP-проверке" (не только к недостающему
    полю), поэтому это не расширяется здесь надуманным полем. Автоматическая
    часть — CTR аномально низкий относительно медианы своего бакета СРЕДИ
    запросов на позициях 1–10 (`_S25_MAX_POSITION_FOR_SNIPPET_CHECK`, тот же
    метод, что S04, у же диапазон: только страница 1, где элементы выдачи
    визуально заметны). Каждая строка несёт
    `structured_data_field_available: false` и `manual_serp_check_required:
    true` — находка остаётся кандидатом на ручную проверку, не вердиктом.

13. **S26** ("географический/локальный спрос не покрыт отдельными
    релевантными страницами") — requires=[wordstat, seo_queries]; закрыто
    задачей FIX-block4-seo-wordstat-consumption той же механикой, что S07
    (разрыв 1 выше): кластер спроса Wordstat (scope=='gap-specific') без
    совпадения в карте страниц seo_queries.query. Без canonical["wordstat"]
    по-прежнему пишет unavailable ("ядро не посчитано: источник wordstat не
    готов", тот же прецедент, что S07/A07/A16/A25). **Ограничение, не
    устранённое этой задачей:** canonical wordstat не несёт гео-поля на
    строку — `config.sources.wordstat.regions` задаёт единый регион(ы) для
    ВСЕЙ выгрузки целиком, а не per-фразовую гео-метку, а этот
    compute-модуль контракт client config не читает (см. докстринг модуля,
    "config клиента НЕ читается"). Поэтому S26 механически равен S07
    (никакой отдельной гео-фильтрации), каждая строка несёт
    `geo_dimension_available: false` — не выдаём совпадение с S07 за
    географический анализ. Отдельный гео-разрез потребовал бы правки
    build_wordstat()/wordstat.py (per-регион экстракция или колонка региона),
    что вне allowed_files этой задачи.

14. **S27** ("JS-контент или ссылки недоступны поисковому роботу") —
    реализует компонент, зарезервированный в разрыве 6 выше (`js_content_diff`
    относится к S27, не к S11, по data-export-spec-v2.md §G1). Требует
    `site_pages` (optional=[site_crawl] по methodology.yaml, но без него
    считать нечего — тот же принцип, что S11-S14/S16/S17: если `site_pages`
    отсутствует ИЛИ `js_content_diff` не заполнен ни на одной обойдённой
    странице (headless выключен, playwright недоступен в среде обхода, либо
    сайт полностью SSR — различить эти причины нечем без записи в manifest
    обхода, см. src/extract/site_crawl.py: `headless_stats`), проверка пишет
    unavailable с явной формулировкой "ядро не посчитано: источник site_crawl
    не готов" (промт задачи 5bC, тот же принцип, что S26) — не тихий пропуск и
    не пометка "optional". Кандидат — `text_changed=true` ИЛИ непустой
    `links_only_in_rendered` при материальном органическом объёме страницы.

15. **S23/S24 device-разрез** — та же обязательная конвенция, что S08/S09
    (докстринг выше, "S08-S10: обязательный device-разрез"): overall
    (device-агностический) агрегат считает ВСЕ строки, `*_by_device`-находки
    исключают только `device="unknown"`. Единая точка правды —
    `_exclude_unknown_device_sql()`/`_UNKNOWN_DEVICE`, которую переиспользуют
    S08, S09, S23, S24 (не по копии условия на функцию, промт задачи 5bC: "не
    дублировать логику, вынести общую функцию device-фильтрации"). На стороне
    `visits` (S23/S24 by-device engagement) фильтр фактически избыточен —
    `map_device` никогда не пишет "unknown" (см. build_canonical.py) — но
    применяется для единообразия конвенции и на случай будущего источника
    визитов без device-разбивки.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow.parquet as pq

from . import common
from ..extract import wordstat_config as WC
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
# "диапазон позиций, устройств и типов запросов" — S04 использует только
# известные device; unknown/null не участвуют в CTR-прокси.
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

# S11: минимум суммарных показов страницы, чтобы считать её "востребованной"
# при отсутствии in_sitemap=true (иначе шум на единичных показах) — тот же
# принцип материальности, что _MIN_SHOWS_FOR_OPPORTUNITY выше.
_S11_MIN_SHOWS_FOR_IMPORTANT = 20

# S12: минимум суммарных показов страницы, у которой canonical указывает на
# другой URL, чтобы расхождение считалось материальным, а не единичным крауле.
_S12_MIN_SHOWS_FOR_CHECK = 20

# S13: тот же порог материальности для "страница с трафиком отсутствует в
# sitemap".
_S13_MIN_SHOWS_FOR_CHECK = 20

# S14: с какого HTTP-статуса посадочная считается недоступной (тот же порог и
# обоснование, что _C04_BAD_STATUS_MIN в block3.py — независимая константа,
# блоки compute не делят пороги через common.py).
_S14_BAD_STATUS_MIN = 400

# S15: один хоп (напр. http->https, без-www->www) — норма, не "лишний";
# цепочкой ("цепочки и массовые редиректы", множественное число) считается от
# двух хопов — тот же принцип и число, что _C05_MIN_CHAIN_HOPS_FOR_FINDING в
# block3.py.
_S15_MIN_CHAIN_HOPS_FOR_FINDING = 2

# S17: минимум показов, чтобы отсутствие/дубль метаданных считался материальным.
_S17_MIN_SHOWS_FOR_CHECK = 20

# S18: минимум входящих внутренних ссылок, ниже которого страница считается
# "слабо связанной"; 0 входящих — отдельный флаг "страница-сирота".
_S18_LOW_INLINK_THRESHOLD = 2

# S19: глубина от главной (BFS-хопы, depth_from_home), начиная с которой
# архитектура считается "требующей слишком много кликов" — эвристика (каталог
# не даёт числа, тот же принцип, что и остальные пороги-эвристики модуля).
_S19_DEEP_THRESHOLD = 4

# S20: во сколько раз органическая вовлечённость мобильного сегмента должна
# быть ниже вовлечённости десктопа, чтобы разрыв считался материальным (тот
# же коэффициент, что _S05_DECLINE_CLICK_RATIO — просело минимум на 30%);
# минимум визитов в каждом сравниваемом сегменте.
_S20_MOBILE_ENGAGEMENT_GAP_RATIO = 0.7
_S20_MIN_VISITS_FOR_DEVICE_COMPARISON = 30

# S21: минимум показов у КАЖДОЙ системы (Яндекс/Google) по странице, чтобы
# сравнение было материальным; разрыв позиций между системами и во сколько
# раз CTR одной системы должен превышать другую, чтобы считать картину
# "противоположной", а не шумом снятия/агрегации.
_S21_MIN_SHOWS_FOR_COMPARISON = 20
_S21_POSITION_GAP_THRESHOLD = 10.0
_S21_CTR_RATIO_THRESHOLD = 3.0

# S22: тот же порог материальности, что _MIN_SHOWS_FOR_OPPORTUNITY/S08 —
# минимум показов страницы и минимум органических визитов, чтобы судить о
# «пути к деньгам».
_S22_MIN_SHOWS_FOR_CHECK = 20
_S22_MIN_ORGANIC_VISITS_FOR_CHECK = 20

# S23: минимум визитов в каждом из сравниваемых сегментов (органика и прочий
# трафик той же страницы); во сколько раз вовлечённость органики должна быть
# ниже вовлечённости прочего трафика, чтобы разрыв считался материальным (тот
# же коэффициент, что _S20_MOBILE_ENGAGEMENT_GAP_RATIO — просело минимум на 30%).
_S23_MIN_VISITS_FOR_COMPARISON = 20
_S23_ENGAGEMENT_GAP_RATIO = 0.7

# S24: тот же принцип и числа тренда, что S05 (_S05_MIN_SHOWS_FOR_TREND /
# _S05_DECLINE_CLICK_RATIO) — независимые константы, блоки/проверки этого
# модуля не делят пороги между собой; "высококонверсионная" страница — доля
# вовлечённых органических визитов не ниже порога.
_S24_MIN_SHOWS_FOR_TREND = 50
_S24_DECLINE_CLICK_RATIO = 0.7
_S24_HIGH_ENGAGEMENT_RATE = 0.05
_S24_MIN_ORGANIC_VISITS_FOR_CHECK = 20

# S25: тот же метод и числа, что S04 (медиана CTR по бакету), суженные до
# страницы 1 (позиции 1-10) — там, где элементы выдачи/rich results визуально
# заметны и способны менять CTR при равной позиции.
_S25_MIN_SHOWS_FOR_ROW = 20
_S25_MIN_QUERIES_FOR_MEDIAN = 5
_S25_CTR_LOW_RATIO = 0.5
_S25_MAX_POSITION_FOR_SNIPPET_CHECK = 10.0

# S27: минимум органических показов страницы, чтобы расхождение raw/rendered
# считалось значимым для SEO (страница без показов не влияет на выдачу).
_S27_MIN_SHOWS_FOR_CHECK = 20


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


_SEO_ALWAYS_CANDIDATE_FINDINGS: frozenset[str] = frozenset({
    "position_4_10_opportunity",
    "strike_zone_11_20",
    "monthly_shows_anomaly",
    "commercial_demand_without_landing_page",
    "query_page_overlap_overall",
    "query_page_overlap_by_device",
    "wrong_page_ranking_candidate",
    "robots_blocks_important_page",
    "canonical_points_elsewhere",
    "traffic_page_missing_from_sitemap",
    "sitemap_contains_broken_url",
    "organic_traffic_to_broken_page",
    "duplicate_cluster",
    "missing_metadata",
    "duplicate_title",
    "orphan_page",
    "low_inlink_page",
    "page_too_deep",
    "geo_demand_without_landing_page",
})


def _seo_candidate(row: dict[str, Any]) -> bool:
    """Определить S-кандидата только по уже рассчитанным сигналам строки."""
    finding = row.get("finding")
    if finding in _SEO_ALWAYS_CANDIDATE_FINDINGS:
        return True
    if finding == "seasonality_reconciliation":
        return row.get("verdict") in {
            "seasonality_explains_anomaly",
            "anomaly_not_fully_explained_by_seasonality",
            "no_wordstat_data_for_anomaly_months",
        }
    if finding in {"field_cwv", "manual_lab_cwv"}:
        return bool(row.get("any_metric_poor")) or any(
            row.get(key) == "poor" for key in row if key.endswith("_rating")
        )
    return any(bool(row.get(key)) for key in (
        "organic_demand_mix_brand_heavy",
        "ctr_anomalously_low",
        "intent_mismatch_candidate",
        "excessive_redirect_chain",
        "mobile_engagement_significantly_worse",
        "cross_system_divergent",
        "no_conversion_path",
        "organic_significantly_worse",
        "losing_visibility_candidate",
        "snippet_gap_candidate",
        "js_rendering_gap_candidate",
    ))


def _annotate_seo_rows(artifact: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавить единый candidate-контракт, сохранив смешанные S-артефакты."""
    annotated: list[dict[str, Any]] = []
    evidence_ids = common.assign_evidence_ids(artifact, [dict(row) for row in rows])
    for source_row, evidence_id in zip(rows, evidence_ids):
        row = dict(source_row)
        check_id = str(row.get("check_id") or artifact.upper())
        finding = str(row.get("finding") or "")
        candidate = _seo_candidate(row)

        if (
            row.get("status") in {"unavailable", "manual_required"}
            or finding == "cwv_unavailable"
            or (
                finding == "seasonality_reconciliation"
                and row.get("verdict") == "cannot_determine_without_wordstat"
            )
        ):
            role = "limitation"
            reason = f"{check_id.lower()}_source_unavailable"
        elif candidate:
            role = "candidate"
            reason = f"{check_id.lower()}_{finding or 'signal'}"
        elif finding == "summary":
            role = "summary"
            reason = f"{check_id.lower()}_summary"
        elif finding in {
            "by_source",
            "monthly_shows_trend",
            "mobile_seo_context",
            "mobile_vs_desktop_organic_engagement",
            "seasonality_reconciliation",
        }:
            role = "context"
            reason = f"{check_id.lower()}_context"
        else:
            role = "detail"
            reason = f"{check_id.lower()}_detail"

        row.update({
            "evidence_id": evidence_id,
            "evidence_label": common.evidence_label(row),
            "row_ref": evidence_id,
            "candidate": candidate,
            "row_role": role,
            "candidate_reason": reason,
            "context_refs": [],
        })
        annotated.append(row)

    context_refs: dict[str, list[str]] = {}
    for row in annotated:
        if row["row_role"] in {"summary", "baseline", "context"}:
            context_refs.setdefault(str(row["check_id"]), []).append(row["evidence_id"])
    for row in annotated:
        if row["candidate"]:
            row["context_refs"] = list(context_refs.get(str(row["check_id"]), []))
    return annotated


def _annotate_written_artifacts(metrics_dir: Path, artifacts: list[str]) -> None:
    """Перезаписать только артефакты текущего запуска с S-разметкой."""
    for artifact in artifacts:
        path = metrics_dir / f"{artifact}.json"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            rows = json.load(fh)
        if isinstance(rows, list):
            common.write_metric_artifact(
                metrics_dir, artifact, _annotate_seo_rows(artifact, rows)
            )


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


# S08/S09/S23/S24 обязательный device-разрез: "unknown" (Вебмастер не отдаёт
# device, has_device_column=False у обоих экстракторов — см. build_canonical.
# build_seo_queries_webmaster) исключается ТОЛЬКО из *_by_device находок,
# остаётся в device-агностическом overall наравне с остальными строками (см.
# докстринг модуля, "S08-S10: обязательный device-разрез"). Единая точка
# определения фильтра — не дублируется по функциям блока.
_UNKNOWN_DEVICE = "unknown"


def _exclude_unknown_device_sql() -> str:
    """SQL-условие ``device != 'unknown'`` для *_by_device выборок блока 4.

    Общий для всех проверок с device-разрезом (S08/S09/S23/S24) — visits
    всегда несёт конкретное устройство (map_device по умолчанию -> "desktop",
    "unknown" в этой таблице не появляется), поэтому на visits-запросах
    условие безопасно-избыточно; на seo_queries оно и убирает Вебмастер-строки
    без разбивки по устройству.
    """
    return f"device != '{_UNKNOWN_DEVICE}'"


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
        "GROUP BY query, page ORDER BY query, page"
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
        "FROM visits WHERE source_group = 'organic' GROUP BY entry_page ORDER BY entry_page"
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
        "FROM visits WHERE source_group = 'organic' GROUP BY entry_page, device ORDER BY entry_page, device"
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


# ── site_pages (полная схема) — для S11-S17 (задача 5bB) ────────────────────
_CRAWL_COVERAGE_CAVEAT = (
    "site_pages содержит ограниченный список URL (site_crawl.py: "
    "top_n_each_source по умолчанию 20 на источник) — «частичное покрытие по "
    "построению» (data-export-spec-v2.md §G1, ред. 2), НЕ признак короткого "
    "списка по ошибке. Находки этой проверки относятся только к обойдённым "
    "URL и не экстраполируются на сайт целиком."
)


def _load_site_pages_full(canonical: dict[str, Path], paths: Any) -> dict[str, dict[str, Any]]:
    """{нормализованный_путь: {...}} из site_pages — полная схема (в отличие

    от _load_site_titles выше, который берёт только title/h1). Первая строка
    на путь побеждает при коллизии (тот же принцип, что _load_site_titles и
    block3._load_site_pages).
    """
    if "site_pages" not in canonical or not _table_nonempty(canonical["site_pages"]):
        return {}
    con = common.open_duckdb(paths)
    try:
        rows = con.execute(
            "SELECT url, http_status, redirect_chain, final_url, canonical_url, "
            "robots_directive, in_sitemap, title, description, h1 FROM site_pages"
        ).fetchall()
    finally:
        con.close()

    out: dict[str, dict[str, Any]] = {}
    for (url, http_status, redirect_chain, final_url, canonical_url,
         robots_directive, in_sitemap, title, description, h1) in rows:
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
            "url": url,
            "http_status": http_status,
            "redirect_hops": chain_len,
            "final_url": final_url,
            "canonical_url": canonical_url,
            "canonical_path": _url_path(canonical_url) if canonical_url else None,
            "robots_directive": robots_directive,
            "in_sitemap": bool(in_sitemap) if in_sitemap is not None else None,
            "title": title,
            "description": description,
            "h1": h1,
        }
    return out


def _robots_blocks_indexing(directive: str | None) -> bool:
    """True, если директива содержит noindex или disallow (регистронезависимо)."""
    if not directive:
        return False
    lowered = directive.lower()
    return "noindex" in lowered or "disallow" in lowered


def _seo_shows_clicks_by_path(con: Any) -> dict[str, tuple[int, int]]:
    """{нормализованный_путь: (total_shows, total_clicks)} по seo_queries."""
    rows = con.execute(
        "SELECT page, SUM(total_shows), SUM(total_clicks) FROM seo_queries GROUP BY page ORDER BY page"
    ).fetchall()
    out: dict[str, tuple[int, int]] = {}
    for page, shows, clicks in rows:
        path = _url_path(page)
        prev_shows, prev_clicks = out.get(path, (0, 0))
        out[path] = (prev_shows + int(shows or 0), prev_clicks + int(clicks or 0))
    return out


# ── site_link_graph — для S18/S19 (задача 5bB) ──────────────────────────────
def _load_link_graph(canonical: dict[str, Path], paths: Any) -> list[tuple[str, str, int | None]]:
    """[(from_path, to_path, depth_from_home), ...] из site_link_graph."""
    if "site_link_graph" not in canonical or not _table_nonempty(canonical["site_link_graph"]):
        return []
    con = common.open_duckdb(paths)
    try:
        rows = con.execute(
            "SELECT from_url, to_url, depth_from_home FROM site_link_graph"
        ).fetchall()
    finally:
        con.close()
    return [
        (_url_path(f), _url_path(t), int(d) if d is not None else None)
        for f, t, d in rows
    ]


def _inbound_link_counts(edges: list[tuple[str, str, int | None]]) -> dict[str, int]:
    """{to_path: число различных страниц, ссылающихся на него} (уникальные from_path)."""
    sources_by_target: dict[str, set[str]] = {}
    for from_path, to_path, _ in edges:
        sources_by_target.setdefault(to_path, set()).add(from_path)
    return {target: len(sources) for target, sources in sources_by_target.items()}


def _min_depth_by_page(edges: list[tuple[str, str, int | None]]) -> dict[str, int]:
    """{to_path: минимальная depth_from_home среди всех входящих рёбер}."""
    out: dict[str, int] = {}
    for _, to_path, depth in edges:
        if depth is None:
            continue
        if to_path not in out or depth < out[to_path]:
            out[to_path] = depth
    return out


# ── CrUX (S20) — читаем data/raw/crux/crux.json НАПРЯМУЮ (нет canonical),
# тот же приём, что C01/C02 в block3.py (см. докстринг модуля, разрыв 7) ────
_CWV_THRESHOLDS_MS: dict[str, tuple[float, float]] = {
    "largest_contentful_paint": (2500, 4000),
    "interaction_to_next_paint": (200, 500),
    "first_contentful_paint": (1800, 3000),
}
_CWV_CLS_THRESHOLDS: tuple[float, float] = (0.1, 0.25)


def _rate_cwv_metric(name: str, value: float | None) -> str | None:
    """good | needs_improvement | poor по официальным порогам CWV (web.dev/vitals)."""
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


def _read_crux_raw(paths: Any) -> dict[str, Any] | None:
    path = Path(paths.raw) / "crux" / "crux.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


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


def _organic_visit_context_by_device(con: Any) -> dict[str, dict[str, Any]]:
    """{device: {visits, engaged_visits, engagement_rate}} по органическому

    сегменту (тот же принцип вовлечённости, что _organic_visits_by_page — 4
    группы целей).
    """
    rows = con.execute(
        "SELECT device, COUNT(*), "
        "COUNT(*) FILTER (WHERE form_open OR form_submit OR call_click OR messenger_click) "
        "FROM visits WHERE source_group = 'organic' GROUP BY device ORDER BY device"
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for device, total, engaged in rows:
        total = int(total or 0)
        engaged = int(engaged or 0)
        out[device] = {
            "visits": total,
            "engaged_visits": engaged,
            "engagement_rate": round(engaged / total, 4) if total else None,
        }
    return out


# ── S01 — брендовый и небрендовый органический трафик смешаны (легаси 5.2) ──
def _run_s01(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        by_brand = con.execute(
            "SELECT is_brand, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY is_brand ORDER BY is_brand"
        ).fetchall()
        by_source = con.execute(
            "SELECT source, is_brand, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "GROUP BY source, is_brand ORDER BY source, is_brand"
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
        columns = {str(column[1]) for column in con.execute(
            "PRAGMA table_info('seo_queries')"
        ).fetchall()}
        if "device" not in columns:
            device_reason = "колонка seo_queries.device отсутствует"
            groups: list[dict[str, Any]] = []
        else:
            known_devices = con.execute(
                "SELECT DISTINCT lower(trim(CAST(device AS VARCHAR))) FROM seo_queries "
                "WHERE device IS NOT NULL AND trim(CAST(device AS VARCHAR)) <> '' "
                f"AND lower(trim(CAST(device AS VARCHAR))) <> '{_UNKNOWN_DEVICE}'"
            ).fetchall()
            if not known_devices:
                device_reason = (
                    "seo_queries.device не заполнен или содержит только unknown"
                )
                groups = []
            else:
                device_reason = None
                groups = _aggregate_query_page(
                    con,
                    "WHERE device IS NOT NULL AND trim(CAST(device AS VARCHAR)) <> '' "
                    f"AND lower(trim(CAST(device AS VARCHAR))) <> '{_UNKNOWN_DEVICE}'",
                )
    finally:
        con.close()

    if device_reason is not None:
        common.write_metric_artifact(
            metrics_dir,
            "s04",
            [{
                "check_id": "S04",
                "status": "manual_required",
                "reason": f"S04 требует известного device: {device_reason}",
                "confidence": _cap("MED", confidence_cap),
            }],
            confidence_cap=confidence_cap,
        )
        return

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
    _write_unavailable(
        metrics_dir,
        "S05",
        "кластер запросов для сравнения страниц не представлен в canonical seo_queries",
    )


# ── S06 — сезонность vs падение/рост SEO (легаси 5.5) ───────────────────────
def _wordstat_monthly_demand(con: Any) -> dict[str, int]:
    """{month: суммарный wordstat count} по фразам с purpose, включающим

    "seasonality" — единственные фразы, которые src/extract/wordstat.py
    специально подбирает для отслеживания сезонной кривой спроса
    (_merge_seasonality_candidates: seed-маска безусловно + топ по частоте,
    фильтр только junk — в отличие от gap_candidates для S07/S26, которые
    дополнительно исключают general). purpose хранится как comma-joined
    строка (см. build_wordstat в build_canonical.py) — "seasonality" ищется
    подстрокой, других значений с таким токеном как подстрокой не бывает.
    """
    rows = con.execute(
        "SELECT month, SUM(count) FROM wordstat "
        "WHERE purpose LIKE '%seasonality%' AND month IS NOT NULL "
        "GROUP BY month ORDER BY month"
    ).fetchall()
    return {m: int(c or 0) for m, c in rows if m}


def _reconcile_seasonality(
    anomalies: list[dict[str, Any]],
    wordstat_monthly: dict[str, int],
    wordstat_available: bool,
    confidence_cap: str,
) -> dict[str, Any]:
    """Сверить GSC-анoмалии показов и позиции с недельным спросом Wordstat.

    Сезонность объясняет аномалию только когда Wordstat движется в ту же
    сторону, что показы GSC, а средняя позиция не ухудшается. Ухудшение
    позиции при совпадающем спросе — конфликт направлений: спрос мог изменить
    объём показов, но есть отдельный SEO-сигнал. Без Wordstat
    (wordstat_available=False) вердикт остаётся LOW/cannot_determine_without_wordstat.
    """
    base: dict[str, Any] = {
        "check_id": "S06",
        "finding": "seasonality_reconciliation",
        "wordstat_available": wordstat_available,
    }
    if not wordstat_available:
        base.update({
            "limitation": (
                "wordstat.parquet не строится в canonical-слое в текущем состоянии "
                "transform (см. AUDIT-wordstat-canonical, docs/implementation_status.md). "
                "Без сопоставления с Wordstat нельзя утверждать, что колебания показов "
                "вызваны сезонностью, а не реальной SEO-проблемой (каталог §11, «Что "
                "Claude не должен утверждать», п.9) — вердикт остаётся гипотезой."
            ),
            "verdict": "cannot_determine_without_wordstat",
            "confidence": _cap("LOW", confidence_cap),
        })
        return base

    if not anomalies:
        base.update({
            "limitation": "аномалий показов seo_queries не найдено — сверять с Wordstat нечего.",
            "verdict": "no_anomaly_to_reconcile",
            "confidence": _cap("MED", confidence_cap),
        })
        return base

    per_month: list[dict[str, Any]] = []
    all_confirmed = True
    any_checked = False
    for anomaly in anomalies:
        month = anomaly["month"]
        demand = wordstat_monthly.get(month)
        if demand is None:
            per_month.append({
                "month": month, "anomaly_type": anomaly["type"],
                "wordstat_demand": None, "seasonality_confirmed": None,
            })
            all_confirmed = False
            continue
        any_checked = True
        other_months = [v for m, v in wordstat_monthly.items() if m != month]
        baseline = _median(other_months) if other_months else None
        ratio = (demand / baseline) if baseline else None
        if ratio is None:
            wordstat_direction = None
        elif ratio >= _S06_SPIKE_RATIO:
            wordstat_direction = "spike"
        elif ratio <= _S06_DROP_RATIO:
            wordstat_direction = "drop"
        else:
            wordstat_direction = "stable"
        directions_match = wordstat_direction == anomaly["type"]
        position_worsened = anomaly["position_direction"] == "worsened"
        position_supports_seasonality = anomaly["position_direction"] in {"stable", "improved"}
        direction_conflict = bool(directions_match and position_worsened)
        confirmed = bool(directions_match and position_supports_seasonality)
        if not confirmed:
            all_confirmed = False
        per_month.append({
            "month": month,
            "anomaly_type": anomaly["type"],
            "position_direction": anomaly["position_direction"],
            "wordstat_demand": demand,
            "wordstat_baseline": round(baseline, 2) if baseline is not None else None,
            "wordstat_ratio_to_baseline": round(ratio, 3) if ratio is not None else None,
            "wordstat_direction": wordstat_direction,
            "wordstat_direction_matches_shows": directions_match,
            "direction_conflict": direction_conflict,
            "seasonality_confirmed": confirmed,
        })

    if not any_checked:
        base.update({
            "limitation": "у Wordstat нет данных за месяцы аномалий seo_queries — сверить нечем.",
            "verdict": "no_wordstat_data_for_anomaly_months",
            "months": per_month,
            "confidence": _cap("MED", confidence_cap),
        })
        return base

    verdict = (
        "seasonality_explains_anomaly" if all_confirmed
        else "anomaly_not_fully_explained_by_seasonality"
    )
    base.update({
        "months": per_month,
        "verdict": verdict,
        "confidence": _cap("MED", confidence_cap),
    })
    return base


def _run_s06(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    wordstat_available = "wordstat" in canonical and _table_nonempty(canonical["wordstat"])

    con = common.open_duckdb(paths)
    try:
        by_month = con.execute(
            "SELECT month, SUM(total_shows), SUM(total_clicks), "
            "SUM(avg_show_position * total_shows) FILTER (WHERE avg_show_position IS NOT NULL), "
            "SUM(total_shows) FILTER (WHERE avg_show_position IS NOT NULL) "
            "FROM seo_queries WHERE source = 'gsc' GROUP BY month ORDER BY month"
        ).fetchall()
        webmaster_present = con.execute(
            "SELECT COUNT(*) FROM seo_queries WHERE source = 'webmaster'"
        ).fetchone()[0] > 0
        wordstat_monthly = _wordstat_monthly_demand(con) if wordstat_available else {}
    finally:
        con.close()

    months = [
        (m, int(s or 0), int(c or 0), (pos_w / shows_pos) if (pos_w is not None and shows_pos) else None)
        for m, s, c, pos_w, shows_pos in by_month
    ]
    rows: list[dict[str, Any]] = []
    if webmaster_present:
        rows.append({
            "check_id": "S06",
            "finding": "webmaster_monthly_dynamics",
            "source": "webmaster",
            "status": "unavailable",
            "reason": (
                "Вебмастер доступен только как снимок за окно; помесячную "
                "динамику показов и позиции для S06 он не подтверждает."
            ),
        })

    if not months:
        rows.append({
            "check_id": "S06",
            "finding": "gsc_monthly_dynamics",
            "source": "gsc",
            "status": "unavailable",
            "reason": "В canonical[\"seo_queries\"] нет помесячных строк GSC для S06.",
        })
        common.write_metric_artifact(metrics_dir, "s06", rows, confidence_cap=confidence_cap)
        return

    rows.append({
        "check_id": "S06",
        "finding": "monthly_shows_trend",
        "source": "gsc",
        "months": [
            {
                "month": m,
                "total_shows": s,
                "total_clicks": c,
                "avg_show_position": round(position, 4) if position is not None else None,
            }
            for m, s, c, position in months
        ],
        "months_count": len(months),
        "min_months_for_trend": _S06_MIN_MONTHS_FOR_TREND,
        "confidence": _cap("MED", confidence_cap),
    })

    anomalies: list[dict[str, Any]] = []
    if len(months) >= _S06_MIN_MONTHS_FOR_TREND:
        shows_median = _median([s for _, s, _, _ in months])
        position_median = _median([p for _, _, _, p in months if p is not None])
        for m, s, c, position in months:
            ratio = (s / shows_median) if shows_median else None
            is_spike = ratio is not None and ratio >= _S06_SPIKE_RATIO
            is_drop = ratio is not None and ratio <= _S06_DROP_RATIO
            if not (is_spike or is_drop):
                continue
            anomaly_type = "spike" if is_spike else "drop"
            if position is None or position_median is None:
                position_direction = "unavailable"
            elif position > position_median:
                position_direction = "worsened"
            elif position < position_median:
                position_direction = "improved"
            else:
                position_direction = "stable"
            anomalies.append({
                "month": m,
                "type": anomaly_type,
                "position_direction": position_direction,
            })
            rows.append({
                "check_id": "S06",
                "finding": "monthly_shows_anomaly",
                "source": "gsc",
                "month": m,
                "total_shows": s,
                "total_clicks": c,
                "avg_show_position": round(position, 4) if position is not None else None,
                "baseline_median_shows": round(shows_median, 2) if shows_median is not None else None,
                "baseline_median_position": round(position_median, 4) if position_median is not None else None,
                "ratio_to_baseline": round(ratio, 3) if ratio is not None else None,
                "anomaly_type": anomaly_type,
                "position_direction": position_direction,
                "spike_ratio_threshold": _S06_SPIKE_RATIO,
                "drop_ratio_threshold": _S06_DROP_RATIO,
                "confidence": _cap("MED", confidence_cap),
            })

    rows.append(_reconcile_seasonality(anomalies, wordstat_monthly, wordstat_available, confidence_cap))

    common.write_metric_artifact(metrics_dir, "s06", rows, confidence_cap=confidence_cap)


# ── S07 — коммерческий спрос без релевантной посадочной ─────────────────────
# Материальность кластера спроса — суммарный wordstat.count фразы за весь
# период (не единичные показы недели); тот же порядок величины, что
# _MIN_SHOWS_FOR_OPPORTUNITY (общий принцип модуля, независимая константа —
# блоки/проверки этого файла не делят пороги между собой).
# Порог для S07 читается из config/defaults.yaml (block4_seo.s07_min_demand_count) —
# эта константа остаётся только фолбэком, если ключ не задан (FIX-s07-site-
# pages-join, п.3 промта: "не оставлять магическим числом в block4_seo.py").
# S26 переиспользует эту же константу напрямую (не через defaults) — S26 вне
# scope этого фикса, поведение/источник порога для S26 не менялся.
_S07_MIN_DEMAND_COUNT = 20


def _wordstat_gap_demand(con: Any) -> list[dict[str, Any]]:
    """[{normalized_phrase, phrase, demand_total}, ...] по фразам scope=='gap-specific'.

    scope=='gap-specific' — реальный коммерческий спрос за вычетом junk и
    general (классификация уже выполнена в extract, см. src/extract/
    wordstat.py:_add_candidate/_merge_gap_candidates — комментарий там прямо
    называет этот отбор "S07"). demand_total — сумма wordstat.count по всем
    неделям окна на фразу (MIN(phrase) — детерминированный представитель
    написания фразы для группы, все строки группы делят один normalized_phrase).
    """
    rows = con.execute(
        "SELECT normalized_phrase, MIN(phrase), SUM(count) FROM wordstat "
        "WHERE scope = 'gap-specific' GROUP BY normalized_phrase ORDER BY normalized_phrase"
    ).fetchall()
    return [
        {"normalized_phrase": np, "phrase": ph, "demand_total": int(dt or 0)}
        for np, ph, dt in rows
    ]


def _seo_known_query_set(con: Any) -> set[str]:
    """Множество seo_queries.query, нормализованных тем же normalize(), что и

    wordstat.normalized_phrase (src/extract/wordstat_config.normalize —
    единая точка сравнения текста запросов, не дублируем правило второй копией).
    """
    rows = con.execute("SELECT DISTINCT query FROM seo_queries").fetchall()
    return {WC.normalize(q) for (q,) in rows if q}


def _gap_demand_candidates(
    con: Any, min_demand: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(все материальные кластеры, кластеры без совпадения в seo_queries.query).

    "Совпадение" здесь — ТОЛЬКО текстовое: normalize(phrase) кластера
    буквально равен normalize() какого-то query из seo_queries (объединённый
    GSC+Вебмастер, см. _aggregate_query_page). Поле называется
    has_matching_query, а не has_matching_page (см. AUDIT-s07-s26-formula-
    match, 2026-07-29: старое имя has_matching_page утверждало про
    существование релевантной страницы то, чего эта функция не проверяет —
    только совпадение с уже проранжированным запросом). Проверку "есть ли
    РЕЛЕВАНТНАЯ СТРАНИЦА" через canonical["site_pages"] делает вызывающая
    сторона (см. _run_s07/_phrase_matches_site_page) — S26 её сознательно не
    делает (geo_dimension_available=false, отдельная задача вне этого фикса).
    """
    demand = _wordstat_gap_demand(con)
    known_queries = _seo_known_query_set(con)
    clusters = [c for c in demand if c["demand_total"] >= min_demand]
    for c in clusters:
        c["has_matching_query"] = c["normalized_phrase"] in known_queries
    gap_candidates = [c for c in clusters if not c["has_matching_query"]]
    return clusters, gap_candidates


# ── S07: сопоставление кластера с картой страниц (canonical["site_pages"]) ──
def _normalize_words(text: str | None) -> set[str]:
    """normalize(text) -> множество слов (WC.normalize схлопывает регистр и

    пробелы, split() токенизирует) — единица сравнения для текстового
    пересечения S07 (см. _phrase_matches_site_page).
    """
    if not text:
        return set()
    return set(WC.normalize(text).split())


def _url_path_word_source(path: str) -> str:
    """URL-путь -> текст со словами вместо разделителей slug (/, -, _), чтобы

    сегменты вида "/arenda-avto/" участвовали в текстовом пересечении наравне
    с title/h1.
    """
    return re.sub(r"[/_-]+", " ", path or "")


def _site_page_word_sets(canonical: dict[str, Path], paths: Any) -> list[set[str]]:
    """[{слова title+h1+url-путь}, ...] — один набор слов на страницу site_pages.

    Переиспользует _load_site_titles (тот же паттерн доступа к title/h1, что
    уже используется в модуле для S08/S25) — не дублируем чтение site_pages
    второй SQL-выборкой.
    """
    titles = _load_site_titles(canonical, paths)
    out: list[set[str]] = []
    for path, info in titles.items():
        words = (
            _normalize_words(info.get("title"))
            | _normalize_words(info.get("h1"))
            | _normalize_words(_url_path_word_source(path))
        )
        out.append(words)
    return out


def _phrase_matches_site_page(normalized_phrase: str, page_word_sets: list[set[str]]) -> bool:
    """True, если ВСЕ слова кластера встречаются на какой-то одной странице

    (title, h1 или URL-путь) — простое текстовое пересечение множеств слов,
    без учёта порядка слов и без словоформ/семантики (каталог v2, строка 263,
    не даёт более точной формулы сопоставления — не придумываем сверх
    "простого текстового пересечения", промт задачи FIX-s07-site-pages-join).
    """
    phrase_words = set(normalized_phrase.split()) if normalized_phrase else set()
    if not phrase_words:
        return False
    return any(phrase_words <= words for words in page_word_sets if words)


_S07_SITE_PAGES_UNAVAILABLE_REASON = (
    "ядро не посчитано: нет карты страниц — canonical[\"site_pages\"] "
    "недоступна или пуста (site_crawl не выполнен) — формула S07 (каталог "
    "v2 §9, строка 263: \"Сопоставить кластеры Wordstat/GSC с картой "
    "страниц\", источник \"Wordstat + GSC + сайт\") требует сайт как третий "
    "источник, сопоставить коммерческий спрос с реальными страницами нечем"
)


def _run_s07(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """requires=[wordstat, seo_queries], optional=[site_crawl]. Кластер спроса —

    фраза Wordstat scope=='gap-specific' (реальный коммерческий спрос, не
    бренд/не мусор). "Релевантная посадочная" проверяется ДВУМЯ независимыми
    сигналами (AUDIT-s07-s26-formula-match, 2026-07-29 — прежняя query-only
    логика не соответствовала формуле каталога "сопоставить с картой
    страниц"):
      1) has_matching_query — фраза (после normalize()) буквально совпадает с
         каким-то query из seo_queries (_gap_demand_candidates, старая логика);
      2) has_matching_page — на какой-то странице canonical["site_pages"] ВСЕ
         слова фразы встречаются в title, h1 или URL-пути (простое текстовое
         пересечение множеств слов, см. _phrase_matches_site_page).
    Находка (commercial_demand_without_landing_page) — материальный кластер
    (SUM(count) >= min_demand) без совпадения НИ ПО ОДНОМУ из двух сигналов.
    Кластер, у которого есть страница (has_matching_page), но нет query
    (страница существует, но не ранжируется ни по одному запросу кластера) —
    НЕ находка этой проверки: страница релевантна намерению, каталог просит
    искать именно ОТСУТСТВИЕ страницы, а не проблему ранжирования уже
    существующей (та проблема — предмет S02/S03/S09).
    Материальность SUM(count) — config/defaults.yaml: block4_seo.
    s07_min_demand_count (см. комментарий там же, ссылка на каталог, строка
    263); _S07_MIN_DEMAND_COUNT остаётся фолбэком, если ключ не задан.
    Без canonical["wordstat"] ИЛИ без canonical["site_pages"] — unavailable,
    данных нет, не придумываем (CLAUDE.md, протокол микрозадач п.5).
    """
    if not ("wordstat" in canonical and _table_nonempty(canonical["wordstat"])):
        _write_unavailable(
            metrics_dir, "S07",
            "ядро не посчитано: источник wordstat не готов — wordstat.parquet "
            "не строится в canonical-слое (src/extract/wordstat.py объявляет "
            "canonical_tables=['wordstat'], но build_canonical.py эту таблицу "
            "не собирает) — сопоставить коммерческий спрос с картой страниц нечем",
        )
        return

    if not ("site_pages" in canonical and _table_nonempty(canonical["site_pages"])):
        _write_unavailable(metrics_dir, "S07", _S07_SITE_PAGES_UNAVAILABLE_REASON)
        return

    min_demand = int(
        ((defaults or {}).get("block4_seo") or {}).get(
            "s07_min_demand_count", _S07_MIN_DEMAND_COUNT
        )
    )
    page_word_sets = _site_page_word_sets(canonical, paths)

    con = common.open_duckdb(paths)
    try:
        clusters, query_gap_candidates = _gap_demand_candidates(con, min_demand)
    finally:
        con.close()

    gap_candidates: list[dict[str, Any]] = []
    for c in query_gap_candidates:
        c["has_matching_page"] = _phrase_matches_site_page(c["normalized_phrase"], page_word_sets)
        if not c["has_matching_page"]:
            gap_candidates.append(c)

    rows: list[dict[str, Any]] = [{
        "check_id": "S07",
        "finding": "summary",
        "clusters_evaluated": len(clusters),
        "query_gap_candidate_count": len(query_gap_candidates),
        "gap_candidate_count": len(gap_candidates),
        "min_demand_threshold": min_demand,
        "match_method": (
            "has_matching_query: normalize(phrase) exact match against "
            "seo_queries.query; has_matching_page: all normalize()d phrase "
            "words present in some site_pages title/h1/url-path (word-set "
            "intersection)"
        ),
        "confidence": _cap("MED", confidence_cap),
    }]
    for c in sorted(gap_candidates, key=lambda x: -x["demand_total"]):
        rows.append({
            "check_id": "S07",
            "finding": "commercial_demand_without_landing_page",
            "phrase": c["phrase"],
            "normalized_phrase": c["normalized_phrase"],
            "demand_total": c["demand_total"],
            "min_demand_threshold": min_demand,
            "has_matching_query": c["has_matching_query"],
            "has_matching_page": c["has_matching_page"],
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "s07", rows, confidence_cap=confidence_cap)


# ── S08 — страница не соответствует намерению запроса ───────────────────────
def _run_s08(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    min_sample = int(defaults.get("min_sample_visits", 500))
    con = common.open_duckdb(paths)
    try:
        overall = con.execute(
            "SELECT page, SUM(total_shows), SUM(total_clicks) FROM seo_queries GROUP BY page ORDER BY page"
        ).fetchall()
        by_device = con.execute(
            "SELECT page, device, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            f"WHERE {_exclude_unknown_device_sql()} GROUP BY page, device ORDER BY page, device"
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
        usable_gsc_pages = con.execute(
            "SELECT COUNT(*) FROM seo_queries "
            "WHERE source = 'gsc' "
            "AND NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL"
        ).fetchone()[0]
        if not usable_gsc_pages:
            common.write_metric_artifact(metrics_dir, "s09", [{
                "check_id": "S09",
                "status": "manual_required",
                "reason": "S09 требует GSC page-dimension: все релевантные GSC page пусты",
                "confidence": _cap("MED", confidence_cap),
            }], confidence_cap=confidence_cap)
            return
        overall = con.execute(
            "SELECT query, page, SUM(total_shows) FROM seo_queries "
            "WHERE NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL "
            "GROUP BY query, page ORDER BY query, page"
        ).fetchall()
        by_device = con.execute(
            "SELECT query, device, page, SUM(total_shows) FROM seo_queries "
            "WHERE NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL "
            f"AND {_exclude_unknown_device_sql()} GROUP BY query, device, page ORDER BY query, device, page"
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
            "FROM seo_queries GROUP BY query, page ORDER BY query, page"
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


# ── S11 — важные страницы закрыты robots.txt/noindex ────────────────────────
def _run_s11(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """robots/noindex-компонент S11 (см. докстринг модуля, разрыв 6 — компонент

    "недоступный рендеринг" не реализуется здесь).
    """
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S11",
            "site_pages недоступна (site_crawl не выполнен) — директивы "
            "robots и статус sitemap проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    candidate_count = 0
    for path, info in sorted(site_pages.items()):
        if not _robots_blocks_indexing(info["robots_directive"]):
            continue
        shows, clicks = shows_by_path.get(path, (0, 0))
        important = bool(info["in_sitemap"]) or shows >= _S11_MIN_SHOWS_FOR_IMPORTANT
        if not important:
            continue
        candidate_count += 1
        rows.append({
            "check_id": "S11",
            "finding": "robots_blocks_important_page",
            "page": info["url"] or path,
            "robots_directive": info["robots_directive"],
            "in_sitemap": info["in_sitemap"],
            "total_shows": shows,
            "total_clicks": clicks,
            "min_shows_threshold": _S11_MIN_SHOWS_FOR_IMPORTANT,
            "js_rendering_component_implemented": False,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S11",
        "finding": "summary",
        "candidate_count": candidate_count,
        "crawled_url_count": len(site_pages),
        "min_shows_threshold": _S11_MIN_SHOWS_FOR_IMPORTANT,
        "js_rendering_component_implemented": False,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s11", rows, confidence_cap=confidence_cap)


# ── S12 — canonical указывает на неверную страницу ──────────────────────────
def _run_s12(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S12",
            "site_pages недоступна (site_crawl не выполнен) — canonical "
            "страниц проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    candidate_count = 0
    for path, info in sorted(site_pages.items()):
        canonical_path = info["canonical_path"]
        if not canonical_path or canonical_path == path:
            continue
        shows, clicks = shows_by_path.get(path, (0, 0))
        if shows < _S12_MIN_SHOWS_FOR_CHECK:
            continue
        candidate_count += 1
        rows.append({
            "check_id": "S12",
            "finding": "canonical_points_elsewhere",
            "page": info["url"] or path,
            "canonical_url": info["canonical_url"],
            "total_shows": shows,
            "total_clicks": clicks,
            "min_shows_threshold": _S12_MIN_SHOWS_FOR_CHECK,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S12",
        "finding": "summary",
        "candidate_count": candidate_count,
        "crawled_url_count": len(site_pages),
        "min_shows_threshold": _S12_MIN_SHOWS_FOR_CHECK,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s12", rows, confidence_cap=confidence_cap)


# ── S13 — sitemap неполный/устаревший/с ошибочными URL ──────────────────────
def _run_s13(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S13",
            "site_pages недоступна (site_crawl не выполнен) — статус "
            "sitemap проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    missing_from_sitemap: list[dict[str, Any]] = []
    broken_in_sitemap: list[dict[str, Any]] = []
    for path, info in sorted(site_pages.items()):
        shows, clicks = shows_by_path.get(path, (0, 0))
        if info["in_sitemap"] is False and shows >= _S13_MIN_SHOWS_FOR_CHECK:
            missing_from_sitemap.append({
                "check_id": "S13",
                "finding": "traffic_page_missing_from_sitemap",
                "page": info["url"] or path,
                "total_shows": shows,
                "total_clicks": clicks,
                "min_shows_threshold": _S13_MIN_SHOWS_FOR_CHECK,
                "confidence": _cap("MED", confidence_cap),
            })
        if (info["in_sitemap"] and info["http_status"] is not None
                and info["http_status"] >= _S14_BAD_STATUS_MIN):
            broken_in_sitemap.append({
                "check_id": "S13",
                "finding": "sitemap_contains_broken_url",
                "page": info["url"] or path,
                "http_status": info["http_status"],
                "bad_status_threshold": _S14_BAD_STATUS_MIN,
                "confidence": _cap("MED", confidence_cap),
            })

    rows: list[dict[str, Any]] = [{
        "check_id": "S13",
        "finding": "summary",
        "traffic_pages_missing_from_sitemap": len(missing_from_sitemap),
        "sitemap_broken_urls": len(broken_in_sitemap),
        "crawled_url_count": len(site_pages),
        "min_shows_threshold": _S13_MIN_SHOWS_FOR_CHECK,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    }]
    rows.extend(missing_from_sitemap)
    rows.extend(broken_in_sitemap)

    common.write_metric_artifact(metrics_dir, "s13", rows, confidence_cap=confidence_cap)


# ── S14 — органический трафик ведёт на 404/soft 404/удалённые страницы ─────
def _run_s14(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S14",
            "site_pages недоступна (site_crawl не выполнен) — HTTP-статусы "
            "органических посадочных проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    broken_count = 0
    for path, (shows, clicks) in sorted(shows_by_path.items()):
        if shows <= 0 and clicks <= 0:
            continue
        info = site_pages.get(path)
        if info is None or info["http_status"] is None or info["http_status"] < _S14_BAD_STATUS_MIN:
            continue
        broken_count += 1
        rows.append({
            "check_id": "S14",
            "finding": "organic_traffic_to_broken_page",
            "page": info["url"] or path,
            "http_status": info["http_status"],
            "total_shows": shows,
            "total_clicks": clicks,
            "bad_status_threshold": _S14_BAD_STATUS_MIN,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S14",
        "finding": "summary",
        "broken_page_count": broken_count,
        "crawled_url_count": len(site_pages),
        "bad_status_threshold": _S14_BAD_STATUS_MIN,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s14", rows, confidence_cap=confidence_cap)


# ── S15 — цепочки и массовые редиректы размывают сигнал ─────────────────────
def _run_s15(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S15",
            "site_pages недоступна (site_crawl не выполнен) — цепочки "
            "редиректов проверить нечем",
        )
        return

    has_seo = "seo_queries" in canonical and _table_nonempty(canonical["seo_queries"])
    shows_by_path: dict[str, tuple[int, int]] = {}
    if has_seo:
        con = common.open_duckdb(paths)
        try:
            shows_by_path = _seo_shows_clicks_by_path(con)
        finally:
            con.close()

    rows: list[dict[str, Any]] = []
    excessive_count = 0
    for path, info in sorted(site_pages.items()):
        hops = info["redirect_hops"]
        if hops < 1:
            continue
        excessive = hops >= _S15_MIN_CHAIN_HOPS_FOR_FINDING
        if excessive:
            excessive_count += 1
        shows, clicks = shows_by_path.get(path, (0, 0))
        rows.append({
            "check_id": "S15",
            "finding": "redirect_chain",
            "page": info["url"] or path,
            "final_url": info["final_url"],
            "redirect_hops": hops,
            "min_hops_for_finding": _S15_MIN_CHAIN_HOPS_FOR_FINDING,
            "excessive_redirect_chain": excessive,
            "total_shows": shows if has_seo else None,
            "total_clicks": clicks if has_seo else None,
            "seo_queries_available": has_seo,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S15",
        "finding": "summary",
        "excessive_redirect_chain_count": excessive_count,
        "crawled_url_count": len(site_pages),
        "min_hops_for_finding": _S15_MIN_CHAIN_HOPS_FOR_FINDING,
        "seo_queries_available": has_seo,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s15", rows, confidence_cap=confidence_cap)


# ── S16 — индекс раздут дублями/параметрами/тонкими страницами ─────────────
def _run_s16(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S16",
            "site_pages недоступна (site_crawl не выполнен) — сравнить "
            "известные/индексируемые/полезные URL нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    duplicate_targets: dict[str, set[str]] = {}
    for path, info in site_pages.items():
        target = info["canonical_path"] or path
        if target == path:
            continue
        duplicate_targets.setdefault(target, set()).add(path)

    duplicate_rows: list[dict[str, Any]] = []
    for target, sources in sorted(duplicate_targets.items()):
        if len(sources) < 2:
            continue
        duplicate_rows.append({
            "check_id": "S16",
            "finding": "duplicate_cluster",
            "canonical_target": target,
            "duplicate_source_count": len(sources),
            "duplicate_sources": sorted(sources),
            "confidence": _cap("MED", confidence_cap),
        })

    indexable_count = sum(
        1 for info in site_pages.values()
        if info["http_status"] == 200 and not _robots_blocks_indexing(info["robots_directive"])
    )
    pages_with_shows = sum(1 for path in site_pages if shows_by_path.get(path, (0, 0))[0] > 0)

    rows: list[dict[str, Any]] = [{
        "check_id": "S16",
        "finding": "summary",
        "crawled_url_count": len(site_pages),
        "indexable_url_count": indexable_count,
        "pages_with_organic_shows": pages_with_shows,
        "duplicate_cluster_count": len(duplicate_rows),
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    }]
    rows.extend(duplicate_rows)

    common.write_metric_artifact(metrics_dir, "s16", rows, confidence_cap=confidence_cap)


# ── S17 — title/description/H1 отсутствуют/дублируются/не по спросу ────────
def _run_s17(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S17",
            "site_pages недоступна (site_crawl не выполнен) — title/"
            "description/H1 проверить нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    missing_rows: list[dict[str, Any]] = []
    title_pages: dict[str, list[str]] = {}
    for path, info in sorted(site_pages.items()):
        shows, clicks = shows_by_path.get(path, (0, 0))
        if shows < _S17_MIN_SHOWS_FOR_CHECK:
            continue
        missing_fields = [
            field for field in ("title", "description", "h1")
            if not (info.get(field) or "").strip()
        ]
        if missing_fields:
            missing_rows.append({
                "check_id": "S17",
                "finding": "missing_metadata",
                "page": info["url"] or path,
                "missing_fields": missing_fields,
                "total_shows": shows,
                "total_clicks": clicks,
                "min_shows_threshold": _S17_MIN_SHOWS_FOR_CHECK,
                "confidence": _cap("MED", confidence_cap),
            })
        title = (info.get("title") or "").strip()
        if title:
            title_pages.setdefault(title, []).append(path)

    duplicate_rows: list[dict[str, Any]] = []
    for title, paths_list in sorted(title_pages.items()):
        if len(paths_list) < 2:
            continue
        duplicate_rows.append({
            "check_id": "S17",
            "finding": "duplicate_title",
            "title": title,
            "pages": sorted(paths_list),
            "confidence": _cap("MED", confidence_cap),
        })

    rows: list[dict[str, Any]] = [{
        "check_id": "S17",
        "finding": "summary",
        "missing_metadata_count": len(missing_rows),
        "duplicate_title_count": len(duplicate_rows),
        "crawled_url_count": len(site_pages),
        "min_shows_threshold": _S17_MIN_SHOWS_FOR_CHECK,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    }]
    rows.extend(missing_rows)
    rows.extend(duplicate_rows)

    common.write_metric_artifact(metrics_dir, "s17", rows, confidence_cap=confidence_cap)


# ── S18 — важные страницы имеют мало внутренних ссылок или являются сиротами
def _run_s18(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    site_pages = _load_site_pages_full(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S18",
            "site_pages недоступна (site_crawl не выполнен) — внутренний "
            "граф ссылок проверить нечем",
        )
        return

    edges = _load_link_graph(canonical, paths)
    if not edges:
        _write_unavailable(
            metrics_dir, "S18",
            "site_link_graph недоступна (link_graph.parquet не построен — "
            "BFS не дал рёбер либо не выполнялся, см. src/extract/"
            "site_crawl.py) — внутренние ссылки страниц проверить нечем",
        )
        return

    inbound = _inbound_link_counts(edges)

    rows: list[dict[str, Any]] = []
    orphan_count = 0
    low_inlink_count = 0
    for path in sorted(site_pages):
        if path == "/":
            continue
        count = inbound.get(path, 0)
        is_orphan = count == 0
        is_low = 0 < count < _S18_LOW_INLINK_THRESHOLD
        if is_orphan:
            orphan_count += 1
        if is_low:
            low_inlink_count += 1
        if not (is_orphan or is_low):
            continue
        rows.append({
            "check_id": "S18",
            "finding": "orphan_page" if is_orphan else "low_inlink_page",
            "page": path,
            "inbound_internal_link_count": count,
            "low_inlink_threshold": _S18_LOW_INLINK_THRESHOLD,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S18",
        "finding": "summary",
        "orphan_page_count": orphan_count,
        "low_inlink_page_count": low_inlink_count,
        "crawled_url_count": len(site_pages),
        "low_inlink_threshold": _S18_LOW_INLINK_THRESHOLD,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s18", rows, confidence_cap=confidence_cap)


# ── S19 — архитектура сайта требует слишком много кликов до коммерции ──────
def _run_s19(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    edges = _load_link_graph(canonical, paths)
    if not edges:
        _write_unavailable(
            metrics_dir, "S19",
            "site_link_graph недоступна (link_graph.parquet не построен — "
            "BFS не дал рёбер либо не выполнялся, см. src/extract/"
            "site_crawl.py) — глубину от главной проверить нечем",
        )
        return

    has_visits = "visits" in canonical and _table_nonempty(canonical["visits"])
    organic_context: dict[str, dict[str, Any]] = {}
    if has_visits:
        con = common.open_duckdb(paths)
        try:
            organic_rows = con.execute(
                "SELECT entry_page, COUNT(*), "
                "COUNT(*) FILTER (WHERE form_open OR form_submit OR call_click OR messenger_click) "
                "FROM visits WHERE source_group = 'organic' GROUP BY entry_page ORDER BY entry_page"
            ).fetchall()
        finally:
            con.close()
        for entry_page, total, engaged in organic_rows:
            path = _url_path(entry_page)
            total = int(total or 0)
            engaged = int(engaged or 0)
            organic_context[path] = {
                "organic_visits": total,
                "organic_engagement_rate": round(engaged / total, 4) if total else None,
            }

    depth_by_page = _min_depth_by_page(edges)

    rows: list[dict[str, Any]] = []
    deep_count = 0
    for path, depth in sorted(depth_by_page.items()):
        deep = depth >= _S19_DEEP_THRESHOLD
        if deep:
            deep_count += 1
        if not deep:
            continue
        row: dict[str, Any] = {
            "check_id": "S19",
            "finding": "page_too_deep",
            "page": path,
            "depth_from_home": depth,
            "deep_threshold": _S19_DEEP_THRESHOLD,
        }
        row.update(organic_context.get(path, {}))
        row["confidence"] = _cap("MED", confidence_cap)
        rows.append(row)

    rows.insert(0, {
        "check_id": "S19",
        "finding": "summary",
        "pages_evaluated": len(depth_by_page),
        "deep_page_count": deep_count,
        "deep_threshold": _S19_DEEP_THRESHOLD,
        "visits_available": has_visits,
        "commercial_classification_available": False,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s19", rows, confidence_cap=confidence_cap)


# ── S20 — мобильная производительность и CWV ухудшают органическую конверсию
def _run_s20(
    paths: Any, defaults: dict[str, Any], canonical: dict[str, Path],
    confidence_cap: str, metrics_dir: Path,
) -> None:
    """Источник CWV — тот же приём, что C01/C02 в block3.py (см. докстринг

    модуля, разрыв 7). "CrUX empty -> только manual lab data с MED cap" —
    прямое требование промта задачи 5bB.
    """
    manual_cap_enabled = bool(defaults.get("crux_min_field_data", True))
    raw = _read_crux_raw(paths)

    con = common.open_duckdb(paths)
    try:
        mobile_seo = con.execute(
            "SELECT SUM(total_shows), SUM(total_clicks), "
            "SUM(avg_show_position * total_shows) FILTER (WHERE avg_show_position IS NOT NULL), "
            "SUM(total_shows) FILTER (WHERE avg_show_position IS NOT NULL) "
            "FROM seo_queries WHERE device = 'mobile'"
        ).fetchone()
        has_visits = "visits" in canonical and _table_nonempty(canonical["visits"])
        organic_by_device = _organic_visit_context_by_device(con) if has_visits else {}
    finally:
        con.close()

    mobile_shows, mobile_clicks, pos_w, shows_pos = mobile_seo
    mobile_shows = int(mobile_shows or 0)
    mobile_clicks = int(mobile_clicks or 0)
    mobile_avg_position = (pos_w / shows_pos) if (pos_w is not None and shows_pos) else None

    rows: list[dict[str, Any]] = [{
        "check_id": "S20",
        "finding": "mobile_seo_context",
        "mobile_total_shows": mobile_shows,
        "mobile_total_clicks": mobile_clicks,
        "mobile_avg_position": round(mobile_avg_position, 2) if mobile_avg_position is not None else None,
        "confidence": _cap("MED", confidence_cap),
    }]

    mobile_engagement = organic_by_device.get("mobile")
    desktop_engagement = organic_by_device.get("desktop")
    if mobile_engagement and desktop_engagement:
        both_material = (
            mobile_engagement["visits"] >= _S20_MIN_VISITS_FOR_DEVICE_COMPARISON
            and desktop_engagement["visits"] >= _S20_MIN_VISITS_FOR_DEVICE_COMPARISON
        )
        gap_ratio = None
        mobile_worse = False
        if (both_material and desktop_engagement["engagement_rate"]
                and mobile_engagement["engagement_rate"] is not None):
            gap_ratio = mobile_engagement["engagement_rate"] / desktop_engagement["engagement_rate"]
            mobile_worse = gap_ratio <= _S20_MOBILE_ENGAGEMENT_GAP_RATIO
        rows.append({
            "check_id": "S20",
            "finding": "mobile_vs_desktop_organic_engagement",
            "mobile_visits": mobile_engagement["visits"],
            "mobile_engagement_rate": mobile_engagement["engagement_rate"],
            "desktop_visits": desktop_engagement["visits"],
            "desktop_engagement_rate": desktop_engagement["engagement_rate"],
            "engagement_ratio_mobile_to_desktop": round(gap_ratio, 3) if gap_ratio is not None else None,
            "gap_ratio_threshold": _S20_MOBILE_ENGAGEMENT_GAP_RATIO,
            "min_visits_threshold": _S20_MIN_VISITS_FOR_DEVICE_COMPARISON,
            "material_sample": both_material,
            "mobile_engagement_significantly_worse": bool(mobile_worse),
            "confidence": _cap("MED", confidence_cap),
        })

    if raw and raw.get("cwv_field_data_available"):
        for record in raw.get("records") or []:
            if not record.get("field_data_available"):
                continue
            p75 = record.get("p75") or {}
            ratings = {f"{name}_rating": _rate_cwv_metric(name, value) for name, value in p75.items()}
            rows.append({
                "check_id": "S20",
                "finding": "field_cwv",
                "target_type": record.get("target_type"),
                "target": record.get("target"),
                **p75,
                **ratings,
                "any_metric_poor": any(v == "poor" for v in ratings.values()),
                "device_specific": False,
                "device_specific_note": (
                    "CrUX-запрос не фильтрует по formFactor (см. src/extract/"
                    "crux.py, тот же прецедент, что C01 в block3.py) — p75 "
                    "агрегирован по всем устройствам, не только мобильным."
                ),
                "source": "crux_field",
                "confidence": _cap("MED", confidence_cap),
            })
        common.write_metric_artifact(metrics_dir, "s20", rows, confidence_cap=confidence_cap)
        return

    inputs = common.load_inputs(paths)
    manual = inputs.get("manual_cwv")
    if _yaml_populated(manual, ("tested_at",)):
        cap_level = "MED" if manual_cap_enabled else "LOW"
        for pattern in (manual or {}).get("patterns") or []:
            if not isinstance(pattern, dict):
                continue
            ratings = {
                "lcp_rating": _rate_cwv_metric("largest_contentful_paint", pattern.get("lcp_ms")),
                "cls_rating": _rate_cwv_metric("cumulative_layout_shift", pattern.get("cls")),
                "inp_rating": _rate_cwv_metric("interaction_to_next_paint", pattern.get("inp_ms")),
            }
            rows.append({
                "check_id": "S20",
                "finding": "manual_lab_cwv",
                "device": (manual.get("meta") or {}).get("device"),
                **pattern, **ratings,
                "source": "manual_lab",
                "confidence": _cap(cap_level, confidence_cap),
            })
        common.write_metric_artifact(metrics_dir, "s20", rows, confidence_cap=confidence_cap)
        return

    rows.append({
        "check_id": "S20",
        "finding": "cwv_unavailable",
        "reason": (
            "нет ни полевых данных CrUX (data/raw/crux/crux.json отсутствует "
            "или cwv_field_data_available=false), ни ручного лабораторного "
            "замера (inputs/manual_cwv.yaml не заполнен — meta.tested_at пуст)"
        ),
        "confidence": _cap("LOW", confidence_cap),
    })
    common.write_metric_artifact(metrics_dir, "s20", rows, confidence_cap=confidence_cap)


# ── S21 — Яндекс и Google показывают противоположную картину (легаси 5.4) ───
def _run_s21(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Сравнение агрегировано по странице за всё окно (не помесячно, см.

    докстринг модуля, разрыв 10 — Вебмастер отдаёт один снимок на всё окно,
    помесячный тренд для него не существует).
    """
    con = common.open_duckdb(paths)
    try:
        rows_raw = con.execute(
            "SELECT page, source, SUM(total_shows) AS shows, SUM(total_clicks) AS clicks, "
            "SUM(avg_show_position * total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS pos_w, "
            "SUM(total_shows) FILTER (WHERE avg_show_position IS NOT NULL) AS shows_pos "
            "FROM seo_queries GROUP BY page, source ORDER BY page, source"
        ).fetchall()
    finally:
        con.close()

    by_page: dict[str, dict[str, dict[str, Any]]] = {}
    for page, source, shows, clicks, pos_w, shows_pos in rows_raw:
        path = _url_path(page)
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        shows_pos = int(shows_pos or 0)
        position = (pos_w / shows_pos) if (pos_w is not None and shows_pos > 0) else None
        by_page.setdefault(path, {})[source] = {"shows": shows, "clicks": clicks, "position": position}

    rows: list[dict[str, Any]] = []
    divergent_count = 0
    for path, sources in sorted(by_page.items()):
        gsc = sources.get("gsc")
        webmaster = sources.get("webmaster")
        if not gsc or not webmaster:
            continue
        if gsc["shows"] < _S21_MIN_SHOWS_FOR_COMPARISON or webmaster["shows"] < _S21_MIN_SHOWS_FOR_COMPARISON:
            continue

        gsc_ctr = (gsc["clicks"] / gsc["shows"]) if gsc["shows"] else None
        wm_ctr = (webmaster["clicks"] / webmaster["shows"]) if webmaster["shows"] else None
        position_gap = None
        if gsc["position"] is not None and webmaster["position"] is not None:
            position_gap = abs(gsc["position"] - webmaster["position"])
        ctr_ratio = None
        if gsc_ctr and wm_ctr:
            ctr_ratio = max(gsc_ctr, wm_ctr) / min(gsc_ctr, wm_ctr)

        divergent = bool(
            (position_gap is not None and position_gap >= _S21_POSITION_GAP_THRESHOLD)
            or (ctr_ratio is not None and ctr_ratio >= _S21_CTR_RATIO_THRESHOLD)
        )
        if divergent:
            divergent_count += 1
        rows.append({
            "check_id": "S21",
            "finding": "cross_system_divergence",
            "page": path,
            "gsc_shows": gsc["shows"],
            "gsc_clicks": gsc["clicks"],
            "gsc_position": round(gsc["position"], 2) if gsc["position"] is not None else None,
            "gsc_ctr": round(gsc_ctr, 4) if gsc_ctr is not None else None,
            "webmaster_shows": webmaster["shows"],
            "webmaster_clicks": webmaster["clicks"],
            "webmaster_position": round(webmaster["position"], 2) if webmaster["position"] is not None else None,
            "webmaster_ctr": round(wm_ctr, 4) if wm_ctr is not None else None,
            "position_gap": round(position_gap, 2) if position_gap is not None else None,
            "ctr_ratio": round(ctr_ratio, 3) if ctr_ratio is not None else None,
            "position_gap_threshold": _S21_POSITION_GAP_THRESHOLD,
            "ctr_ratio_threshold": _S21_CTR_RATIO_THRESHOLD,
            "cross_system_divergent": divergent,
            "confidence": _cap("MED", confidence_cap),
        })

    pages_compared = len(rows)
    rows.insert(0, {
        "check_id": "S21",
        "finding": "summary",
        "pages_compared": pages_compared,
        "divergent_page_count": divergent_count,
        "min_shows_threshold": _S21_MIN_SHOWS_FOR_COMPARISON,
        "position_gap_threshold": _S21_POSITION_GAP_THRESHOLD,
        "ctr_ratio_threshold": _S21_CTR_RATIO_THRESHOLD,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s21", rows, confidence_cap=confidence_cap)


# ── S22 — контент получает органику, не переводит её в коммерческий раздел ──
def _run_s22(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Автоматическая часть пересекается с S08 по данным (см. докстринг модуля,

    разрыв 11) — здесь дополнительно считается доля кликов органики,
    оседающая на страницах без единой вовлечённости (site-level агрегат,
    которого нет в S08).
    """
    con = common.open_duckdb(paths)
    try:
        overall = con.execute(
            "SELECT page, SUM(total_shows), SUM(total_clicks) FROM seo_queries GROUP BY page ORDER BY page"
        ).fetchall()
        total_organic_clicks_row = con.execute(
            "SELECT SUM(total_clicks) FROM seo_queries"
        ).fetchone()
        organic_by_page = _organic_visits_by_page(con)
    finally:
        con.close()

    total_organic_clicks = int((total_organic_clicks_row or (0,))[0] or 0)
    site_titles = _load_site_titles(canonical, paths)

    rows: list[dict[str, Any]] = []
    dead_end_count = 0
    dead_end_clicks = 0
    for page, shows, clicks in overall:
        shows = int(shows or 0)
        clicks = int(clicks or 0)
        if shows < _S22_MIN_SHOWS_FOR_CHECK:
            continue
        path = _url_path(page)
        organic_visits, engaged = organic_by_page.get(path, (0, 0))
        if organic_visits < _S22_MIN_ORGANIC_VISITS_FOR_CHECK:
            continue
        no_conversion_path = engaged == 0
        if no_conversion_path:
            dead_end_count += 1
            dead_end_clicks += clicks
        context = site_titles.get(path, {})
        rows.append({
            "check_id": "S22",
            "finding": "organic_page_without_conversion_path",
            "page": page,
            "total_shows": shows,
            "total_clicks": clicks,
            "organic_visits": organic_visits,
            "organic_engaged_visits": engaged,
            "no_conversion_path": bool(no_conversion_path),
            "page_title": context.get("title"),
            "page_h1": context.get("h1"),
            "page_classification_available": False,
            "min_shows_threshold": _S22_MIN_SHOWS_FOR_CHECK,
            "min_organic_visits_threshold": _S22_MIN_ORGANIC_VISITS_FOR_CHECK,
            "confidence": _cap("MED", confidence_cap),
        })

    dead_end_click_share = (dead_end_clicks / total_organic_clicks) if total_organic_clicks else None
    rows.insert(0, {
        "check_id": "S22",
        "finding": "summary",
        "pages_evaluated": len(rows),
        "dead_end_page_count": dead_end_count,
        "dead_end_clicks": dead_end_clicks,
        "total_organic_clicks": total_organic_clicks,
        "dead_end_click_share": round(dead_end_click_share, 4) if dead_end_click_share is not None else None,
        "page_classification_available": False,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s22", rows, confidence_cap=confidence_cap)


# ── S23 — органические посадочные конвертируют хуже сопоставимых страниц ───
def _organic_vs_other_by_page(con: Any) -> dict[str, tuple[int, int, int, int]]:
    """{normalized_path: (organic_visits, organic_engaged, other_visits, other_engaged)}.

    "Сопоставимая" группа — та же страница, другой трафик (source_group !=
    organic): контролирует саму страницу, не требует внешней классификации
    типа страницы (см. докстринг модуля, разрыв 11 — то же ограничение схемы).
    """
    rows = con.execute(
        "SELECT entry_page, "
        "COUNT(*) FILTER (WHERE source_group = 'organic') AS organic_visits, "
        "COUNT(*) FILTER (WHERE source_group = 'organic' AND "
        "(form_open OR form_submit OR call_click OR messenger_click)) AS organic_engaged, "
        "COUNT(*) FILTER (WHERE source_group != 'organic') AS other_visits, "
        "COUNT(*) FILTER (WHERE source_group != 'organic' AND "
        "(form_open OR form_submit OR call_click OR messenger_click)) AS other_engaged "
        "FROM visits GROUP BY entry_page ORDER BY entry_page"
    ).fetchall()
    out: dict[str, tuple[int, int, int, int]] = {}
    for entry_page, ov, oe, otv, ote in rows:
        path = _url_path(entry_page)
        prev_ov, prev_oe, prev_otv, prev_ote = out.get(path, (0, 0, 0, 0))
        out[path] = (
            prev_ov + int(ov or 0), prev_oe + int(oe or 0),
            prev_otv + int(otv or 0), prev_ote + int(ote or 0),
        )
    return out


def _organic_vs_other_by_page_device(con: Any) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    """{(normalized_path, device): (organic_visits, organic_engaged, other_visits, other_engaged)}.

    Тот же device-разрез, что S08/S09 (см. `_exclude_unknown_device_sql`) —
    единый фильтр, не отдельная копия условия. visits.device всегда конкретен
    (map_device по умолчанию -> "desktop"), поэтому фильтр здесь избыточен по
    факту, но применяется для единообразия конвенции блока (задача 5bC, промт:
    "S23/S24 используют device так же, как в 5bA").
    """
    rows = con.execute(
        "SELECT entry_page, device, "
        "COUNT(*) FILTER (WHERE source_group = 'organic') AS organic_visits, "
        "COUNT(*) FILTER (WHERE source_group = 'organic' AND "
        "(form_open OR form_submit OR call_click OR messenger_click)) AS organic_engaged, "
        "COUNT(*) FILTER (WHERE source_group != 'organic') AS other_visits, "
        "COUNT(*) FILTER (WHERE source_group != 'organic' AND "
        "(form_open OR form_submit OR call_click OR messenger_click)) AS other_engaged "
        f"FROM visits WHERE {_exclude_unknown_device_sql()} GROUP BY entry_page, device ORDER BY entry_page, device"
    ).fetchall()
    out: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for entry_page, device, ov, oe, otv, ote in rows:
        key = (_url_path(entry_page), device)
        prev_ov, prev_oe, prev_otv, prev_ote = out.get(key, (0, 0, 0, 0))
        out[key] = (
            prev_ov + int(ov or 0), prev_oe + int(oe or 0),
            prev_otv + int(otv or 0), prev_ote + int(ote or 0),
        )
    return out


def _run_s23(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    con = common.open_duckdb(paths)
    try:
        by_page = _organic_vs_other_by_page(con)
        by_page_device = _organic_vs_other_by_page_device(con)
        seo_shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    worse_count = 0
    for path, (ov, oe, otv, ote) in sorted(by_page.items()):
        if ov < _S23_MIN_VISITS_FOR_COMPARISON or otv < _S23_MIN_VISITS_FOR_COMPARISON:
            continue
        organic_rate = oe / ov
        other_rate = ote / otv
        ratio = (organic_rate / other_rate) if other_rate else None
        worse = ratio is not None and ratio <= _S23_ENGAGEMENT_GAP_RATIO
        if worse:
            worse_count += 1
        seo_shows, _ = seo_shows_by_path.get(path, (0, 0))
        rows.append({
            "check_id": "S23",
            "finding": "organic_underperforms_other_traffic",
            "page": path,
            "organic_visits": ov,
            "organic_engaged_visits": oe,
            "organic_engagement_rate": round(organic_rate, 4),
            "other_traffic_visits": otv,
            "other_traffic_engaged_visits": ote,
            "other_traffic_engagement_rate": round(other_rate, 4),
            "engagement_ratio_organic_to_other": round(ratio, 3) if ratio is not None else None,
            "seo_total_shows": seo_shows,
            "gap_ratio_threshold": _S23_ENGAGEMENT_GAP_RATIO,
            "min_visits_threshold": _S23_MIN_VISITS_FOR_COMPARISON,
            "organic_significantly_worse": bool(worse),
            "confidence": _cap("MED", confidence_cap),
        })

    worse_by_device_count = 0
    for (path, device), (ov, oe, otv, ote) in sorted(by_page_device.items()):
        if ov < _S23_MIN_VISITS_FOR_COMPARISON or otv < _S23_MIN_VISITS_FOR_COMPARISON:
            continue
        organic_rate = oe / ov
        other_rate = ote / otv
        ratio = (organic_rate / other_rate) if other_rate else None
        worse = ratio is not None and ratio <= _S23_ENGAGEMENT_GAP_RATIO
        if worse:
            worse_by_device_count += 1
        rows.append({
            "check_id": "S23",
            "finding": "organic_underperforms_other_traffic_by_device",
            "page": path,
            "device": device,
            "organic_visits": ov,
            "organic_engaged_visits": oe,
            "organic_engagement_rate": round(organic_rate, 4),
            "other_traffic_visits": otv,
            "other_traffic_engaged_visits": ote,
            "other_traffic_engagement_rate": round(other_rate, 4),
            "engagement_ratio_organic_to_other": round(ratio, 3) if ratio is not None else None,
            "gap_ratio_threshold": _S23_ENGAGEMENT_GAP_RATIO,
            "min_visits_threshold": _S23_MIN_VISITS_FOR_COMPARISON,
            "organic_significantly_worse": bool(worse),
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S23",
        "finding": "summary",
        "pages_evaluated": sum(1 for r in rows if r["finding"] == "organic_underperforms_other_traffic"),
        "pages_organic_worse": worse_count,
        "device_rows_evaluated": sum(
            1 for r in rows if r["finding"] == "organic_underperforms_other_traffic_by_device"
        ),
        "device_rows_organic_worse": worse_by_device_count,
        "min_visits_threshold": _S23_MIN_VISITS_FOR_COMPARISON,
        "gap_ratio_threshold": _S23_ENGAGEMENT_GAP_RATIO,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s23", rows, confidence_cap=confidence_cap)


def _s24_trend_candidate(
    months_map: dict[str, tuple[int, int]],
    organic_visits: int,
    engaged: int,
) -> dict[str, Any] | None:
    """Общая оценка тренда+вовлечённости для (страница) или (страница, device).

    Не содержит SQL/device-фильтрации (та живёт в вызывающих запросах, единый
    источник — `_exclude_unknown_device_sql`) — только арифметика, общая для
    overall и by_device веток S24, чтобы не дублировать пороговую логику.
    """
    months = sorted(months_map)
    if len(months) < 2:
        return None
    mid = len(months) // 2
    early_months, late_months = months[:mid], months[mid:]
    early_shows = sum(months_map[m][0] for m in early_months)
    early_clicks = sum(months_map[m][1] for m in early_months)
    late_clicks = sum(months_map[m][1] for m in late_months)
    if early_shows < _S24_MIN_SHOWS_FOR_TREND:
        return None
    if organic_visits < _S24_MIN_ORGANIC_VISITS_FOR_CHECK:
        return None

    click_ratio = (late_clicks / early_clicks) if early_clicks > 0 else None
    declining = click_ratio is not None and click_ratio <= _S24_DECLINE_CLICK_RATIO
    engagement_rate = engaged / organic_visits
    high_value = engagement_rate >= _S24_HIGH_ENGAGEMENT_RATE
    losing_visibility = bool(declining and high_value)

    return {
        "months_available": months,
        "early_clicks": early_clicks,
        "late_clicks": late_clicks,
        "click_ratio_late_to_early": round(click_ratio, 3) if click_ratio is not None else None,
        "decline_ratio_threshold": _S24_DECLINE_CLICK_RATIO,
        "organic_visits": organic_visits,
        "organic_engaged_visits": engaged,
        "organic_engagement_rate": round(engagement_rate, 4),
        "high_engagement_rate_threshold": _S24_HIGH_ENGAGEMENT_RATE,
        "page_declining": bool(declining),
        "high_value_page": bool(high_value),
        "losing_visibility_candidate": losing_visibility,
    }


# ── S24 — высококонверсионные SEO-страницы теряют видимость ────────────────
def _run_s24(paths: Any, confidence_cap: str, metrics_dir: Path) -> None:
    """Соединяет тренд S05 (падение показов/кликов по месяцам) с органической

    вовлечённостью страницы (та же вовлечённость, что S08/S22) — кандидат,
    только если страница ОДНОВРЕМЕННО теряет видимость И уже доказала свою
    коммерческую ценность (высокая вовлечённость), а не любая падающая страница.
    Device-разрез — та же конвенция, что S08/S09/S23 (единый фильтр
    `_exclude_unknown_device_sql`, промт задачи 5bC: "S23/S24 используют
    device так же, как в 5bA").
    """
    con = common.open_duckdb(paths)
    try:
        usable_gsc_page_history = con.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT page FROM seo_queries "
            "WHERE source = 'gsc' "
            "AND NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL "
            "GROUP BY page HAVING COUNT(DISTINCT month) >= 2"
            ")"
        ).fetchone()[0]
        if not usable_gsc_page_history:
            common.write_metric_artifact(metrics_dir, "s24", [{
                "check_id": "S24",
                "status": "manual_required",
                "reason": (
                    "S24 требует GSC page-dimension с минимум двумя месяцами visibility"
                ),
                "confidence": _cap("MED", confidence_cap),
            }], confidence_cap=confidence_cap)
            return
        by_page_month = con.execute(
            "SELECT page, month, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "WHERE source = 'gsc' "
            "AND NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL "
            "GROUP BY page, month ORDER BY page, month"
        ).fetchall()
        by_page_device_month = con.execute(
            "SELECT page, device, month, SUM(total_shows), SUM(total_clicks) FROM seo_queries "
            "WHERE source = 'gsc' "
            "AND NULLIF(TRIM(CAST(page AS VARCHAR)), '') IS NOT NULL "
            f"AND {_exclude_unknown_device_sql()} GROUP BY page, device, month ORDER BY page, device, month"
        ).fetchall()
        organic_by_page = _organic_visits_by_page(con)
        organic_by_page_device = _organic_visits_by_page_device(con)
    finally:
        con.close()

    by_page: dict[str, dict[str, tuple[int, int]]] = {}
    for page, month, shows, clicks in by_page_month:
        by_page.setdefault(page, {})[month] = (int(shows or 0), int(clicks or 0))

    rows: list[dict[str, Any]] = []
    losing_visibility_count = 0
    for page, months_map in sorted(by_page.items()):
        path = _url_path(page)
        organic_visits, engaged = organic_by_page.get(path, (0, 0))
        candidate = _s24_trend_candidate(months_map, organic_visits, engaged)
        if candidate is None:
            continue
        if candidate["losing_visibility_candidate"]:
            losing_visibility_count += 1
        rows.append({
            "check_id": "S24",
            "finding": "high_value_page_losing_visibility",
            "page": page,
            **candidate,
            "confidence": _cap("MED", confidence_cap),
        })

    by_page_device: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for page, device, month, shows, clicks in by_page_device_month:
        by_page_device.setdefault((page, device), {})[month] = (int(shows or 0), int(clicks or 0))

    losing_visibility_by_device_count = 0
    for (page, device), months_map in sorted(by_page_device.items()):
        path = _url_path(page)
        organic_visits, engaged = organic_by_page_device.get((path, device), (0, 0))
        candidate = _s24_trend_candidate(months_map, organic_visits, engaged)
        if candidate is None:
            continue
        if candidate["losing_visibility_candidate"]:
            losing_visibility_by_device_count += 1
        rows.append({
            "check_id": "S24",
            "finding": "high_value_page_losing_visibility_by_device",
            "page": page,
            "device": device,
            **candidate,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S24",
        "finding": "summary",
        "pages_evaluated": sum(1 for r in rows if r["finding"] == "high_value_page_losing_visibility"),
        "losing_visibility_candidates": losing_visibility_count,
        "device_rows_evaluated": sum(
            1 for r in rows if r["finding"] == "high_value_page_losing_visibility_by_device"
        ),
        "device_losing_visibility_candidates": losing_visibility_by_device_count,
        "min_shows_threshold": _S24_MIN_SHOWS_FOR_TREND,
        "min_organic_visits_threshold": _S24_MIN_ORGANIC_VISITS_FOR_CHECK,
        "high_engagement_rate_threshold": _S24_HIGH_ENGAGEMENT_RATE,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s24", rows, confidence_cap=confidence_cap)


# ── S25 — сниппет не использует структурированные данные/элементы выдачи ───
def _run_s25(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Структурированных данных/типа сниппета нет в canonical-схеме (см.

    докстринг модуля, разрыв 12) — автоматическая часть ограничена CTR-
    аномалией внутри позиций 1-10 (тот же метод, что S04); финальный вердикт
    остаётся за ручной SERP-проверкой (сама формулировка проверки в каталоге
    её требует).
    """
    con = common.open_duckdb(paths)
    try:
        groups = _aggregate_query_page(con)
    finally:
        con.close()

    site_titles = _load_site_titles(canonical, paths)

    candidates = [
        g for g in groups
        if g["shows"] >= _S25_MIN_SHOWS_FOR_ROW
        and g["position"] is not None
        and g["position"] <= _S25_MAX_POSITION_FOR_SNIPPET_CHECK
    ]

    rows: list[dict[str, Any]] = []
    gap_count = 0
    if len(candidates) >= _S25_MIN_QUERIES_FOR_MEDIAN:
        ctrs = [g["clicks"] / g["shows"] for g in candidates if g["shows"] > 0]
        median_ctr = _median(ctrs)
        if median_ctr is not None and median_ctr > 0:
            for g in candidates:
                ctr = g["clicks"] / g["shows"] if g["shows"] else None
                ratio = (ctr / median_ctr) if ctr is not None else None
                gap = ratio is not None and ratio <= _S25_CTR_LOW_RATIO
                if gap:
                    gap_count += 1
                path = _url_path(g["page"])
                context = site_titles.get(path, {})
                rows.append({
                    "check_id": "S25",
                    "finding": "serp_feature_gap_candidate",
                    "query": g["query"],
                    "page": g["page"],
                    "total_shows": g["shows"],
                    "total_clicks": g["clicks"],
                    "avg_position": round(g["position"], 2),
                    "ctr": round(ctr, 4) if ctr is not None else None,
                    "page1_median_ctr": round(median_ctr, 4),
                    "ctr_to_median_ratio": round(ratio, 3) if ratio is not None else None,
                    "low_ctr_ratio_threshold": _S25_CTR_LOW_RATIO,
                    "snippet_gap_candidate": bool(gap),
                    "page_title": context.get("title"),
                    "structured_data_field_available": False,
                    "manual_serp_check_required": True,
                    "confidence": _cap("MED", confidence_cap),
                })

    rows.insert(0, {
        "check_id": "S25",
        "finding": "summary",
        "page1_queries_evaluated": len(candidates),
        "snippet_gap_candidates": gap_count,
        "min_shows_threshold": _S25_MIN_SHOWS_FOR_ROW,
        "min_queries_for_median_threshold": _S25_MIN_QUERIES_FOR_MEDIAN,
        "max_position_for_check": _S25_MAX_POSITION_FOR_SNIPPET_CHECK,
        "structured_data_field_available": False,
        "manual_serp_check_required": True,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s25", rows, confidence_cap=confidence_cap)


# ── S26 — геоспрос не покрыт отдельными релевантными страницами ────────────
def _run_s26(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """requires=[wordstat, seo_queries]. Та же механика кластер-спрос-vs-

    карта-страниц, что S07 (см. _gap_demand_candidates) — canonical wordstat
    не несёт отдельного гео-поля на строку (config.sources.wordstat.regions
    задаёт регион для ВСЕЙ выгрузки целиком, а не per-фразовую метку, а этот
    compute-модуль client config не читает вовсе, см. докстринг модуля,
    "config клиента НЕ читается"), поэтому geo_dimension_available=false в
    каждой строке — не выдаём совпадение с S07 за географический анализ.
    Без canonical["wordstat"] — unavailable, как и раньше.
    """
    if not ("wordstat" in canonical and _table_nonempty(canonical["wordstat"])):
        _write_unavailable(
            metrics_dir, "S26",
            "ядро не посчитано: источник wordstat не готов — wordstat.parquet не "
            "строится в canonical-слое (src/extract/wordstat.py объявляет "
            "canonical_tables=['wordstat'], но build_canonical.py эту таблицу не "
            "собирает) — гео-спрос сопоставить с картой страниц нечем",
        )
        return

    con = common.open_duckdb(paths)
    try:
        clusters, gap_candidates = _gap_demand_candidates(con, _S07_MIN_DEMAND_COUNT)
    finally:
        con.close()

    rows: list[dict[str, Any]] = [{
        "check_id": "S26",
        "finding": "summary",
        "clusters_evaluated": len(clusters),
        "gap_candidate_count": len(gap_candidates),
        "min_demand_threshold": _S07_MIN_DEMAND_COUNT,
        "match_method": "normalize(phrase) exact match against seo_queries.query",
        "geo_dimension_available": False,
        "confidence": _cap("MED", confidence_cap),
    }]
    for c in sorted(gap_candidates, key=lambda x: -x["demand_total"]):
        rows.append({
            "check_id": "S26",
            "finding": "geo_demand_without_landing_page",
            "phrase": c["phrase"],
            "normalized_phrase": c["normalized_phrase"],
            "demand_total": c["demand_total"],
            "min_demand_threshold": _S07_MIN_DEMAND_COUNT,
            "geo_dimension_available": False,
            "confidence": _cap("MED", confidence_cap),
        })

    common.write_metric_artifact(metrics_dir, "s26", rows, confidence_cap=confidence_cap)


# ── S27 — JS-контент или ссылки недоступны поисковому роботу ───────────────
def _load_site_pages_js_diff(canonical: dict[str, Path], paths: Any) -> dict[str, dict[str, Any]]:
    """{нормализованный_путь: {url, js_content_diff_raw}} из site_pages.

    Отдельный загрузчик от _load_site_pages_full/_load_site_titles (см. их
    докстринги) — им js_content_diff не нужен, здесь нужен только он.
    """
    if "site_pages" not in canonical or not _table_nonempty(canonical["site_pages"]):
        return {}
    con = common.open_duckdb(paths)
    try:
        rows = con.execute("SELECT url, js_content_diff FROM site_pages").fetchall()
    finally:
        con.close()
    out: dict[str, dict[str, Any]] = {}
    for url, js_content_diff in rows:
        path = _url_path(url)
        if path in out:
            continue
        out[path] = {"url": url, "js_content_diff_raw": js_content_diff}
    return out


def _run_s27(paths: Any, canonical: dict[str, Path], confidence_cap: str, metrics_dir: Path) -> None:
    """Реализует компонент, зарезервированный в докстрине модуля (разрыв 6 и

    14): js_content_diff относится к S27, не к S11 (data-export-spec-v2.md
    §G1). Без site_pages либо без заполненного js_content_diff ядро не
    считается — явная unavailable-запись (промт задачи 5bC), не тихий пропуск.
    """
    site_pages = _load_site_pages_js_diff(canonical, paths)
    if not site_pages:
        _write_unavailable(
            metrics_dir, "S27",
            "ядро не посчитано: источник site_crawl (обход сайта) не готов — "
            "сравнить исходный HTML и отрендеренный контент нечем",
        )
        return

    parsed: dict[str, dict[str, Any]] = {}
    for path, info in site_pages.items():
        raw = info.get("js_content_diff_raw")
        diff = None
        if raw:
            try:
                diff = json.loads(raw)
            except (TypeError, ValueError):
                diff = None
        if diff is not None:
            parsed[path] = {"url": info["url"], "diff": diff}

    if not parsed:
        _write_unavailable(
            metrics_dir, "S27",
            "ядро не посчитано: источник site_crawl не готов — js_content_diff "
            "не заполнен ни на одной обойдённой странице (headless-рендеринг "
            "не выполнялся, playwright недоступен в среде обхода, либо сайт "
            "полностью SSR — различить эти причины нечем без записи в "
            "manifest обхода, см. src/extract/site_crawl.py: headless_stats)",
        )
        return

    con = common.open_duckdb(paths)
    try:
        shows_by_path = _seo_shows_clicks_by_path(con)
    finally:
        con.close()

    rows: list[dict[str, Any]] = []
    candidate_count = 0
    for path, entry in sorted(parsed.items()):
        shows, clicks = shows_by_path.get(path, (0, 0))
        if shows < _S27_MIN_SHOWS_FOR_CHECK:
            continue
        diff = entry["diff"]
        links_only_in_rendered = diff.get("links_only_in_rendered") or []
        text_changed = bool(diff.get("text_changed"))
        candidate = bool(text_changed or links_only_in_rendered)
        if candidate:
            candidate_count += 1
        rows.append({
            "check_id": "S27",
            "finding": "js_rendering_gap_candidate",
            "page": entry["url"],
            "total_shows": shows,
            "total_clicks": clicks,
            "text_changed": text_changed,
            "links_only_in_rendered_count": len(links_only_in_rendered),
            "raw_link_count": diff.get("raw_link_count"),
            "rendered_link_count": diff.get("rendered_link_count"),
            "min_shows_threshold": _S27_MIN_SHOWS_FOR_CHECK,
            "js_rendering_gap_candidate": candidate,
            "confidence": _cap("MED", confidence_cap),
        })

    rows.insert(0, {
        "check_id": "S27",
        "finding": "summary",
        "pages_with_js_diff_data": len(parsed),
        "pages_evaluated": len(rows),
        "js_rendering_gap_candidates": candidate_count,
        "min_shows_threshold": _S27_MIN_SHOWS_FOR_CHECK,
        "crawl_coverage_caveat": _CRAWL_COVERAGE_CAVEAT,
        "confidence": _cap("MED", confidence_cap),
    })

    common.write_metric_artifact(metrics_dir, "s27", rows, confidence_cap=confidence_cap)


# ── Диспетчер блока ──────────────────────────────────────────────────────────
def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Выполнить S01-S27 из числа доступных; вернуть имена записанных артефактов.

    Блок 4 полностью реализован (задачи 5bA/5bB/5bC, см. докстринг модуля).
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
        _run_s07(paths, defaults, canonical, caps.get("S07", "HIGH"), metrics_dir)
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

    # ── Задача 5bB: S11-S20 ──────────────────────────────────────────────────
    if "S11" in runnable_ids and has_seo:
        _run_s11(paths, canonical, caps.get("S11", "HIGH"), metrics_dir)
        artifacts.append("s11")

    if "S12" in runnable_ids and has_seo:
        _run_s12(paths, canonical, caps.get("S12", "HIGH"), metrics_dir)
        artifacts.append("s12")

    if "S13" in runnable_ids and has_seo:
        _run_s13(paths, canonical, caps.get("S13", "HIGH"), metrics_dir)
        artifacts.append("s13")

    if "S14" in runnable_ids and has_seo:
        _run_s14(paths, canonical, caps.get("S14", "HIGH"), metrics_dir)
        artifacts.append("s14")

    if "S15" in runnable_ids and "site_pages" in canonical:
        _run_s15(paths, canonical, caps.get("S15", "HIGH"), metrics_dir)
        artifacts.append("s15")

    if "S16" in runnable_ids and has_seo:
        _run_s16(paths, canonical, caps.get("S16", "HIGH"), metrics_dir)
        artifacts.append("s16")

    if "S17" in runnable_ids and has_seo:
        _run_s17(paths, canonical, caps.get("S17", "HIGH"), metrics_dir)
        artifacts.append("s17")

    if "S18" in runnable_ids and "site_pages" in canonical:
        _run_s18(paths, canonical, caps.get("S18", "HIGH"), metrics_dir)
        artifacts.append("s18")

    if "S19" in runnable_ids and "site_pages" in canonical:
        _run_s19(paths, canonical, caps.get("S19", "HIGH"), metrics_dir)
        artifacts.append("s19")

    if "S20" in runnable_ids and has_seo:
        _run_s20(paths, defaults, canonical, caps.get("S20", "HIGH"), metrics_dir)
        artifacts.append("s20")

    # ── Задача 5bC: S21-S27 ──────────────────────────────────────────────────
    if "S21" in runnable_ids and has_seo:
        _run_s21(paths, caps.get("S21", "HIGH"), metrics_dir)
        artifacts.append("s21")

    if "S22" in runnable_ids and has_seo and has_visits:
        _run_s22(paths, canonical, caps.get("S22", "HIGH"), metrics_dir)
        artifacts.append("s22")

    if "S23" in runnable_ids and has_seo and has_visits:
        _run_s23(paths, caps.get("S23", "HIGH"), metrics_dir)
        artifacts.append("s23")

    if "S24" in runnable_ids and has_seo and has_visits:
        _run_s24(paths, caps.get("S24", "HIGH"), metrics_dir)
        artifacts.append("s24")

    if "S25" in runnable_ids and has_seo:
        _run_s25(paths, canonical, caps.get("S25", "HIGH"), metrics_dir)
        artifacts.append("s25")

    if "S26" in runnable_ids and has_seo:
        _run_s26(paths, canonical, caps.get("S26", "HIGH"), metrics_dir)
        artifacts.append("s26")

    if "S27" in runnable_ids and has_seo:
        _run_s27(paths, canonical, caps.get("S27", "HIGH"), metrics_dir)
        artifacts.append("s27")

    _annotate_written_artifacts(metrics_dir, artifacts)
    return artifacts
