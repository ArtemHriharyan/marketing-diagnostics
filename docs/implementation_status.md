# Статус реализации — audit 2026-07-14

Тесты: `pytest tests/`
Результат: **289 passed** из 289 (после task 4D 2026-07-14).

Регрессия transform (task 4E, 2026-07-14): `pytest tests/test_build_canonical.py` — **92 passed**, 0 failed, 0 errors.
Все тестируемые группы: dedupe_visits (3), apply_utm_threshold (4), expand_manual_costs (5), is_brand_query (3), classify_traffic_source (13), map_device (7), goal_flags (2), normalize_entry_page (5), classify_strategy_optimize_for (6), crm normalization (9), build() сквозные (7), build_visits backfill (4), vat normalization (7), normalize_url (7), dedupe_site_* (2), seo_queries source_mode/completeness (4), 4A–4D новые тесты — все GREEN.

---

## Таблица статусов

| Задача | Статус  | Недостающий критерий / комментарий |
|--------|---------|-------------------------------------|
| **1A** | DONE    | — |
| **1B** | DONE    | — |
| **1C** | DONE    | — |
| **1D** | DONE    | проверка совместимости 2026-07-14: methodology×2 pass, degradation×6 pass, config×16 pass, intake _template exit=0, intake pognali.rent exit=0 |
| **2A** | DONE    | — |
| **2A-patch** | CODE DONE, live run pending | 2026-07-21: поля Logs API сверены построчно с https://yandex.ru/dev/metrika/ru/logs/fields/visits — убраны isRobot/screenResolution/lastSignGCLID/lastSignhasGCLID, добавлены goalsDateTime+goalsSerialNumber (D01/D09), from (T01/T03), bounce+endURL (C06/C07/C12), isRobotPro опционально с graceful degradation. **Уточнение 2026-07-22 (после боевого прогона):** доступный тариф отклонил и isRobotPro — детекция бота через Logs API для этого доступа невозможна ПОСТОЯННО (не тарифная деградация). isRobotPro убран из кандидатов насовсем, никакой негоциации/ретраев вокруг него больше нет: `manifest.bot_detection_available` жёстко `False` (константа `BOT_DETECTION_AVAILABLE`). `ym:s:regionCity` заменена попыткой `ym:s:regionArea`: имя не гадается — проверяется отдельным `logrequests/evaluate` на каждый прогон (`_resolve_region_field`); принято → `region_field="ym:s:regionArea"`, `region_field_verified=true`; API отклонил → откат на `regionCity`, `verified=false` + реальный текст ошибки API в `manifest.region_field_error` (другое имя в рамках задачи не пробуется). `ym:s:ipAddress` по-прежнему не запрашивается. `config/methodology.yaml` D11: `type_downgraded="permanent_LOW"` + новое поле `downgrade_reason`, `type_downgrade_if` остаётся `null` (постоянное ограничение читается напрямую по `type_downgraded`, БЕЗ условия по manifest-флагу — см. CLAUDE.md, раздел «Схема ID проверок»). `data-export-spec-v1.md` раздел A обновлён под факт. SCHEMA_VERSION visits-v3→visits-v4 (довыгрузка сработает и для окон, частично выгруженных под v3 в ходе боевого прогона). `tests/test_metrika_logs_patch.py` переписан под новое поведение (3 теста на isRobotPro graceful-degradation заменены на bot_detection-постоянство + 2 теста региона + 1 тест D11 в methodology.yaml) — 11/11 pass. **BLOCKER (расширен):** 3 старых теста в `tests/test_extract_smoke.py` падают — `test_metrika_logs_negotiation_isolates_unsupported_fields` и `test_metrika_logs_backfill_preserves_old_files` (pre-existing, симулируют отклонение через `ym:s:lastSignhasGCLID`, больше не запрашивается) и **новый** `test_metrika_logs_writes_raw_and_manifest` (хардкодит `"ym:s:regionCity" in metrika_logs.VISIT_FIELDS`, что перестало быть верным после замены на `regionArea`); файл вне `allowed_files` этой задачи, не редактировался. **Зависимость для отдельной задачи (не в allowed_files):** `src/transform/build_canonical.py` (строки ~499, ~551, ~689, ~1280) читает регион визита ТОЛЬКО по жёсткому имени `ym:s:regionCity` — если боевой прогон примет `regionArea` (verified=true), transform молча даст `region_city=null` для всех строк, пока build_canonical.py не научится смотреть `manifest.region_field` и брать значение из фактически присутствующей колонки (`regionArea` или `regionCity`). Не исправлено в этой задаче — вне allowed_files. Этот же пробел ломает ещё один тест вне allowed_files этой задачи: `tests/test_build_canonical.py::test_dedupe_new_fields_use_last_dt_row` строит CSV-фикстуру по `metrika_logs.VISIT_FIELDS` и кладёт значение под ключ `ym:s:regionCity`, которого больше нет в `VISIT_FIELDS` (там теперь `ym:s:regionArea`) — значение в письменную строку не попадает вовсе, `region_city` в результате `None` вместо `"Kazan"`. Итого полный `pytest tests/` после этой задачи: **417 passed, 11 failed** (9 из 11 — pre-existing до этой задачи: gsc_manual×3, webmaster_manual×2, wordstat legacy×2, metrika_logs×2 lastSignhasGCLID; 2 новых из-за regionCity→regionArea, оба вне allowed_files: metrika_logs×1 в test_extract_smoke.py + build_canonical×1 выше). |
| **2A-patch-2** | DONE    | 2026-07-22: устранена зависимость, оставленная 2A-patch. `src/transform/build_canonical.py` больше не хардкодит `ym:s:regionCity` — новый `_resolve_region_field(manifest_metrika_entry)` читает `manifest.region_field` (записан extract в задаче 2A-patch: `ym:s:regionArea`, если API его принял, либо откат `ym:s:regionCity`, если отклонил); отсутствующий ключ (manifest до 2A-patch) -> откат на исторический `ym:s:regionCity` (константа `_REGION_FIELD_LEGACY_DEFAULT`), а не пустая колонка. Имя поля прокинуто через `_parse_visit_row`, `_parse_backfill_row`, `_read_metrika_backfill`, `_join_backfill`, `build_visits` (новый опциональный параметр `manifest_metrika_entry`); `build()` передаёт `sources.get("metrika_logs")` из `data/raw/manifest.json`. Оба ранее падавших теста (`tests/test_extract_smoke.py::test_metrika_logs_writes_raw_and_manifest`, `tests/test_build_canonical.py::test_dedupe_new_fields_use_last_dt_row`) обновлены под `regionArea` (не откат назад на `regionCity`) — pass. Новые тесты в `tests/test_build_canonical.py`: `test_region_field_falls_back_to_region_city_when_not_verified` (manifest `region_field_verified=false` -> raw CSV реально с колонкой `regionCity` -> canonical читает её, не `None`) и `test_region_field_defaults_to_region_city_without_manifest_entry` (manifest без записи `region_field` вовсе -> тот же откат) — обе pass. Полный `pytest tests/`: **443 passed, 9 failed** — все 9 pre-existing и не связаны с этой задачей (gsc_manual×3, webmaster_manual×2, wordstat legacy×2, metrika_logs×2 `lastSignhasGCLID` в `test_extract_smoke.py`, вне allowed_files). |
| **2A-direct-strategy-fix** | DONE | 2026-07-22: чинит невалидный FieldNames в `campaigns.get`, обнаруженный боевым прогоном (error 8000, `clients/pognali.rent/logs/extract_20260722_012238.log:63` — API вернул полный enum допустимых значений, "Strategy" среди них нет). `src/extract/direct.py`: `"Strategy"` убран из `CAMPAIGN_FIELD_NAMES`; новая `CAMPAIGNS_FIELD_NAMES_ENUM` (frozenset, взят дословно из текста ошибки) + `_validate_field_names()` — сверяет FieldNames с этим enum ДО отправки запроса и логирует отфильтрованные невалидные имена (не после ответа API), так что опечатка/устаревшее поле больше не роняет источник целиком. `BiddingStrategy` запрашивается отдельным параметром `TextCampaignFieldNames: ["BiddingStrategy"]` в `_fetch_strategies` (TEXT_CAMPAIGN — единственный тип кампаний у клиента сейчас; MOBILE_APP_CAMPAIGN/CPM_BANNER_CAMPAIGN/UNIFIED_CAMPAIGN потребуют свой `*CampaignFieldNames` — известное ограничение, не реализовано). `_strategy_field_present`/`_strategy_field_samples` переписаны читать вложенный `TextCampaign.BiddingStrategy` (через новый `_text_campaign_bidding_strategy()`), а не плоское поле `Strategy` верхнего уровня. `tests/test_direct_2a_strategy.py` обновлён под новый контракт (ломающее изменение, зафиксированное этой задачей): запрос содержит `TextCampaignFieldNames`, не содержит `"Strategy"` в `FieldNames`; парсинг `BiddingStrategyType` из `Search` и `Network`; плоский верхнеуровневый `Strategy` больше не распознаётся; невалидное имя поля фильтруется до отправки запроса, не роняя источник. 36 тестов в `test_direct_2a_strategy.py` + `test_direct_2b_patch.py` — 34 pass, 2 pre-existing fail (`test_query_report_dimensions`, `test_geo_report_schema` — ожидают старую семантику `cost_normalized`, сломанную задачей 4X-direct-normalize-2, не связано с этой задачей). Полный `pytest tests/` (кроме `test_site_crawl*.py` — см. ниже) не показал новых регрессий: те же 11 pre-existing failures, что документированы в 4X-direct-normalize-2/2A-patch/3A-patch. **Побочная находка, не устранена (вне allowed_files):** `tests/test_site_crawl_pages.py` не собирается (`ImportError: cannot import name '_is_path_disallowed'`) — `src/extract/site_crawl.py` в рабочей копии не содержит функций robots.txt-парсинга, описанных как реализованные в записи задачи 3.5-patch этого же файла; похоже на параллельную правку того же файла в другой сессии поверх HEAD, не в скоупе и не в allowed_files этой задачи — не исправлялось. |
| **2B** | DONE    | 2B-patch 2026-07-20: window truncation 180d, isolation, UTF-8 fix, 16 tests |
| **2B-patch-2** | CODE DONE, live run pending | см. запись ниже — код + 30 тестов green (mock), реальный прогон на pognali.rent не выполнялся в этой сессии |
| **2C** | DONE    | — |
| **2D** | DONE    | — |
| **3A** | DONE    | build_canonical.py базовые преобразования. GSC manual path (task_id gsc-3A, task_id 3A-rewrite 2026-07-17): gsc_manual.py переписан под формат папок YYYY-MM/Запросы.csv/Диаграмма.csv/Страницы.csv/Устройства.csv. Выходной контракт seo_queries не изменился. column_map в config.yaml заполнен кириллическими заголовками GSC. tests/test_gsc_manual.py переписан: 9 тестов — 9 pass 2026-07-17. |
| **3A-patch** | DONE    | 2026-07-22: gsc_manual.py — Запросы.csv теперь может быть комбинированным (query+page+device в одной строке сразу, contract 3A: `column_map["page"]`+`column_map["device"]` оба присутствуют в заголовке) — page/device берутся из строки, `incomplete_dimensions=false`; Страницы.csv в этом случае становится необязательным (page уже есть в Запросы.csv). Старый раздельный формат (только query) по-прежнему парсится без падения, но помечается caveat `incomplete_dimensions` + попадает в `incomplete_dimensions_months`/`device_missing_months` (manifest и report). Сверка кликов Диаграмма vs Запросы (>10% caveat) не менялась. Новый `docs/gsc_export_instructions.md` — как выбрать несколько измерений сразу в интерфейсе GSC перед экспортом. SCRIPT_VERSION 0.2.0→0.3.0. 4 новых теста в tests/test_gsc_manual.py (комбинированный формат, pages необязателен при комбинированном, legacy incomplete_dimensions=true, legacy всё ещё требует Страницы.csv) — 13/13 pass. BLOCKER: 3 старых теста в tests/test_extract_smoke.py (test_gsc_manual_validates_and_writes_same_contract, test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat, test_gsc_manual_missing_device_column_flags_month) падают — это pre-existing из 3A-rewrite (2026-07-17), тестируют старый плоский формат gsc_YYYY-MM.csv без папок YYYY-MM, файл вне allowed_files этой задачи, не редактировался. |
| **3B** | DONE    | webmaster_manual: переписан под wide-формат (Query×Url×YYYY-MM_cols); агрегация по (query,page), CTR пересчёт, DEMAND=max; manifest: has_page_column=true, page_device_breakdown=true, has_demand_column; tests/test_webmaster_manual.py (12 тестов) — 12 pass 2026-07-17. BLOCKER: build_seo_queries_webmaster (build_canonical.py:942) хардкодит page=None — page из JSON теряется в transform. |
| **3C** | DONE    | — |
| **3C-patch** | CODE DONE, live run pending | 2026-07-22: см. запись ниже — конфиг и подключение к оркестратору уже были на месте, добавлены тесты (ping + оркестратор-интеграция); реальный CRUX_API_KEY в этой сессии недоступен. |
| **3D** | DONE    | Побочных изменений нет: 3A/3B затрагивают build_canonical.py, 3C — scripts/verify_metrika.py; wordstat.py и crm_import.py не изменены. Git-репо отсутствует (проверка кодом). 39 тестов GSC/Webmaster/CrUX/Wordstat/CRM — 39 pass 2026-07-14. |
| **3.5A** | DONE  | Каркас кролера без HTTP: src/extract/site_crawl.py (build_url_priority_list, resolve_max_urls, extract); crawl_seed_urls + crawl.max_urls=30 в _template/config.yaml; inputs/manual_cwv.yaml и inputs/manual_form_tests.yaml (meta/patterns/conclusions); manifest caveat при усечении. 20 тестов test_site_crawl.py — 20 pass 2026-07-14. |
| **3.5B** | DONE  | HTTP-обход страниц: _MetaParser (stdlib html.parser), _parse_page_meta, _parse_sitemap_xml, fetch_sitemap, crawl_pages, write_pages_parquet, _resolve_base_url (crawl.base_url → webmaster.host_id). Выход pages.parquet по схеме PAGES_SCHEMA (url, http_status, redirect_chain, final_url, canonical_url, robots_directive, in_sitemap, title, description, h1, crawled_at). Фикстурный мини-сайт через MockSession/MockResponse без сетевых запросов. 37 тестов test_site_crawl_pages.py — 37 pass 2026-07-14. |
| **3.5C** | DONE  | JS-diff + внутренние ссылки + BFS + link_graph.parquet. _LinkParser, _TextParser, _extract_links (internal/external via urljoin+netloc), _visible_text, _render_headless (playwright, мягкая деградация при отсутствии), compute_js_diff ({raw_link_count, rendered_link_count, links_only_in_rendered, text_changed}), crawl_bfs (BFS depth≤3, цикло-защита через visited, рёбра записываются для уже посещённых URL), write_link_graph_parquet (from_url,to_url,depth_from_home). PAGES_SCHEMA расширена полем js_content_diff; LINK_GRAPH_SCHEMA добавлена. extract() запускает BFS и пишет link_graph.parquet. playwright>=1.40 добавлен в requirements.txt. 50 тестов test_site_crawl_bfs.py — 50 pass 2026-07-14. |
| **3.5-CONNECT** | DONE | site_crawl подключён к run_extract в orchestrator.py: вызывается при наличии crawl.base_url, пропускается без ошибки при его отсутствии (принцип 4). Тесты site_crawl — GREEN (см. ниже). |
| **3.5D** | DONE  | Приёмка краулера на локальном мини-сайте 2026-07-14. pytest test_site_crawl.py + test_site_crawl_pages.py + test_site_crawl_bfs.py — **87 passed** из 87. Схема pages.parquet (PAGES_SCHEMA, 12 колонок) подтверждена test_write_pages_parquet_schema; схема link_graph.parquet (LINK_GRAPH_SCHEMA, 3 колонки) — test_write_link_graph_parquet_schema. Типы: http_status=Int64, in_sitemap=bool, depth_from_home=Int64. Manifest: rows/date_from/date_to/fetched_at/extracted_at/canonical_tables проходят через update_source; extra-поля total_candidates, urls_queued, pages_crawled, bfs_edges записываются без потерь. Caveat частичного покрытия: test_caveat_set_when_truncated/test_no_caveat_when_within_limit — pass; текст кавета содержит max_urls и кол-во отброшенных кандидатов. Производственный код не изменён. |
| **4A** | DONE    | last_traffic_source_naive, browser, os, screen_resolution, region_country, region_city в SCHEMAS["visits"] и build_visits (inline v2 + backfill join). Два новых теста: test_last_traffic_source_naive_does_not_affect_source_classification (naive≠source_group, source_final из lastsign); test_dedupe_new_fields_use_last_dt_row (browser/region_city берётся из строки с позднейшим dt). 72 passed из 72 (test_build_canonical.py). |
| **4B** | DONE    | Ломающее изменение costs: cost_rub заменён на cost_raw + cost_normalized + cost_status. Нормализация по finance.vat_basis_by_source (из config["finance"]); при отсутствии базы НДС — normalized=null, status=vat_basis_unknown (не «молча»). Добавлены _vat_lookup, _apply_vat_to_rows; build_costs принимает vat_basis_by_source; build() читает config.get("finance"). 7 новых тестов (net/gross/unknown/фиксы/mixed). 79 passed (test_build_canonical.py), 276 passed всего. |
| **4D** | DONE    | site_pages.parquet + site_link_graph.parquet в canonical; normalize_url (строчные scheme/netloc, без trailing-slash) + dedupe_site_pages/dedupe_site_link_graph; seo_queries.source_mode (api\|manual) и seo_queries.completeness (verified\|unverified) — из manifest-записи источника; build() проксирует sources.get("gsc") в build_seo_queries_gsc; бренд-классификация и объединение Google/Yandex сохранены. 13 новых тестов (normalize_url, URL-дедуп страниц/графа, manual/unverified GSC+Webmaster, defaults api/verified, месяц без device не удаляется). 92 passed (test_build_canonical.py), 289 passed всего. |
| **4E** | DONE    | Регрессия всего transform/canonical (2026-07-14). pytest tests/test_build_canonical.py — 92 passed, 0 failed. Падений нет; задачам-владельцам нечего распределять. |
| **3.5-patch** | PARTIAL | 2026-07-22. **(1) Покрытие — FIXED.** Баг: `build_url_priority_list` читал устаревшие пост-4D имена `seo_queries_gsc.parquet`/`seo_queries_webmaster.parquet`, которых больше не существует (4D объединил их в один `seo_queries.parquet` с колонкой `source`). На реальных данных pognali.rent очередь была 3 URL (только explicit_seed) вместо ожидаемых десятков. Добавлена `_pages_from_seo_queries()` (фильтр по `source`, сортировка по `total_clicks`); `_pages_matching_keywords` переписана под единую таблицу. На pognali.rent покрытие выросло с 3 до 21 URL (18 через top_organic_webmaster; GSC даёт 0, т.к. total_clicks=0 во всех строках — сами данные пустые, не баг сборки). **Direct/costs остаётся вне охвата**: `costs.parquet` (build_costs) — campaign-level, колонки `entry_page` там нет и не будет без отдельной постраничной выгрузки Директа; `_pages_from_canonical(..., "costs.parquet", "entry_page", ...)` корректно деградирует в [] — это не баг site_crawl, а отсутствующий источник данных (вне `allowed_files` этой задачи — потребует правки build_costs/direct.py). Дубли explicit_seed vs canonical-страниц в разной форме (абсолютный URL vs относительный путь) не схлопываются — те же страницы могут попасть в очередь дважды под разными строками; не блокер (обе формы резолвятся в один URL при обходе), но стоит нормализовать при следующей правке. Тесты: test_site_crawl.py обновлён под новую схему (costs без entry_page, seo_queries объединённая) — 21 pass. **(2) robots_directive — FIXED (код), верификация на реальном Disallow-URL не выполнена.** Баг: `robots_directive` парсил только `<meta name="robots">`; X-Robots-Tag заголовок и правила robots.txt игнорировались полностью — на URL, заблокированном только через robots.txt Disallow (без meta), поле было пустым. Добавлены `_parse_robots_txt`/`_select_robots_rules`/`_is_path_disallowed` (RFC 9309-совместимые группы, longest-match Allow/Disallow), `fetch_robots_txt` (мягкая деградация как у sitemap), `_get_header` (регистронезависимый доступ к заголовкам), `_combine_robots_directive` (сводит meta+X-Robots-Tag+robots.txt в одну строку через "; ", однокомпонентный случай не меняется — обратная совместимость). `extract()` теперь вызывает `fetch_robots_txt` и передаёт правила в `crawl_pages`. 18 новых тестов (парсинг групп, longest-match, комбинации сигналов, сеть недоступна) — все pass. **BLOCKER:** нет сетевого доступа из этой среды к pognali.rent (WebFetch/curl/browser — заблокировано), поэтому фактическая проверка на «заведомо известном Disallow-URL» с реального robots.txt не выполнена — нужен URL+ожидаемый результат от оператора. **(3) js_content_diff — ROOT CAUSE FOUND, требует решения оператора.** Diff-логика (`compute_js_diff`, `_extract_links`, `_visible_text`) корректна и полностью покрыта тестами (без изменений). Причина пустого diff в этой среде: Chromium для playwright не установлен (`playwright install chromium` не выполнялся — `_render_headless` всегда ловит исключение запуска и возвращает None, это штатная мягкая деградация, не баг). Из-за этого js_content_diff=None **неотличим** от «сайт SSR, различий нет». Добавлена наблюдаемость: `extract()` считает `headless_pages_attempted`/`headless_diff_populated` в manifest.extra и логирует явное предупреждение, если headless включён, но diff пуст на всех проверенных страницах. **BLOCKER:** нужно от оператора — (a) подтверждение SSR/SPA стека pognali.rent; (b) если SPA — URL страницы с известным JS-контентом для реальной проверки diff>0 (нужен также `playwright install chromium` в среде исполнения). Итоговый pytest: `pytest tests/test_site_crawl.py tests/test_site_crawl_pages.py tests/test_site_crawl_bfs.py` — **105 passed** из 105. Полный `pytest tests/` — 417 passed, 11 failed (все 11 — предсуществующие, не связаны с site_crawl.py: gsc_manual/webmaster_manual/wordstat/metrika_logs/build_canonical, см. соответствующие задачи выше). **Инцидент 2026-07-22 (после этой записи):** незакоммиченная реализация robots.txt из этой задачи была потеряна рабочим деревом (повторяющиеся `git reset --hard HEAD` в `git reflog`, зафиксировано задачей `2A-direct-strategy-fix` выше как "похоже на параллельную правку… не в скоупе"). Работа не пропала — сохранилась в `git stash@{0}` (`WIP on master: d5aa955`, создан автоматически тем же процессом одновременно с reset). Восстановлена и смёржена задачей **3.5-merge-recovered** (см. ниже) с независимо разработанным `3.5-hang-fix` — обе части сосуществуют в текущем `site_crawl.py`. |
| **3.5-hang-diag** | DONE | 2026-07-22. Диагностика зависания BFS-обхода после `ReadTimeout` на одном URL (лог `clients/pognali.rent/logs/extract_20260722_012238.log:135-137`, обрывается сразу после трёх ошибок BFS без финального `"BFS завершён"`). Причина: `session.get(..., timeout=timeout)` — скалярный таймаут ограничивает только паузу МЕЖДУ чтениями сокета (requests/urllib3 сбрасывают таймер на каждом полученном чанке), а не общую длительность запроса; медленно "текущий" бинарный ответ (в логе — `.jpg` сразу после ReadTimeout) мог зависнуть на неопределённое время без повторного срабатывания таймаута. Ретраев в `crawl_bfs` нет вовсе (одна попытка, `except`→`continue`) — зависание не было ретрай-циклом. Сессия общая между `crawl_pages`/`crawl_bfs`/`fetch_sitemap`/`fetch_robots_txt`, но обход строго последовательный (без потоков) — исчерпание пула соединений исключено как причина. Отчёт без правок кода (задача только на чтение). |
| **3.5-hang-fix** | DONE | 2026-07-22. Устраняет причину, найденную `3.5-hang-diag`. Новый `_guarded_get()` в `site_crawl.py` — двухслойная защита, применена в ОБОИХ местах (`crawl_pages`, `crawl_bfs`): слой 1 — `(CRAWL_CONNECT_TIMEOUT_SEC=5, CRAWL_TIMEOUT_SEC=15)` кортеж вместо скалярного timeout (ограничивает паузу между чтениями); слой 2 — жёсткий `CRAWL_HARD_TIMEOUT_SEC=30` на всю длительность запроса, не зависящий от активности чтения: весь `session.get()+.text` выполняется в `concurrent.futures.ThreadPoolExecutor(max_workers=1)`, ожидание — `future.result(timeout=hard_timeout)`; при превышении вызывающий код гарантированно получает управление обратно (фоновый поток, если завис на сокете, не убивается — `executor.shutdown(wait=False)`, не блокирует). Content-Type фильтр (`_is_skippable_content_type`, `_SKIP_CONTENT_TYPE_PREFIXES` — image/video/audio/font/pdf/zip/octet-stream): тело не скачивается для бинарных ответов, `.text` не читается. `crawl_pages`/`crawl_bfs` получили новый опциональный параметр `hard_timeout` (по умолчанию `CRAWL_HARD_TIMEOUT_SEC`) для тестируемости. Ошибка/hard_timeout на одном URL не прерывает обход остальной очереди (уже было верно для error, подтверждено для hard_timeout новыми тестами). 7 новых тестов в `test_site_crawl_pages.py`/`test_site_crawl_bfs.py` (hard_timeout не зависает — таймер теста <1.5s при mock-задержке 2.0s, обход продолжается после hard_timeout на одном URL, Content-Type image/pdf пропускается без парсинга meta). Реализация делалась параллельно с независимой сессией, восстанавливающей потерянный `3.5-patch` (см. `3.5-merge-recovered`) — итоговый мёрж объединяет обе части. |
| **3.5-merge-recovered** | DONE | 2026-07-22. Мёржит две независимые незакоммиченные ветки работы над `site_crawl.py`, разошедшиеся после reset-инцидента (см. запись `3.5-patch`): (а) `git stash@{0}` — полная реализация `3.5-patch` (robots.txt RFC 9309, unified `seo_queries.parquet`, `headless_stats`); (б) текущая рабочая копия — `3.5-hang-fix` (`_guarded_get`, `concurrent.futures`, Content-Type skip). Перед мёржем содержимое `stash@{0}:src/extract/site_crawl.py` побайтово сверено (`diff -b`) с файлом `site_crawl_STASHED_RECOVERED.py`, который был предоставлен как основа задачи — идентичны (различие только в CRLF/LF) — происхождение подтверждено, не просто предположение из текста задачи. Мёрж сделан вручную (не автоматический `git merge`/`stash pop`, т.к. `stash@{0}` также содержит несвязанные изменения в `CLAUDE.md`, `config/*.yaml`, `direct.py`, `gsc_manual.py`, `metrika_logs.py`, `build_canonical.py` — вне `allowed_files` этой задачи, не тронуты, `stash@{0}` не удалён/не применён целиком, эти изменения остаются доступны для отдельной задачи с этими файлами в allowed_files). Точки пересечения (`crawl_pages`, `crawl_bfs`) объединены построчно: сохранена robots.txt-логика (`robots_rules`, `x_robots_tag`, `_combine_robots_directive`) из (а), сырой `session.get()` внутри обеих функций заменён на `_guarded_get()` из (б). `tests/test_site_crawl.py`/`tests/test_site_crawl_pages.py` уже совпадали с восстановленными из стэша версиями (проверено `diff -b`, 0 расхождений) — не редактировались. `pytest tests/test_site_crawl.py tests/test_site_crawl_pages.py tests/test_site_crawl_bfs.py` — **112 passed** из 112 (105 старых + 7 новых hang-fix). Импорт-тест подтверждает одновременное присутствие `fetch_robots_txt`/`_parse_robots_txt`/`_is_path_disallowed` И `_guarded_get`/`concurrent.futures`/`hard_timeout`-параметров в одном модуле. **Не восстановлено (вне allowed_files этой задачи, осталось в `stash@{0}`):** остальные 5 файлов из стэша (`direct.py`, `gsc_manual.py`, `metrika_logs.py`, `build_canonical.py`, `CLAUDE.md`, `config/*.yaml` + соответствующие тесты) — судя по содержимому стэша, это черновики задач за пределами `site_crawl.py`; нужна отдельная задача с этими файлами в `allowed_files`, чтобы решить, сохранились ли они и там тоже, или полностью потеряны как site_crawl.py. |
| **4X-webmaster-transform** | DONE | Новый модуль src/transform/webmaster_popular_queries.py: reshape_popular_queries_wide_to_long — разворот wide popular-queries Вебмастера (Query×Url×{YYYY-MM}_shows/position/demand/ctr/clicks) в long (query, url, month, shows, position, demand, ctr, clicks). Пустая ячейка -> NaN, явный 0 сохраняется как 0 (не смешивается с NaN); отсутствующая колонка метрики целиком -> NaN. Не агрегирует и не отбрасывает нулевые месяцы — отдельный контракт от src.extract.webmaster_manual (который сворачивает wide в одну строку на query×page и пропускает shows=0). clients/_template/config.yaml: brand_terms уже существовал (root-level, используется build_seo_queries_gsc/webmaster) — уточнён комментарий под будущую regex-классификацию is_brand в compute (варианты написания/транслит), поле и его расположение не менялись. tests/test_transform_webmaster_popular_queries.py — 12 новых тестов (детект месяцев, базовый разворот, NaN vs 0 в разных комбинациях, отсутствующая колонка метрики, кастомный column_map, порядок строк, smoke на brand_terms) — 12 pass 2026-07-22. build_canonical.py и src/extract/webmaster_manual.py не изменялись (вне allowed_files). |
| **4X-traffic-resolve** | DONE    | build_canonical.py: `resolve_traffic_source(df, lookback_cutoff)` — carry-forward lastsign-источника для визитов с сырым `ym:s:lastsignTrafficSource` in {internal, undefined} по clientID в хронологическом порядке, только вперёд по времени; добавляет `source_group_resolved` + `traffic_source_resolved` (bool) в SCHEMAS["visits"], не трогая source_group/source_final/last_traffic_source_naive. Новое сырое поле `last_sign_traffic_source_raw` в _parse_visit_row (нужно, т.к. после classify_traffic_source "undefined" неотличимо от "other"). `compute_traffic_resolve_stats()` — доля unresolved среди internal/undefined, пишется в manifest как отдельный флаг `flags.traffic_source_resolve` (не смешан с flags.metrika_backfill). config/defaults.yaml: `transform.traffic_resolve_lookback_days: 30`. build_visits() 3-tuple контракт не менялся (тесты вне allowed_files распаковывают `df, utm, stats` — новая статистика лежит внутри stats и вынимается в build()). tests/test_transform_visits_traffic_resolve.py — 9 новых тестов (цепочка ad→internal→internal→direct только вперёд, clientID без реального источника → unresolved без ошибки, визит с реальным источником не меняется, граница lookback включительно/исключительно, порядок строк на выходе = порядку на входе, пустой df, доля unresolved) — 9 pass. Регрессия `pytest tests/test_build_canonical.py` — 98 passed, `tests/test_config_schema.py` — 16 passed. **Blocker (вне allowed_files, зафиксировано, не реализовано):** extract-слой (`src/extract/metrika_logs.py`) не расширяет окно выгрузки на `traffic_resolve_lookback_days` дней назад для построения цепочки clientID — сейчас `build_visits` получает только визиты отчётного окна, поэтому `lookback_cutoff` фактически не отсекает ничего (в df просто нет визитов раньше `date_from`) и carry-forward работает только ВНУТРИ окна. Это ожидаемое поведение по методологии («резолвить сколько можется»), но полноценный lookback (клиенты с первым реальным визитом до окна) потребует отдельной задачи с `src/extract/metrika_logs.py` в allowed_files (расширить `date_from` запроса на N дней, не примешивая эти визиты ни в одну метрику/отчёт кроме цепочки resolve). |
| **4X-metrika-lookback** | PARTIAL | 2026-07-22. Устраняет extract-часть blocker'а задачи 4X-traffic-resolve. `src/extract/metrika_logs.py`: `_run_full` теперь дополнительно запрашивает `config.transform.traffic_resolve_lookback_days` (config/defaults.yaml, default 30) дней ДО `data_window.date_from` — только контекст для carry-forward цепочки clientID (T02/T03), не для метрик. Новая функция `_fetch_lookback()` переиспользует уже согласованный набор `fields` основного окна (валидность полей от диапазона дат не зависит — повторной evaluate нет), пишет результат в новый подкаталог `LOOKBACK_SUBDIR` ("lookback/", тот же приём, что и `backfill/`): `_read_metrika_logs_rows`/`build_visits` глобят только `visits_*.csv.gz` ВЕРХНЕГО уровня `src_dir`, поэтому лог-визиты lookback им физически не видны — гарантия «не используются ни в одной другой метрике» обеспечена расположением файла, без изменений в build_canonical.py (вне allowed_files этой задачи). Манифест фиксирует `lookback_requested_days`, `lookback_date_from_requested`, `lookback_date_to`, `lookback_rows`, `lookback_parts`, `lookback_effective_date_from`, `lookback_days_covered` — фактическая глубина считается ПО ДАННЫМ (по чанкам с rows>0), а не предполагается равной запрошенной (если у счётчика нет истории так далеко назад, старые чанки просто возвращают 0 строк). Применяется только в `_run_full` (полная выгрузка); `_run_backfill` (довыгрузка новых полей поверх уже выгруженного окна) lookback не трогает — не в скоупе задачи. tests/test_metrika_logs_lookback.py — 7 новых тестов (запрос уходит с расширенной датой на 30 дней назад, дефолт из config без явного defaults, lookback_days=0 отключает лишний запрос, файлы лежат в lookback/ отдельно от verhнего уровня, manifest фиксирует глубину/директорию, частичная история счётчика — фактическая глубина честно меньше запрошенной, лог-визиты lookback НЕ попадают в build_visits — проверено импортом неизменённого src.transform.build_canonical как оракула) — 7 pass. Регрессия: `pytest tests/test_metrika_logs_patch.py` — 11 passed; `pytest tests/test_extract_smoke.py` — 9 failed/41 passed, те же 9 pre-existing падений, что и до этой задачи (regionCity/lastSignhasGCLID/gsc_manual/webmaster_manual/wordstat — не связаны с lookback); `pytest tests/test_build_canonical.py tests/test_config_schema.py tests/test_transform_visits_traffic_resolve.py tests/test_metrika_logs_lookback.py tests/test_metrika_logs_patch.py` — 189 passed. **Не сделано — статус PARTIAL, два пункта задания вне allowed_files этой задачи (`src/extract/metrika_logs.py` только, `build_canonical.py` не входит):** (а) пункт 2 задания («пометить визиты флагом is_lookback_only=true в raw/canonical») реализован только через раздельное расположение файла (директория `lookback/`), НЕ как per-row канонический булев столбец — raw-слой хранит ровно то, что вернул API, без синтетических колонок (принцип неизменности сырья), поэтому per-row `is_lookback_only` в `visits.parquet` требует отдельной задачи с `build_canonical.py` в allowed_files (научить `build_visits` читать `metrika_logs/lookback/` отдельно и проставлять флаг explicit); (б) `_should_backfill`/`_already_extracted` не проверяют наличие lookback-данных — если окно уже выгружено с текущим `SCHEMA_VERSION` (visits-v4, не менялась этой задачей, т.к. lookback не меняет состав полей визита), extract пропускает повторную выгрузку целиком и lookback для уже существующих окон НЕ дозаписывается; починка потребовала бы отдельного реконсиляционного условия (по аналогии со schema_version) и не была частью явного задания — зафиксировано как известное ограничение, не молча. |
| **4X-lookback-wiring-check** | PARTIAL, шаг 3 ждёт подтверждения | 2026-07-22. **Шаг 1 — подтверждено фактом, не переделано вслепую:** `resolve_traffic_source` (через `build_visits`) НЕ видит `metrika_logs/lookback/` — `_read_metrika_logs_rows` использует `raw_dir.glob("visits_*.csv.gz")` (нерекурсивный glob), подкаталоги физически не входят в результат. Это тот же архитектурный пробел, что уже зафиксирован в задаче 4X-metrika-lookback, пункт (а); повторно подтверждён явным тестом (`test_build_visits_does_not_see_lookback_subdir_rows`, `test_read_metrika_logs_rows_globs_top_level_only_by_construction`), а не переделан — `build_canonical.py` в этой задаче трогается только на чтение (см. allowed_files). **Шаг 2 — DONE:** `src/extract/metrika_logs.py`: новый параметр `extract(..., force_lookback_backfill=True)` + функция `_run_lookback_backfill_only()` — принудительно дозаполняет `LOOKBACK_SUBDIR` для уже извлечённого окна, не дожидаясь `_should_backfill`/`_already_extracted` и не трогая `visits_*.csv.gz`/`backfill/`. Поля переиспользуются из `existing.available_fields`/`fields` (без повторного `/logrequests/evaluate`). Все прочие поля предыдущей записи манифеста (schema_version, region_field, dropped_fields и т.п.) явно переносятся (`_ENTRY_MANAGED_KEYS`) — `manifest.update_source` перезаписывает запись целиком, merge не делает сам. Если у окна ещё нет записи в манифесте — форсировать нечего, откат на обычную `_run_full` (уже включает lookback). Аналог CLI-флага не добавлялся: у существующего параметра `backfill` в этом модуле тоже нет CLI/orchestrator-обвязки (`run.py`/`src/pipeline/orchestrator.py` не грепаются на "backfill" вовсе) — новый параметр `force_lookback_backfill` следует тому же прецеденту (вызывается напрямую как kwarg `extract()`, `run.py`/`orchestrator.py` вне allowed_files этой задачи). tests/test_lookback_wiring_check.py — 7 новых тестов (видимость lookback/ подтверждена дважды, force_lookback_backfill не вызывает evaluate повторно, не трогает основной слой/backfill/, переносит прежние поля манифеста, без предыдущей записи откатывается на full run, canonical build_visits() до/после принудительного lookback идентичен побайтово через `pd.testing.assert_frame_equal`) — 7 pass. Регрессия: `pytest tests/test_metrika_logs_lookback.py tests/test_metrika_logs_patch.py tests/test_build_canonical.py tests/test_config_schema.py tests/test_transform_visits_traffic_resolve.py` — 196 passed; `tests/test_extract_smoke.py` — те же 9 pre-existing падений, что и раньше (не связаны с этой задачей). **Шаг 3 — НЕ ВЫПОЛНЕН, ждёт явного подтверждения оператора:** `clients/pognali.rent/.env` содержит настоящий `METRIKA_TOKEN`, а исходящая сеть до `api-metrika.yandex.net` в этой сессии оказалась доступна (проверено `GET /management/v1/counters` без токена → 401, т.е. хост отвечает) — технически принудительный прогон возможен. Реальный `data/raw/manifest.json` клиента показывает `schema_version="visits-v3"` (устарела относительно текущей `SCHEMA_VERSION="visits-v4"`) и НЕ содержит ни одного `lookback_*` поля — лайв-запуска с lookback для этого клиента никогда не было. Прогон принудительного backfill на реальном счётчике — это вызов боевого стороннего API (создаёт асинхронный logrequest на серверах Яндекса, расходует квоту токена) с реальными последствиями за пределами песочницы; в отличие от локальных обратимых правок кода, это не отменить кликом, поэтому запуск не выполнен без явного «да» от оператора в чате (см. правила по hard-to-reverse/external-system действиям). Дождавшись подтверждения, команда для реального прогона: `python -c "from src.extract import metrika_logs; from src.pipeline import intake; ..."` (нужно собрать `paths`/`config`/`env` клиента pognali.rent тем же способом, что и `run.py --stage extract`, с `force_lookback_backfill=True`) — код для этого уже готов и покрыт тестами, реального прогона не хватает. |
| **4X-direct-normalize** | PARTIAL | 2026-07-22. **Уточнение по заданию:** allowed_files называл несуществующий `src/transform/direct_*.py` — по прецеденту 4X-webmaster-transform создан новый модуль `src/transform/direct_normalize.py` (build_canonical.py не редактировался, вне allowed_files). **(1) cost_normalized = cost_raw/1_000_000 — для search_query_performance/campaign_performance/geo уже реализовано ДО этой задачи** в build_canonical.py (`build_direct_queries`/`build_direct_campaigns`/`build_direct_geo` + `_parse_cost`, задача 2B-patch/наименование исправлено после находки в step0). Не переделывалось (не в allowed_files, работает и покрыто test_build_canonical.py). Добавлено то, чего не было: `build_direct_placements()` в direct_normalize.py — тот же `_parse_cost` для `placements/placement_performance.tsv` (PLACEMENT_FIELDS без Date/Impressions, отчёт агрегирован за весь период). **ВАЖНО — терминологический конфликт, не молчу о нём:** `data-export-spec-v1.md` §C, правило D06/D07, запрещает выводить `cost_normalized` из `cost_raw` автоформулой без ответа Q01 — но это правило про `costs.parquet` (build_costs), где `cost_normalized` = НДС-база (net/gross/vat_basis_unknown, задача 4B). Для отчётных Direct-таблиц (queries/campaigns/geo/placements) `cost_normalized` — ДРУГАЯ величина: перевод микрорубли→рубли, без отношения к НДС; это существующий прецедент в коде (не введён этой задачей). Одинаковое имя `cost_normalized` с разным смыслом в costs.parquet vs report-level Direct-таблицах — риск путаницы для будущих задач, стоит либо переименовать, либо явно задокументировать в data-export-spec-v1.md (не сделано — вне allowed_files, спецификация не входит в allowed_files этой задачи). **(2) geo с явной колонкой month:** `build_direct_geo_monthly()` — читает КАЖДЫЙ `direct/geo/????-??.tsv` отдельно (в build_canonical.build_direct_geo месяцы сливаются через `_read_tsv_dir` без колонки month), month берётся из имени файла-чанка. Оригинальные помесячные TSV не трогаются (только чтение) — проверено тестом. Raw-файлы называются `direct/geo/YYYY-MM.tsv` (см. src/extract/direct.py), а не `geo_performance_*.csv`, как в тексте задачи — реального источника с таким именем в проекте нет, ориентировался на факт. **(3) ad_texts фильтр по State:** `filter_ad_texts_by_state()` + `write_ad_texts_archive()` — State=="ACTIVE" (регистронезависимо) идёт в active-список (для будущей LLM-проверки, A20–A24), остальное (включая объявления без State) пишется в `ad_texts_archived.json` в переданный out_dir; исходный `ad_texts.json` не удаляется и не изменяется. **Не сделано (требует build_canonical.py, вне allowed_files):** подключение всех трёх новых функций к общему `build()`/`SCHEMAS` — `direct_placements.parquet`/`geo.parquet` сейчас нигде не пишутся автоматически, functions публичны и покрыты тестами, но не вызываются пайплайном. tests/test_transform_direct_normalize.py — 13 новых тестов (cost_normalized 65630000→65.63, множественные строки, geo все месяцы без дублей, geo не теряет/не меняет исходные файлы, geo.parquet сквозная запись, ad_texts смешанные State, отсутствие file/State). Полный `pytest tests/` после задачи: 430 passed, 11 failed — все 11 pre-existing (regionCity→regionArea, gsc_manual/webmaster_manual/wordstat legacy, см. задачи 2A-patch/3A-patch/WS-1 выше), ни один не связан с этой задачей. |
| **4X-direct-normalize-2** | PARTIAL | 2026-07-22. Устраняет коллизию имени `cost_normalized`, отмеченную предыдущей задачей. **(1)+(2) DONE в build_canonical.py:** в `build_direct_queries`/`build_direct_campaigns`/`build_direct_geo` поле, ранее называвшееся `cost_normalized` (raw/1_000_000, валютная конверсия), переименовано в `cost_rub` — считается всегда, независимо от Q01. `cost_normalized` теперь отдельное поле, всегда `null` на этом слое; добавлен флаг `vat_basis_applied` (всегда `False` из transform) — оба заполняются compute-слоем после ответа на Q01 (`finance.vat_basis_by_source` из `client_answers.yaml`). `SCHEMAS["direct_queries"/"direct_campaigns"/"direct_geo"]` обновлены (`cost_rub: float`, `cost_normalized: float`, `vat_basis_applied: bool`). Инвариант в `_join_goal_convs` (сумма расхода не меняется джойном с целями) переключён с `cost_normalized` на `cost_rub` — сравнивать сумму always-null поля было бы бессмысленно (ложный инвариант 0.0==0.0). `costs.parquet` (build_costs, задача 4B) не тронут — там `cost_normalized`/`cost_status` уже корректно НДС-семантические, это другая таблица и другой контракт (см. докстринг `_parse_cost`, явно указывает не путать). **Не сделано (вне allowed_files этой задачи):** `direct_placements` в `src/transform/direct_normalize.py` (модуль из задачи 4X-direct-normalize) — по-прежнему использует старое имя `cost_normalized` для валютной конверсии; этот файл не входит в allowed_files (`src/transform/build_canonical.py` только), не мигрирован — коллизия имён для этой таблицы остаётся открытой. **(3) НЕ СДЕЛАНО — стоп по конфликту с реальным состоянием кода:** задание предполагает, что `src/compute/block1.py` где-то читает `cost_normalized` для Direct-таблиц и требует правки чтения поля. Проверено: `src/compute/block1.py` (и все прочие `src/compute/block0..6.py`) — пустые заглушки (`raise NotImplementedError`), НИЧЕГО не читают ни из `costs`, ни из `direct_queries/campaigns/geo`. Более того, собственный докстринг `block1.py` описывает проверки 1.1–1.5 (доходимость формы, разрыв платный/сайт, качественные причины отвала — тематика CRO/форм из старой нумерации методологии), источники — `visits.parquet`/`webvisor_findings`/`client_answers`, НЕ `costs`/Direct-таблицы. Экономические проверки A04–A08 (CPA/бюджет/эффективность, к которым по смыслу относится это задание) по `config/methodology.yaml` требуют `[costs, visits]` — то есть `costs.parquet` (build_costs, уже НДС-корректен с 4B), а не отчётные Direct-таблицы (queries/campaigns/geo), которых это задание касается. Править нечего: угадывать несуществующий код и реализовывать compute-логику "заодно" — вне протокола (см. CLAUDE.md, «Протокол микрозадач», п.5: при конфликте с источниками истины — остановиться и перечислить конфликт, не угадывать). Нужна отдельная задача с явным scope на реализацию блока/проверок, читающих `cost_rub`/`cost_normalized`/`vat_basis_applied`, после того как будет решено, в каком физическом файле новой (буквенной) схемы блоков живут A04–A08. **(4) НЕ СДЕЛАНО — вне allowed_files:** `data-export-spec-v2.md` не входит в allowed_files этой задачи (только `build_canonical.py`, `block1.py`, тесты, этот файл) — раздел C не обновлён; терминологическая коллизия (cost_raw/cost_rub/cost_normalized, три поля вместо двух) остаётся незадокументированной в спецификации v2. Тесты: `tests/test_build_canonical.py` — обновлён существующий сквозной тест build() (direct_queries.cost_normalized→null/vat_basis_applied=False, добавлена проверка cost_rub) + 6 новых тестов (cost_rub считается всегда для queries/campaigns/geo при отсутствии Q01, схема parquet cost_rub/cost_normalized/vat_basis_applied, инвариант джойна целей теперь по cost_rub) — **103 passed** (test_build_canonical.py). **BLOCKER (новый, не в allowed_files, не исправлен):** `tests/test_direct_2b_patch.py::test_query_report_dimensions` и `::test_geo_report_schema` падают — ожидали старое поведение `cost_normalized == cost_raw/1_000_000`; это прямое следствие намеренного ломающего переименования этой задачи (аналогично прецеденту 4B), файл вне allowed_files, не редактировался. Полный `pytest tests/`: **446 passed, 11 failed** — 9 pre-existing (не связаны с этой задачей) + 2 новых из test_direct_2b_patch.py (описаны выше). |
| **4X-direct-wiring** | DONE | 2026-07-22. Подключены к `build()`/`SCHEMAS` три функции, ранее существовавшие только в `src/transform/direct_normalize.py` (задача 4X-direct-normalize, вне allowed_files той и этой задачи). **Реализация — НЕ реэкспорт/импорт старых функций, а свежие определения прямо в `build_canonical.py`:** `direct_normalize.py` всё ещё использует дособытийное имя `cost_normalized` для валютной конверсии (коллизия, зафиксированная 4X-direct-normalize-2 как открытая, т.к. этот файл был вне allowed_files обеих задач) — реэкспорт как есть тихо вернул бы старую путаницу в новые таблицы. Поэтому `build_direct_placements`/`build_direct_geo_monthly` написаны заново в `build_canonical.py` по образцу `build_direct_queries`/`campaigns`/`geo`, с уже принятым контрактом `cost_raw`(int, микрорубли)/`cost_rub`(float, валютная конверсия, всегда)/`cost_normalized`(float, null до Q01)/`vat_basis_applied`(bool, всегда False из transform). `direct_normalize.py` не редактировался и не удалялся (вне allowed_files) — его копии `build_direct_placements`/`build_direct_geo_monthly` теперь orphaned/дублирующий код, кандидат на удаление отдельной задачей с этим файлом в allowed_files. `filter_ad_texts_by_state` — единственная функция, реально переиспользована через ленивый импорт (`from . import direct_normalize` внутри `build()`, не на верхнем уровне модуля — иначе циклический импорт, т.к. `direct_normalize.py` импортирует `build_canonical` как `bc`); эта функция не завязана на cost-именование, реэкспорт безопасен. **SCHEMAS:** добавлены `direct_placements` (placement/ad_network_type/campaign_id/cost_raw/cost_rub/cost_normalized/vat_basis_applied/clicks/conversions_all — PLACEMENT_FIELDS не содержит Date/Impressions, см. src/extract/direct.py) и `geo` (то же, что direct_geo, + явная колонка `month`, из имени файла-чанка `direct/geo/????-??.tsv`) — таблица `geo` сознательно ОТДЕЛЬНАЯ от уже существующей `direct_geo` (та же исходная выгрузка, без month) — так было явно затребовано исходной задачей 4X-direct-normalize (`geo.parquet` как отдельный файл); консолидация двух geo-таблиц в одну не выполнялась — архитектурное решение вне скоупа этой чисто «подключающей» задачи, отмечено как повод для будущей ревизии. **build():** после `direct_geo`/`campaign_strategies` добавлены блоки `direct_placements`→`direct_placements.parquet`, `geo`(monthly)→`geo.parquet`; отдельно — ad_texts: `filter_ad_texts_by_state` -> `canonical/ad_texts.json` (только ACTIVE, для будущей LLM-проверки A20–A24) + `canonical/ad_texts_archived.json` (остальное, не удаляется), только если `direct/ad_texts.json` есть в raw; `flags["ad_texts"] = {active_count, archived_count}` в canonical manifest. Тесты (`tests/test_build_canonical.py`): `test_build_wires_placements_geo_monthly_and_ad_texts` — сквозной build() с фикстурами всех трёх источников (placements TSV, 2 помесячных geo TSV, ad_texts.json со State ACTIVE/ARCHIVED), проверяет реальные выходные файлы (direct_placements.parquet, geo.parquet с обоими месяцами, ad_texts.json/ad_texts_archived.json с правильным разбиением, наличие в canonical manifest.json) — не только unit-тест функций отдельно. `test_build_no_ad_texts_source_writes_no_ad_texts_files` — без raw ad_texts.json canonical-файлы не создаются. Оба + вся `test_build_canonical.py` — **105 passed**. Полный `pytest tests/`: **455 passed, 11 failed** — те же 11, что и после 4X-direct-normalize-2 (9 pre-existing + 2 test_direct_2b_patch.py), состав не изменился этой задачей. (Один прогон в процессе работы показал транзиентный 12-й фейл в `tests/test_metrika_logs_lookback.py` — не воспроизвёлся при повторном запуске и никак не связан с изменёнными этой задачей файлами/темой (visits/metrika_logs lookback, вне allowed_files); похоже на гонку с параллельной правкой того же репозитория в другой сессии, не заслуга/вина этой задачи.) |
| **4X-direct-placements-align** | DONE | 2026-07-22. Закрывает гэп, оставленный 4X-direct-wiring: `build_direct_placements` в `src/transform/direct_normalize.py` (этот файл наконец в allowed_files) переименован под контракт `cost_raw`(микрорубли, как было)/`cost_rub`(валютная конверсия raw/1_000_000, считается всегда — было `cost_normalized`)/`cost_normalized`(новое поле, всегда `null` на этом слое)/`vat_basis_applied`(новое поле, всегда `False`) — то же самое, что уже сделано для queries/campaigns/geo в `build_canonical.py` (4X-direct-normalize-2) и для копий этих же функций внутри `build_canonical.py` (4X-direct-wiring). Модульный докстринг `direct_normalize.py` переписан, чтобы явно отделить `build_direct_placements` (контракт выровнен) от `build_direct_geo_monthly` (контракт НЕ выровнен — по заданию эта задача касалась только placements, `build_direct_geo_monthly` в этом файле по-прежнему называет валютную конверсию `cost_normalized`, та же коллизия остаётся открытой для будущей задачи). **Дублирование, оставленное 4X-direct-wiring, не устранено** (не входило в scope): `build_canonical.py` имеет свою отдельную, уже корректную копию `build_direct_placements` для реального пайплайна — правка этой задачи в `direct_normalize.py` не влияет на неё; `direct_normalize.build_direct_placements` теперь корректен сам по себе, но по-прежнему не вызывается ниоткуда (orphaned, как и раньше). Тесты (`tests/test_transform_direct_normalize.py`): переименован `test_placements_cost_normalized_known_example` → `test_placements_cost_rub_known_example` (проверяет `cost_rub`=65.63, `cost_normalized` null, `vat_basis_applied=False`); `test_placements_multiple_rows_all_normalized` → `test_placements_multiple_rows_all_cost_rub_normalized` (аналогично на 2 строках); добавлен `test_all_four_direct_tables_share_cost_contract_fields` — один общий тест на все четыре Direct-таблицы (`direct_placements` из `direct_normalize`, `direct_queries`/`direct_campaigns`/`direct_geo` из `build_canonical`, только чтение/импорт — не редактировался), подтверждает идентичный набор денежных полей и семантику на одинаковой фикстуре. `pytest tests/test_transform_direct_normalize.py` — **14 passed**. Полный `pytest tests/`: **456 passed, 11 failed** — тот же состав 11 pre-existing, что и после 4X-direct-wiring; регрессий не внесено. |
| **4X-direct-reconcile** | REPORT-ONLY (без правок кода, по заданию) | 2026-07-22. Разведка дублирования Direct build_*-функций между `src/transform/build_canonical.py` и `src/transform/direct_normalize.py`, накопившегося за 4X-direct-normalize → 4X-direct-normalize-2 → 4X-direct-wiring → 4X-direct-placements-align. **(1) Реально исполняется при `run.py <client> --stage transform`:** только `build_canonical.py`. Цепочка вызовов подтверждена по коду: `run.py:44` → `orch.run_transform` → `src/pipeline/orchestrator.py:461,467` (`from ..transform import build_canonical; build_canonical.build(...)`). `direct_normalize.py` НЕ импортируется ни `run.py`, ни `orchestrator.py` напрямую — единственная связь: `build_canonical.build()` делает ленивый `from . import direct_normalize as _direct_normalize` внутри тела функции (см. build_canonical.py:1741) и вызывает ТОЛЬКО `_direct_normalize.filter_ad_texts_by_state(...)` (build_canonical.py:1742). `write_ad_texts_archive` (direct_normalize.py) при этом НЕ вызывается вообще нигде — `build()` пишет `ad_texts_archived.json` инлайн через `json.dump` (build_canonical.py:1746-1747), дублируя то, что уже делает `write_ad_texts_archive`; сама эта функция сейчас мёртвый код (экспортируется, тестируется, но не используется пайплайном). **(2) Построчное сравнение `build_direct_placements`:** идентичны по логике в обоих файлах — единственное отличие `path = direct_dir / ...` (build_canonical.py) vs `path = Path(direct_dir) / ...` (direct_normalize.py), поведенчески без разницы (direct_dir и так Path на всех вызовах; `Path(Path(x))` — no-op). Оба используют контракт `cost_raw`/`cost_rub`/`cost_normalized=None`/`vat_basis_applied=False`. Реально исполняется копия из `build_canonical.py` (см. п.1); копия в `direct_normalize.py` корректна, но orphaned (задача 4X-direct-placements-align привела её к тому же контракту, не зная, что реальный пайплайн её не вызывает). **(3) `build_direct_geo_monthly` — РАСХОДИТСЯ между копиями, подтверждено построчным диффом:** копия в `build_canonical.py` (реально исполняется) — контракт УЖЕ разделён: `cost_raw`(int)/`cost_rub`(float, всегда)/`cost_normalized`(None)/`vat_basis_applied`(False), как и утверждала задача 4X-direct-normalize-2/4X-direct-wiring — утверждение НЕ было ошибочным для этого файла. Копия в `direct_normalize.py` — контракт НЕ разделён: возвращает только `cost_raw`/`cost_normalized`(валютная конверсия raw/1_000_000, старая семантика), полей `cost_rub`/`vat_basis_applied` нет вовсе; это ожидаемо и уже задокументировано в докстринге самого файла (строки 24-28) как результат явного решения задачи 4X-direct-placements-align ограничить скоуп только placements. `build_direct_queries`/`build_direct_campaigns`/`build_direct_geo` — существуют ТОЛЬКО в `build_canonical.py` (никогда не дублировались в `direct_normalize.py`), все три подтверждены построчным чтением: `cost_raw`(int)/`cost_rub`(float)/`cost_normalized`(None)/`vat_basis_applied`(False) — контракт разделён корректно и единообразно.
| **4X-lookback-canonical-flag** | DONE (transform); сопутствующий blocker закрыт задачей 4X-lookback-canonical-flag-tests | 2026-07-22. Закрывает архитектурный пробел, зафиксированный 4X-metrika-lookback/4X-lookback-wiring-check: `build_visits()` (`src/transform/build_canonical.py`) теперь читает и верхний уровень `raw_dir`, и `raw_dir/lookback/` (новая `_read_metrika_lookback_rows`, glob `visits_lookback_*.csv.gz`, тот же `_parse_visit_row`, что и у основных визитов — поля лукбэк-запроса переиспользуют уже согласованный набор основного окна, отдельного backfill-джойна не нужно). Каждая строка результата помечена явным булевым `is_lookback_only` (True — визит лукбэк-окна). UTM-порог и склейка backfill считаются ТОЛЬКО по основному окну (до подмешивания лукбэк-строк) — присутствие лукбэк-данных не меняет `source_final`/`is_ad`/backfill-статистику визитов основного окна. `resolve_traffic_source()` вызывается на объединённом df (основное окно + лукбэк вместе, отсортированы по client_id/dt) — лукбэк-визит с реальным источником внутри `lookback_cutoff` теперь ДЕЙСТВИТЕЛЬНО может восстановить цепочку clientID для ambiguous-визита основного окна (internal/undefined), чего раньше не происходило вообще (лукбэк был физически невидим `_read_metrika_logs_rows`). `traffic_source_resolve` статистика считается только по строкам основного окна (лукбэк не входит в знаменатель unresolved). **Решение по фильтрации (задание явно предлагало выбрать один из двух вариантов и задокументировать):** `build_visits()` возвращает лукбэк-строки с флагом (не фильтрует сама — нужно для тестируемости эффекта carry-forward); фактическую фильтрацию `is_lookback_only=true` перед записью `visits.parquet` выполняет `build()` (`report_visits_df = visits_df[visits_df["is_lookback_only"] == False]`) — компьют-слой (вне allowed_files) физически никогда не видит лукбэк-строк, колонка `is_lookback_only` не входит в `SCHEMAS["visits"]` и в parquet не попадает вовсе. Новые тесты в `tests/test_build_canonical.py`: `test_build_visits_lookback_rows_tagged_and_used_for_carry_forward` (лукбэк реально чинит carry-forward через границу окна), `test_build_visits_without_lookback_dir_stays_unresolved` (контраст без lookback/), `test_build_visits_lookback_before_cutoff_does_not_resolve` (граница cutoff соблюдается), `test_build_excludes_lookback_rows_from_visits_parquet` (сквозной `build()` — parquet без лукбэк-строк и без колонки-флага), `test_build_visits_main_rows_unchanged_with_or_without_lookback` (побайтовое сравнение визитов основного окна с/без лукбэк-данных на фикстуре без ambiguous-визитов — единственный сценарий, где присутствие лукбэк вообще могло бы что-то изменить). `pytest tests/test_build_canonical.py tests/test_transform_visits_traffic_resolve.py` — **119 passed**. **BLOCKER (файл вне allowed_files этой задачи — `tests/**/test_build_canonical*.py`/`tests/**/test_transform_visits_traffic_resolve*.py` только, `test_lookback_wiring_check.py` не входит):** `tests/test_lookback_wiring_check.py::test_build_visits_does_not_see_lookback_subdir_rows` и `::test_force_lookback_backfill_does_not_change_existing_canonical_output` падают — они явно документировали и проверяли СТАРОЕ (архитектурно неверное) поведение «лукбэк невидим build_visits», которое эта задача намеренно устраняет; `test_read_metrika_logs_rows_globs_top_level_only_by_construction` в том же файле по-прежнему проходит (не переделан этой задачей: `_read_metrika_logs_rows` остаётся нерекурсивным, лукбэк читается отдельной новой функцией `_read_metrika_lookback_rows`, а не изменением `_read_metrika_logs_rows`). Нужна отдельная задача с `tests/test_lookback_wiring_check.py` в allowed_files, чтобы заменить/удалить эти два теста под новый контракт (как и предполагало исходное задание словами «заменить/обновить его»). Полный `pytest tests/` не прогонялся (не требовалось заданием — см. CLAUDE.md, «Протокол микрозадач», п.7); регрессия ограничена целевыми файлами. |
| **4X-lookback-canonical-flag-tests** | DONE | 2026-07-22. Закрывает blocker из 4X-lookback-canonical-flag: два теста в `tests/test_lookback_wiring_check.py`, документировавшие СТАРЫЙ контракт («лукбэк физически невидим build_visits»), переписаны под новый. `test_build_visits_does_not_see_lookback_subdir_rows` → `test_build_visits_sees_lookback_rows_flagged`: собственный набор HTTP-моков (не переиспользует `_full_routes`, т.к. там оба чанка — основной и лукбэк — используют один `request_id`/статичный текст ответа и потому неразличимы) с колбэком `download_responder(n)`, различающим по счётчику вызовов основной чанк (`MAIN_PART_TEXT`, visit `v1`) от лукбэк-чанка (свой текст, visit `vlb`); утверждается, что `build_visits()` возвращает 2 строки, `v1` с `is_lookback_only=False`, `vlb` с `is_lookback_only=True`. `test_force_lookback_backfill_does_not_change_existing_canonical_output`: сравнение сужено — раньше `assert_frame_equal` шло по всему df «до» и «после» force_lookback_backfill (что стало заведомо неверным: «после» теперь на 1 строку больше — новая `is_lookback_only=True` строка `vlb`, это ожидаемое поведение 4X-lookback-canonical-flag, а не регрессия); теперь тест отдельно проверяет, что лукбэк-строка `vlb` реально появилась и помечена (иначе последующее сравнение было бы бессмысленным — совпадало бы случайно на пустом множестве), а затем сравнивает `df_before` только с подмножеством `df_after[~is_lookback_only]` — смысл исходной проверки (принудительный лукбэк не искажает метрики основного окна) сохранён, а не просто обойдён. `test_read_metrika_logs_rows_globs_top_level_only_by_construction` и остальные тесты файла не тронуты (контракт `_read_metrika_logs_rows` не менялся). `build_canonical.py` не редактировался (allowed_files не включал его). `pytest tests/test_lookback_wiring_check.py` — **7 passed**. |

**Итоговая таблица:**

| Функция | Файл с реальным использованием (вызывается из build()/orchestrator) | Статус переименования cost_rub/cost_normalized |
|---|---|---|
| `build_direct_queries` | `build_canonical.py` (только там и существует) | Сделано |
| `build_direct_campaigns` | `build_canonical.py` (только там и существует) | Сделано |
| `build_direct_geo` | `build_canonical.py` (только там и существует) | Сделано |
| `build_direct_placements` | `build_canonical.py` (своя копия; копия в `direct_normalize.py` не вызывается) | Сделано в обеих копиях (идентичны, кроме косметики) |
| `build_direct_geo_monthly` | `build_canonical.py` (своя копия; копия в `direct_normalize.py` не вызывается) | Расходится между копиями: сделано в `build_canonical.py`, НЕ сделано в `direct_normalize.py` (там всё ещё `cost_normalized`=валюта, нет `cost_rub`/`vat_basis_applied`) |
| `filter_ad_texts_by_state` | `direct_normalize.py` (реально вызывается из `build()` через ленивый импорт) | Н/п (поле cost не касается) |
| `write_ad_texts_archive` | Нигде — не вызывается ни `build_canonical.py`, ни чем-либо ещё (мёртвый код, обнаружено этой разведкой, не запрошено заданием) | Н/п |

**Не исправлено (по заданию — только отчёт):** дублирование `build_direct_placements`/`build_direct_geo_monthly` между двумя файлами; расхождение контракта `build_direct_geo_monthly` в orphaned-копии; мёртвый код `write_ad_texts_archive`. Рекомендация для отдельной задачи с обоими файлами в allowed_files: удалить дублирующие копии из `direct_normalize.py` (оставив там только `filter_ad_texts_by_state`/`write_ad_texts_archive`, либо тоже удалить последнюю, либо начать её реально вызывать вместо инлайн-`json.dump` в `build()`), либо наоборот — перенести реализацию в `direct_normalize.py` и импортировать оттуда в `build_canonical.py`. Полный `pytest tests/` не запускался в рамках этой задачи (только чтение, кода не менялось; последний известный результат — 456 passed, 11 failed, см. 4X-direct-placements-align). |
| **4X-direct-cleanup** | DONE | 2026-07-22. Выполняет рекомендацию 4X-direct-reconcile: первый вариант (удалить дубли из `direct_normalize.py`, не трогая `build_canonical.py`). **(1) Удалено из `src/transform/direct_normalize.py`:** `build_direct_placements` (был идентичен рабочей копии в `build_canonical.py`, безопасно — подтверждено построчным диффом в 4X-direct-reconcile) и `build_direct_geo_monthly` (устаревшая копия с коллизией имён `cost_normalized`=валюта — опасный "образец", удалена, а не исправлена на месте, т.к. никем не вызывалась). **(2) Удалено `write_ad_texts_archive`** — подтверждено 4X-direct-reconcile как мёртвый код (`build_canonical.build()` пишет `ad_texts_archived.json` инлайн, эту функцию никто не вызывал). Модуль теперь содержит только `filter_ad_texts_by_state`, докстринг переписан с явной историей (зачем были и почему удалены остальные функции), импорт `from . import build_canonical as bc` и `import pandas as pd` убраны как более не нужные (единственная оставшаяся функция их не использует). **(3) Построчная сверка инлайн-логики ad_texts в `build()` (тот же уровень строгости, что для cost-полей в 4X-direct-reconcile, не просто "reimplements"):** прочитаны `build_canonical.py:1736-1748` дословно. Подтверждено построчно: `_direct_normalize.filter_ad_texts_by_state(direct_dir)` (:1742) — тот самый фильтр `State=="ACTIVE"` (регистронезависимо, строка приводится к upper через `.strip().upper()`), отсутствие поля `State` трактуется как не-ACTIVE (попадает в archived) — совпадает с исходным контрактом задачи 4X-direct-normalize дословно ("оставлять только строки State=ACTIVE, остальные — в отдельный ad_texts_archived.json, не удалять"). Условие записи — `if (direct_dir / "ad_texts.json").exists():` (:1743) — раздел вообще не исполняется, если raw-файла нет (сравнимо с `write_ad_texts_archive`, который возвращал `None` в этом случае). Запись — `open(canonical_dir / "ad_texts.json", "w")` (:1744) для active и `open(canonical_dir / "ad_texts_archived.json", "w")` (:1746) для archived — оба пути строго в `canonical_dir`, ни один вызов `open(...)` во всём блоке (:1736-1748) не открывает `direct_dir / "ad_texts.json"` в режиме записи и не вызывает `os.remove`/`Path.unlink` — raw-файл физически не может быть изменён или удалён этим кодом. Проверено также фактическим прогоном (см. тесты ниже): байты и `mtime` raw-файла идентичны до/после `build()`. Расхождение с `write_ad_texts_archive`, которое чисто структурное, а не поведенческое: инлайн-код пишет ОБА файла (active и archived) одним проходом, тогда как удалённая функция писала только archived (active записывался отдельным `json.dump` прямо в `build()`, до и после удаления функции — не менялось этой задачей). Вывод: инлайн-реализация полностью и корректно покрывает исходный контракт задачи 4X-direct-normalize; предыдущая формулировка "reimplements" в 4X-direct-reconcile была верной по факту, но не подтверждённой построчно — теперь подтверждена. **(4) Тесты (`tests/test_transform_direct_normalize.py`, переписан):** удалены все тесты `build_direct_placements`/`build_direct_geo_monthly`/`write_ad_texts_archive` (14 → 5 тестов) — 3 юнит-теста `filter_ad_texts_by_state` (смешанные State, отсутствие файла, отсутствие поля State) сохранены без изменений. Добавлен `test_build_ad_texts_inline_logic_keeps_raw_intact_and_splits_correctly` — сквозной прогон `build_canonical.build()` с фикстурой из 3 объявлений (ACTIVE/ARCHIVED/без State), явно сравнивает `read_bytes()`+`mtime` raw `ad_texts.json` до/после `build()` (байт-в-байт и время модификации не изменились), проверяет разбиение по active/archived (включая запись без State в archived) и `flags["ad_texts"]` в canonical manifest. Добавлен `test_build_no_ad_texts_source_writes_no_ad_texts_files` (аналог уже существующего в `test_build_canonical.py` из задачи 4X-direct-wiring — продублирован здесь намеренно, чтобы модуль тестировался самодостаточно в рамках своего allowed_files, не редактируя `test_build_canonical.py`). `pytest tests/test_transform_direct_normalize.py` — **5 passed**. Полный `pytest tests/`: **454 passed, 11 failed** — тот же состав 11 pre-existing (не связаны с этой задачей); подтверждено grep'ом по всему репозиторию, что `direct_normalize.build_direct_placements`/`build_direct_geo_monthly`/`write_ad_texts_archive` нигде больше не упоминаются (кроме истории в докстринге) — удаление не оставило висячих ссылок. |
| **common-error-logging-fix** | DONE | 2026-07-22. `src/extract/_common.py::http_request`: сетевой сбой (`except Exception as exc` в цикле ретраев) поднимал `SourceUnavailable` только с `type(exc).__name__` — сам текст исключения (детали SSL/DNS/timeout) терялся, аналитик видел лишь имя класса без причины. Сообщение расширено до `f"{type(exc).__name__}: {exc}"` — оба сохранены, ничего не отброшено. Сообщение уже попадало в обычный лог оркестратора (`orchestrator.py:396`, `log(...)` вызывается безусловно, debug-only канала для этого сообщения в коде нет) — доп. правок для этого пункта не потребовалось, только текст самого исключения стал информативным. **Пункт 3 задания (убрать формулировку «код 3» рядом с сетевой ошибкой) НЕ выполнен — вне allowed_files этой задачи:** сама фраза `(код {exc.exit_code})` формируется в `orchestrator.py:396,413`, который не входит в `allowed_files` (только `src/extract/_common.py`, `tests/**/test_common*.py`, этот файл) и не был изменён по протоколу микрозадач (п.2 — не расширять скоуп самостоятельно). Нужна отдельная задача с `orchestrator.py` в allowed_files, чтобы подписать `exc.exit_code` явно как внутренний код деградации оркестратора (`EXIT_SOURCE_UNAVAILABLE`), а не как код конкретной сетевой/SSL-ошибки. Новый `tests/test_common_error_logging.py` (2 теста): полный текст исходного исключения (не только имя класса) присутствует в `SourceUnavailable`; `http_request` не имеет debug-гейта для этого сообщения. `pytest tests/test_common_error_logging.py tests/test_extract_smoke.py -k http_` — 5 passed. |
| **common-error-logging-fix-orchestrator** | DONE | 2026-07-22. Продолжение `common-error-logging-fix`, пункт 3. `src/pipeline/orchestrator.py:396,413`: сообщение лога `extract[...]: ИСТОЧНИК НЕДОСТУПЕН — {exc} (код {exc.exit_code})` держало `(код N)` вплотную к тексту исходного исключения (который теперь, после предыдущей задачи, включает `str(exc)` сетевой/SSL-ошибки) — читалось так, будто число рядом является кодом самой сетевой ошибки, хотя это `EXIT_SOURCE_UNAVAILABLE` — внутренний код деградации оркестратора (`src/extract/_common.py`), не зависящий от природы исключения. В обоих местах формулировка заменена на `{exc} (внутренний код оркестратора {exc.exit_code}, не код ошибки из текста выше)` — логика остановки источника не менялась, только текст сообщения. Новый `tests/test_orchestrator_error_logging.py` (2 теста, фейковый extractor-модуль через `sys.modules` + `EXTRACTORS`/`load_client_config` монкипатч, без сети): лог явно содержит «внутренний код оркестратора N» и не содержит «(код N)» вплотную к тексту ошибки — для `SourceUnavailable` (сетевой сбой) и для `AuthError`. `pytest tests/test_orchestrator_error_logging.py` — 2 passed. Регрессия: `pytest tests/test_common_error_logging.py tests/test_extract_smoke.py tests/test_smoke.py` — те же 9 pre-existing падений в `test_extract_smoke.py`, не связанные с этой задачей (подтверждено `git stash` на `orchestrator.py`: тот же набор падений без правки). |
| **ad_texts-state-fix** | DONE | 2026-07-22. Исправлен критерий active-фильтра `filter_ad_texts_by_state` (`src/transform/direct_normalize.py`) — `State=="ACTIVE"` никогда не совпадал с реальными данными API: по официальной документации Ad.State допустимые значения — ON/OFF/SUSPENDED/ARCHIVED, значения "ACTIVE" не существует (баг унаследован ещё из первой реализации, задача 4X-direct-normalize, и с тех пор ни разу не переисследовался). Критерий заменён на `State=="ON"` (регистронезависимо, как и раньше). **Категоризация OFF/SUSPENDED (решение подтверждено оператором в чате, не выбрано самостоятельно):** объединены с archived — active строго `ON`, всё остальное (OFF, SUSPENDED, ARCHIVED, отсутствие State) уходит в `ad_texts_archived.json`, отдельная категория "suspended" не заводилась. **Расширение allowed_files по ходу задачи (оба раза — с подтверждением оператора, не самостоятельно):** (1) реальное место бага оказалось в `src/transform/direct_normalize.py`, а не в `build_canonical.py`, как предполагал исходный allowed_files задачи (в `build_canonical.py` — только вызов через ленивый импорт и один комментарий-упоминание критерия, строка 1806, тоже поправлен); (2) `tests/test_build_canonical.py:598` использовал ту же фикстуру `State: "ACTIVE"` и сломался бы фиксом — фикстура заменена на `"ON"`, ассерты не менялись. **Дополнение 2026-07-22 (по прямому запросу оператора после отчёта):** `data-export-spec-v2.md` (строки 9, 88) поправлен — критерий `State=ACTIVE` заменён на `State=ON` с явным перечислением допустимых значений (ON/OFF/SUSPENDED/ARCHIVED), добавлено разъяснение, что `State` и `Status` — разные поля объекта Ad (`Status` — результат модерации: MODERATION/ACCEPTED/REJECTED/DRAFT, ранее ошибочно назван значением `State`). Правки в тексте помечены как «ред. 4» по конвенции ревизий, уже принятой в файле (см. «ред. 2, уточнено ред. 3» в разделе про валюту). Тесты: `tests/test_transform_direct_normalize.py` — фикстуры `ACTIVE`→`ON`/`active`→`on` (регистр), докстринги поправлены под новый критерий и явно называют допустимые значения Ad.State. `pytest tests/test_transform_direct_normalize.py tests/test_build_canonical.py` — **115 passed**. |
| **seo_queries-impressions-threshold** | DONE | 2026-07-22. Отбрасывает шумовые SEO-запросы: `filter_seo_queries_min_shows(df, min_shows)` (`src/transform/build_canonical.py`, рядом с `is_brand_query`) — строки `seo_queries` с `total_shows < min_shows` исключаются. Единый порог для GSC и Вебмастер (в реестре/методологии не нашлось признаков, что источники должны различаться, — решение не угадывалось: разница "GSC 100-200 показов", упомянутая в задании, не встретилась ни в `config/methodology.yaml`, ни в `catalog-proveryaemyh-marketingovyh-ugroz-v2.md`, ни в `marketing-diagnostics-methodology-v2.md`). Порог вынесен в конфиг, не захардкожен: `config/defaults.yaml: transform.seo_queries_min_total_shows: 10`, читается в `build()` тем же паттерном, что и `traffic_resolve_lookback_days`. Применяется в `build()` после дедупа `(query, page, source)`, до записи parquet; если после фильтра `seo_df` пуст — `seo_queries.parquet` не пишется и `"seo_queries"` не попадает в `built` (тот же паттерн, что у остальных таблиц). Новые тесты в `tests/test_build_canonical.py`: `test_filter_seo_queries_min_shows_excludes_below_threshold` (9 исключается, 10 остаётся), `test_filter_seo_queries_min_shows_empty_df_passthrough`, `test_seo_queries_build_filters_low_impressions_via_orchestrator` (сквозной `build()`), `test_seo_queries_build_respects_configured_threshold` (кастомный порог из defaults). `pytest tests/test_build_canonical.py -k seo_queries` — **14 passed**. |
| **wordstat-permission-vs-auth-error-message** | DONE | 2026-07-22. `src/extract/_common.py`: 401 (UNAUTHENTICATED) и 403 (PERMISSION_DENIED) больше не маппятся в одно и то же вводящее в заблуждение "токен мёртв, обнови в .env" — `auth_dead_message(source, status=None)` получил опциональный `status`; при `status==403` возвращает отдельное сообщение ("ключ валиден, но не хватает прав — проверь роль сервисного аккаунта/биллинг в кабинете, замена токена не поможет"), при 401 или отсутствии статуса — старый текст без изменений (обратная совместимость для вызовов без status: `get_token`, `gsc_api.py`, `webmaster_api.py`, `direct.py`). `http_request()` и `ensure_ok()` (единственные места в `_common.py`, где реально проверяется `AUTH_STATUSES`) теперь прокидывают фактический `status` в `auth_dead_message`. **Не устранено (вне allowed_files):** `webmaster_api.py:97` и `gsc_api.py`/`direct.py` имеют собственные прямые проверки `status in C.AUTH_STATUSES` / вызовы `auth_dead_message(SOURCE)` без `status` — для 403 через эти пути по-прежнему возвращается общий текст, так как эти файлы не в `allowed_files` этой задачи. Новый `tests/test_common_auth_message.py` (9 тестов): `auth_dead_message` напрямую (401/403/default), `http_request` и `ensure_ok` с фикстурами 401/403 — сообщения различаются и не ретраятся. `pytest tests/test_common_auth_message.py tests/test_common_error_logging.py` — **9 passed**. Полный `pytest tests/test_extract_smoke.py` — те же 9 pre-existing падений (подтверждено `git stash` на `_common.py`), не связаны с этой задачей. |
| **goal-flags-overtrigger-symmetry-check** | DONE | 2026-07-22. Проверка на реальных данных Pognali (34227 визитов, `data/raw/metrika_logs/visits_*.csv.gz`, без backfill): переотработка (>1 срабатывания одной цели за визит) — **не уникальна для form_submit**, паттерн подтверждён для всех четырёх групп целей. Доля визитов-хитов (>=1 срабатывание), где сработало >1 раз: form_submit — 553/629 = **87.9%** (уже была известна, см. комментарий в `clients/pognali.rent/config.yaml: goals`, ×2.5–3.9 переотработка); form_open — 313/606 = **51.7%**; messenger_click — 78/341 = **22.9%**; call_click — 14/146 = **9.6%**. Ни одна из трёх групп не показала "ровно одно срабатывание на визит" — асимметрия "считаем count только там, где уже нашли проблему" устранена: `goal_flags()` (`src/transform/build_canonical.py`) теперь возвращает `form_open_count`, `call_click_count`, `messenger_click_count` по аналогии с `form_submit_count` (все четыре — `sum(1 for g in goal_ids if g in <group>_ids)`, дубликаты `goal_ids` из `parse_goal_ids` для этого уже сохранялись). Прокинуто через `_parse_visit_row` в канонические колонки `visits.parquet` и добавлено в `SCHEMAS["visits"]` (все три — `"int"`, PyArrow `int64`, как и `form_submit_count`); базовые 16 колонок контракта не тронуты — новые поля добавлены отдельным блоком. Тесты (`tests/test_build_canonical.py`): существующие `test_goal_flags_marks_visit_level_achievements_and_counts_submits` / `test_goal_flags_no_achievements` обновлены под новый набор ключей `goal_flags()`; новый `test_goal_flags_counts_overtrigger_symmetrically_across_all_groups` — фикстура с намеренно задублированными id в каждой из четырёх групп (`10×3, 20×2, 30×4, 40×2`), проверяет, что все `*_count` считаются одинаково без асимметрии. `pytest tests/test_build_canonical.py` — **115 passed**. Не затронуто (вне allowed_files этой задачи): `scripts/verify_metrika.py:34` упоминает только `form_submit_count` в описании сверки Logs↔Reports — при желании расширить сверку на новые `*_count` поля нужна отдельная задача с этим файлом в `allowed_files`. |
| **4G-seo-queries-device** | DONE | 2026-07-23. Добавляет `device` в `seo_queries.parquet` — раньше колонки не было вовсе, поэтому S08-S10/S23/S24 не считались. `build_seo_queries_gsc` (`src/transform/build_canonical.py`): группировка изменена с `(query, page, month)` на `(query, page, device, month)` — device больше не схлопывается между строками; значение берётся из сырья как есть (contract 3A combined-экспорт даёт реальное устройство), отсутствующая колонка ИЛИ пустое значение в CSV → `"unknown"` построчно, строка не отбрасывается. `build_seo_queries_webmaster` — Вебмастер (`summary/popular-queries`) не отдаёт device вообще (`has_device_column=False` у обоих экстракторов, `webmaster_manual.py`/`webmaster_api.py`, не менялись, вне `allowed_files`) → каждая строка получает `device="unknown"` безусловно, ретроактивных допущений из Метрики нет. `SCHEMAS["seo_queries"]` — новая колонка `"device": "string"`. **Побочная правка в рамках той же задачи (нельзя было не сделать):** дедуп в `build()` (`seo_df.drop_duplicates(subset=[...])`) расширен с `["query","page","source"]` до `["query","page","source","device"]` — без этого только что добавленный device-разрез немедленно схлопывался бы обратно на этапе записи parquet (`keep="first"` тихо съедал бы все device-строки, кроме первой). `completeness`/`source_mode` логика не тронута. Новые/обновлённые тесты в `tests/test_build_canonical.py`: `test_build_seo_queries_gsc_keeps_devices_separate_and_flags_brand` (переписан из `..._aggregates_devices_...` — ломающее изменение контракта, это и есть цель задачи), `test_build_seo_queries_gsc_missing_device_column_falls_back_to_unknown`, `test_build_seo_queries_gsc_empty_device_value_falls_back_to_unknown`, `test_build_seo_queries_webmaster_device_is_always_unknown`; `test_build_seo_queries_gsc_month_without_device_not_dropped` — докстринг и ассерт обновлены под новый факт (device участвует в группировке). `pytest tests/test_build_canonical.py -k seo_queries` — **17 passed**. Полный `pytest tests/test_build_canonical.py` — **124 passed**, 0 failed. |
| **4H-geo-dedup** | DONE | 2026-07-23. Устраняет дублирование `geo.parquet`/`direct_geo.parquet` внутри `src/transform/build_canonical.py` — обе таблицы читали один и тот же `direct/geo/*.tsv` (у `build_direct_geo` `_read_tsv_dir` уже матчил и помесячные чанки `????-??.tsv`), различались только тем, что `geo` (`build_direct_geo_monthly`) несла отдельную колонку `month`, взятую из имени файла-чанка, а `direct_geo` — нет. Найдено 0 консьюмеров `geo.parquet` где-либо в `src/` (compute/analyze/report/pipeline не читают ни `geo`, ни `direct_geo` вовсе — обе таблицы существовали, но не были подключены ни к одной проверке методологии), поэтому конфликта семантики поля не возникло. `build_direct_geo` теперь сам считает `month = date.strftime("%Y-%m")` построчно (тривиально выводится из `date`, который уже парсится в той же функции — не отдельный источник правды); `SCHEMAS["direct_geo"]` получил колонку `"month": "string"`. `build_direct_geo_monthly` и `SCHEMAS["geo"]` удалены из `build_canonical.py` целиком, вызов в `build()` (писал `geo.parquet`) убран — `geo.parquet` на выходе transform больше не создаётся. Помесячные исходные TSV по-прежнему не удаляются и не изменяются (это делала уже `build_direct_geo` через `_read_tsv_dir`, поведение не менялось). `data-export-spec-v2.md` (не в `allowed_files` этой задачи) называет консолидированную таблицу `geo.parquet` — оставлено как есть, не редактировалось; выбор в пользу имени `direct_geo.parquet` сделан заданием явно (единообразно с `direct_queries`/`direct_campaigns`/`direct_placements`), а не самостоятельно. Тесты (`tests/test_build_canonical.py`): `test_build_direct_geo_cost_rub_always_computed_cost_normalized_null` дополнен проверкой `row["month"] == "2026-06"`; `test_build_wires_placements_geo_monthly_and_ad_texts` переименован в `test_build_wires_placements_direct_geo_and_ad_texts` и переписан — читает `direct_geo.parquet` вместо `geo.parquet`, проверяет `month` по обоим чанкам (2026-05/2026-06) и корректное соответствие `month`↔`date` по строкам, явно утверждает отсутствие `geo` в `built`/manifest и отсутствие файла `geo.parquet` на диске. `pytest tests/test_build_canonical.py` — **124 passed**, 0 failed. Полный `pytest tests/` — **493 passed, 13 failed**; тот же состав pre-existing падений, что и до этой задачи (подтверждено `git stash`/повторным прогоном) — ни одно не про `geo`/`direct_geo`/`month`, кроме `test_direct_2b_patch.py::test_geo_report_schema`/`test_query_report_dimensions`, которые падают по другой, уже известной причине (ожидают `cost_normalized` = валютная конверсия вместо `None`, не связано с консолидацией `month`). |
| **4I-goals-canonical** | DONE | 2026-07-23. Новая каноническая таблица `goals.parquet` (`build_goals` в `src/transform/build_canonical.py`) из `data/raw/metrika_reports/goals_list.json` (Management API): 1 строка = 1 цель — `goal_id`, `name`, `type` (сырой тип из выгрузки, не втиснут в абстрактную триаду URL/событие/составная — она не совпадает с реальными значениями `action/url/step/messenger/button/social/email/phone`), `url_pattern` + `conditions_raw` (для составных `type=step` conditions верхнего уровня пусты — оба поля берутся из вложенных `steps[*].conditions`, не теряются молча), `created_at`/`updated_at`. Последние два поля отсутствуют в реальном `goals_list.json` целиком (не абстрактная спека, а факт: проверено на боевом фикстур-примере Pognali) — колонки остаются `null`, отсутствие зафиксировано в `data/canonical/manifest.json` как `flags.goals_missing_fields = ["created_at", "updated_at"]`, не выдумано. QA-caveat (`goals_qa_caveat` + `collect_visit_goal_ids`): сверяет `goal_id` из `goals.parquet` с множеством `goalsID`, реально пришедших в сыром Logs API (`ym:s:goalsID` — `visits.parquet` этот список не хранит, там только булевы флаги по группам, см. `goal_flags`); расхождение пишется в `flags.goals_qa = {missing_in_visits, mismatch}`, не проглатывается. `config/methodology.yaml`: `D02`/`D03.requires` дополнены `goals` отдельно от `visits` (только эти две записи, остальной блок 0 не тронут). **Известный разрыв вне скоупа задачи (extract не трогать):** `src/extract/metrika_reports.py::CANONICAL_TABLES` всё ещё объявляет только `["visits"]` — `available_tables_from_manifest` (degradation.py) берёт доступность таблиц из raw `manifest.json`, который пишет extract, поэтому D02/D03 фактически станут `runnable` только после отдельного патча extract, добавляющего `goals` в `CANONICAL_TABLES`/`_record_manifest`; в этой задаче это сознательно не сделано (не в `allowed_files`). Бизнес-логика самих D02/D03 (классификация click-vs-submit, разметка микро/макро) не реализована — это 5B. Тесты: `tests/test_build_canonical.py` (schema на структуре, воспроизводящей реальный `goals_list.json` — action/url/step/auto-без-conditions; `collect_visit_goal_ids`; `goals_qa_caveat` mismatch/no-mismatch; сквозной `build()` с искусственным расхождением goal_id) + новый `tests/test_methodology_goals_requires.py` (`requires=={visits,goals}` для D02/D03; `degradation.build_degradation_report` не runnable при `available={visits}`, runnable при `available={visits,goals}`; requires соседних D04/D05 не задеты). `pytest tests/test_build_canonical.py tests/test_methodology_goals_requires.py` — **135 passed**, 0 failed; `pytest tests/test_smoke.py tests/test_config_schema.py tests/test_degradation.py` — **39 passed**, без регрессий. |

---

## Детали по задачам

### Промт 1 — Каркас пайплайна

**1A — `config/methodology.yaml`** DONE
Ровно 100 проверок (D01–D12, A01–A26, T01–T10, C01–C25, S01–S27); инварианты
уникальности id и legacy_id проверяются тестом — pass.

**1B — `src/pipeline/degradation.py`** DONE
Полная реализация: build_degradation_report, split_checks, evaluate_check,
table_source_modes, collect_manifest_flags, available_tables_from_manifest.
Все 17 тестов test_smoke.py — pass, включая downgrade A07 (A→B), confidence_cap
по manual-источникам, гейт перед report.
Добавлен `tests/test_degradation.py` — 6 выделенных тестов: недоступный источник,
type downgrade true/false, один manual required -> MED, все api -> HIGH.

**1C — `src/pipeline/manifest.py`** DONE
update_source / load_manifest работают, используются всеми экстракторами.

**1D — `clients/_template/` + `config/defaults.yaml`** DONE
Шаблон со всеми ключами sources; test_intake_template_does_not_crash — pass.

---

### Промт 2 — Слой extract

**2A — `metrika_logs.py` + `metrika_reports.py`** DONE
SCHEMA_VERSION = "visits-v2", PATCH_DATE. Бинарная негоциация полей
(logrequests/evaluate), backfill-режим, _should_backfill, неизменность old visits_*.csv.gz.
5 тестов metrika_logs + 2 metrika_reports — все pass.

**2B — `direct.py`** DONE
8 выгрузок: campaign_performance, search_query_performance, placements/,
campaign_strategies.json, campaign_targeting.json, ad_texts.json,
keywords.parquet, product_feed.parquet. Флаги campaign_report_has_lost_impression_share,
archived_campaigns_retrievable, feed_used в manifest. cost_basis=net_no_vat.
10 тестов — все pass (включая error 58/513, деградацию вторичных отчётов).

**2B-patch (финальная версия)** DONE — 2026-07-20
REPORT_WINDOW_LIMIT_DAYS = {SEARCH_QUERY_PERFORMANCE_REPORT: 180}; обрезка окна запросов
до max(requested, today-180); window_infos/{requested,effective,truncated} в manifest;
caveat_type=source_window_limit (не data_quality_issue) при обрезке.
Изоляция ошибок: report_status per type (campaigns/queries/geo), SourceUnavailable только
если все три упали. geo_report_available + geo_caveat.reason в manifest.
UTF-8 fix: _api_error + _fetch_report читают content.decode("utf-8") вместо resp.text.
16 тестов test_direct_2b_patch.py — 16 passed (11 старых + 5 новых ШАГ 0).

**2C — gsc_api + gsc_manual + webmaster_api + webmaster_manual + wordstat** DONE
- GSC API: пагинация startRow, одинаковый контракт с manual (RAW_FIELDS).
- GSC manual: CSV-валидация, device=unknown, clicks_ui_caveat, validation_report.
- Webmaster API: user_id, популярные запросы, история с honest notes об усечении.
- Webmaster manual: агрегация, policy degrade/aggregate, limitation_note.
- Wordstat: очередь create→poll→get→delete, батчи, rate-limit паузы, UTF-8 quirk v4.
15 тестов — все pass.

**2D — `crm_import.py` + `crux.py`** DONE
- CRM: нормализация дат/статусов/сумм, SHA-256 хэш телефона, validation_report.
- CrUX: 404 = штатно, cwv_field_data_available, p75-сводка,
  URL-запросы только если у origin данные есть.
- Задача 3C (точечный CrUX extractor): добавлен `tests/test_crux.py` — 3 теста
  (данные есть, данных нет/404, временная 5xx → SourceUnavailable) — все pass.
6 + 3 = 9 тестов CrUX — все pass.

**3C-patch — подключение CRUX_API_KEY и проверка реального вызова из общего пайплайна** CODE DONE, live run pending — 2026-07-22.

Проверено (изменений не потребовалось — уже было на месте):
1. `clients/_template/config.yaml`: `sources.crux.api_key_env: "CRUX_API_KEY"` уже
   задан (ключ читается из `.env` по имени, не хардкод); `.env.example` тоже уже
   документирует `CRUX_API_KEY`.
2. `orchestrator.EXTRACTORS["crux"] == ["crux"]` — `crux.extract` уже
   диспетчеризуется из `run_extract` наравне с остальными источниками (условие
   `sources.crux.enabled`). Изменений в `src/pipeline/orchestrator.py` не
   потребовалось.

Добавлено в `tests/test_crux.py` (7 новых тестов, было 3 → стало 10):
- `test_crux_missing_api_key_raises_clear_error` — без `CRUX_API_KEY` extract()
  падает с `SourceUnavailable`, упоминающим имя ключа в сообщении, HTTP-вызовов
  нет, `crux.json` не создаётся (не тихий пустой результат).
- `test_ping_true_with_valid_config_and_key` / `test_ping_false_without_key` /
  `test_ping_false_without_origin` / `test_ping_true_via_gsc_site_url_fallback` —
  `ping()` даёт осмысленный True/False по валидному конфигу+ключу.
- `test_crux_dispatch_wired_in_orchestrator` — регрессия на карту
  `EXTRACTORS`/`_modules_for_source`, чтобы crux не выпал из диспетчеризации
  молча.
- `test_crux_extract_called_from_orchestrator_full_run` — полный прогон
  `orchestrator.run_extract()` с временным client-каталогом (config.yaml + .env,
  замоканный ключ `fake-orchestrator-key`), `requests.Session.request`
  подменён на уровне класса (сама диспетчеризация, чтение `.env`/config и
  запись manifest — настоящие, не мокнутые); подтверждено, что
  `data/raw/crux/crux.json` создаётся и `manifest.sources.crux.cwv_field_data_available`
  проставляется через реальный путь вызова оркестратора.

10 тестов `tests/test_crux.py` — 10 pass. Полный `pytest tests/` — 387 passed,
9 failed (все 9 — известные и не связанные с CrUX: 2A-patch metrika_logs
blocker, gsc_manual/webmaster_manual, wordstat legacy v4 — см. записи выше;
падений от этого патча нет).

**Blocker:** реального `CRUX_API_KEY` в этой сессии нет — прогон
`ping()`/`extract()` выполнен только с замоканным ключом через полный путь
оркестратора. Ставить 3C-patch = DONE только после реального прогона на
клиенте с настоящим ключом (`cwv_field_data_available: true|false`, без
`error`).

---

### Промт 3 — Слой transform + verify_metrika

**3A — `build_canonical.py` (базовые преобразования)** DONE
dedupe_visits, classify_traffic_source, map_device, expand_manual_costs,
is_brand_query, goal_flags, normalize_entry_page, classify_strategy_optimize_for;
build_costs, build_seo_queries_gsc/webmaster, build_crm; write_canonical_table.
18 тестов — pass.

**3B — backfill join в `build_visits`** PARTIAL
Код написан (_join_backfill, _read_metrika_backfill, _parse_backfill_row).

**Баг:** условие `patch_already_present = all(col in df.columns for col in _BACKFILL_COLUMNS)`
всегда True, потому что `_parse_visit_row` всегда добавляет patch-колонки в
возвращаемый dict (значения None, если полей нет в CSV). Итог: merge пропускается
даже при наличии backfill/, поля patched всегда остаются None.

Три теста падают:
- `test_build_visits_base_plus_backfill_integration`: last_traffic_source_naive = None (ожидается "ad")
- `test_build_visits_unmatched_backfill_recorded`: backfill_matched = 0 (ожидается 1)
- `test_build_visits_parquet_dtypes_and_original_columns`: screen_width = NaN (ожидается 360)

`test_build_visits_without_backfill_keeps_base_null_fields` — pass (backfill нет → поля null — корректно).

**3C — `scripts/verify_metrika.py`** DONE
8 тестов test_verify_metrika.py — pass (инфляция цели, несовпадение,
multi-batch, пороги статусов, нулевое деление).

**3D — `build()` в оркестратор** DONE
bc.build() вызывается из orchestrator.run_transform(), manifest обновляется
через flags["metrika_backfill"]. test_build_writes_only_tables_with_raw_source — pass.

---

## Следующая задача

**2B-patch:** DONE (2026-07-19). pytest test_direct_2b_patch.py → 11 passed; test_build_canonical.py → 96 passed (4 новых). 5 pre-existing failures в test_extract_smoke.py (gsc_manual/webmaster, не связаны с 2B-patch).

**2B-patch-2 (2026-07-20):** CODE DONE, live-прогон на pognali.rent НЕ выполнялся в
этой сессии (нет доступа к реальному API/токену) — статус DONE по протоколу задачи
ставить нельзя до реального прогона с `report_status: {campaigns: ok, queries: ok,
geo: ok}`.

Три исправления в `src/extract/direct.py`:
1. **QUERY_FIELDS**: убрано `Device` (error 4000 на реальном аккаунте, не принят
   Reports API для SEARCH_QUERY_PERFORMANCE_REPORT). QUERY_FIELDS_GOAL — аналогично.
   Итоговый состав не проверен на error 4000 повторно (нет live-доступа) — если
   API отклонит ещё одно поле, потребуется повторный цикл убрать/проверить.
2. **Geo**: `report_type` для гео-отчёта заменён `GEO_PERFORMANCE_REPORT` (не
   существует, error 8000) → `CUSTOM_REPORT` (по образцу PLACEMENT_FIELDS).
   GEO_FIELDS/GEO_FIELDS_GOAL по составу не менялись. REPORT_WINDOW_LIMIT_DAYS
   для CUSTOM_REPORT/geo не добавлен — не проверено эмпирически на реальном
   окне, требует live-прогона (может понадобиться error 4001 → лимит по аналогии
   с queries).
3. **JSON API v5 селекторы**: `adgroups.get`/`ads.get`/`keywords.get` теперь
   вызываются с `SelectionCriteria.CampaignIds` из списка кампаний
   `_fetch_strategies()` (шаг 5, уже выполняется раньше шагов 6–9 — порядок
   шагов менять не пришлось). `feeds.get` требует `Ids` явно (error 8000) и не
   имеет отдельного метода перечисления фидов клиента без него — вслепую
   больше не вызывается; `_fetch_feed` всегда возвращает `feed_used=False` +
   note с явным объяснением ограничения API (не баг).

Побочное изменение вне `allowed_files` (согласовано с пользователем): в
`tests/test_extract_smoke.py::test_direct_feed_used_writes_parquet` старое
ожидание `feed_used=True` при наличии фида противоречило подтверждённому
поведению API (feeds.get не может обнаружить фид без готового Ids) —
тест обновлён под `feed_used=False`, остальной файл не тронут.

Тесты: `pytest tests/test_direct_2b_patch.py` → 30 passed (23 старых/новых
mock-теста для 2B-patch-2 внутри файла + существовавшие). `pytest
tests/test_extract_smoke.py -k direct` → 11 passed. Полный
`tests/test_extract_smoke.py`: 5 failed (gsc_manual/webmaster_manual,
pre-existing, не связаны с этим патчем — те же 5 падают и до изменений).

**Blocker:** реальный прогон на аккаунте pognali.rent не выполнен (нет
API-доступа в этой сессии). До прогона: не исключено, что (а) QUERY_FIELDS
после убирания Device отклонит ещё одно поле; (б) CUSTOM_REPORT для гео
потребует REPORT_WINDOW_LIMIT_DAYS; (в) CUSTOM_REPORT отклонит одно из полей
GEO_FIELDS (LocationOfPresenceId/Name/Device). Ставить 2B-patch-2=DONE только
после этого прогона с report_status: {campaigns: ok, queries: ok, geo: ok}.

---

## 2B-patch / step0 findings

**Дата анализа:** 2026-07-19

### Диагностика расхождения (аренда авто владивосток, CampaignId 119193036)

**Проверено по коду:**

1. **DateFrom/DateTo не логируются постановочно** (`_fetch_report` использует
   даты из `date_from`/`date_to`, но в manifest фиксируются только общие
   границы окна, не даты конкретного запроса). Без логов невозможно снаружи
   проверить, за какой период API реально получил запрос.

2. **Нет помесячного чанкинга для SEARCH_QUERY_PERFORMANCE_REPORT** — весь период
   запрашивается одним запросом. Это архитектурное отличие от Метрики, которая
   чанкует по месяцам. При большом окне (12 месяцев) Reports API может усекать
   выборку или обрабатывать её иначе, чем UI.

3. **Нет AdNetworkType-фильтра** — отчёт по определению поисковой (SEARCH_QUERY),
   явного фильтра нет. Скорее всего, не причина расхождения.

4. **cost_raw именование** — в существующем `build_direct_queries` и `build_costs`
   `cost_raw`/`cost_rub` хранятся в **рублях** (после деления на 1 000 000),
   что противоречит имени. Двойного деления нет. Требует исправления согласно
   data-export-spec-v1 (cost_raw = int64 микрорублей, cost_normalized = float64 рублей).

**Вывод:** Причина расхождения **не найдена точно** из анализа кода — требуется
прогон с реальным токеном и логированием дат. Наиболее вероятная гипотеза:
**period mismatch** — пользователь сравнивал UI за период, отличающийся от
фактически переданного в API DateFrom/DateTo.

**Исправление (блокирующее):** добавлено логирование `period_logs` (date_from,
date_to, rows) на каждый чанк в manifest + помесячный чанкинг для всех трёх
отчётов (campaign/query/geo). После следующего прогона расхождение должно стать
идентифицируемым по `period_logs` в manifest.json.

---

**3B-fix:** исправить `_join_backfill` в `src/transform/build_canonical.py`.

Проблема: `patch_already_present` нужно определять не по наличию колонок в df
(они там всегда), а по факту наличия файлов в `backfill/` или по тому, имеют ли
patch-поля в df ненулевые значения (лучше — проверять существование
`backfill_dir` и наличие в нём `visits_backfill_*.csv.gz`).
Правило: если backfill-директория есть и непуста → делать merge; иначе → skip.
После исправления три падающих теста должны стать green.

`allowed_files: [src/transform/build_canonical.py]`

---

**WS-0** DONE — 2026-07-21. `clients/_template/inputs/wordstat_stopwords.yaml`
(схема entries: phrase/scope/reason/added_by/added_at, 5 примеров-заглушек) +
`src/extract/wordstat_config.py` (normalize(), load_stopwords(), classify() —
не зависит от wordstat.py, wordstat.py не изменён). Пустой entries -> classify
всегда None (флаг wordstat_stopwords_empty в manifest — задача WS-1, которая
будет вызывать classify()). 10 тестов `tests/test_wordstat_config.py` — 10 pass.

---

**WS-1** DONE — 2026-07-21. `src/extract/wordstat.py` полностью переписан:
месячный агрегат (legacy v4 очередь отчётов) заменён на topRequests (топ
ассоциированных запросов, сырьё в `topRequests_raw/<маска>.json`) + dynamics
(недельная динамика, один вызов на фразу на весь диапазон) через Wordstat API
(api.wordstat.yandex.net, Bearer-токен). gap_candidates/seasonality_candidates
строятся через wordstat_config.classify(), дедуп по normalize() в
target_queries с полем purpose. Выход: `wordstat_weekly.parquet` (+ purpose),
`wordstat_core_queries.parquet` (+ purpose, scope). HTTP 503 (квота) —
отдельный ретрай с бэкоффом поверх C.http_request, manifest фиксирует
wordstat_quota_hit/wordstat_calls_made по факту прогона (квота не хардкодится).
`clients/_template/config.yaml`: sources.wordstat получил regions/devices,
добавлены top_n_gap/top_n_seasonality, wordstat_geo убран (заменён на
sources.wordstat.regions). CANONICAL_TABLES не менялся (["wordstat"] —
имя будущей canonical-таблицы, не совпадает с именами сырых parquet).
11 тестов `tests/test_wordstat.py` — 11 pass.

**Blocker:** `tests/test_extract_smoke.py` (вне allowed_files WS-1) содержит
2 старых теста на legacy v4 месячный агрегат
(`test_wordstat_queue_cycle_writes_raw_and_manifest`,
`test_wordstat_dead_token_raises`) — оба теперь падают, т.к. старое поведение
удалено по решению продукта (п.7 задачи). Нужна отдельная задача с
`tests/test_extract_smoke.py` в allowed_files, чтобы удалить/переписать их
(`test_wordstat_no_seeds_raises` в том же файле по-прежнему проходит).

---

**WS-2** DONE — 2026-07-22 (task_id wordstat-transport-cloud-v2-migration).
Транспорт `src/extract/wordstat.py` полностью заменён: старый REST v1
(`api.wordstat.yandex.net`, Bearer-токен) отключён Яндексом безвозвратно
(подтверждено поддержкой, не проблема сертификата) — заменён на Yandex Cloud
Search API v2 (`searchapi.api.cloud.yandex.net`, `Authorization: Api-Key
<WORDSTAT_API_KEY>`, новое имя секрета вместо старого `WORDSTAT_TOKEN`).
Точная схема запроса/ответа сверена не по пересказу, а по официальному proto
(`yandex-cloud/cloudapi` → `yandex/cloud/searchapi/v2/wordstat_service.proto`,
т.к. `aistudio.yandex.ru` отдавал CAPTCHA инструментам фетча) — `GetTop` ->
`POST /v2/wordstat/topRequests`, `GetDynamics` -> `POST /v2/wordstat/dynamics`,
`GetRegionsTree` -> `POST /v2/wordstat/getRegionsTree` (бесплатен, используется
в `ping()`). `folderId` обязателен в теле каждого запроса (INVALID_ARGUMENT без
него) — новое поле `sources.wordstat.folder_id` в `clients/_template/config.yaml`
(не секрет, обычный клиентский конфиг). Маппинг под старую модель данных (WS-1
не менялась): `results` (не `associations`) -> topRequests-кандидаты;
int64-поля (`count`) приходят JSON-строками -> `int()`; `date`/`fromDate`/
`toDate` — `google.protobuf.Timestamp` (RFC3339) -> `"YYYY-MM-DD"` на выходе,
RFC3339 на входе; `regions` теперь `repeated string` (было int) -> `str()` при
сборке тела; `devices` — enum `DEVICE_ALL|DEVICE_DESKTOP|DEVICE_PHONE|
DEVICE_TABLET` (сборка через `f"DEVICE_{d.upper()}"`, конфиг не менялся).
Операторы масок (`!слово`, `+слово`, `[слово]`, сравнение через `|`) в v2 НЕ
поддерживаются (подтверждено доками + независимым источником) — задокументировано
в докстринге; `wordstat_seeds` их и раньше не использовал, адаптация не
потребовалась. Старый 503-цикл квоты (специфика v1) удалён — v2 не документирует
такое поведение, лимиты (429/5xx) идут через общий `C.http_request`, как у
Директа; `wordstat_quota_hit` убран из manifest, `wordstat_calls_made` остался.
Manifest получил `api_version_used="cloud_search_v2"`, `migration_reason`,
`folder_id`. `tests/test_wordstat.py` переписан на v2-фикстуры (7 тестов,
7 pass) — старые 503-квота тесты заменены на проверку Api-Key/folderId/regions/
devices в теле запроса и на регрессию отсутствия `folder_id`.

**Blocker (не устранён, вне allowed_files WS-2):** `clients/_template/.env.example`
всё ещё документирует старое имя секрета `WORDSTAT_TOKEN` — новым клиентам
нужно вручную завести `WORDSTAT_API_KEY` (и заполнить `folder_id` в
config.yaml), пока это не поправят отдельной задачей.

---

**wordstat-folder-id-config** DONE — 2026-07-22 (обновлено: реальный
`folder_id` для pognali.rent получен от оператора тем же днём —
`ajebnohb0odjms4dgq25`, вписан в `clients/pognali.rent/config.yaml`,
TODO-заглушка снята). `clients/_template/config.yaml`:
`sources.wordstat.folder_id` переведён с `null` на `""` с расширенным
комментарием (где взять — Yandex Cloud Console, раздел «Каталог»; не секрет,
но клиент-специфично, сверять с оператором). `clients/pognali.rent/config.yaml`:
`wordstat: {enabled: true}` переписан в блочную форму, добавлен
`folder_id: ""` с TODO-комментарием — **реальное значение НЕ вписано**:
проверил `.env`, config.yaml, весь репозиторий на предмет уже известного
folder_id — нигде не встречается (только старый `WORDSTAT_TOKEN` в `.env`, не
относящийся к v2/Cloud); задал вопрос оператору через AskUserQuestion, ответ
не получен в рамках этой сессии (вопрос отклонён/отложен). Вписывать
угаданное значение не стал (п.5 протокола + явное указание задачи «не
гадать»). Поведение fail-fast подтверждено без изменений в
`src/extract/wordstat.py` (вне allowed_files этой задачи): `_folder_id()`
бросает `C.SourceUnavailable("не задан sources.wordstat.folder_id…")` до
единого HTTP-вызова что при `null`, что при `""` (`str(None or "").strip()`
и `str("" or "").strip()` дают одинаковый пустой результат) — `pytest
tests/test_wordstat.py` (7/7 pass, тесты не менялись, не в allowed_files)
это покрывает: `test_extract_missing_folder_id_raises` (отсутствие) и
`test_requests_use_api_key_auth_and_v2_body_shape` (непустой folder_id
корректно попадает в тело запроса).

---

**2A-direct-strategy** CODE DONE, live run pending — 2026-07-22. `src/extract/direct.py`:

1. **Strategy в FieldNames**: `CAMPAIGN_FIELD_NAMES` дополнен полем `Strategy`
   (рядом с уже запрошенным `Statistics`). Имя вложенного поля вида
   `optimize_for` НЕ зафиксировано как факт — в имеющемся сыром примере
   `campaign_strategies.json` поле `Strategy` вообще отсутствует (не было
   запрошено раньше). Вместо угадывания структуры добавлены
   `_strategy_field_present()`/`_strategy_field_samples()`: по факту ответа
   API пишут в manifest `strategy_field_present` (bool) и
   `strategy_field_samples` (до 3 сырых объектов `Strategy`, как вернул API) —
   реальная структура (включая наличие/имя `optimize_for`) фиксируется на
   первом живом прогоне, не выдумывается заранее.
2. **StatisticsCrit**: подтверждено по коду — `_fetch_strategies()` не передаёт
   и никогда не передавал параметр периода в `campaigns.get` (такого параметра
   в текущих params нет). Сравнить «с явным периодом vs без» на живом аккаунте
   поэтому невозможно без отдельного экспериментального вызова, который в этой
   сессии не выполнялся (нет доступа к реальному DIRECT_TOKEN). Добавлено
   `manifest.statistics_field_scope` = `"unknown"` по умолчанию (константа
   `STATISTICS_FIELD_SCOPE_UNKNOWN`, разрешённые значения
   `STATISTICS_FIELD_SCOPE_VALUES = ("rolling_window", "all_time", "unknown")`) —
   не null, но и не угадано.
3. **Другие вызовы не менялись**: placements/targeting(adgroups,bidmodifiers)/
   ads/keywords — код и FieldNames этих вызовов не тронуты (проверено тестом
   `test_other_calls_unaffected`).

Тесты: `tests/test_direct_2a_strategy.py` (новый файл, 8 тестов) — 8 passed.
Регрессия: `pytest tests/test_direct_2b_patch.py` — 24 passed;
`pytest tests/test_extract_smoke.py -k direct` — 11 passed.

**Blocker:** реальный прогон на аккаунте pognali.rent не выполнен (нет
API-доступа в этой сессии). До прогона неизвестно: (а) вернёт ли API вообще
поле `Strategy` и в какой форме (есть ли `optimize_for` и на каком уровне
вложенности); (б) чему на самом деле равен период `Statistics`
(`rolling_window` vs `all_time`) — для этого нужен отдельный экспериментальный
вызов с попыткой передать период и сравнением результата, чего API v5
`campaigns.get` по имеющемуся коду не поддерживает. Ставить задачу DONE только
после живого прогона, который заполнит `strategy_field_samples` фактическими
данными и даст основание сменить `statistics_field_scope` с `"unknown"` на
подтверждённое значение.

---

**stash-remaining-audit** DONE (report only, no recovery) — 2026-07-22.
Проверялся `stash@{0}` в этом же (вложенном) репозитории `marketing-diagnostics`
(base `d5aa955`, WIP-коммит создан 2026-07-22T02:14:26+03:00) на предмет
неабсорбированного контента для `direct.py`/`gsc_manual.py`/`metrika_logs.py`/
`build_canonical.py`/`CLAUDE.md`/конфигов.

Метод: посчитан blob-хэш (`git rev-parse HEAD:<file>` vs
`git rev-parse stash@{0}:<file>`) для каждого файла и сверен построчный diff
(`git diff HEAD stash@{0} -- <file>`) — не только stat, но и то, что HEAD
реально прошёл через коммит `d047032` («save before reset»,
2026-07-22T13:14:26+03:00, то есть **после** создания стеша) и дошёл до текущего
HEAD `fc3304e` без отличий по этим файлам.

| Файл | Пересекается с закоммиченным? | Логика совпадает или конфликтует? | Вывод |
|------|-------------------------------|-------------------------------------|-------|
| `CLAUDE.md` | Да, то же место (раздел D11) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `config/methodology.yaml` | Да, то же место (D11 `type_downgraded`/`downgrade_reason`) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `config/defaults.yaml` | Да, то же место (`transform.traffic_resolve_lookback_days`) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `clients/_template/config.yaml` | Да, то же место (`brand_terms` комментарий + `crux.enabled`) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `src/extract/direct.py` | Да, весь diff (Strategy/StatisticsCrit, см. запись 2A-direct-strategy выше) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `src/extract/gsc_manual.py` | Да, весь diff (см. запись 3A-patch выше) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `src/extract/metrika_logs.py` | Да, весь diff (см. запись 2A-patch выше) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |
| `src/transform/build_canonical.py` | Да, весь diff (см. запись 2A-patch-2 выше, `_resolve_region_field`) | Совпадает — blob-хэш идентичен HEAD | **safe-to-drop** |

Вывод: для всех 8 файлов содержимое в стеше побайтово идентично текущему HEAD
(`git diff HEAD stash@{0} -- <file>` пустой на всех восьми; сверка на не-файле
из этого списка, `direct.py` против базового `d5aa955`, дала 263 строки diff —
подтверждает, что сам метод сравнения работает, а не молчаливо гасит различия).
Коммит `d047032` («save before reset») зафиксировал ровно то же состояние
рабочего дерева, что лежало в WIP-стеше, и это состояние без изменений дошло
до текущего HEAD. **Ничего восстанавливать не нужно** — стеш для этих 8 файлов
устарел (полностью дублирует уже закоммиченную работу), можно безопасно
`git stash drop stash@{0}` после того, как будут проверены остальные файлы
стеша, не входившие в эту задачу.

**Вне scope этой задачи** (стеш также трогает эти файлы — не проверялись
здесь): `src/extract/site_crawl.py`, `docs/implementation_status.md`,
`tests/test_build_canonical.py`, `tests/test_crux.py`,
`tests/test_extract_smoke.py`, `tests/test_gsc_manual.py`,
`tests/test_site_crawl.py`, `tests/test_site_crawl_pages.py`. Судя по
сообщению текущего HEAD-коммита (`fc3304e`: «restore robots.txt (3.5-patch)
from stash, merge with hang-fix»), из стеша избирательно доносили только
часть, касающуюся `site_crawl.py` — эти файлы стоит проверить отдельной
задачей тем же методом, прежде чем дропать стеш целиком. Также в рабочем
дереве обнаружены untracked-артефакты неясного происхождения (`how 0f4c935
--stat`, `how d5aa955 --stat`, `site_crawl_STASHED_RECOVERED.py`) — похожи на
черновые файлы ручного восстановления, не трогались в рамках этой задачи.

---

**is_robot-column-removal** DONE — 2026-07-22 (task_id is_robot-column-removal).
Колонка `is_robot` убрана из `SCHEMAS["visits"]` (`src/transform/build_canonical.py`)
полностью — не always-null колонка, а отсутствие колонки вообще в
`visits.parquet`. Убраны мёртвые присвоения `df["is_robot"] = None` /
`merged["is_robot"] = None` в `_join_backfill`. Флаг доступности
`is_robot_available` (manifest `flags.metrika_backfill`) — отдельная сущность,
не физическая колонка — оставлен без изменений (D11 confidence_cap в
`config/methodology.yaml` захардкожен как `permanent_LOW` и не зависит от
этого флага). `tests/test_build_canonical.py` обновлён: проверки теперь
утверждают отсутствие `is_robot` в колонках df/parquet-схеме вместо
проверки on always-null. `pytest tests/test_build_canonical.py` — 114 passed.

---

**direct-tsv-report-header-fix** DONE — 2026-07-22 (task_id
direct-tsv-report-header-fix, продолжение диагностики
direct-campaigns-geo-empty-fields-diag). Причина 100%-пустых строк в
direct_campaigns/direct_geo/direct_queries — служебные строки без
табуляции на границах реальных TSV Директа: строка "название отчёта +
период" первой строкой (несмотря на `skipReportHeader`) и "Total rows: N"
последней строкой (несмотря на `skipReportSummaryRow`); csv.DictReader
принимал строку-название за fieldnames, из-за чего каждая строка данных
(включая настоящий заголовок) читалась как несовпадающая. Два фикса:
1) `src/extract/direct.py`: `skipReportHeader` `"false"` -> `"true"` —
   чинит будущие выгрузки на уровне API.
2) `src/transform/build_canonical.py`: `_read_tsv` теперь отбрасывает
   первую и/или последнюю строку файла, если в них нет табуляции — чинит
   уже скачанное сырьё и не полагается только на настройки API.
Фикстуры Direct TSV в `tests/test_build_canonical.py` обновлены под
реальный формат (служебная строка сверху + "Total rows: N" снизу);
добавлены прямые тесты `_read_tsv` (стрип заголовка, стрип футера,
файл без служебных строк — обратная совместимость, отсутствующий файл).
Перепарсинг реального сырья Pognali (`clients/pognali.rent/data/raw/direct/`):
direct_campaigns 1377/1377 непустых строк (было 1407/1407 пустых),
direct_geo 21681/21681 (было 21711/21711 пустых), direct_queries
15253/15253 (было 15265/15265 пустых); cost_rub/clicks/impressions
правдоподобны (сумма cost_rub campaigns ≈ geo: 492 661.44 ₽ vs
492 661.37 ₽ — расхождение в копейках от округления, не баг).
Замечено отдельно (не в скоупе этой задачи): сумма строк по помесячным
`campaigns/*.tsv` (1377) меньше, чем в legacy `campaign_performance.tsv`
(1405 после срезки служебных строк) — два файла расходятся по числу
строк, возможно разные источники/окна выгрузки; требует отдельной
диагностики, если legacy-файл ещё где-то используется как источник истины.
`pytest tests/test_build_canonical.py` — 119 passed. `pytest tests/` —
480 passed, 12 failed (все 12 — предсуществующие сбои на master, не
связанные с этой задачей: подтверждено `git stash`/сравнением с базовым
деревом до правок).

**direct-tsv-report-header-fix — исправление регрессии** — 2026-07-22.
Пользователь сообщил: после фикса SEARCH_QUERY_PERFORMANCE_REPORT и
CUSTOM_REPORT (геоотчёт, площадки) перестали выгружаться на боевом
аккаунте. Причина — часть 1 фикса выше (`skipReportHeader`: `"false"` ->
`"true"` в `src/extract/direct.py`): API Директа отвечает ошибкой на этот
заголовок именно для этих двух типов отчёта; CAMPAIGN_PERFORMANCE_REPORT
при этом отрабатывает нормально (несимметричное поведение API, не
угадывалось заранее). Исправление: `skipReportHeader` возвращён к
`"false"` в `_auth_headers` (`src/extract/direct.py`). Откат безопасен —
защита от служебной строки-названия реализована на стороне transform
(`_read_tsv` в `src/transform/build_canonical.py`, часть 2 исходного
фикса) и не зависит от этого заголовка API вообще; со сброшенным флагом
сырьё снова содержит строку-название первой строкой, и `_read_tsv` её
по-прежнему корректно отбрасывает. `pytest tests/test_build_canonical.py`
— 119 passed. `pytest tests/` — 488 passed, 12 failed (те же
предсуществующие сбои, не связаны с задачей).

---

**crux-config-enable** DONE — 2026-07-22. Добавлена секция `sources.crux`
в `clients/pognali.rent/config.yaml` (её не было — блок `sources` шёл
`wordstat` -> `crm_csv` напрямую), поля взяты по факту сигнатуры
`src/extract/crux.py::extract`/`_resolve_origin`/`_api_key_env`/`_key_urls`,
не по аналогии: `enabled: true`, `api_key_env: "CRUX_API_KEY"`,
`origin: "https://pognali.rent"`, `key_urls` — 3 посадочных URL (главная,
каталог, контакты, из уже существующих `crawl_seed_urls`; MAX_KEY_URLS=5
не превышен). Правка только конфига, код/тесты не менялись.

---

**AUDIT-goals-extractor** ЕСТЬ_РАБОТАЕТ — 2026-07-23. Диагностика (без
реализации): список целей счётчика (Management API `goals`) выгружается
`_fetch_goals()` в `src/extract/metrika_reports.py` (`goals_list.json`),
модуль подключён в `src/pipeline/orchestrator.py::EXTRACTORS["metrika"] =
["metrika_reports", "metrika_logs"]` и реально вызывается в `run_extract`.
Подтверждено фактическим файлом боевого прогона
`clients/pognali.rent/data/raw/metrika_reports/goals_list.json` (непустой
список целей с id/name/conditions). Это НЕ то же самое, что `goalsID`
внутри визитов Logs API (достижения целей, без метаданных) — метаданные
целей идут отдельно через Management API. В `config/methodology.yaml`
проверки D02/D03 формально указывают `requires: [visits]` (без отдельной
записи про goals list в реестре зависимостей) — расхождение между
фактическим наличием метаданных целей и тем, что реестр их не требует
явно, в этой задаче не устранялось (вне скоупа: только диагностика).

---

**AUDIT-match-type** ПОДТВЕРЖДЕНО_ДОКУМЕНТАЦИЕЙ — 2026-07-23. Диагностика
(без реализации): происхождение `MatchType=NONE` рядом с `MatchType=KEYWORD`
для одной и той же фразы в `direct_queries.parquet`.

Код (`src/extract/direct.py`, `QUERY_FIELDS`, строка 128) запрашивает
`MatchType` как поле `SEARCH_QUERY_PERFORMANCE_REPORT` Reports API.
`src/transform/build_canonical.py:1098` копирует значение как есть
(`row.get("MatchType")` -> `match_type`), без какой-либо трансформации —
это НЕ та же функция, что `_keyword_match_type()` в `direct.py:1183`
(эвристика по операторам фразы для отдельной таблицы `keywords.parquet` —
другой источник, keywords.get, там API вообще не отдаёт MatchType полем).

Значения подтверждены источником `MatchType field: yandex.ru/dev/direct/doc/ru/report-format`
и его английской версией `yandex.com/dev/direct/doc/en/report-format`
(независимо зафетчены, совпали) — ровно 4 значения enum:
`KEYWORD` = "показ по ключевой фразе" (impression for a keyword),
`SYNONYM` = "показ по семантическому соответствию" (semantic match),
`RELATED_KEYWORD` = "показ по дополнительной релевантной фразе" (related
keyword), `NONE` = "в остальных случаях" (all other cases). `NONE` — это
официально документированная категория «прочее», а не признак ошибки
API и не синоним «автотаргетинга» конкретно — документация не сужает
её до одного механизма показа.

Проверено на боевом сырье `clients/pognali.rent/data/raw/direct/queries/*.tsv`
(6 месяцев, 2026-01..2026-06): реально встречаются `NONE` (10618),
`SYNONYM` (3530), `KEYWORD` (1105); `RELATED_KEYWORD` в данных клиента
не встретился (0 строк) — это ограничение конкретного аккаунта/периода,
не повод считать значение неверным в enum.

Пункт 3 задачи (два источника показа vs один запрос с двумя типами):
опровергнуто на том же сырье — одна и та же пара (Query, AdGroupId)
регулярно встречается сразу с 2-3 разными `MatchType` в разных строках
отчёта одного периода (пример: «прокат авто», AdGroupId 5561710978 ->
NONE, SYNONYM, KEYWORD). `MatchType` — это сегмент отчёта (подтверждено
`yandex.ru/dev/direct/doc/en/fields-list`: MatchType помечен как segment
для SEARCH_QUERY_PERFORMANCE_REPORT), то есть строки отчёта естественно
разбиваются по этому измерению на уровне отдельных показов/аукционов —
одна и та же фраза может в разное время дать показ по точному
совпадению ключевой фразы (KEYWORD), по синонимайзеру (SYNONYM) или ни
по одному из именованных механизмов (NONE). Это не склейка двух разных
отчётов и не баг пайплайна — построчная гранулярность самого API-отчёта.

Источник подтверждения: документация (Yandex Direct API v5, report-format
+ fields-list, RU и EN) + реальный боевой ответ API (сырые TSV клиента).
Production-код не менялся.

---

**5A** DONE — 2026-07-23. Общая инфраструктура compute (без бизнес-проверок
D/A/T/C/S — они не реализовывались, только каркас). Новый `src/compute/common.py`:
`load_canonical`/`open_duckdb` (view поверх `data/canonical/*.parquet`, без
сервера — path подставляется как экранированный SQL-литерал, не
bind-параметр: DuckDB не готовит `CREATE VIEW` с параметрами),
`load_inputs` (`inputs/*.yaml`), `load_degradation`
(`data/metrics/degradation_report.json`); `validate_metric_value`/`validate_row`
(запрет NaN/inf и неподдерживаемых типов); `assert_confidence_within_cap` +
`ConfidenceCapViolation` (сравнение через
`src.pipeline.degradation.min_confidence` — единственный источник истины по
порядку HIGH>MED>LOW); `write_metric_artifact` (атомарная запись csv+json
через tempfile+`os.replace`, валидация всех строк ДО записи — на невалидном
входе не остаётся частично записанных файлов; опциональный `confidence_cap`
проверяется для каждой строки с полем `confidence`); `dispatch_blocks`
(вызывает `run(paths, defaults, runnable_ids)` block0..block6 по умолчанию,
`runnable_ids` — из `degradation_report["runnable_check_ids"]`; блок, ещё не
реализованный (`NotImplementedError`) или упавший с любой другой ошибкой, не
останавливает соседние блоки — принцип 4; параметр `modules` для инъекции
тестовых заглушек); `build_metrics_summary` (только структура — counts,
skipped[id/block/reason], block_status, artifacts; ни одного бизнес-числа).

`src/pipeline/orchestrator.py::run_compute` подключает диспетчер после записи
`degradation_report.json`: вызывает `dispatch_blocks`, пишет
`data/metrics/metrics_summary.json` через `write_json_atomic`, логирует статус
каждого блока. `block0..block6.py` не редактировались (вне allowed_files) —
все ещё заглушки `NotImplementedError`, дальнейшая реализация D/A/T/C/S —
отдельные задачи.

Новые тесты `tests/test_compute_common.py` (21 шт.): runnable (dispatch
передаёт корректный `runnable_ids` блоку), skipped (причина недоступности
сохраняется в `metrics_summary.skipped`), cap violation
(`assert_confidence_within_cap`/`write_metric_artifact` бросают
`ConfidenceCapViolation`, если `confidence` выше `confidence_cap`, и ничего не
пишут на диск), output schema (csv/json атомарно, содержимое совпадает,
невалидное значение — NaN — не пишет ни одного файла). `pytest
tests/test_compute_common.py` — 21 passed. Полный `pytest tests/` — 525
passed, 13 failed; все 13 подтверждены предсуществующими до этой задачи
(сравнение через `git stash`/прогон на базовом дереве): `test_direct_2b_patch.py`
×2, `test_extract_smoke.py` ×9 (gsc_manual/webmaster_manual/wordstat/metrika_logs),
`test_metrika_logs_lookback.py` ×1, `test_transform_direct_normalize.py` ×1 — ни
один из этих файлов этой задачей не затрагивался.
