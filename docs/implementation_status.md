# Статус реализации — audit 2026-07-14

Тесты: `pytest tests/`
Результат: **289 passed** из 289 (после task 4D 2026-07-14).

Регрессия transform (task 4E, 2026-07-14): `pytest tests/test_build_canonical.py` — **92 passed**, 0 failed, 0 errors.
Все тестируемые группы: dedupe_visits (3), apply_utm_threshold (4), expand_manual_costs (5), is_brand_query (3), classify_traffic_source (13), map_device (7), goal_flags (2), normalize_entry_page (5), classify_strategy_optimize_for (6), crm normalization (9), build() сквозные (7), build_visits backfill (4), vat normalization (7), normalize_url (7), dedupe_site_* (2), seo_queries source_mode/completeness (4), 4A–4D новые тесты — все GREEN.

---

## Таблица статусов

| Задача | Статус  | Недостающий критерий / комментарий |
|--------|---------|-------------------------------------|
| **P01** | DONE | 2026-08-01: bracketed goalsID сохраняются в независимую `visit_goals.parquet` с `achievement_count`; goal-флаги используют тот же парсер; nullable attribution-ключи и canonical schemas отражены в manifest; целевой тест `tests/test_build_canonical.py` пройден. |
| **P02** | DONE | 2026-08-01: конфигурационные visit-level воронки считаются по `visit_goals.parquet` до block3 и пишутся в `funnels.json`; поддержаны несколько ID на этап, несколько воронок, переходы и аномалии, QA повторов, порядок при наличии времени и разрезы month/channel/device/entry_page; C06 использует настроенные воронки. Целевые тесты: **63 passed**. |
| **P03** | DONE | 2026-08-02: устранён blocker P10 — `cost_summary` читает `spend_components` через `ClientPaths.config_file`; регрессионный тест подтверждает ненулевые расходы. Целевые тесты: **8 passed** (`tests/test_cost_summary.py`). |
| **P04** | DONE | 2026-08-01: добавлена конфигурационная экономика привлечения для `crm_attributed`, `crm_share_estimate` и `tracked_funnel`; CRM-семантика, доли, компоненты и traffic filter задаются только клиентским config; `acquisition_economics.json` содержит выручку на CRM-запись, формулы, допущения и явную деградацию. Целевые тесты: **27 passed**. |
| **P05** | DONE | 2026-08-02: устранён blocker P10 — `seasonality` использует canonical CRM-поля `lead_date` и `amount_rub`; тест покрывает месячные индексы CRM-броней и выручки. Целевые тесты: **3 passed** (`tests/test_seasonality.py`). |
| **P06** | DONE | 2026-08-01: добавлен детерминированный `analysis_candidates.json` в columnar-формате; явно размеченные кандидаты получают ссылочный baseline/context, ограничения сохраняются, дубли устраняются, а legacy и частичная разметка отражаются в coverage без неявного включения в analyze. Целевые тесты: **28 passed**. |
| **P07** | DONE | 2026-08-01: D/A/T-артефакты размечены как candidate/baseline/summary/limitation со стабильными reason-кодами, row refs и адресным контекстом без изменения формул; candidate contract coverage — 100%. Целевые тесты: **123 passed**. |
| **P08** | DONE | 2026-08-01: C/S-артефакты размечены как candidate/baseline/summary/context/limitation со стабильными reason-кодами и адресным контекстом без изменения формул; mixed S06 сохраняет GSC+Wordstat-кандидата при unavailable только у Вебмастера; candidate contract coverage — 100%. Целевые тесты: **106 passed**. |
| **P09** | DONE | 2026-08-02: повторный blocker реального P10 устранён — cap проверяется по финальному сериализованному пакету вместе с `system_prompt`; funnels сокращён до totals/transitions/gaps/anomalies, 638 кандидатов сгруппированы по check_id+candidate_reason с сегментами `columns+rows` и получают бюджет раньше необязательного контекста без удаления. Stress-test: final_size <100000, omitted=0, C06/S06 присутствуют. Целевые тесты: **46 passed**. |
| **P10** | BLOCKED | 2026-08-02: повторный `transform -> compute -> build_input_pack` выполнен без LLM; canonical подтверждает open/submit/both = 3002/634/620, CRM = 1357 оплаченных броней и 24 281 956 ₽ выручки, gross-расход = 1 580 161,44 ₽, обе экономические модели и 5 рядов сезонности рассчитаны; D11 корректно `permanent_LOW`/`LOW`, C06/S06 есть среди 638 кандидатов, предварительно исключено 0. Приёмка не пройдена: `build_input_pack` падает до записи аудита, обязательное ядро с system prompt = 279 685 байт при cap 100 000 (секция кандидатов = 269 984 байта). Причина вне `allowed_files` P10: P09 восстанавливает sparse columnar-строки с сотнями отсутствующих полей как `null`, затем сохраняет их как общие поля каждой группы. Требуется отдельная правка `src/analyze/draft_findings.py` и целевых тестов P09. |
| **FIX-d11-permanent-degradation** | DONE | 2026-08-02: `type_downgraded=permanent_LOW` применяется безусловно и ограничивает `confidence_cap=LOW` независимо от manifest-флагов и доступности источников. Целевые тесты: **15 passed** (`tests/test_degradation.py`). |
| **CHECKPOINT-post-fixes** | BLOCKED (analyze billing) | 2026-07-31: полный pytest и локальный smoke-run детерминированных стадий завершены; extract не завершился в ограниченное время и использовал ранее сохранённый raw. Внешний analyze дошёл до вызова провайдера, но был отклонён по биллингу; черновики не созданы, report корректно остановлен пустым approval-гейтом. Клиентские идентификаторы и значения из журнала удалены. |
| **1A** | DONE    | — |
| **1B** | DONE    | — |
| **1C** | DONE    | — |
| **1D** | DONE    | проверка совместимости 2026-07-14: methodology×2 pass, degradation×6 pass, config×16 pass, intake _template exit=0, intake pognali.rent exit=0 |
| **2A** | DONE    | — |
| **2A-patch** | CODE DONE, live run pending | 2026-07-21: поля Logs API сверены построчно с https://yandex.ru/dev/metrika/ru/logs/fields/visits — убраны isRobot/screenResolution/lastSignGCLID/lastSignhasGCLID, добавлены goalsDateTime+goalsSerialNumber (D01/D09), from (T01/T03), bounce+endURL (C06/C07/C12), isRobotPro опционально с graceful degradation. **Уточнение 2026-07-22 (после боевого прогона):** доступный тариф отклонил и isRobotPro — детекция бота через Logs API для этого доступа невозможна ПОСТОЯННО (не тарифная деградация). isRobotPro убран из кандидатов насовсем, никакой негоциации/ретраев вокруг него больше нет: `manifest.bot_detection_available` жёстко `False` (константа `BOT_DETECTION_AVAILABLE`). `ym:s:regionCity` заменена попыткой `ym:s:regionArea`: имя не гадается — проверяется отдельным `logrequests/evaluate` на каждый прогон (`_resolve_region_field`); принято → `region_field="ym:s:regionArea"`, `region_field_verified=true`; API отклонил → откат на `regionCity`, `verified=false` + реальный текст ошибки API в `manifest.region_field_error` (другое имя в рамках задачи не пробуется). `ym:s:ipAddress` по-прежнему не запрашивается. `config/methodology.yaml` D11: `type_downgraded="permanent_LOW"` + новое поле `downgrade_reason`, `type_downgrade_if` остаётся `null` (постоянное ограничение читается напрямую по `type_downgraded`, БЕЗ условия по manifest-флагу — см. CLAUDE.md, раздел «Схема ID проверок»). `data-export-spec-v1.md` раздел A обновлён под факт. SCHEMA_VERSION visits-v3→visits-v4 (довыгрузка сработает и для окон, частично выгруженных под v3 в ходе боевого прогона). `tests/test_metrika_logs_patch.py` переписан под новое поведение (3 теста на isRobotPro graceful-degradation заменены на bot_detection-постоянство + 2 теста региона + 1 тест D11 в methodology.yaml) — 11/11 pass. **BLOCKER (расширен):** 3 старых теста в `tests/test_extract_smoke.py` падают — `test_metrika_logs_negotiation_isolates_unsupported_fields` и `test_metrika_logs_backfill_preserves_old_files` (pre-existing, симулируют отклонение через `ym:s:lastSignhasGCLID`, больше не запрашивается) и **новый** `test_metrika_logs_writes_raw_and_manifest` (хардкодит `"ym:s:regionCity" in metrika_logs.VISIT_FIELDS`, что перестало быть верным после замены на `regionArea`); файл вне `allowed_files` этой задачи, не редактировался. **Зависимость для отдельной задачи (не в allowed_files):** `src/transform/build_canonical.py` (строки ~499, ~551, ~689, ~1280) читает регион визита ТОЛЬКО по жёсткому имени `ym:s:regionCity` — если боевой прогон примет `regionArea` (verified=true), transform молча даст `region_city=null` для всех строк, пока build_canonical.py не научится смотреть `manifest.region_field` и брать значение из фактически присутствующей колонки (`regionArea` или `regionCity`). Не исправлено в этой задаче — вне allowed_files. Этот же пробел ломает ещё один тест вне allowed_files этой задачи: `tests/test_build_canonical.py::test_dedupe_new_fields_use_last_dt_row` строит CSV-фикстуру по `metrika_logs.VISIT_FIELDS` и кладёт значение под ключ `ym:s:regionCity`, которого больше нет в `VISIT_FIELDS` (там теперь `ym:s:regionArea`) — значение в письменную строку не попадает вовсе, `region_city` в результате `None` вместо `"Kazan"`. Итого полный `pytest tests/` после этой задачи: **417 passed, 11 failed** (9 из 11 — pre-existing до этой задачи: gsc_manual×3, webmaster_manual×2, wordstat legacy×2, metrika_logs×2 lastSignhasGCLID; 2 новых из-за regionCity→regionArea, оба вне allowed_files: metrika_logs×1 в test_extract_smoke.py + build_canonical×1 выше). |
| **2A-patch-2** | DONE    | 2026-07-22: устранена зависимость, оставленная 2A-patch. `src/transform/build_canonical.py` больше не хардкодит `ym:s:regionCity` — новый `_resolve_region_field(manifest_metrika_entry)` читает `manifest.region_field` (записан extract в задаче 2A-patch: `ym:s:regionArea`, если API его принял, либо откат `ym:s:regionCity`, если отклонил); отсутствующий ключ (manifest до 2A-patch) -> откат на исторический `ym:s:regionCity` (константа `_REGION_FIELD_LEGACY_DEFAULT`), а не пустая колонка. Имя поля прокинуто через `_parse_visit_row`, `_parse_backfill_row`, `_read_metrika_backfill`, `_join_backfill`, `build_visits` (новый опциональный параметр `manifest_metrika_entry`); `build()` передаёт `sources.get("metrika_logs")` из `data/raw/manifest.json`. Оба ранее падавших теста (`tests/test_extract_smoke.py::test_metrika_logs_writes_raw_and_manifest`, `tests/test_build_canonical.py::test_dedupe_new_fields_use_last_dt_row`) обновлены под `regionArea` (не откат назад на `regionCity`) — pass. Новые тесты в `tests/test_build_canonical.py`: `test_region_field_falls_back_to_region_city_when_not_verified` (manifest `region_field_verified=false` -> raw CSV реально с колонкой `regionCity` -> canonical читает её, не `None`) и `test_region_field_defaults_to_region_city_without_manifest_entry` (manifest без записи `region_field` вовсе -> тот же откат) — обе pass. Полный `pytest tests/`: **443 passed, 9 failed** — все 9 pre-existing и не связаны с этой задачей (gsc_manual×3, webmaster_manual×2, wordstat legacy×2, metrika_logs×2 `lastSignhasGCLID` в `test_extract_smoke.py`, вне allowed_files). |
| **2A-direct-strategy-fix** | DONE | 2026-07-22: чинит невалидный FieldNames в `campaigns.get`, обнаруженный боевым прогоном (error 8000, `clients/pognali.rent/logs/extract_20260722_012238.log:63` — API вернул полный enum допустимых значений, "Strategy" среди них нет). `src/extract/direct.py`: `"Strategy"` убран из `CAMPAIGN_FIELD_NAMES`; новая `CAMPAIGNS_FIELD_NAMES_ENUM` (frozenset, взят дословно из текста ошибки) + `_validate_field_names()` — сверяет FieldNames с этим enum ДО отправки запроса и логирует отфильтрованные невалидные имена (не после ответа API), так что опечатка/устаревшее поле больше не роняет источник целиком. `BiddingStrategy` запрашивается отдельным параметром `TextCampaignFieldNames: ["BiddingStrategy"]` в `_fetch_strategies` (TEXT_CAMPAIGN — единственный тип кампаний у клиента сейчас; MOBILE_APP_CAMPAIGN/CPM_BANNER_CAMPAIGN/UNIFIED_CAMPAIGN потребуют свой `*CampaignFieldNames` — известное ограничение, не реализовано). `_strategy_field_present`/`_strategy_field_samples` переписаны читать вложенный `TextCampaign.BiddingStrategy` (через новый `_text_campaign_bidding_strategy()`), а не плоское поле `Strategy` верхнего уровня. `tests/test_direct_2a_strategy.py` обновлён под новый контракт (ломающее изменение, зафиксированное этой задачей): запрос содержит `TextCampaignFieldNames`, не содержит `"Strategy"` в `FieldNames`; парсинг `BiddingStrategyType` из `Search` и `Network`; плоский верхнеуровневый `Strategy` больше не распознаётся; невалидное имя поля фильтруется до отправки запроса, не роняя источник. 36 тестов в `test_direct_2a_strategy.py` + `test_direct_2b_patch.py` — 34 pass, 2 pre-existing fail (`test_query_report_dimensions`, `test_geo_report_schema` — ожидают старую семантику `cost_normalized`, сломанную задачей 4X-direct-normalize-2, не связано с этой задачей). Полный `pytest tests/` (кроме `test_site_crawl*.py` — см. ниже) не показал новых регрессий: те же 11 pre-existing failures, что документированы в 4X-direct-normalize-2/2A-patch/3A-patch. **Побочная находка, не устранена (вне allowed_files):** `tests/test_site_crawl_pages.py` не собирается (`ImportError: cannot import name '_is_path_disallowed'`) — `src/extract/site_crawl.py` в рабочей копии не содержит функций robots.txt-парсинга, описанных как реализованные в записи задачи 3.5-patch этого же файла; похоже на параллельную правку того же файла в другой сессии поверх HEAD, не в скоупе и не в allowed_files этой задачи — не исправлялось. |
| **2B** | DONE    | 2B-patch 2026-07-20: window truncation 180d, isolation, UTF-8 fix, 16 tests |
| **2B-patch-2** | CONFIRMED (live) | см. запись ниже для кода; живой прогон 2026-07-22 20:22–20:28 (`clients/pognali.rent/logs/extract_20260722_202250.log`) отработал без error 4000/8000 для campaigns/queries/geo (`report_status` де-факто ok/ok/ok) — см. AUDIT-live-verification-status ниже |
| **2C** | DONE    | — |
| **2D** | DONE    | — |
| **3A** | DONE    | build_canonical.py базовые преобразования. GSC manual path (task_id gsc-3A, task_id 3A-rewrite 2026-07-17): gsc_manual.py переписан под формат папок YYYY-MM/Запросы.csv/Диаграмма.csv/Страницы.csv/Устройства.csv. Выходной контракт seo_queries не изменился. column_map в config.yaml заполнен кириллическими заголовками GSC. tests/test_gsc_manual.py переписан: 9 тестов — 9 pass 2026-07-17. |
| **3A-patch** | DONE    | 2026-07-22: gsc_manual.py — Запросы.csv теперь может быть комбинированным (query+page+device в одной строке сразу, contract 3A: `column_map["page"]`+`column_map["device"]` оба присутствуют в заголовке) — page/device берутся из строки, `incomplete_dimensions=false`; Страницы.csv в этом случае становится необязательным (page уже есть в Запросы.csv). Старый раздельный формат (только query) по-прежнему парсится без падения, но помечается caveat `incomplete_dimensions` + попадает в `incomplete_dimensions_months`/`device_missing_months` (manifest и report). Сверка кликов Диаграмма vs Запросы (>10% caveat) не менялась. Новый `docs/gsc_export_instructions.md` — как выбрать несколько измерений сразу в интерфейсе GSC перед экспортом. SCRIPT_VERSION 0.2.0→0.3.0. 4 новых теста в tests/test_gsc_manual.py (комбинированный формат, pages необязателен при комбинированном, legacy incomplete_dimensions=true, legacy всё ещё требует Страницы.csv) — 13/13 pass. BLOCKER: 3 старых теста в tests/test_extract_smoke.py (test_gsc_manual_validates_and_writes_same_contract, test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat, test_gsc_manual_missing_device_column_flags_month) падают — это pre-existing из 3A-rewrite (2026-07-17), тестируют старый плоский формат gsc_YYYY-MM.csv без папок YYYY-MM, файл вне allowed_files этой задачи, не редактировался. |
| **3B** | DONE    | webmaster_manual: переписан под wide-формат (Query×Url×YYYY-MM_cols); агрегация по (query,page), CTR пересчёт, DEMAND=max; manifest: has_page_column=true, page_device_breakdown=true, has_demand_column; tests/test_webmaster_manual.py (12 тестов) — 12 pass 2026-07-17. BLOCKER: build_seo_queries_webmaster (build_canonical.py:942) хардкодит page=None — page из JSON теряется в transform. |
| **3C** | DONE    | — |
| **3C-patch** | CONFIRMED (live) | 2026-07-22: код+тесты см. запись ниже; реальный `CRUX_API_KEY` использован тем же днём позже — живой прогон 2026-07-22 17:23 UTC дал `cwv_field_data_available=true`, без `error` (`clients/pognali.rent/data/raw/crux/crux.json`, лог `extract_20260722_202250.log:95-102`) — см. AUDIT-live-verification-status ниже |
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
| **5bA** | DONE | 2026-07-29. Новый модуль `src/compute/block4_seo.py` — S01-S10 (SEO и органический спрос, каталог v2 §9). Не путать с уже существующими заглушками `block4.py`/`block5.py` (легаси-нумерация до методологии v2, не в `allowed_files` этой задачи, не тронуты) — диспетчер вызывает блоки по имени модуля (`common.BLOCK_MODULE_NAMES`), поэтому подключение `block4_seo` к оркестратору осталось отдельной задачей (файл `common.py` вне `allowed_files`). S01 (брендовый/небрендовый органический микс), S02/S03 (позиции 4-10 / strike zone 11-20, легаси 5.1), S04 (CTR-аномалия по бакетам позиций × is_brand, тот же коэффициент 0.5, что A20 в block1.py), S05 (тренд по страницам, ранняя/поздняя половина доступных месяцев), S09 (пересечение страниц по запросу). **Структурные разрывы, задокументированные в docstring модуля:** (1) `wordstat.parquet` не строится в canonical (extract объявляет `CANONICAL_TABLES=["wordstat"]`, transform таблицу не собирает — тот же класс разрыва, что CrUX в block3.py) → S07 (requires=[wordstat]) всегда пишет `unavailable`; S06 (optional=[wordstat]) считает помесячный тренд seo_queries, но `seasonality_reconciliation.wordstat_available=false` и `confidence=LOW` всегда (каталог §11 п.9 запрещает утверждать сезонность без Wordstat). (2) `build_canonical.py` дедуплицирует seo_queries по `(query,page,source,device)` БЕЗ `month` — S05/S06 написаны так, чтобы корректно деградировать (`insufficient_month_history`), если на практике доступен только один месяц на страницу. (3) S10 не имеет конфигурационного "целевого URL" (такого поля не существует) — использует органическую вовлечённость (visits, source_group=organic) как прокси: "фактический" URL = лучше всего ранжируемая страница среди конкурентов по запросу (см. S09), сравнивается с "лучше конвертирующей" среди тех же кандидатов; `target_url_from_config: false` явно в каждой строке. S08/S09/S10 — обязательный device-разрез (прямое требование промта): `device="unknown"` исключается ТОЛЬКО из `*_by_device` находок, участвует в device-агностическом overall-агрегате наравне с остальными строками. S08 — единственная проверка блока с визит-уровневым confidence (`_sample_confidence` по органическим визитам страницы, тот же принцип, что C12 в block3.py); остальные S01-S07/S09/S10 — отчётные агрегаты seo_queries, капаются на MED напрямую (тот же приём, что A02-A11 в block1.py). S11-S27 не реализованы (уже зарегистрированы в methodology.yaml, диспетчер их не вызывает). Новый `tests/test_block4_seo.py` — 14 тестов (по одному минимум на S01-S10 + device-разрез unknown-исключение для S08/S09 + confidence_cap из degradation_report + S10 без visits) — `pytest tests/test_block4_seo.py` — **14 passed**, 0 failed. |
| **5bB** | DONE | 2026-07-29. Расширяет `src/compute/block4_seo.py` — S11-S20 (технический SEO и производительность, каталог v2 §9). Читает `site_pages`/`site_link_graph` (canonical) и `data/raw/crux/crux.json`/`inputs/manual_cwv.yaml` (напрямую, тот же приём, что C01/C02 в block3.py — CrUX не даёт канонической таблицы). S11 (robots/noindex на важной странице — только этот компонент, "недоступный рендеринг" отнесён к S27 по data-export-spec-v2.md §G1, не реализован здесь, `js_rendering_component_implemented=false`), S12 (canonical на другой URL при материальном трафике), S13 (страницы с трафиком вне sitemap + sitemap с battled/битыми URL), S14 (органический трафик на HTTP>=400), S15 (цепочки редиректов >=2 хопов, requires=[site_crawl]), S16 (дубли по canonical-кластерам + сводные счётчики indexable/pages_with_shows), S17 (пустые title/description/H1 при трафике + дубли title), S18 (страницы-сироты/слабо связанные по `site_link_graph`, requires=[site_crawl]), S19 (глубина от главной по BFS `depth_from_home` >= порога, "коммерческая страница" не классифицируется — `commercial_classification_available=false`), S20 (CWV field/manual + органическая вовлечённость mobile vs desktop, source_group=organic). Каждая проверка S11-S19, читающая `site_pages`, несёт в summary `crawl_coverage_caveat`+`crawled_url_count` — краулер обходит только `top_n_each_source` (по умолчанию 20) URL, находки не экстраполируются на весь сайт (прямое требование промта задачи). S20: CrUX пуст -> ручной лабораторный замер (`inputs/manual_cwv.yaml`) с принудительным MED-потолком, ни один источник недоступен -> `cwv_unavailable` с confidence LOW (прямое требование промта: "CrUX empty -> только manual lab data с MED cap"). Диспетчер `run()`: S11/S12/S13/S14/S16/S17/S20 гейтятся на `has_seo` (requires=[seo_queries]), S15/S18/S19 — на `"site_pages" in canonical` (requires=[site_crawl], тот же приём, что C03/C08/C11/C14/C17 в block3.py — `site_crawl.py:CANONICAL_TABLES=["pages"]` не совпадает с реальным именем таблицы `site_pages`, поэтому эти ID никогда не станут runnable через автоматическую деградацию; тесты конструируют `runnable_ids` явно). S21-S27 не реализованы (уже зарегистрированы в methodology.yaml, диспетчер их не вызывает). Расширен `tests/test_block4_seo.py` — +19 тестов (минимум один сценарий на S11-S20, включая unavailable без site_pages/site_link_graph и оба сценария CrUX empty для S20) — `pytest tests/test_block4_seo.py` — **29 passed**, 0 failed. |
| **5bC** | DONE | 2026-07-29. Завершает блок 4 в `src/compute/block4_seo.py` — S21-S27, последний диапазон SEO-блока (каталог v2 §9). S21 (Яндекс vs Google: агрегат по `seo_queries.source` на уровне страницы — Вебмастер отдаёт один снимок за всё окно, не помесячный ряд, как GSC, поэтому сравниваются позиция/CTR за окно, не тренды; расхождение позиций >=10 либо CTR-ratio >=3x). S22 (доля кликов органики, оседающая на страницах без единой вовлечённости — `dead_end_click_share`; автоматическая часть по данным пересекается с S08, т.к. в canonical-схеме нет классификации "информационная/коммерческая страница" — задокументировано явно в docstring, `page_classification_available=false`). S23 (та же страница, органика vs весь остальной трафик по `visits.source_group` — "сопоставимая группа" получена контролем самой страницы, без придуманного поля классификации). S24 (соединяет тренд S05 с органической вовлечённостью страницы — кандидат только если ОДНОВРЕМЕННО падает видимость И уже доказана высокая конверсия). S25 (структурированных данных нет в canonical — автоматическая часть ограничена CTR-аномалией на позициях 1-10, тот же метод, что S04; каждая строка несёт `structured_data_field_available=false`+`manual_serp_check_required=true` — каталог сам требует ручную SERP-проверку). S26 (requires=[wordstat, seo_queries] — тот же структурный разрыв, что S07: `wordstat.parquet` не строится в canonical ни при каких условиях -> ВСЕГДА `unavailable` с явной формулировкой "ядро не посчитано: источник wordstat не готов", не upsell-примечание — прямое требование промта задачи). S27 (реализует компонент `js_content_diff`, зарезервированный в docstring 5bB как принадлежащий S27, не S11, по data-export-spec-v2.md §G1: отсутствие `site_pages` ИЛИ `js_content_diff`, не заполненного ни на одной обойдённой странице, тоже пишет явную "ядро не посчитано: источник site_crawl не готов" — не тихий пропуск; кандидат — `text_changed=true` либо непустой `links_only_in_rendered`). Диспетчер `run()`: S21/S25/S26/S27 гейтятся на `has_seo`, S22/S23/S24 — на `has_seo and has_visits` (requires=[seo_queries, visits] по methodology.yaml). В `src/compute/common.py` (`build_metrics_summary`) добавлен структурный (не бизнес-) агрегат `seo_confidence_cap` — по runnable-проверкам блока 4 (id начинается с "S"): `runnable_count`/`med_cap_count`/`med_cap_share`, чтобы report мог показать долю MED-капнутых SEO-проверок одной цифрой, не обходя все `s*.json` (прямое требование промта задачи). Расширен `tests/test_block4_seo.py` — +18 тестов (минимум один сценарий на S21-S27, включая три unavailable-сценария S26/S27, плюс 2 теста на новый агрегат `seo_confidence_cap`) — `pytest tests/test_block4_seo.py` — **42 passed**, 0 failed; `pytest tests/test_compute_common.py` (не изменялся, только проверка регрессии) — **21 passed**, 0 failed. **Доп. правка после ре-промта того же task_id 5bC** (уточнение: "S23/S24 используют device так же, как в 5bA — не дублировать логику, вынести общую функцию device-фильтрации"): выделена единая `_exclude_unknown_device_sql()`/`_UNKNOWN_DEVICE` (раньше `WHERE device != 'unknown'` дублировался литералом в S08 и S09) — S08/S09 переведены на неё; S23 получил `organic_underperforms_other_traffic_by_device` (device из `visits`, та же конвенция "unknown исключён только из by_device, overall не меняется" — на visits это фактически no-op, т.к. `map_device` никогда не пишет "unknown", применено для единообразия), S24 получил `high_value_page_losing_visibility_by_device` (device из `seo_queries`, где "unknown" реален — Вебмастер) через общую `_s24_trend_candidate()` (не дублирует пороговую арифметику между overall/by_device). +4 теста (by-device для S23, by-device + unknown-exclusion для S24) — `pytest tests/test_block4_seo.py tests/test_compute_common.py` — **66 passed**, 0 failed. |
| **5bD** | VERIFIED, код не менялся | 2026-07-29. Проверка (без правок кода, `allowed_files` этой задачи — только этот файл): `pytest tests/test_block4_seo.py tests/test_money_frame.py` — **61 passed**, 0 failed (Python 3.14.6, `.venv`). Сценарий 1 (источники S готовы — `seo_queries`/`site_pages`/`site_link_graph`/`crux`/`manual_cwv` присутствуют в фикстурах теста): все "flags_"/"reports_" тесты S01-S27 зелёные, конкретные находки считаются (S01-S05, S08-S27 кроме структурно недоступных S07/S26). Сценарий 2 (crawler/GSC/Webmaster отсутствуют) — подтверждён явными негативными тестами, а не общим прогоном: (а) GSC/Webmaster отсутствуют → `seo_queries.parquet` не построен → `block4_seo.run()` возвращает **пустой** список артефактов, ни одна S01-S10/S21-S27 запись не пишется молча (`test_run_ignores_s01_10_when_seo_queries_missing`, `test_run_ignores_s21_27_when_seo_queries_missing`); (б) crawler (site_crawl) не запускался → `site_pages`/`site_link_graph` отсутствуют → S11/S18/S27 пишут явный `status="unavailable"` с текстом причины через common.py grep: `"ядро не посчитано: источник site_crawl (обход сайта) не готов"` (S11), `"...источник site_crawl не готов"` (S27) — не тихая деградация (`test_s11_unavailable_without_site_pages`, `test_s18_unavailable_without_link_graph`, `test_s27_unavailable_without_site_pages`, `test_s27_unavailable_when_js_diff_never_populated`); (в) `money_frame.py:87` — константа `SEO_NOT_READY_NOTE = "SEO не учтён: источник не готов"` — при отсутствии ЛЮБЫХ `s*.json` с реальными данными (только `status=unavailable` строки не считаются "готово") `money_frame.run()` пишет ровно одну `kind=caveat` строку с этим текстом в `money_frame.json` (`test_seo_not_ready_adds_explicit_caveat`, `test_seo_unavailable_status_row_does_not_count_as_ready`); при наличии хотя бы одной содержательной S-записи (`test_seo_ready_when_s_check_has_data`) — оговорки нет. Итог: оба требования промта подтверждены существующим покрытием — сценарий 2 явно помечен как неполный (пустой список артефактов либо `status=unavailable`+причина), money_frame содержит оговорку именно тогда, когда SEO не готов, и не содержит, когда готов. Отдельно от строгого скоупа задачи (wordstat, не crawler/GSC/Webmaster): S06/S07/S26 всегда деградируют из-за отсутствия `wordstat.parquet` в canonical — это другой, ранее задокументированный (5bA/5bC) структурный разрыв, к текущей проверке не относится, тесты `test_s06_reports_trend_and_wordstat_unavailable`/`test_s07_always_unavailable_wordstat_missing`/`test_s26_always_unavailable_wordstat_missing` зелёные, но не были частью критерия "crawler/GSC/Webmaster отсутствуют". Код не исправлялся, только этот файл. |
| **7C** | DONE | 2026-07-29. Дополняет `src/report/build_report.py` (7A/7B) — план действий, assignee, LOW-находки и переполнение в приложение. `## План действий`: `### 2 недели` (первые `MAX_ACTION_PLAN_2W=7` находок с непустой рекомендацией по тому же `_priority_key`, что и «Три главных разрыва») + `### 2 месяца` (следующие `MAX_ACTION_PLAN_2M=5`) — **решение, не заданное источниками истины** (в `schemas.Finding`/каталоге v2/methodology-v2.md нет поля трудоёмкости/срока): лимиты по числу пунктов, не по факту трудозатрат, задокументировано в докстринге модуля. `_assignee(finding)`: необязательный ключ `assignee` карточки находки (в `schemas.Finding` тоже отсутствует — не заводился, т.к. вне `allowed_files`/схема не в этой задаче) → «уточнить», если не проставлен аналитиком. `_build_findings_section`/`split_findings_for_report`: находки уровня LOW больше не показываются в «Ключевые находки» (только HIGH/MED/client-HIGH, лимит `MAX_REPORT_FINDINGS` не изменился) — уходят в новый `## Приложение` вместе с находками сверх лимита (раньше просто считались в пометке `«Показаны N из M»`, без списка — теперь перечислены). `## Приложение` → `### Дополнительные находки` (LOW + переполнение, каждая строка несёт confidence/assignee/деньги/рекомендацию) и `### SEO-ядро — не посчитано` (те же элементы `degradation.skipped`, что и общий раздел, отфильтрованные по `block == 4`, для навигации клиента). `## Что не удалось проверить` → `## Что не удалось проверить и почему` (контент не менялся — reason уже был в тексте). Заголовок отчёта: убрана ссылка на «задачу 7A» и приложения-сноски (появились), осталась только оговорка про отсутствие повестки созвона (это 7D). 17 новых тестов в `tests/test_report_build.py` (план 2W/2M по приоритету и пусто, assignee default/explicit, заголовок skipped-раздела, LOW вне основного раздела и в приложении, все находки LOW, переполнение лимита в приложении не теряется, SEO-ядро фильтр по блоку и чистый случай, unit-тест `split_findings_for_report`). `pytest tests/test_report_build.py` — **31 passed**, 0 failed (было 20 до этой задачи). Полный прогон не запускался (не требовался промтом). **Не сделано (намеренно, по плану — 7D):** сноски-приложения содержательного типа (не только LOW/SEO-ядро) и повестка созвона с клиентом. **Побочное наблюдение (не исправлено, вне `allowed_files`):** `src/pipeline/orchestrator.py:573` всё ещё логирует «report: заглушка — src/report/build_report.py не реализован» — стало неверным с 7A, не относится к этой задаче. |
| **7A** | DONE | 2026-07-29. Реализует `src/report/build_report.py` (был `raise NotImplementedError`) — детерминированный рендерер-скелет `report/diagnostic_report.md`, без LLM. Читает `findings/approved/*.yaml`, `data/metrics/degradation_report.json`, `data/metrics/metrics_summary.json`, `config.yaml`, `config/defaults.yaml` (currency_round) и новый `config/report_glossary.yaml` (20 терминов — check_id/блоки/data_window/significant/HIGH-MED-LOW/client-HIGH/confidence_cap/source_modes/type/деградация/4 денежные категории/money_not_assessable/"сценарий, не прогноз"/CPA/CTR/CR/п.п./бренд-небренд). Разделы: заголовок (клиент/ниша/гео/период), резюме (структурные счётчики выполнено/не выполнено — без бизнес-чисел, тем же принципом, что и `metrics_summary`), ключевые находки (топ `MAX_REPORT_FINDINGS=8`), «что не удалось проверить» (skipped переносится дословно — id/block/reason без перефразирования), глоссарий. Форматирование: `format_rub` (округление по `currency_round`, разделитель тысяч пробелом, None → «в ₽ не оценить»), `format_percent` (доля→%), `format_pp` (разница долей → п.п. со знаком). **Решение, не заданное явно источниками истины (задокументировано в docstring модуля, не угадано молча):** карточка находки (`schemas.Finding`) не несёт поля `priority`; статичные баллы «Критичность/Реальность» каталога v2 §3 существуют только на уровне check_id в markdown-таблице и не входят в машинный реестр `config/methodology.yaml` — переносить их в report-слой значило бы завести второй, не согласованный с реестром источник истины вне `allowed_files` этой задачи. Сортировка `_priority_key` вместо этого строится на полях, которые реально есть на находке: уверенность (HIGH/client-HIGH выше MED выше LOW) → |money_amount_rub| по убыванию (находки без суммы — после находок с суммой) → блок каталога (D,A,T,C,S) → check_id, для детерминированности. Лимит `MAX_REPORT_FINDINGS=8` — фиксированная константа (бюджет ≤10 страниц минус ~2 страницы под заголовок/резюме/деградацию/глоссарий), не выведена из конфига — в `config/defaults.yaml` не заводилась (файл вне `allowed_files`). **Не сделано (по заданию — намеренно вне этой задачи):** приложения-сноски и повестка звонка с клиентом (skeleton явно помечает это в тексте отчёта: «Черновой рендер (задача 7A): без приложений-сносок и повестки созвона»). Тесты: новый `tests/test_report_build.py` — 14 тестов (сквозной `build()` с находками/без них, сортировка HIGH/client-HIGH/MED/LOW и по сумме, находки без суммы после находок с суммой, дословный перенос `skipped` включая пустой список, `format_rub`/`format_percent`/`format_pp`, обрезка по `MAX_REPORT_FINDINGS` с пометкой и без неё при их отсутствии, глоссарий 15-20 терминов из реального `config/report_glossary.yaml`) — **14 passed**. Регрессия: `pytest tests/ --ignore=tests/test_block1.py --ignore=tests/test_block3.py` (эти два файла не собираются в этой среде — `ModuleNotFoundError: scipy`, окружение, не связано с этой задачей) — **688 passed, 14 failed**; все 14 падений pre-existing (`test_direct_2b_patch.py`×2, `test_extract_smoke.py`×9, `test_metrika_logs_lookback.py`×1, `test_money_frame.py`×1, `test_transform_direct_normalize.py`×1 — уже задокументированы в записях выше этой таблицы), ни одно не относится к `src/report/`. |
| **7D** | DONE | 2026-07-29. Завершает `src/report/build_report.py` (7A/7B/7C) — приложения-таблицы (CSV), сноски на них из основного текста и повестка звонка с клиентом. **Обнаруженный конфликт с заданием (задокументировано, не угадано молча):** промт задачи требовал вопросы «из `llm_notes`» для повестки звонка — такого поля нет ни в `schemas.Finding`, ни в каталоге v2, ни в methodology-v2.md, ни в `config/methodology.yaml` (проверено grep по всем трём источникам истины). Решено по прямому прецеденту `assignee` (задача 7C): `llm_notes` заведён как необязательный ключ карточки YAML вне формальной схемы находки — `_llm_notes(finding)` читает его через `.get()`, при отсутствии/пустоте возвращает `[]` (вопросов на звонок по находке нет), никогда не выдумывает вопрос. **CSV-таблицы приложения** (`_build_appendix_tables`, пишутся всегда, даже пустыми — только заголовок): `report/appendix_tables/findings_appendix.csv` (дополнительные находки — check_id/name/confidence/money_category/money_amount_rub/assignee/recommended_action), `skipped_checks.csv` (id/block/reason — весь `degradation.skipped`), `seo_core_gaps.csv` (тот же skipped, отфильтрованный по блоку S — подмножество `skipped_checks.csv`). **Сноски** — фиксированные номера `[1]`/`[2]`/`[3]`, привязанные статично к этим трём таблицам (не автонумеруются по мере появления в тексте, т.к. ровно три таблицы пишутся при каждой сборке): `[1]` — заголовок «Дополнительные находки», `[2]` — заголовок «Что не удалось проверить и почему», `[3]` — заголовок «SEO-ядро — не посчитано»; новый раздел `## Сноски` в конце отчёта перечисляет пути к файлам. **`oral_review_agenda.md`** (`build_oral_review_agenda`, пишется отдельным файлом рядом с `diagnostic_report.md`) — бюджет 60 минут разложен явно (`ORAL_REVIEW_MINUTES_INTRO=5` + находки по `ORAL_REVIEW_MINUTES_PER_FINDING=10` + `ORAL_REVIEW_MINUTES_WRAP=5`), `MAX_ORAL_REVIEW_FINDINGS=5` выведен из этого бюджета, а не назначен произвольно; находки — топ-5 той же отсортированной по `_priority_key` последовательности, что и «Три главных разрыва»/план действий (не топ-3 вердикта — под звонок отведено больше времени); вопросы под каждой находкой — из `llm_notes`, свод открытых вопросов — в разделе «Вопросы и дальнейшие шаги». Заголовок отчёта (`_build_header`) обновлён: убрана более не верная оговорка «без повестки созвона» (7A/7C), заменена на ссылку на новый файл повестки. Тесты: `tests/test_report_build.py` — 20 новых тестов (группы 16-19: appendix_tables с находками/пусто, сноски-заголовки и раздел «Сноски», повестка с вопросами/без них/лимит топ-5/без находок, сквозной смоук-тест полного отчёта — markdown+3 CSV+повестка за один `build()`, согласованность числа строк CSV с текстом приложения) — `pytest tests/test_report_build.py` — **40 passed**, 0 failed (было 20 после 7C). `pytest tests/test_orchestrator_analyze_gate.py` — **5 passed** (гейт report не задет). Полный `pytest tests/` — **795 passed, 13 failed**; тот же состав pre-existing падений, что и в записях выше (`test_direct_2b_patch.py`×2, `test_extract_smoke.py`×9, `test_metrika_logs_lookback.py`×1, `test_transform_direct_normalize.py`×1), ни одно не относится к `src/report/`. **Тестовый прогон на фикстурном клиенте `pognali.rent`** (реальные `data/canonical`/`data/metrics` уже посчитаны): (1) `python run.py pognali.rent --stage report` с пустым `findings/approved/` — гейт корректно отказал; (2) добавлена временная одна утверждённая находка (не закоммичена, `clients/` целиком в `.gitignore`), вызван `build_report.build()` напрямую с `orchestrator.ClientPaths('pognali.rent')` — `diagnostic_report.md`, все 3 CSV и `oral_review_agenda.md` записались корректно с реальными skipped-проверками (21 строка `skipped_checks.csv`, 4 — `seo_core_gaps.csv`); временная находка и сгенерированные файлы отчёта удалены после проверки. **Побочное наблюдение (не исправлено, вне `allowed_files`):** `src/pipeline/orchestrator.py::run_report` по-прежнему не вызывает `build_report.build()` вовсе (только логирует стаб-сообщение «report: заглушка... не реализован» и возвращает `True`) — уже отмечено в 7C как отдельная задача по подключению `build_report` к оркестратору, этой задачей не устранено (файл вне `allowed_files`). |
| **FIX-ad-extensions-coverage** | DONE | 2026-07-30. Решение принято ПО ОФИЦИАЛЬНОЙ ДОКУМЕНТАЦИИ (сверено дважды независимыми WebFetch на `ref-v5/adextensions/get.html`), а не по аналогии: `adextensions.get` в Яндекс.Директ API v5 отдаёт **единственный тип расширения — CALLOUT** (уточнение) и только его текст (CalloutText); валидные `FieldNames` = `Id/Type/Status/StatusClarification/Associated`. **Цена/акция/срок/наличие через adextensions.get НЕ доступны ни в каком поле — отдельного типа расширения (PRICE/PROMOTION) в API нет.** Обходной путь не изобретался (принцип 5.d). Итог — вариант 3 задания (закрыть как unavailable явно), но **точечно, не молча "все A21-A24"**: только **A24** («устаревшая цена/акция/срок/наличие в объявлении») реально зависит от этих структурных полей. A21 (высокий CTR+низкая CR) и A23 (конкретный спрос→общая страница) этим НЕ затронуты — поля расширений им не нужны, что подтверждено `requires` в `config/methodology.yaml` (A21: `[direct_queries, visits]`; A23: `[visits, costs]`; A24: `[direct_queries]`); A22 — отдельная текстовая LLM-проверка, не тронута. Слепое пометить A21-A24 unavailable было бы фактически неверным — не сделано (принцип «не угадывать»). **Изменения в `src/extract/direct.py`:** (1) исправлен латентный баг — прежний код просил `"State"` в `FieldNames` для `adextensions.get`, а State там **невалиден** (возвращается в ответе, но не запрашивается) → error 8000 молча ронял весь вызов, тот же класс, что и `"Strategy"` в `campaigns.get`; `"State"` убран. (2) Новые константы `ADEXTENSION_TYPES=["CALLOUT"]`, `ADEXTENSIONS_FIELD_NAMES_ENUM`/`ADEXTENSIONS_FIELD_NAMES` + `_validate_field_names(...)` ДО запроса (тот же приём, что 2A-direct-strategy-fix) — невалидное поле фильтруется, не роняя источник. (3) `adextensions.get` теперь фильтруется `SelectionCriteria.Types=["CALLOUT"]`. (4) `_record_manifest` пишет постоянный caveat (как D11, не зависит от прогона): `ad_extensions_types_available=["CALLOUT"]`, `ad_extensions_price_fields_available=false`, `ad_extensions_caveat={affected_checks:["A24"], reason:...}` — A24 больше не в молчаливом «не проверено». `_fetch_ad_texts` получил параметр `log` (для валидации), докстринг модуля/функции описывают ограничение. **Тесты:** новый `tests/test_direct_ad_extensions.py` — 6 тестов (константы CALLOUT/no-State; реальный вызов шлёт только документированные поля + Types=CALLOUT + CalloutText; невалидное поле фильтруется до запроса и не роняет extract; ошибка adextensions изолирована — extract не падает, note записан; A24 закрыт явным caveat с причиной, A21/A23 НЕ в affected_checks; успешный CALLOUT-ответ выгружается в ad_texts.json). `pytest tests/test_direct_ad_extensions.py tests/test_direct_2a_strategy.py` — **17 passed**. `tests/test_direct_2b_patch.py` — 21 passed, 2 failed (`test_query_report_dimensions`/`test_geo_report_schema` — pre-existing из 4X-direct-normalize-2, семантика `cost_normalized`, не связаны с этой задачей и не тронуты ею). **Не тронуто (вне allowed_files):** `config/methodology.yaml` — A24 не получил `requires`-флаг на новый manifest-ключ и не переведён в другой `type_effective` через `type_downgrade_if` (потребовало бы правки реестра и `degradation.py`); manifest-caveat записан, но автоматическое понижение `type_effective` A24 по нему — отдельная задача с methodology.yaml/degradation.py в allowed_files. Blocker: нет. |
| **FIX-a24-type-effective-downgrade** | DONE | 2026-07-30. Закрывает зависимость, оставленную `FIX-ad-extensions-coverage`: манифест-флаг `ad_extensions_price_fields_available` теперь действительно понижает A24 вниз по цепочке manifest → degradation → confidence_cap. `config/methodology.yaml` A24: `type_downgrade_if: "ad_extensions_price_fields_available == false"`, `type_downgraded: "B"` (по прецеденту A07 — единственная действующая пара `type_downgrade_if`/`type_downgraded` в реестре; D11 `permanent_LOW` НЕ подошёл в качестве образца — проверено, что `type_downgrade_if=null` там намеренно не участвует в вычислении `type_effective`/`confidence_cap` через `degradation.py` вообще, LOW-потолок для D11 зашит отдельно и напрямую в `src/compute/block0.py::_run_d11`, а для A24 это означало бы трогать `block1.py` — запрещено промтом). **Обнаружено и заполнено:** `type_downgrade_if` в текущем `src/pipeline/degradation.py::evaluate_check` управлял только `type_effective`, но НИКАК не влиял на `confidence_cap` (тот считается исключительно из `source_modes`, независимо от типовых понижений) — под задачу «confidence тоже должен понизиться» такой связи попросту не существовало. Добавлено новое опциональное поле реестра `confidence_cap_downgraded` (задокументировано в шапке `methodology.yaml`) — применяется в `evaluate_check` ТОЛЬКО когда `type_downgrade_if` этой же проверки истинен, через `min_confidence(confidence_cap, confidence_cap_downgraded)`; поле опционально и не задано ни для одной другой записи реестра (включая A07) — поведение прочих проверок не изменилось. A24 — единственная запись с `confidence_cap_downgraded: "MED"`. `src/compute/block1.py::_run_a24` не тронут (запрещено промтом) — не потребовалось: он и так хардкодит `confidence: "LOW"` на каждую строку независимо от `confidence_cap`, т.е. row-level confidence уже на полу; изменение реально влияет на `data/metrics/degradation_report.json.checks[*].confidence_cap` (вход `analyze`), не на `a24.parquet` построчно. Тесты: `tests/test_degradation.py` — 3 новых (флаг false -> `type_effective=B`+`confidence_cap=MED`; флаг true -> оба без изменений; проверка без `confidence_cap_downgraded` (A07-подобная) не меняет `confidence_cap` при сработавшем `type_downgrade_if`). `pytest tests/test_degradation.py` — **9 passed** (было 6). `pytest tests/test_smoke.py` — **17 passed**, регрессий нет. Blocker: нет. |
| **FIX-stale-regression-fixtures** | DONE | 2026-07-30. Фикстуры `test_extract_smoke.py` синхронизированы с публичными контрактами: негоциация Metrika изолирует актуальное кандидатное поле вместо удалённого `lastSignhasGCLID`; Wordstat проверяет v2-цикл `topRequests -> dynamics`, обязательный `folderId` и `AuthError` на HTTP 401. Production-код не менялся. |
| **FIX-vat-source-tag-mapping** | DONE | 2026-07-30. Закрывает несовпадение имён между Q01 (`finance.vat_basis_by_source[].source` в `client_answers.yaml`) и `source_tag` в `costs.parquet`/direct-таблицах, подтверждённое `AUDIT-block0-client-answers-wiring-and-source-tag-mismatch`: `_vat_lookup()` (`build_canonical.py`) сравнивало строки точно после `.strip()`, без `.lower()`/транслитерации — из трёх реальных пар совпадала только `"direct"→"direct"`, `"seo"→"seo_fee"` и `"Яндекс Бизнес"→"yandex_business"` уходили в `vat_basis_unknown`, хотя ответ на Q01 фактически был дан. Добавлен явный словарь `_VAT_SOURCE_TAG_ALIASES` (ровно три пары, не общая нормализация регистра/транслитерации — она могла бы неверно сработать на будущих `source_tag`, которых сейчас нет в данных) — применяется в `_vat_lookup()` к `src` до записи в `out`, ДО сравнения с `source_tag` строк расходов. `src/compute/block1.py` не тронут (не в `allowed_files`) — `_direct_vat_multiplier`/`_open_duckdb_with_direct_vat` переиспользуют тот же `_vat_lookup`, поэтому фикс подхватился автоматически для `direct_queries`/`direct_campaigns`/`direct_geo`/`direct_placements` без правки block1.py (`"direct"→"direct"` — identity, поведение существующих тестов `test_block1_direct_vat_normalization.py` не изменилось, все 4 теста прошли без правок). Тесты (`tests/test_build_canonical.py`): 3 unit-теста на `_vat_lookup()` (seo-алиас, Яндекс Бизнес-алиас, неизвестный source остаётся как есть) + 3 сквозных через `build()` (seo→seo_fee применяет НДС, Яндекс Бизнес→yandex_business применяет НДС, source_tag вне трёх пар остаётся `vat_basis_unknown`, как раньше). `pytest tests/test_build_canonical.py -k vat tests/test_block1_direct_vat_normalization.py` (через `.venv`, где установлен `scipy`) — **18 passed**, 0 failed. Blocker: нет. |

| **FIX-vat-basis-path** | DONE | 2026-07-30. `run_transform()` deterministically loads `inputs/client_answers.yaml` and passes Q01 to `build()`, so `costs.parquet` no longer reads VAT basis only from config. Direct `build()` calls retain the legacy config fallback; supplied empty/null Q01 remains unknown. Added gross/net/unknown, YAML handoff, and D06 (`answer_not_applied=false`) tests. |
| **FIX-d02-d03-goals-manifest-contract** | DONE | 2026-08-01. `metrika_reports` объявляет `goals` в `canonical_tables`, поэтому raw manifest штатно делает D02/D03 runnable через degradation; block0 больше не обходит `runnable_ids`, а при runnable и пустом/отсутствующем goals пишет explicit unavailable-result. Целевые тесты пройдены. |
| **FIX-d12-join-integrity-contract** | DONE | 2026-08-01. D12 валидирует агрегированные pre/post JOIN-контроли из canonical manifest и не считает сегментацию `costs` фан-аутом без доказательства JOIN. |
| **FIX-d08-campaign-status-contract** | DONE | 2026-08-01. D08 использует `campaign_status` с provenance из `campaigns.get States=ALL`; non-active State + исторический расход — единственный finding, а unknown/not-returned — coverage gap. `last_positive_over_14_days` сохранён только как evidence. Тесты: 217 passed. |
| **AUDIT-byte-cap** | АУДИТ, код не менялся | 2026-08-02. Диагностика `ValueError` в `_apply_byte_cap` (`src/analyze/draft_findings.py:364`) на реальном прогоне pognali.rent. Код не менялся, `docs/implementation_status.md` — единственная запись вывода (только этот пункт). **1. Правило "обязательного ядра" как есть в коде (не по спекам):** `_apply_byte_cap` (draft_findings.py:334-367) резервирует без права урезания: `client_context`, `check_names` (имена всех 100 проверок реестра), `known_check_ids`, `analysis_candidates.columns/rows` (сгруппированные P06-кандидаты — ВСЕ, сколько их обнаружено в `analysis_candidates.json` этим прогоном, без списка/лимита по check_id), `degradation` (компактная таблица по всем 100 проверкам), `constraints`, `coverage`, `excluded_candidates`, `audit`. Урезается по порядку, только если размер всё ещё превышает cap: (a) `analysis_candidates.context`, затем (b) секции `compact_context.*` и `inputs.*`, крупнейшие сначала. Никакого явного флага "mandatory"/"обязательная" в `config/methodology.yaml` нет (grep по всем 100 записям `checks[*]` — совпадает только ключ `requires`, ни `mandatory`, ни `core`). Фактически в этом прогоне кандидаты обнаружены для **24 check_id** (29 candidate-groups: A15/A17/A20/A23/A24, C06/C12/C21, D01-D04/D07/D11, S02/S03/S06/S07/S13/S25/S26/S27, T02/T06) — не "~90" (гипотеза задания) и не 5 (старая продуктовая рамка). **2-3. Фактические числа (реальный прогон, `_json_size` как в самой функции):** `input_pack_bytes` (тело без system_prompt) = **276 559**, `final_serialized_bytes` (с system_prompt) = **280 352**. `byte_cap` = **100 000** — из константы `INPUT_PACK_BYTE_CAP` в `draft_findings.py:79` (не из `config/defaults.yaml` — ключ `analyze_input_pack_byte_cap` там отсутствует; не из `clients/pognali.rent/config.yaml` — override там тоже отсутствует, grep по обоим файлам пуст). **4. Разбивка по компонентам (байт):** `analysis_candidates` 239 073 (из них сами candidate-rows — 220 323, вложенный `coverage` — 18 619 см. п.6), `coverage` (верхний уровень) 18 958, `check_names` 11 048, `degradation` 4 268, `constraints` 1 791, `audit` 358, `client_context` 277, `known_check_ids` 601, `compact_context`/`inputs`/`excluded_candidates` — единицы байт (уже прижаты к 0 в исходных payload); `system_prompt` 3 764. Разбивка `analysis_candidates` по блокам: **S 99 855 байт (45.3%)**, D 39 547 (17.9%), C 30 976 (14.1%), A 27 995 (12.7%), T 21 950 (10.0%). **Гипотеза "SEO даёт непропорционально много caveats-текста" — ОПРОВЕРГНУТА фактическими байтами:** прямой подсчёт всех полей с `caveat` в имени (в `common` и в `segments.columns` каждой candidate-группы) даёт **0 байт** из 220 323 байт candidate-строк. Доминирование блока S объясняется не текстом деградации/оговорок, а объёмом реальных построчных данных (S02 — 141 кандидат-запрос, S03 — 159, S25 — 78 — по одной строке на SEO-запрос/страницу; для сравнения у A/C/D/T кандидат-групп на порядок меньше строк на проверку). **6. Задвоение и избыточность (подтверждено байтами):** (a) объект `coverage` из `analysis_candidates.json` попадает в pack ДВАЖДY — один раз как `pack["analysis_candidates"]["coverage"]` (18 619 байт, `draft_findings.py:267`), второй раз как `pack["coverage"]["source"]` (тот же объект, `draft_findings.py:540`) — оба места вне списка урезаемых `_apply_byte_cap`, то есть дубль целиком входит в "обязательное" ядро; убрать задвоение (не делал — вне права этой задачи) вернуло бы ровно один экземпляр. (b) Внутри каждой candidate-группы `common` (результат `_group_candidate_rows`) содержит объединение ключей ВСЕХ типов проверок методологии (в наблюдаемых группах — 202-204 ключа), из которых реально непустых — 6-11; JSON сериализует ключи с `null`-значением как есть. Посчитано по всем 29 группам: суммарный размер `common` = 154 881 байт, из них **146 654 байт (66.6% от всех 220 323 байт candidate-строк) — чистый null-key overhead**, не несущий данных. Это крупнейший вклад в превышение cap, крупнее и дубля coverage, и самого SEO-блока. **5. Пересчёт byte_cap после решения "SEO — ядро" (2026-07-13):** файл `marketing-diagnostics-methodology-v2.md` (источник истины №3 по CLAUDE.md, раздел 1 с этим решением) **в репозитории отсутствует** — как и `catalog-proveryaemyh-marketingovyh-ugroz-v2.md`/`data-export-spec-v2.md` (проверено `Glob` по всему дереву, кроме `.venv`). Проверить формулировку и дату решения независимо от задания невозможно. `git log -S "INPUT_PACK_BYTE_CAP" -- src/analyze/draft_findings.py` показывает, что константа `INPUT_PACK_BYTE_CAP = 100_000` введена коммитом `62d1e56` ("refactor: build compact deterministic analyze pack", 2026-08-02 00:03) — это ЖЕ ДАТА текущего прогона/аудита, значение не менялось ни разу после введения (проверено по всей истории файла), и ни в коммите, ни в коде нет обоснования, откуда взято число 100 000 (не привязано к количеству проверок явно). **Вывод по п.5: НЕ ПОДТВЕРЖДЕНО** — недостаточно данных (отсутствует исходный документ методологии), связь величины 100 000 именно с "5 проверками" из старой рамки не установлена и не опровергнута; известно только, что константа новая (введена в текущем рефакторинге) и не имеет документированного вывода числа. **Итог:** превышение cap (180 352 байта сверх 100 000) объясняется совокупно тремя факторами с фактическими числами — null-key overhead в `common` (146 654 байт, доминирующий), задвоение `coverage` (18 619 байт) и сам объём SEO-кандидатов (99 855 байт блока S, из них 0 байт — caveats). Blocker: нет для пп.1-4,6; п.5 — blocker "источник методологии недоступен в репозитории", ответ не подтверждён и не опровергнут явно, как и требовало задание. Production-код не менялся; читались только `src/analyze/draft_findings.py`, `config/defaults.yaml`, `clients/pognali.rent/config.yaml`, `config/methodology.yaml`, реальные `data/metrics/*` pognali.rent (не пересчитывались) — через временный read-only скрипт вне репозитория (`scratchpad/audit_byte_cap.py`), воспроизводящий `build_input_pack()`/`_apply_byte_cap()` дословно для получения чисел до исключения. |
| **PACK-0** | DONE | 2026-08-03: проверка assumptions отвязана от размера отправляемого пакета. `build_input_pack(..., return_full=True)` отдаёт пару send_pack/full_pack (full_pack — секции до проекций и до byte-cap); корпус `validate_finding_evidence(inputs=...)` собирается `build_validation_corpus(full_pack)`, поэтому урезание контекста byte-cap'ом больше не отбраковывает корректные находки. Числа evidence/money_amount_rub по-прежнему сверяются с `data/metrics/<check_id>.json` с диска. Cap вынесен в `config/defaults.yaml`: `analyze_input_pack_byte_cap: 150000`, `analyze_input_pack_warn_bytes: 120000` (≈68 тыс. токенов при 2.2 байта/токен); при отсутствии ключей используются константы `INPUT_PACK_BYTE_CAP`/`INPUT_PACK_WARN_BYTES`. Превышение warn_bytes — WARNING в лог стадии (опциональный параметр `draft(log=...)`, иначе `logging`), не исключение. `_analyze_input_pack.json` остаётся точным слепком отправленного тела (send_pack + system_prompt). Сжатие пакета не делалось — PACK-1. Тесты: `pytest tests/test_analyze_draft_findings.py tests/test_analyze_draft_findings_llm.py tests/test_analyze_validate_findings.py` — 53 passed. |
| **PACK-1** | DONE | 2026-08-03: входной пакет analyze сжат с 340 210 до **114 267 байт** на реальном клиенте pognali.rent (cap 150 000, `omitted_context: []` — контекст больше не режется). Пять детерминированных сокращений, ни одного вызова LLM (принцип 3): (1) `_group_candidate_rows` отдаёт `common` без null-ключей (`_drop_null_values`; `""`, `0`, `false` — значимые и остаются), из `segments` убираются колонки, пустые во всех строках группы: 220 353 -> 73 834 б; (2) строки `analysis_candidates.context` — построчные словари непустых полей без union-массива `columns`: 30 900 -> 10 565 б; (3) `coverage` перестал дублироваться (был и в `analysis_candidates.coverage`, и в `pack["coverage"]["source"]`), объект остался ровно в одном месте — `pack["coverage"]` — и лишился ключа `artifacts` (18 046 б телеметрии сканирования, это QA пайплайна, не evidence): 37 577 -> 888 б; (4) реестры сужены до `used_check_ids` — check_id, реально присутствующих в кандидатах прогона (24 из 100 для pognali.rent): `check_names`, `degradation.rows`, `constraints.source_cap_by_check`, `known_check_ids`; `degradation.counts` остаётся общерегистровым. Сужение `known_check_ids` осознанно сужает enum в `_findings_response_schema`; компенсация — скаляры `client_context.checks_total` (100) и `client_context.checks_not_runnable` (14), чтобы охват был виден явно; (5) `inputs.wordstat_stopwords` (9 175 б) не отправляется — это вход стадии extract, не evidence; в корпусе сверки assumptions (`build_validation_corpus(full_pack)`) он сохранён полностью, как и полные реестры. Ни одно значимое (не-null) значение кандидата не потеряно: сверка множеств (check_id, reason, поле, значение) на реальном `analysis_candidates.json` даёт 4784 пары до и 4784 после, 0 потерь; по строкам контекста — 310 и 310. Инварианты сохранены: кандидаты не выбрасываются (638 включённых, `candidates_omitted: 0`, S03 159 / S02 141 / C06 92 едут целиком), порядок резки в `_apply_byte_cap` прежний, `compact_context.seasonality` не тронута, сборка детерминирована и идемпотентна (два прогона дают равные пакеты), `_analyze_input_pack.json` остаётся точным слепком отправленного тела. Ломающее изменение фикстуры (санкционировано промтом, п.9 протокола): `test_real_p10_stress_pack_keeps_638_candidates_under_final_cap` перестроен на union-схему ~200 колонок / ~10 заполненных на строку вместо 8 плотных колонок — прежняя фикстура давала ложное «зелено». По той же причине обновлён `test_build_input_pack_collects_all_sections` (реестры теперь сужены). Тесты: `pytest tests/test_analyze_draft_findings.py tests/test_analyze_draft_findings_llm.py tests/test_analyze_validate_findings.py` — 57 passed. |
| **PACK-2** | DONE | 2026-08-03: `_apply_byte_cap` больше не роняет стадию на клиенте крупнее pognali.rent. После снятия всего опционального контекста включается последний эшелон `_truncate_candidate_groups`: группы кандидатов обрабатываются по убыванию размера (тай-брейк — размер JSON, затем check_id/candidate_reason) и обрезаются до top-N по влиянию, не глубже необходимого — цикл прерывается сразу после попадания в cap. Критерий влияния — первое поле из `analyze_candidate_impact_keys`, реально различающее строки группы (money_amount_rub -> loss_rub -> cost_rub -> gap_visits -> visits -> clicks -> shows), фолбэк `row_order`; N — `analyze_candidate_top_n`. Оба ключа читаются из `config/defaults.yaml` через `resolve_candidate_top_n` / `resolve_candidate_impact_keys`, константы `CANDIDATE_TOP_N=25` / `CANDIDATE_IMPACT_KEYS` — только фолбэк (как у byte-cap). Хвост не исчезает молча: в `segments.tail_aggregate` пишутся число свёрнутых кандидатов, критерий, сумма/min/max метрики и текстовая пометка о неполноте списка, а в `pack["audit"]["truncated_candidate_groups"]` — check_id, candidate_reason, candidates_sent, candidates_aggregated, criterion (плюс `coverage.candidates_aggregated`). `ValueError` остаётся ровно для одного случая — даже полная обрезка не укладывается в cap (конфигурационная ошибка, не данные). Реальный pognali.rent: 114 299 байт при cap 150 000, `truncated_candidate_groups: []`, `omitted_context: []` — путь не срабатывает. Обрезка детерминирована: два прогона на синтетическом клиенте вдвое крупнее (1276 кандидатов) дают побайтово одинаковый пакет. Blocker: ключи `analyze_candidate_top_n` / `analyze_candidate_impact_keys` НЕ добавлены в `config/defaults.yaml` — файл вне `allowed_files` задачи; до их добавления работают константы-фолбэки. Тесты: `pytest tests/test_analyze_draft_findings.py` — 36 passed; `pytest tests/ -k analyze` — 66 passed. |
| **PACK-2-FIX-1** | DONE | 2026-08-03: закрыты B1+B2 вместе (одна причина отказа, три проявления). **B1** — `_truncate_candidate_groups` стала прогрессивной: проход 1 применяет `top_n` разом ко всем группам (не только к превышающим его), затем при необходимости N снижается одинаковыми шагами сразу для всех групп (`n //= 2`, не по одной группе за раз — детерминизм), пока пакет не влезет; на N=0 группа схлопывается целиком в `tail_aggregate` (0 кандидатов в `segments.rows`). Каждый уровень N считается заново от исходного (до обрезки) состояния группы — иначе повторное снижение теряло бы данные для агрегата. Клиент с 400/200 мелкими группами (< top_n каждая), который раньше проходил мимо алгоритма и падал ValueError, теперь укладывается в cap. Побочно найден и исправлен реальный баг: возвращаемый `final_size` не учитывал вес самого `audit.truncated_candidate_groups` (список обновлялся ПОСЛЕ замера размера), из-за чего для сотен групп функция могла соврать, что пакет уложился, хотя после дозаписи audit-списка становилось больше cap — теперь `audit.truncated_candidate_groups` пересчитывается ДО каждого замера размера. **B2** — `_refresh_coverage` вызывается ПОСЛЕ `_truncate_candidate_groups` (не до) и считает по фактическому состоянию: `candidates_included` = сумма фактических `len(segments.rows)`, `candidates_omitted`/`candidates_aggregated` = сумма `tail_aggregate.truncated_candidates` по всем группам (включая полное схлопывание) — та же арифметика, что видна в audit, независимого счётчика больше нет. Инвариант `candidates_included + candidates_omitted == candidate_count` по всем группам — тест на три случая (без обрезки/частичная/полное схлопывание). **Falsy-баг** в `resolve_candidate_top_n` исправлен: явный `analyze_candidate_top_n: 0` больше не подменяется дефолтом 25 (`"key" in defaults`, а не `value or default`). Грепом найден третий экземпляр того же паттерна — `resolve_warn_bytes` (draft_findings.py, рядом с уже известным `resolve_byte_cap`) — НЕ исправлен, вне скоупа этой задачи. **system_prompt** — добавлен явный запрет №9: при наличии `tail_aggregate` у группы модель не достраивает и не экстраполирует недостающих кандидатов, не заявляет полный охват по проверке, использует `truncated_sum/min/max` явно как агрегат. `clients/pognali.rent`-масштаб (синтетическая фикстура на 638 кандидатов) по-прежнему без единой обрезки. Тесты: `pytest tests/test_analyze_draft_findings.py` — 39 passed. |
| **PACK-2-FIX-1B** | DONE | 2026-08-04: закрыт review-кейс «400 групп x 10 кандидатов» и шире — любое N групп сверх бюджета оверхеда. Причина, которую FIX-1 не закрывал: фиксированный оверхед схлопнутой группы (check_id, candidate_reason, note агрегата, отдельная запись в `audit.truncated_candidate_groups`) растёт линейно с ЧИСЛОМ групп, а прогрессивное снижение N режет только ось «кандидаты в группе». Добавлен **второй эшелон**: после снижения N до 0, если пакет всё ещё вне cap, полностью схлопнутые группы (`segments.rows == []` и есть `tail_aggregate`) сортируются по возрастанию влияния (тот же критерий `_group_impact_criterion`, новый не вводится; тай-брейк check_id/candidate_reason) и объединяются с конца в один общий элемент `_merge_collapsed_groups`: `tail_aggregate.merged_check_ids`, `merged_groups`, суммарные `truncated_candidates` и объединённые `truncated_sum/min/max` (гранулярность по check_id теряется честно — поимённых кандидатов в этих группах уже 0). В audit вместо N записей появляется одна с `merged_check_ids`. Группы, в которых после снижения N остались кандидаты, в объединение не идут никогда. Минимальное достаточное число объединяемых групп ищется бинарно (размер монотонен по k) — результат тот же, что при пошаговом объединении, без O(N) сериализаций. `_refresh_coverage` читает `merged_check_ids`, поэтому проверки не исчезают из `included_check_ids`; инвариант B2 (`candidates_included + candidates_omitted == исходное число кандидатов`) сохраняется. Факт: 400x10 (4000 кандидатов) собирается без исключения — 149 671 байт при cap 150 000, объединено 172 группы, в пакете 229 элементов; 5000x10 тоже проходит (объединено 4773). Реальный потолок — контент, который ни один эшелон резать не имеет права: 5000 групп по 1 кандидату (segments пуст, схлопывать нечего) по-прежнему даёт ValueError, и текст ошибки теперь фактический (размер, cap, сколько групп объединено, сколько поимённых кандидатов осталось), а не утверждение про «все группы схлопнуты». **Falsy-фикс:** `resolve_byte_cap` и `resolve_warn_bytes` переведены на общий `_resolve_int_default` — явный 0 больше не подменяется константой. Грепом по `src/analyze/*.py` найден четвёртый экземпляр паттерна — `draft_findings.py:1326` (`pack["audit"].get("warn_bytes") or resolve_warn_bytes(defaults)` в `draft()`) — НЕ исправлен по условию задачи. `clients/pognali.rent`-масштаб по-прежнему без единой обрезки. Тесты: `pytest tests/test_analyze_draft_findings.py` — 41 passed; `pytest tests/test_analyze_validate_findings.py` — 14 passed. |
| **PACK-2-FIX-1C** | DONE | 2026-08-04: три проявления byte-cap-дефекта (BLOCKER 1, BLOCKER 2, SHOULD FIX) закрыты структурно, а не точечно. Причина одна: размер меряли НЕ над тем объектом, который отправляется — цикл обрезки мерил промежуточное состояние, после чего `_refresh_coverage` дописывал 206-235 байт, и пакет выходил за cap уже после того, как алгоритм решил, что уложился (FIX-1 чинил этот же класс для `audit.truncated_candidate_groups`, FIX-1B воспроизвёл его для coverage; бинарный поиск, садясь в десятках байт от cap, превратил редкую опасность в закономерный отказ). **1. Единая точка замера:** `_assemble_and_measure` собирает пакет полностью (обрезка -> объединение -> запись audit -> `_refresh_coverage` -> сходимость `_set_pack_size`) и возвращает фактический размер отправляемого объекта; ВСЕ решения byte-cap — снятие опционального контекста, снижение N, объединение, бинарный поиск — принимаются только по нему. Константный margin/резерв под coverage сознательно НЕ вводился: он подобран под текущий размер секции и ломается при добавлении первого же поля. **Контрольный замер:** найденное бинарным поиском k обязательно перепроверяется сборкой, при промахе — линейный добор k+1, k+2...; на недоказанную монотонность размера по k алгоритм больше не опирается. **2. Максимальное объединение перед raise:** до ValueError принудительно выставляется k = len(merge_order) и делается перезамер — отказ возможен только если и это не влезло, поэтому «оба эшелона исчерпаны» больше не противоречит собственным числам (раньше на 1500x3 печаталось «исчерпаны» при 1271 объединённой группе из 1500). **3. Гетерогенный audit:** публичный аксессор `audit_entry_check_ids(entry)` возвращает список check_id для обеих форм записи (`check_id` / `merged_check_ids`); тесты переведены на него, форма самих записей не менялась. **Матрица прогонов** (группы x кандидаты x check_id, cap 150 000), каждый — отдельный тест со структурным инвариантом `len(json.dumps(финальный pack)) <= cap`, B2 и детерминизмом: 400x10x1 — 149 604 б (объединено 172); 400x10x4 — 149 763 б (172); 400x10x24 — 149 574 б (174); 1500x3x4 — 149 539 б (1272); 3000x2x4 — 149 539 б (2772); 1000x5x24 — 149 525 б (773); 600x10x4 — 149 763 б (372). Кейсы 400x10x4, 1500x3x4 и 3000x2x4 падали в ревью. Реальный потолок 5000x1x24 (segments пуст, схлопывать и объединять нечего) по-прежнему даёт ValueError с фактически верными числами; отдельный тест проверяет, что при недостижимом cap на 1500x3x4 объединены все 1500 групп до броска. `clients/pognali.rent` — без единой обрезки, 115 087 байт, побайтово тот же пакет, что на FIX-1B (число 114 299 из постановки — от более раннего состояния данных клиента, изменением не затронуто). full_pack / send_pack не тронуты. Тесты: `pytest tests/test_analyze_draft_findings.py` — 50 passed; `pytest tests/test_analyze_validate_findings.py` — 14 passed. |
| **PACK-2-FIX-2** | DONE | 2026-08-04 (коммит `af3e76b`): закрыт **B3** и три хвоста раундов FIX-1B/1C. **B3 — параметры обрезки в конфиге:** `config/defaults.yaml` получил `analyze_candidate_top_n: 25` (стартовый порог первого эшелона; явный `0` — предельное сжатие, все группы сразу в агрегат) и `analyze_candidate_impact_keys` (8 фактических ключей из `CANDIDATE_IMPACT_KEYS`: `payload.money_amount_rub` -> `money_amount_rub` -> `payload.loss_rub` -> `payload.cost_rub` -> `payload.gap_visits` -> `payload.visits` -> `payload.clicks` -> `payload.shows`) — один критерий на оба эшелона: и top-N внутри группы, и порядок объединения схлопнутых групп (`_collapsed_group_impact` переиспользует `_group_impact_criterion`, своих констант второго эшелона в коде нет — изобретать новые ключи было не для чего). Обрезка top-N — продуктовый рычаг Р3, единственное место оптимизации пакета, где реально теряется информация; настраивать его без деплоя кода теперь можно. Чтение — через `_resolve_int_default` и новый `_resolve_list_default`: явный `0` и явный `[]` (отказ от ранжирования, фолбэк `row_order`) отличаются от «ключа нет». `orchestrator.load_defaults()` отдаёт оба ключа наравне с остальными, явный проброс не нужен — `draft()` уже передаёт весь словарь в `build_input_pack`. Константы модуля `CANDIDATE_TOP_N` / `CANDIDATE_IMPACT_KEYS` сохранены как фолбэк. **SHOULD FIX ревью FIX-1B (четвёртый falsy-экземпляр):** `draft_findings.py` в `draft()` больше не пишет `pack["audit"].get("warn_bytes") or resolve_warn_bytes(defaults)` — значение читается из `pack["audit"]["warn_bytes"]` напрямую; это ВТОРАЯ точка потребления, и `or` отменял фикс FIX-1B на рантайме (explicit `0` честно доезжал до пакета и тут же подменялся на 120 000). Отсутствие ключа — баг резолвера выше по коду, поэтому KeyError, а не молчаливый дефолт. Тест интеграционный, не на резолвер: `draft()` на заведомо МАЛЕНЬКОМ пакете при `analyze_input_pack_warn_bytes: 0` обязан писать WARNING в лог стадии (тест на выход резолвера этот дефект по построению не ловил). Повторный греп по `src/analyze/*.py`: ПЯТОГО экземпляра паттерна на числовых конфигах НЕТ — остальные `x or {}` / `x or []` защищают контейнеры от None (для них falsy и отсутствие эквивалентны), `os.environ.get(...) or DEFAULT` в `_resolve_llm_model` / `_resolve_llm_base_url` читает строковые env-переменные, где пустая строка и есть «не задано». **SHOULD FIX ревью FIX-1C (system_prompt):** запрет №9 расширен на check_id, у которых в пакете не осталось НИ ОДНОЙ строки — они присутствуют только в `tail_aggregate.merged_check_ids`, но остаются в `known_check_ids` и `check_names` (те считаются до byte-cap). По такой проверке модели запрещено поимённое evidence, допустима только ссылка на объединённые агрегаты с пометкой неполноты. Тест проверяет обе стороны: при сработавшем объединении правило есть в промте, а `merged_check_ids` — в пакете. **SHOULD FIX ревью FIX-1C (гетерогенный audit):** единый аксессор `audit_entry_check_ids` был введён ещё в FIX-1C, тесты уже переведены на него — подтверждено, код не трогался. **Тест с вводящим в заблуждение именем:** `test_client_twice_the_size_of_pognali_truncates_instead_of_raising` -> `test_oversized_client_truncates_instead_of_raising`; фикстура — 1276 кандидатов с удлинёнными URL и лишним полем, то есть «клиент, заведомо не влезающий в cap», а настоящий 2x pognali (дублированные строки) даёт ~46 800 байт и обрезку не запускает ни разу. Тело теста и фикстура не менялись, только имя и докстринги (включая докстринг `_oversized_candidate_rows` и комментарий в coverage-тесте, несшие ту же ложную характеристику). Весь файл просмотрен: других расхождений имени/докстринга с фикстурой нет. **Регрессия:** матрица FIX-1C (400x10x1, 400x10x4, 400x10x24, 1500x3x4, 3000x2x4, 1000x5x24, 600x10x4) — все зелёные, структурный инвариант `len(json.dumps(финальный pack)) <= cap` держится; вынос параметров в конфиг ничего не сдвинул. `clients/pognali.rent` — без единой обрезки: 115 642 байта при cap 150 000, `truncated_candidate_groups: []`, `omitted_context: []` (+555 б к FIX-1C — ровно вес расширенного запрета №9 в system_prompt; число 114 299 из постановки относится к более раннему состоянию данных клиента). full_pack / send_pack не затронуты. Тесты: `pytest tests/test_analyze_draft_findings.py` — 54 passed; `pytest tests/test_analyze_validate_findings.py` — 14 passed. |
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

**FIX-feeds-get-contradiction (2026-07-30) — исправление предыдущего вывода
про feeds.get.** Пункт 3 выше был НЕВЕРЕН: сверка с официальной документацией
(ref-v5/feeds/get.html) показала, что `Ids` обязателен ТОЛЬКО когда передан
`SelectionCriteria`; «чтобы получить все фиды пользователя, не указывайте
SelectionCriteria». То есть предварительный список Id не нужен — это был баг,
а не ограничение API. `_fetch_feed` теперь реально вызывает feeds.get БЕЗ
SelectionCriteria (не через `_get_all`, который форсирует пустой
`SelectionCriteria={}`), маппит метаданные фидов в `product_feed.parquet` и
выставляет `feed_used` по факту непустого ответа. Докстринг модуля и комментарий
шага 9 приведены в соответствие. Тесты: `test_feed_listed_without_selection_criteria`
(пустой ответ → feed_used=False, вызов без SelectionCriteria) и
`test_feed_used_writes_parquet_when_present` (непустой → feed_used=True, parquet).
Ложный тест `test_feed_missing_ids_graceful` заменён. Уровень доступности A25 на
слое extract закрыт; сама проверка A25 (сверка фида с сайтом) не реализуется.

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
`b1ggocts4bcj79ds932l` (исправлено, было указано ошибочно — см.
AUDIT-live-verification-status), вписан в `clients/pognali.rent/config.yaml`,
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

---

**5B** DONE — 2026-07-28. Бизнес-логика D01–D06 в `src/compute/block0.py`
(D07–D12 вне скоупа). D01 (переотработка): по каждой из 4 групп ключевых целей
(`form_open/form_submit/call_click/messenger_click`) — достижения/уникальные
визиты с целью, `overtrigger` при отношении >= `goal_inflation_warning`. D02
(цель = клик, а не отправка): классификация `goals.type` — `action/button/
phone/email/messenger/social` (реальные значения выгрузки, см.
4I-goals-canonical) помечены "слабыми" (клик/JS-событие, не доказывают
отправку), `url/step` — "сильными"; `suspect_click_not_submit` — слабый тип +
имя цели похоже на бизнес-отправку (эвристика по ключевым словам). D03
(смешение целей): пересечения `goal_id` между группами `config.goals.*`
(`form_open_goal_ids` и т.д. из client config.yaml через
`orchestrator.load_client_config`) — находка `goal_group_overlap`; плюс
`goal_mix_summary` (цели вне всех групп, `goals_qa` mismatch из canonical
manifest). D04 (покрытие трекингом): по `device` — визиты с трафиком, но
нулём достижений хотя бы одной ключевой цели (`no_tracked_conversions`). D05
(UTM теряется): доля `source_group='ad'` визитов с `utm_source_raw` из набора
"не задано" токенов (зеркалит `_UTM_UNDEFINED_TOKENS` transform, не
импортируется — принцип 2, слой читает выход, не внутренности соседнего
слоя); `utm_uncertain` из `data/canonical/manifest.json.flags` переносится в
вывод (обязательное требование докстринга `build_canonical.py`). D06 (НДС-база):
per-`source_tag` сверка уже посчитанного `costs.parquet.cost_status`
(gross/net/vat_basis_unknown, см. `_apply_vat_to_rows`) с ответом клиента
`inputs/client_answers.yaml: finance.vat_basis_by_source` —
`answer_not_applied`, если объявленная база не совпала с фактической;
`mixed_basis_across_sources`, если среди источников есть и gross, и net.

**Разрыв D02/D03 vs runnable_ids (сознательное решение, задокументировано в
докстринге block0.py):** `src/extract/metrika_reports.py::CANONICAL_TABLES`
до сих пор не объявляет `goals` (известно с 4I-goals-canonical, не в
allowed_files этой задачи) — `degradation.build_degradation_report` держит
D02/D03 в `runnable=False` даже когда `goals.parquet` физически присутствует
и непуст. Как явно указано в промте задачи, D02/D03 в `run()` НЕ гейтятся
через `runnable_ids`: доступность проверяется напрямую (`visits` в
`load_canonical`, `goals.parquet` непуст через `pq.ParquetFile(...).metadata.
num_rows`). Если недоступны — явная запись `{"status": "unavailable",
"reason": "goals metadata недоступна"}` в `d02.json`/`d03.json`, не
молчаливый пропуск (проверено тестом на отсутствующий и на пустой
`goals.parquet`). D01/D04/D05/D06 гейтятся штатно — `id in runnable_ids and
<таблица> in load_canonical(paths)` (defensive: `runnable_ids` может
считать таблицу доступной по raw-манифесту, даже если transform фактически
не записал непустой parquet).

Все вычисленные `confidence` перед записью капаются вниз через
`degradation.min_confidence(confidence, confidence_cap)` — `confidence_cap`
берётся из уже записанного `degradation_report.json.checks[].confidence_cap`
(а не пересчитывается заново); `write_metric_artifact` остаётся страховкой
(`ConfidenceCapViolation`), а не механизмом капа.

Новые тесты `tests/test_block0.py` (17 шт.): по одному положительному и
отрицательному сценарию на D01–D06, недоступность goals (отсутствует/пуст —
явная запись, не пропуск), пропуск D01 вне `runnable_ids`, D02/D03
выполняются при пустом `runnable_ids`, если `goals.parquet` присутствует
(регрессия против разрыва extract-манифеста), confidence капается к потолку
из `degradation_report.json` даже при большой выборке. `pytest
tests/test_block0.py` — **17 passed**. Регрессия: `pytest
tests/test_compute_common.py tests/test_degradation.py
tests/test_methodology_goals_requires.py tests/test_smoke.py
tests/test_config_schema.py` — **64 passed**, 0 failed.

---

**5C** DONE — 2026-07-28. Бизнес-логика D07–D12 в `src/compute/block0.py`,
завершает блок 0 (D01–D06 не переписывались). Все шесть проверок работают
строго в пределах `requires` из `config/methodology.yaml` (не менялся —
вне `allowed_files`) — у compute нет доступа к сырому `manifest.json`
Директа (`archived_campaigns_retrievable` и т.п.), только к
`data/canonical/*.parquet`; там, где формула каталога требует источника,
которого в canonical-слое нет (часовой пояс визита, статус кампании
Директа), проверка сужена до того, что реально наблюдаемо, и это
ограничение зафиксировано в самой находке, а не спрятано.

D07 (расходы неполные/задвоены): два независимых сигнала из `costs.parquet`
+ `inputs/client_answers.yaml: finance.hidden_costs_rub_month` (Q02) — (1)
`declared_cost_check` на каждую заявленную статью — `missing_in_data`, если
у её `source_tag` нулевая сумма в `costs`, `amount_mismatch`, если фактическая
сумма меньше половины ожидаемой (`rub_month × число_месяцев_окна`); (2)
`possible_double_counted_budget` — `source_tag='direct'` и
`'yandex_business'` одновременно ненулевые (каталог §4, правило 8). D08
(архивные/остановленные кампании исключены из истории): группировка
`costs` по `campaign_id` (`source_tag='direct'`) — кампания
`stopped_before_window_end`, если последний день с расходом отстоит от
максимальной даты окна больше чем на `_D08_STOPPED_CAMPAIGN_BUFFER_DAYS=14`
дней; `no_stopped_campaigns_detected` (>=2 кампании и ни одна не
остановилась раньше конца окна) — сигнал риска, не факт: compute не видит
`campaigns.get`/`archived_campaigns_retrievable`, только то, что попало в
`costs`. D09 (периоды/часовые пояса/даты): `incomplete_last_month` — последний
календарный день `visits.date` раньше конца месяца; `visits_costs_period_mismatch`
— расхождение месяца min/max между `visits` и `costs`; часовой пояс не
проверяется — в canonical-слое нет tz-поля визита (протокол микрозадач п.5:
не придумывать проверку без данных). D10 (выгрузка неполная): календарные
дни без единого визита внутри `[MIN(date), MAX(date)]` — `missing_days_count`
+ ограниченный список (`_D10_MISSING_DATES_SAMPLE_LIMIT=20`, флаг
`missing_dates_truncated`); без сверки с UI-агрегатом Метрики (вне
canonical-слоя). D11 (сотрудники/тесты/боты): без `ym:s:isRobot` (недоступен
постоянно, см. D11 в CLAUDE.md) — прокси из `client_id` (частота визитов
>= `_D11_HIGH_FREQUENCY_VISITS_THRESHOLD=50` за окно) и `utm_source_raw`
(тестовые токены `_D11_TEST_MARKER_TOKENS`); `confidence` всегда `"LOW"` по
существу находки (не только из-за потолка) — гипотеза, не факт. D12 (join
на неверном уровне детализации): независимая проверка уникальности ключа
внутри самого блока 0 — `visits.visit_id` (защитно дублирует
`dedupe_visits` из transform, не переиспользуя его внутренности, принцип 2)
и составной `(date, source_tag, campaign_id)` в `costs`.

D09/D12 читают `costs` как опциональный вход (`optional` в methodology.yaml)
— доступность прокидывается из диспетчера параметром `has_costs`, не
пересчитывается внутри самой функции проверки.

Новые тесты `tests/test_block0.py` (14 шт.): по одному положительному и
отрицательному сценарию на D07–D12, плюс два теста на капание confidence
через `degradation_report.json` (D08 — per-campaign HIGH и summary MED оба
капаются до LOW; D12 — HIGH-факт капается до MED), демонстрирующие
заражение/ограничение зависимых метрик потолком проверки. Новый тестовый
хелпер `_write_dated_parquet`/`_write_costs_d`/`_write_visits_d` — пишет
колонку `date` как явный `pyarrow.date32()` (production `write_canonical_table`
в build_canonical.py всегда пишет date-колонки так; существующий
`_write_costs`/`_write_visits` этой гарантии не даёт, т.к. D01–D06 не делали
арифметику над датами). `pytest tests/test_block0.py` — **31 passed** (17
старых + 14 новых). Регрессия: `pytest tests/test_compute_common.py
tests/test_degradation.py tests/test_methodology_goals_requires.py
tests/test_smoke.py tests/test_config_schema.py` — **64 passed**, 0 failed.

---

**5D** DONE — 2026-07-28. Бизнес-логика A01–A11 в `src/compute/block1.py`
(первая часть блока 1 «экономика и эффективность платной рекламы»; A12–A26
вне скоупа этой задачи). Легаси-метрика 1.2 «разрыв платный трафик vs весь
сайт» (marketing-diagnostics-methodology-v2.md §6) считается целиком внутри
A01 — единственное место в пайплайне (не дублируется в block3.py), по прямому
указанию промта задачи.

Деньги — только через `cost_normalized`; если хотя бы одна строка группы
(кампания/фраза/match_type) имеет `cost_normalized IS NULL` (НДС-база не
установлена), сумма группы отдаётся как `null`, а не как частичная сумма по
ненулевым строкам и не как `cost_raw` — деградация вместо подмены (прямое
требование промта). «Чистые конверсии» — только сумма `goal_conv_<id>` по id
из `config.sources.direct.macro_goals` (собственная атрибуция Директа на
уровне кампании/запроса), никогда `conversions_all` (правило 11 каталога:
«конверсия по любой цели» не бизнес-результат). Если `macro_goals` не
настроены — проверка, которой нужны конверсии, пишет явную запись
`unavailable`, а не трактует это как 0.

Два задокументированных разрыва canonical-слоя (тот же принцип, что и у
block0 для D02/D03/D08 — ограничение extract/transform вне `allowed_files`
этой задачи, не устраняется здесь, а явно фиксируется в докстринге и выводе):
(1) `data-export-spec-v2.md` предполагает join `visits`→кампания через
`ym:s:lastSignDirectClickOrder`, но этого поля нет в `SCHEMAS["visits"]`
(build_canonical.py) — кампанийная экономика (A02, A04–A06, A08) поэтому
считает «чистые конверсии» из `goal_conv_<id>` самой `direct_campaigns`
(server-side атрибуция Директа), а не из join визитов на `campaign_id`; (2)
A07 («эффективная кампания теряет показы из-за бюджета/ставок») пишет
**только** `unavailable`, всегда: `WeightedImpressions`/`LostImpressionShare`
не входят ни в `SCHEMAS["costs"]`, ни в `SCHEMAS["direct_campaigns"]` —
экстрактор фиксирует в manifest только факт доступности поля
(`campaign_report_has_lost_impression_share`), не само значение, значение
нигде не сохраняется в canonical-слое.

Уровень уверенности: HIGH зарезервирован за находками визит-уровня с
выборкой >= `min_sample_visits` (CLAUDE.md, «Уверенность находок») — из
проверок этой задачи только `paid_vs_site_gap` внутри A01 считается по
`visits.parquet` визит-в-визит и может быть HIGH. Остальные (A02–A11,
кампанийная/фразовая экономика на отчётных агрегатах Директа) — не выше MED
по построению, независимо от объёма данных; `confidence_cap` из
`degradation_report.json` капает дальше вниз, никогда не поднимает
(`_cap`/`degradation.min_confidence`, тот же паттерн, что в block0).

Статзначимость (A01, `paid_vs_site_gap`): двусторонний z-тест разницы двух
долей (`scipy.stats.norm`, доля формы-отправки платного трафика против всего
сайта) сравнивается с `significance_alpha`; находка `paid_underperforms`
требует одновременно порога `gap_pp >= 0.03` (эвристика по прецеденту legacy
1.2, `00_general-data/marketing-diagnostics-methodology-v1.md`) И
статзначимости И `ad_visits >= min_sample_visits` — малые выборки не
объявляются находкой (каталог, правило 14).

match_type (A09–A11, AUDIT-match-type подтверждено 2026-07-23): KEYWORD/
SYNONYM/RELATED_KEYWORD — разрез «по конкретной фразе» как есть, без склейки
в одну категорию (A09/A10). NONE («прочее», не «неточное совпадение», не
синоним автотаргетинга) — отдельный агрегат `outside_named_phrases` в A09, не
смешивается с разрезом «по фразе»; в A11 участвует наравне с остальными
match_type, т.к. сама проверка — про сравнение типов соответствия между
собой (каталог: «Разнести запросы по источнику подбора, типу соответствия и
конверсии»).

Остальные эвристические пороги (каталог не даёт точных чисел — тот же подход,
что у block0 для D08/D10/D11, задокументированы в модуле рядом с константой):
A02/A05/A11 — минимум 5 чистых конверсий, чтобы считать цель/CPA
статистически осмысленными; A05/A11 — CPA хуже медианы/CPA по KEYWORD в 1.5
раза = «устойчиво хуже»; A06 — разрыв доли расхода/доли конверсий >= 15 п.п. =
«не по эффективности»; A08 — от 5 кампаний, доля «мелких» (< 5% бюджета)
>= 50% = «раздроблена»; A10 — повтор нулевой конверсии в >= 2 разных месяцах =
«кандидат на минус-слово».

Новые тесты `tests/test_block1.py` (21 шт.): минимум один сценарий находки на
каждую из A01–A11, плюс отдельно — статзначимость (A01: значимый и
незначимый/малая выборка случаи), CPA outlier (A05), кампания с расходом и
0 чистых конверсий (A04), неизвестная НДС-база — `cost_normalized IS NULL`
деградирует, а не подставляет `cost_raw` (A04, A09), партия запросов
`match_type=NONE`, не искажающая разрез «по фразе» (A09), unavailable без
`macro_goals` (A02/A09) и без артефактов блока 0 (A03), капание confidence
через `degradation_report.json`. `pytest tests/test_block1.py` — **21
passed**. Регрессия: `pytest tests/test_compute_common.py tests/test_block0.py
tests/test_degradation.py tests/test_methodology_goals_requires.py
tests/test_smoke.py tests/test_config_schema.py` — **95 passed**, 0 failed.

---

**5E** DONE — 2026-07-28. Бизнес-логика A12–A26 в `src/compute/block1.py`
(вторая часть блока 1 «экономика и эффективность платной рекламы» — гео,
устройства, расписание, РСЯ, ретаргетинг, бренд, структура кампаний, CPC/CTR,
запрос-объявление-посадочная, товарный фид, лаг/сезонность). A01–A11
(задача 5D) не переписывались.

**Три структурных разрыва canonical-слоя, подтверждённые построчным чтением
`src/extract/direct.py` и `src/transform/build_canonical.py` перед
реализацией (тот же принцип, что A07 в 5D — не придумывать проверку без
данных):** `campaign_targeting.json` (гео/устройства/расписание/корректировки
ставок как НАСТРОЙКИ), `keywords.parquet`, `product_feed.parquet` — extract
пишет все три в `data/raw/direct/`, но `build_canonical.py` не строит из них
ни одной канонической таблицы; расширение схемы вне `allowed_files` задач
5D/5E. Следствие: **A16** (ретаргетинг) и **A25** (товарный фид) пишут только
`unavailable`, всегда — без campaign_targeting/product_feed нет способа даже
определить, какие кампании ретаргетинговые, или прочитать сам фид. **A18**
(пересечение кампаний за один спрос) реализован только через `direct_queries`
(поисковые запросы) — по ключевым фразам/аудиториям/гео не проверяется; это
сужение уже отражено в `config/methodology.yaml` (`A18.requires ==
["direct_queries"]`), не введено этой задачей.

**Гап `direct_placements.cost_normalized`, названный в промте задачи как
требующий проверки перед использованием — проверен построчным чтением
`build_direct_placements` (build_canonical.py): уже закрыт** (задачи
4X-direct-placements-align/4X-direct-reconcile, см. записи выше) — контракт
`cost_raw`(int, микрорубли)/`cost_rub`(float, всегда)/`cost_normalized`(null
до Q01)/`vat_basis_applied`(False) идентичен `direct_queries`/`campaigns`/
`geo`. A15 использует `cost_normalized` через тот же `_money()`, что A09–A11
— НЕ `cost_raw` с оговоркой, оговорка из промта была актуальна до этой сверки.

Четыре таблицы, реально присутствующие в canonical (`direct_geo`,
`direct_placements`, `ad_texts`, `seo_queries`), используются как
дополнительные входы сверх голого `requires` реестра — тот же прецедент, что
A02/A04–A08 (задача 5D) читают `direct_campaigns` сверх `requires: [costs,
visits]`; `requires` определяет только грубые `runnable_ids` верхнего уровня,
не исчерпывающий список входов проверки. `ad_texts_archived.parquet` **не
читается ни в одной функции блока** (прямое требование промта) — A20–A24
работают только с `canonical["ad_texts"]` (уже отфильтрован по State=ON в
transform, повторная фильтрация не производится); тест
`test_a22_query_ad_keyword_mismatch_and_no_archived_file_needed` явно
проверяет отсутствие archived-файла на диске.

Целевой регион для A12 — эвристика: `config.client.geo` (единственное
доступное, полу-структурированное поле, свободный текст) сопоставляется
подстрокой с `direct_geo.location_of_presence_name`; без `client.geo` A12
пишет `unavailable` (методология v2 §4 запрещает находку без сравнения с
целевым CPA). Час показа (A13) структурно недоступен ни для одного клиента
(`CAMPAIGN_PERFORMANCE_REPORT` — только по дням) — явная запись
`hour_of_day_unavailable` рядом с рабочим разрезом по дню недели (день недели
— из `direct_campaigns.date`, кампанийный агрегат, MED-потолок). A14
(устройства) и A23 (общая посадочная vs тематическая) — визит-уровневые
сравнения (source_group='ad' по `visits.parquet`), поэтому могут быть HIGH
при достаточной выборке — то же исключение, что `paid_vs_site_gap` в A01.
A22 (запрос vs текст объявления) и A24 (устаревшая цена/акция) — эвристики
(пересечение токенов; regex по цене/акции) на LOW по построению, требуют
ручного подтверждения (каталог: тип A+B) — не автоматический вердикт.

Уверенность и деньги — те же правила, что в 5D: `cost_normalized` — 
единственный денежный источник (null при неполной НДС-базе группы, не
`cost_raw`); «чистые конверсии» — только `goal_conv_<id>` по
`config.sources.direct.macro_goals`, никогда `conversions_all`; `_cap`
капает вниз, никогда не поднимает.

Новые тесты `tests/test_block1.py` (21 шт., итого файл — 42): минимум один
сценарий на каждую из A12–A26, плюс отдельно — капание confidence через
`degradation_report.json` (A12), неполные targeting-поля (пустой
`location_of_presence_name` в direct_geo исключается, не роняет проверку,
A12), unavailable без `macro_goals`/без нужной canonical-таблицы (A02-стиль:
A13/A14/A17/A21/A22/A26), всегда-`unavailable` структурные проверки (A16,
A25), ad_texts без ad_texts_archived.parquet на диске (A22). `pytest
tests/test_block1.py` — **42 passed**. Регрессия: `pytest
tests/test_compute_common.py tests/test_block0.py tests/test_block1.py
tests/test_degradation.py tests/test_methodology_goals_requires.py
tests/test_smoke.py tests/test_config_schema.py` — **137 passed**, 0 failed.

---

**5F** DONE — 2026-07-28. Бизнес-логика T01–T10 в `src/compute/block2.py`
(блок 2 «трафик, каналы и атрибуция» — старое содержимое файла, заглушка под
устаревшую нумерацию 2.1–2.5 legacy Блока 2, полностью заменено).

**T02 — «наивная» vs «corrected lastsign»:** наивная модель —
`last_traffic_source_naive`, пропущенная через ту же `classify_traffic_source`
(импортирована из `transform.build_canonical`, не переопределена — одна
таблица маппинга на весь пайплайн). Corrected — `source_group_resolved`,
уже посчитанный transform-слоем обязательным carry-forward шагом
(methodology v2 §5) до попадания canonical в compute; compute только
сравнивает готовые колонки (confusion-матрица + naive/corrected дельта по
ad/organic), ничего не восстанавливает повторно и не переопределяет
source_final/source_group_resolved — прямой трафик не становится «рекламой»
никаким условием внутри block2.py. Отсутствие колонки `source_group_resolved`
(canonical собран до carry-forward) → явный `unavailable`, не суррогат.

**T03/T10 — структурный разрыв (сырой referer/домен не в canonical):**
`ym:s:referer` запрашивается экстрактором (`VISIT_FIELDS_BASE`,
`src/extract/metrika_logs.py`), но не входит в `SCHEMAS["visits"]`
(`build_canonical.py`) — тот же прецедент, что A07/A16/A25 в block1.py.
T03 автоматически считает только частоту/долю разрывов сессии
(internal/undefined → carry-forward), домен-источник — ручная проверка
(`type_default: "A+B"` в methodology.yaml, поле артефакта
`domain_level_detection_available: false` явно, не молчит). T10 — эвристика
по client_id в группе `source_final='referral'`: повторяемость визитов
(>= 5, `_T10_MIN_VISITS_FOR_SPAM_CANDIDATE`) + нулевая вовлечённость
(ни одного form_open/form_submit/call_click/messenger_click) — оба сигнала
прямо названы каталогом («поведение», «повторяемость визитов»); география не
используется (в visits нет гео именно реферера, только гео пользователя —
несвязанный сигнал не выдаётся за подтверждение спама).

Остальные проверки — прямые агрегаты по canonical: T01 (доля UTM-разметки
по visits, отдельно по source_group, плюс поиск нестандартизированных
вариантов utm_source по регистронезависимому ключу), T04 (визит-уровневые
ad-конверсии Метрики против `goal_conv_<id>` Директа из `direct_campaigns`
— две разные модели атрибуции для одного канала, расхождение вне полосы
[0.5, 2.0] помечается явно), T05 (brand/non-brand по `seo_queries.is_brand`
+ `is_brand_query()` на `direct_queries.query`, импортирована из
`transform.build_canonical`), T06 (`inputs/client_answers.yaml`:
`business.offline_lead_channels` + `directories.{yandex_maps,gis2,
calltracking}`, дополнительно — счётчики `call_click_count`/
`messenger_click_count` из visits как частичная видимость), T07 (честный
подсчёт `client_id` как cookie, не клиента — `cookie_is_not_customer_proxy:
true` всегда в артефакте, distinct client_id никогда не схлопывается),
T08 (доля визитов/конверсий по `source_final`, доля расхода по campaign_id
из costs — риск концентрации на канале/кампании), T09 (медианный
бейзлайн по (source_final, date) при >= 7 дней истории и медиане >= 5
визитов/день; всплеск/провал — эвристика x3/x0.33 от медианы; сопоставление
даты аномалии с `client_answers.changes_log` в окне ±3 дня — автоматическая
часть корреляции с «данными другой системы» из каталога, остальное — ручной
разбор в analyze).

Пороги-эвристики (каталог не даёт точных чисел) документированы у каждой
константы в block2.py — тот же принцип, что A02–A26 в block1.py. Confidence:
HIGH только для прямых визит-уровневых долей при выборке >= `min_sample_visits`
(T01/T02/T03/T07/T08 summary-строки); все эвристические пороговые находки
(T04–T06, T09, T10, сегментные разрезы T01/T02/T08) — MED по построению,
`_cap` капает вниз через `degradation_report.json`, никогда не поднимает.

Тесты `tests/test_block2.py` — по 1–2 сценария на каждую из T01–T10, плюс
обязательные из промта: последовательность ad→direct (T02, наивная vs
corrected), несколько cookie одного client (T07, честный подсчёт без
дедупликации), аномальный источник (T09, спайк x6 от медианы), спам-реферал
и нормальный реферал (T10, позитив/негатив), unavailable без
`source_group_resolved` (T02). `pytest tests/test_block2.py` — **15 passed**.

---

**5G** DONE — 2026-07-29. Бизнес-логика C01–C12 в `src/compute/block3.py`
(блок 3 «CRO, сайт и воронка до обращения», первая часть — каталог v2 §8).
C13–C25 вне скоупа этой задачи, не диспетчеризуются.

Три известных структурных разрыва задокументированы в докстринге модуля, не
устранены (вне `allowed_files`): (1) `src/extract/crux.py` не даёт канонической
таблицы (`CANONICAL_TABLES = []`) — `requires: [crux]` (C01/C02) никогда не
станет `runnable` через автоматическую деградацию, тот же класс разрыва, что
у 4I-goals-canonical; (2) `src/extract/site_crawl.py: CANONICAL_TABLES =
["pages"]`, а фактическая каноническая таблица — `site_pages` (SCHEMAS,
build_canonical.py) — то же для `requires: [site_crawl]` (C03/C08/C11); тесты,
как в test_block1.py/test_block2.py, конструируют `runnable_ids` явным
множеством, не полагаясь на реальную деградацию. (3) `inputs/manual_form_tests.
yaml` не упомянут в requires/optional ни одной проверки C01–C12 в
config/methodology.yaml, хотя marketing-diagnostics-methodology-v2.md §6 прямо
называет C03/C08–C11 требующими его — блок читает файл напрямую (тот же приём,
что T06/T09 в block2.py читают `client_answers.yaml` напрямую, не входя в их
`requires`).

CrUX (C01/C02) читается напрямую из `data/raw/crux/crux.json` (не canonical —
у источника нет канонической таблицы), рейтинг по официальным порогам Google
Core Web Vitals (LCP/CLS/INP/FCP, общеизвестный отраслевой стандарт, не
изобретён для задачи); при `cwv_field_data_available=false` — фолбэк на
`inputs/manual_cwv.yaml` с confidence принудительно MED
(`defaults.crux_min_field_data`); при отсутствии обоих источников — явный
`unavailable`. C01 явно помечает `device_specific: false` — CrUX-запрос не
фильтрует по formFactor, p75 агрегирован по всем устройствам, не только
мобильным (структурное ограничение экстрактора). C02 сравнивает p75 каждого
проверенного key_url с origin-агрегатом (шаблон = URL, т.к. отдельной схемы
"тип страницы" в конфиге/canonical нет).

C03/C08/C11 — полностью ручные (каталог Источник=B, methodology v2 §6:
«не автоматизируются в принципе»), общий хелпер `_run_manual_only_check`
транспортирует patterns/conclusions `inputs/manual_form_tests.yaml` КАК ЕСТЬ
(без переклассификации по check_id — единственный источник не несёт разметки
"это C03 vs C08", реклассификация оставлена analyze/аналитику), гейт —
`site_pages` в canonical (крawler как инфраструктурная предпосылка, не
источник самих находок). C04/C05 джойнят `visits.entry_page` (уже нормализован
transform'ом) с `site_pages.url` через тот же принцип нормализации пути
(`_url_path`, зеркало `normalize_entry_page`); без `site_pages` — unavailable.
C05 явно помечает `utm_preservation_verifiable: false` (site_pages хранит
только `final_url`, не query string на каждом хопе цепочки). C06 — воронка
open→submit (легаси 1.1) по сегментам device/source_group; каталожная
трёхступенчатая воронка open→start→submit НЕ реализована — признака "начал
заполнять форму" нет в `goal_flags()`/`config.goals` (структурный разрыв,
поле `stage_start_available: false` в артефакте). C07/C09/C12 — автоматические
визит-уровневые сигналы (общий отвал формы, device-разрез конверсии через
двухвыборочный z-тест, доля визитов без единого целевого действия по
`entry_page`) + необязательное обогащение `inputs/webvisor_findings.yaml` КАК
ЕСТЬ. **C10 — сверка с D01.overtrigger** (`data/metrics/d01.json`, тот же
прецедент, что A03 в block1.py читает d01/d03): переотработка цели
`form_submit` уже подтверждена на реальных данных Pognali как системный
артефакт двойного счёта целей (goal-flags-overtrigger-symmetry-check,
87.9% визитов-хитов), а не обязательно повторной физической отправкой формы —
при `confounded_by_goal_overtrigger=true` автоматический сигнал C10 прижимается
к LOW и не считается самостоятельным подтверждением проблемы.

Тесты `tests/test_block3.py` — обязательные из промта: воронка по сегментам
(C06, device + source_group), CrUX отсутствует → ручной замер с confidence MED
(C01), ручной input отсутствует → unavailable (C03), плюс по 1–2 сценария на
остальные C02/C04/C05/C07/C08/C09/C10/C11/C12 и capping через
degradation_report. `pytest tests/test_block3.py` — **22 passed**. Регрессия:
`pytest tests/test_block0.py tests/test_block1.py tests/test_block2.py
tests/test_block3.py tests/test_compute_common.py tests/test_degradation.py
tests/test_smoke.py` — **154 passed**, 0 failed.

---

**5H** DONE — 2026-08-01. Бизнес-логика C13–C25 в `src/compute/block3.py`; контрактный патч C14/C20/C24: C14=`site_crawl + manual_form_tests` (Webvisor только enrichment, confidence≤MED/cap), C20=`webvisor_findings` только G2 (confidence≤MED/cap, без CWV/device-CR), C24=`visits + site_crawl`, но без URL-level availability пишет стандартный `unavailable` как UNVERIFIABLE без client facts/confidence. Локальный recompute подтвердил корректную деградацию недоступных источников; dispatch удаляет stale CSV/JSON только текущих skipped checks, а degradation serializes `requires` в `checks` и `skipped`.
(та же вторая половина блока 3 — каталог v2 §8), диспетчер `run()` расширен;
C01–C12 не переписаны.

Из 13 проверок только C21 (browser/os/screen сегментация конверсии) несёт
полноценную автоматическую часть — visits хранит browser/os/screen_resolution
(backfill-патч) и form_submit, сравнение сегмента с самым массовым значением
того же измерения тем же двухвыборочным z-тестом, что уже использует C09;
device намеренно исключён из C21 (число уже полностью принадлежит C09 под
своей причинной рамкой — не дублируется под другим check_id). C13/C24 несут
единственный содержательный автоматический сигнал через client_facts —
`inputs/client_answers.yaml` читается напрямую (не входит в requires/optional
ни одного из них в methodology.yaml, тот же приём "requires управляет только
диспетчеризацией", что уже применялся к `manual_form_tests.yaml` в 5G):
C13 — `site_and_form.price_shown_before_submit`/`deposit`, C24 —
`capacity_limits` (Q04). C14/C17/C23 — полностью ручные (тип B, как
C03/C08/C11), гейт `site_pages` в canonical; C14 дополнительно принимает
optional `webvisor_findings`. C20 — только optional `webvisor_findings`
(автоматического сигнала о попапах/баннерах в схеме нет).

Четыре новых структурных разрыва задокументированы в докстринге модуля (не
устранены — extract/transform/config вне `allowed_files` этой задачи):
(7) внутренний поиск по сайту (C18/C19) не выгружается ни одним модулем
`src/extract/` и не имеет канонической таблицы — C19 (type_default="A", по
методологии полностью автоматическая) всегда пишется `unavailable`, не
имитируется по косвенным данным; (8) пошаговая воронка корзины/бронирования
(C22, type_default="A") невосстановима — `goal_flags()`/`config.goals` знает
только 4 плоские группы целей без промежуточных шагов и без отдельной группы
"cart_step"/"booking_step" — C22 всегда `unavailable`; (9) CTA-элементы,
вторичные элементы страницы, попапы/баннеры, наличие товара/услуги и
классификация страниц контент/коммерция не хранятся ни в `site_pages`, ни в
`visits` — C15/C16/C18/C25 (A+B без применимой авто-части) сведены к общему
хелперу `_run_manual_form_tests_fallback` (fallback на
`inputs/manual_form_tests.yaml`, явные поля `automatic_component`/
`limitation` в каждой ручной строке, чтобы проверка не выглядела "забытой");
(10) C21 сознательно не пересчитывает device-конверсию, уже посчитанную в C09
(«один источник правды на одну цифру», тот же прецедент, что A01/A03 vs C06
для легаси 1.2 в 5G).

Тесты `tests/test_block3.py` — минимум 1 сценарий на каждую из C13–C25
(present/unavailable там, где ветвление есть: C13/C14/C16/C18/C20/C24, плюс
C15/C25 fallback, C17/C23 manual-only, C19/C22 always-unavailable, C21
browser-сегментация). `pytest tests/test_block3.py` — **38 passed**
(venv `marketing-diagnostics/.venv` — системный Python без `scipy`/`duckdb`
не годится для этого модуля).

---

**5I** DONE — 2026-07-29. `src/compute/money_frame.py` — денежная рамка
(каталог v2 правило 15; methodology-v2 §8), собирает уже посчитанные числа
из `data/metrics/{aXX,cXX}.json` (block1/block3), новой бизнес-математики не
вводит. Подключение: `money_frame` добавлен последним элементом
`common.BLOCK_MODULE_NAMES`, поэтому в `dispatch_blocks` выполняется уже
после block1(A)/block3(C) и читает их готовые артефакты с диска.

Четыре денежные категории (`MONEY_CATEGORIES`) не смешиваются — подытог
считается отдельно на каждую (`kind="category_total"`), общего грандтотала
по всем четырём нет. "Главные величины" — декларативные `_FLAT_RULES`
(A04/A06/A09/A10/A17: берут уже посчитанное поле со строки с explicit
проблемным флагом) и `_BENCHMARK_RULES` (A05/A11/A12/A13/A14/A19:
`excess = cost - volume*benchmark` по уже посчитанным в самой A-проверке
cost/volume/median-полям); A18 — отдельный обработчик вложенного списка
`campaigns[]`. Сценарии (`equivalent_additional_conversions`) реализованы
только для C06 (флагманская находка, легаси 1.1): разрыв доходимости формы
сегмента относительно сайта в целом × объём сегмента = "недополученные
конверсии", переводится в ₽ через сквозной CPA A04 (`_blended_cpa_from_a04`
— сумма cost_normalized_rub/сумма net_conversions по всем кампаниям); без
A04 сценарий всё равно пишется, но `amount_rub=None` с явным допущением
"сквозной CPA недоступен". Остальные 24 C-проверки в ₽ не переводятся — нет
уже посчитанного разрыва, который можно перевести без новой формулы вне
источников истины (протокол микрозадач CLAUDE.md, п.5). Каждый сценарий
несёт `scenario=True` + `scenario_label="сценарий, не прогноз"`. confidence
каждой находки = `min(confidence строки, confidence_cap проверки из
degradation_report)` — assumptions отдельного потолка не имеют, наследуют
потолок находки; per-row `assert_confidence_within_cap` перед записью.

SEO: `_seo_ready()` проверяет `data/metrics/s??.json` — на момент задачи
S-блок (`src/compute/block4.py`) не реализован, файлов нет ни для одного
клиента, поэтому money_frame всегда добавляет `kind="caveat"` строку
`"SEO не учтён: источник не готов"` (ровно эта строка, не перефразирована) —
рамка не выглядит молча полной. Отдельно пишется `findings_registry.csv`
skeleton (`_CARD_FIELDS` — колонки единой карточки каталога v2 §12):
деньги/уверенность/сегмент/источник/денежная категория заполнены из уже
посчитанного, нарративные колонки (Статус/Доказательство/Рекомендуемое
действие/Как измерить/Что нельзя заключить) оставлены пустыми для слоя
analyze. LLM-приоритизация не реализована (вне скоупа задачи).

Тесты `tests/test_money_frame.py` — плоские величины (A04/A10), CPA-excess
(A05), вложенный A18, раздельные подытоги категорий (без грандтотала),
сценарий C06 с A04 и без (amount_rub=None), LOW-сегмент C06 не становится
сценарием, SEO-оговорка (отсутствует/есть непустой s01.json/только
unavailable-статус), confidence≤cap, findings_registry skeleton (заголовок,
пустые нарративные колонки, маркер сценария, "в ₽ не оценить"), пустой
прогон без падения, подключение к `common.BLOCK_MODULE_NAMES` и к
`dispatch_blocks` по умолчанию. `pytest tests/test_money_frame.py` —
**19 passed**. Регрессия: `pytest tests/test_compute_common.py
tests/test_block1.py tests/test_block3.py tests/test_money_frame.py` —
**120 passed**, 0 failed (venv `marketing-diagnostics/.venv` — системный
Python без `scipy`/`duckdb` не годится для этого модуля).

---

**5J** CHECKPOINT — 2026-07-29. Интеграционная регрессия ядра (D/A/T/C/money)
без SEO, только запуск существующего кода — производственный код не менялся.

1. `pytest tests/test_block0.py tests/test_block1.py tests/test_block2.py
   tests/test_block3.py tests/test_money_frame.py tests/test_degradation.py`
   — **151 passed**, 0 failed.
2. Синтетическая фикстура 2400 визитов (ad-hoc скрипт вне репозитория,
   `clients/_synth5j/` создавался и удалялся временно — каталог `clients/`
   целиком в `.gitignore`, в репозитории не остался): `dispatch_blocks` ->
   `{block0: ok, block1: ok, block2: ok, block3: ok, money_frame: ok}`,
   `block_errors` пуст. D01 переотработка детектируется корректно на этом
   объёме (form_submit achievements_per_visit=3.03, form_open=1.95,
   `overtrigger=true` у обоих) — прогон подтверждает, что бизнес-логика (не
   только dispatch-каркас) не падает и не рассинхронизируется на объёме
   >=2000 визитов.
3. **Pognali regression** (реальные канонические данные
   `clients/pognali.rent/`, 34227 визитов, окно 2025-04-07..2026-06-30,
   `python run.py pognali.rent --stage compute`, 80/100 проверок runnable):

   | ожидалось (отчёт v2) | получено | check_id | вероятный слой ошибки |
   |---|---|---|---|
   | переотработка ×2.5–3.9 | **MATCH**: form_submit achievements_per_visit=3.099, form_open=2.787 (оба `overtrigger=true`) | D01 | — совпадает, регрессия зелёная |
   | воронка 20.9% | не воспроизведено; ближайший веб-аналоговый показатель — C06 `open_to_submit_rate`=0.5528 (form_open→submit), а не 20.9% | C06 (legacy 1.1) | вероятно, 20.9% в отчёте v2 — CRM-метрика (лид→сделка), не веб-метрика; `clients/pognali.rent/inputs/crm_export.csv` — **0 строк** (только заголовки), `sources.crm_csv.enabled: false` в config.yaml. Разрыв в extract/данных клиента, не в compute |
   | реклама 17.2% | не воспроизведено; веб-прокси (visit-level macro-goal конверсия по source_group=ad) = 2.37%, per-user = 2.96% | A01/A05 (нет прямого check_id на этот показатель) | та же причина — вероятно top-of-funnel показатель либо CRM-based ad-attributed rate; без CRM не проверить |
   | полный CPA ~3440 ₽ | не воспроизведено; веб-прокси: `cost_rub` сумма Директа = 492 661.4 ₽ (сверено по 3 независимым канонич. таблицам — campaigns/geo/placements совпадают); / macro-goal ad-конверсии (456) = 1080 ₽; / `conversions_all` из отчётов Директа (2126) = 232 ₽ | A05 (unavailable: macro_goals не настроен в config.yaml) + M (money_frame) | оба веб-прокси на порядок ниже 3440 ₽ — согласуется с гипотезой "полный CPA" = cost/deals (CRM), а не cost/clicks-или-форм; A05 к тому же сейчас `unavailable` (macro_goals пуст в client config), что само по себе отдельный, второй разрыв |

   Итог: единственная числовая проверка с чистым воспроизведением — переотработка
   (D01). Три остальных упираются в один и тот же корень — `crm_export.csv`
   пуст/отключён — это не подгонялось и не чинилось (вне `allowed_files`
   задачи; правка требует `sources.crm_csv.enabled: true` + реальный экспорт
   в `clients/pognali.rent/inputs/crm_export.csv`, отдельная задача).
4. Direct-профиль (та же синтетическая фикстура из п.2, но `sources.direct`
   не объявлен в манифесте): все 26 A-проверок уходят в degradation (0
   runnable), при этом D (7 runnable), T (7 runnable), C (17 runnable) не
   затронуты — "допустимые агрегаты считаются, недоступные проверки явно
   уходят в degradation" подтверждено. То же самое видно и на реальном
   Pognali-прогоне частично: A01/A02/A03 (`campaign_strategies` не в
   манифесте) — skipped с явной причиной, остальные A-проверки (costs+visits
   есть) — ok.

---

**6A** DONE — 2026-07-29. Детерминированная оболочка слоя analyze, без вызова
API Anthropic. `src/analyze/schemas.py` (новый файл) — типизированная
карточка находки `Finding` (поля по единой карточке каталога v2 §12),
`validate_finding`/`validate_findings_batch` (структурные проверки, ничего не
бросают — возвращают список нарушений): формат/регистрация check_id (новый
реестр D/A/T/C/S против `known_check_ids(methodology)`), `significant=false`
запрещено, confidence не выше `confidence_cap` (кроме `client-HIGH`, который
потолку источника не подчиняется), `money_category` — ровно одна из 4
категорий каталога v2 (правило 15, заданы отдельно от
`src.compute.money_frame.MONEY_CATEGORIES` по тому же первоисточнику — не
импортируются, чтобы не тянуть duckdb-зависимости compute в лёгкий
schemas.py), `money_category`+`money_not_assessable` одновременно и
`money_amount_rub` без категории — обе комбинации запрещены,
`MAX_FINDINGS_PER_RUN=12` — лимит на пакет.

`src/analyze/draft_findings.py`: `build_input_pack()` собирает всё, что уйдёт
модели, — `data/metrics/*.json` (кроме `degradation_report`/`metrics_summary`),
`inputs/*.yaml`, полный `degradation_report` (runnable/skipped/checks/counts),
контекст клиента (имя/ниша/гео/бренд-термины/окно анализа),
`known_check_ids`, оба потолка уверенности явно (`confidence_ceilings`:
`sample_size_rule` — параметры `min_sample_visits`/`significance_alpha` из
defaults.yaml, уже применённые к полю `confidence` внутри `metrics`, и
`source_cap_by_check` — per-check `confidence_cap` из degradation) и
`money_categories`. Пакет — только примитивы/списки/словари, целиком проходит
`json.dumps`/`json.loads` без потерь (тест round-trip). `build_system_prompt()`
— текст промта с 8 запретами (числа только из пакета; significant=false;
п.п. ≠ %; денежные категории не смешивать; максимум 12 находок; без
обвинений конкретных людей; confidence не выше меньшего из двух потолков;
check_id только из known_check_ids) — сам вызов API не подключён, это задача
другой сессии. `draft()` собирает пакет + промт и пишет их одним аудиторским
артефактом `_analyze_input_pack.json` в `findings/draft/` (имя с `_`, чтобы не
перепутать с настоящей находкой-карточкой — генерация находок LLM появится
вместе с подключением вызова модели).

Тесты `tests/test_analyze_draft_findings.py` (новый файл, 17 тестов): сборка
всех секций пакета, пустые источники не роняют сборку, JSON round-trip,
`draft()` пишет ровно один артефакт с ожидаемым именем, промт содержит все 8
запретов текстом, валидная находка без нарушений, каждое нарушение
(`significant=false`, `confidence` выше `confidence_cap`, `client-HIGH`
обходит потолок источника, невалидная/смешанная денежная категория, сумма
без категории, неверный формат/незарегистрированный `check_id`, пустое
обязательное поле) детектируется отдельно, лимit пакета в 12 находок.
`pytest tests/test_analyze_draft_findings.py` — **17 passed**. Регрессия:
`pytest tests/test_analyze_draft_findings.py tests/test_money_frame.py
tests/test_compute_common.py tests/test_degradation.py
tests/test_methodology_goals_requires.py` — **67 passed**, 0 failed.

---

**6B** DONE — 2026-07-29. Подключён сам вызов модели в `src/analyze/
draft_findings.py` (единственное место в пайплайне, где это разрешено —
принцип 3 CLAUDE.md); `schemas.py` не менялся. `_call_llm()` — один
структурированный вызов (`output_config.format` c JSON Schema
`_findings_response_schema()`/`_finding_item_schema()`, зеркалящей поля
`Finding`; `enum` на `status`/`confidence`/`money_category` намеренно не
задан в самой схеме — смысловую проверку значений всё равно делает
`schemas.validate_finding`) с предсказуемым бюджетом `LLM_MAX_TOKENS=8000` и
`timeout=180s`/`max_retries=2` на транспортном уровне SDK (сеть/429/5xx) —
без повторной генерации после того, как получен валидный (парсящийся) ответ:
дальнейшая фильтрация находок целиком локальная. Модель — `DEFAULT_LLM_MODEL
="claude-opus-4-8"`, переопределяется через project env `ANALYZE_LLM_MODEL`;
ключ — через `anthropic.Anthropic()` по умолчанию (`ANTHROPIC_API_KEY`/
`ANTHROPIC_AUTH_TOKEN` из process env), явно НЕ из `clients/<name>/.env`
(принцип 6 — секреты клиента относятся к источникам данных, не к самому
пайплайну). `client` — необязательный keyword-параметр `_call_llm()`/
`draft()` для подмены в тестах.

`draft()`: всегда сначала пишет аудиторский артефакт `_analyze_input_pack.json`
(то же тело, что уходит модели), затем зовёт `_call_llm()` один раз, режет
ответ до `schemas.MAX_FINDINGS_PER_RUN` (лишние из ответа модели отбрасываются
без повторного вызова), для каждой находки собирает `schemas.Finding` через
`_finding_from_dict()` (только известные поля dataclass; форма не совпала ->
`None`, находка молча пропускается — глубокая проверка evidence снаружи
scope, задача 6C), прогоняет `schemas.validate_finding()` (regsitry
check_id + `confidence_cap` конкретного check_id из
`degradation_report.json`) и пишет прошедшие как
`findings/draft/F-<блок>-<nn>.yaml` — блок = первая буква `check_id`
(D/A/T/C/S), `nn` — сквозной счётчик внутри блока за этот прогон
(`_finding_filenames()`), YAML в порядке карточки каталога v2
(`schemas.finding_to_ordered_dict`). `requirements.txt`: добавлен
`anthropic>=0.69` (пакет отсутствовал в окружении).

Тесты: `tests/test_analyze_draft_findings.py` — сценарий "ровно один
артефакт" обновлён под мок-клиент (`_MockClient`) с пустым `findings: []` —
явно заданное ломающее изменение контракта `draft()` (теперь она зовёт
модель). Новый файл `tests/test_analyze_draft_findings_llm.py` (10 тестов,
мок API, сеть не трогаем): форма запроса `_call_llm` (model/max_tokens/
system/messages/output_config.format) и разбор ответа; `_resolve_llm_model`
— дефолт и переопределение через `ANALYZE_LLM_MODEL`; валидные находки из
разных блоков пишутся как `F-D-01.yaml`/`F-A-01.yaml`; несколько находок
одного блока нумеруются подряд (`F-A-01`, `F-A-02`); невалидная находка
(`significant=false`) отбрасывается без повторного `messages.create` (ровно
1 вызов); находки сверх `MAX_FINDINGS_PER_RUN` обрезаются, вызов модели
по-прежнему один. `pytest tests/test_analyze_draft_findings.py
tests/test_analyze_draft_findings_llm.py tests/test_money_frame.py
tests/test_compute_common.py tests/test_degradation.py
tests/test_methodology_goals_requires.py` — **74 passed**, 0 failed
(венв `.venv`; `anthropic` в тестах не импортируется — везде передан мок
`client`).

---

**6C** DONE — 2026-07-29. Новый модуль `src/analyze/validate_findings.py`
(`schemas.py` не менялся) — глубокая программная проверка evidence поверх
структурной `schemas.validate_finding`, чтобы не пропускать галлюцинации
LLM. `validate_finding_evidence(finding, *, metrics, inputs=None,
degradation_report=None)` возвращает список причин отказа (пустой список —
находка подтверждена), ничего сама не пишет:

- **source_file** — `data/metrics/<check_id в нижнем регистре>.json`
  (соглашение об имени то же, что у `common.write_metric_artifact` в
  `src/compute/block*.py`); отсутствие файла в пакете `metrics` -> отказ.
- **evidence/money_amount_rub** — каждое число, извлечённое из свободного
  текста (`extract_numbers()`: тысячи через пробел/неразрывный пробел,
  десятичные через `.`/`,`), обязано находиться среди числовых значений
  source_file (допуск на округление до рубля — `_ABS_TOL`/`_REL_TOL`; для
  чисел в диапазоне доли/процента добавлены эквиваленты ×100/÷100, диапазон
  ограничен намеренно, иначе ×100 денежной суммы даёт ложные совпадения).
  Не найдено -> отказ с текстом числа.
- **confidence <= compute-уровень** — третий потолок поверх
  `confidence_cap` источника (который уже проверяет `schemas.
  validate_finding`): confidence находки не выше наивысшего `confidence`
  среди строк source_file для этого check_id
  (`compute_confidence_for_check`); `client-HIGH` этот потолок обходит (как
  и потолок источника — факт не из compute).
- **assumptions** — числа в каждом элементе списка обязаны подтверждаться
  где-то во всём входном пакете (`metrics ∪ inputs ∪ degradation_report`,
  не только в source_file конкретной проверки — assumptions часто
  опираются на анкету клиента/соседние проверки).

`draft_findings.draft()` (`src/analyze/draft_findings.py`) подключил обе
проверки: для каждого элемента ответа модели сначала `schemas.
validate_finding`, затем `validate_findings_mod.validate_finding_evidence`;
объединённые причины при непустом списке -> находка идёт не в
`findings/draft/`, а в **`findings/draft/rejected/R-<nn>.yaml`**
(`{"reasons": [...], "finding": <исходный элемент ответа модели>}`) — это
ломающее изменение контракта `draft()` по сравнению с 6B (там невалидные
находки молча отбрасывались, не записываясь никуда). Ответы, не собравшиеся
в `schemas.Finding` (не хватает обязательных полей), тоже идут в
`rejected/` с причиной "не совпадают поля". Возвращаемый `draft()` список
`names` не включает файлы `rejected/` (только аудиторский артефакт + принятые
находки) — существующие тесты 6B, проверяющие `names`, не потребовали
изменения контракта возврата.

Тесты: новый `tests/test_analyze_validate_findings.py` (12 тестов) —
валидная находка без нарушений; выдуманное число в evidence; выдуманный
`money_amount_rub`; confidence выше compute-уровня отклонена, confidence
на уровне/ниже принята, `client-HIGH` обходит потолок; несуществующий
source_file (в т.ч. пустой пакет metrics целиком); неподтверждённое число
в assumptions отклонено, подтверждённое из `inputs` принято;
`extract_numbers()` — нормализация тысяч/десятичных разделителей.
`tests/test_analyze_draft_findings_llm.py` — 4 теста, ожидавшие находки в
`findings/draft/`, дополнены fixture `data/metrics/<check>.json`,
подтверждающим числа evidence (иначе новая проверка отклоняла бы их как
неподтверждённые) — явно заданное ломающее изменение контракта; сценарий
"невалидная находка отбрасывается" дополнен проверкой, что она попала в
`findings/draft/rejected/` с причиной `significant=false` в `reasons`.
`pytest tests/test_analyze_draft_findings.py
tests/test_analyze_draft_findings_llm.py tests/test_analyze_validate_findings.py
tests/test_money_frame.py tests/test_compute_common.py tests/test_degradation.py
tests/test_methodology_goals_requires.py` — **84 passed**, 1 failed
(`test_money_frame.py::test_dispatch_blocks_runs_money_frame_by_default` —
`ModuleNotFoundError: scipy`, окружение, `src/compute/block1.py`, вне
`allowed_files` этой задачи и не связано с изменениями 6C).

**6D** DONE — 2026-07-29. `run_analyze()` (`src/pipeline/orchestrator.py`)
подключён к `src.analyze.draft_findings.draft()` вместо заглушки: загружает
`config.yaml` и `config/methodology.yaml`, вызывает `draft()` без подмены
`client` (в проде — реальный `anthropic.Anthropic()`, см. докстринг
`draft_findings.py`) и логирует список записанных карточек находок.

Перед вызовом `draft()` `findings/draft/` перезаписывается целиком
(`shutil.rmtree` + `mkdir`) — файлы находок нумеруются заново внутри каждого
прогона (`F-<блок>-<nn>.yaml`), поэтому без очистки более многочисленный
предыдущий прогон мог бы оставить лишние файлы рядом с новыми; это и делает
повторный запуск `analyze` идемпотентным (принцип 2 — свой слой можно
перезаписывать целиком). `findings/approved/` стейдж не создаёт и не трогает —
гейт перед `report` (`approved_findings_present`/`report_gate_message`)
изменению не подвергался. После записи черновиков выводится инструкция
аналитику: проверить `findings/draft/`, вручную перенести утверждённые в
`findings/approved/`, затем повторить `--stage report`.

Тесты: новый `tests/test_orchestrator_analyze_gate.py` (5 тестов,
`draft_findings.draft` подменяется через monkeypatch — реальный LLM не
нужен) — `run_analyze` делегирует в `draft()`; `report` остаётся под гейтом
после `analyze` (approved пуст); лог содержит инструкцию о ручной проверке
с путями `findings/draft`/`findings/approved` и командой `--stage report`;
находки в `rejected/` не считаются approved (гейт `report` по-прежнему
закрыт); повторный запуск `run_analyze` с меньшим числом находок не
оставляет файлы предыдущего прогона.
`pytest tests/test_orchestrator_analyze_gate.py tests/test_analyze_draft_findings.py
tests/test_analyze_draft_findings_llm.py tests/test_analyze_validate_findings.py
tests/test_orchestrator_error_logging.py tests/test_smoke.py` — **59 passed**.
Blocker: нет.

**AUDIT-report-wiring** — аудит, без правок кода — 2026-07-29.
`run_report` (`src/pipeline/orchestrator.py:567-574`) НЕ вызывает
`build_report.build()`. Тело функции — только проверка гейта
(`approved_findings_present`), `mkdir` для `report/` и одна строка лога:
`"report: заглушка — src/report/build_report.py не реализован."`
(orchestrator.py:573). Эта строка лога фактически неверна — `build_report.py`
полностью реализован (задачи 7A–7D: `build()`, вердикт, план действий,
находки, приложение, CSV-таблицы, повестка звонка — все на месте,
`src/report/build_report.py:694-735`), но `run_report` его не импортирует и
не вызывает.

Проверено запуском: временная находка в
`clients/pognali.rent/findings/approved/F-AUDIT-01.yaml` (гейт открыт) +
`python run.py pognali.rent --stage report` → exit code 0, в лог выведена
только строка-заглушка, `report/diagnostic_report.md` не создан,
`report/` остался с одним `.gitkeep`. Тот же результат уже был
зафиксирован раньше в этот же день в
`clients/pognali.rent/logs/report_20260729_210140.log` и
`report_20260729_210201.log` (идентичная строка-заглушка) — судя по всему,
кто-то уже гонял этот же сценарий ранее сегодня и убрал фикстуру, не
починив вызов. Фикстура и логи прогона удалены после проверки.

Blocker: `run_report` не подключён к `build_report.build()` — `--stage report`
и `--stage all` завершаются «успешно» (код 0, лог без ошибки) на клиенте с
непустым `findings/approved/`, но `diagnostic_report.md` не создаётся.
Требуется отдельная задача на исправление (эта задача — только фиксация факта).

**AUDIT-wordstat-canonical** — аудит, без правок кода — 2026-07-29.
Подтверждено: путь `raw wordstat -> canonical -> compute` обрывается на шаге
transform. `build_wordstat()` в `src/transform/build_canonical.py` не
существует (grep по файлу даёт ровно 2 упоминания "wordstat" — оба в
докстринге модуля, строки 34-35: "wordstat.parquet вне контракта этой задачи
(схема не задана) — сырьё data/raw/wordstat/ пока не трансформируется").
`data/canonical/wordstat.parquet` нигде не пишется.

Сырьё реально существует и не пусто: `src/extract/wordstat.py` пишет
`data/raw/wordstat/wordstat_weekly.parquet` и `wordstat_core_queries.parquet`
(подтверждено на диске: `clients/pognali.rent/data/raw/wordstat/`), объявляет
`CANONICAL_TABLES = ["wordstat"]` в манифесте — но это имя будущей
canonical-таблицы, для которой transform не реализован (сам модуль это
явно фиксирует в докстринге, строки 104-106).

`src/compute/block4_seo.py` целиком согласован с этим разрывом (свой
докстринг, "Структурные разрывы задачи 5bA", п.1, строки 46-59, и "5bC",
п.13, строки 201-209) — не расхождение, а задокументированное состояние:
- `_run_s07` (requires=[wordstat, seo_queries], строки 1141-1163) и `_run_s26`
  (requires=[wordstat, seo_queries], строки 2625-2647) проверяют
  `"wordstat" in canonical and _table_nonempty(canonical["wordstat"])` —
  условие всегда False, т.к. ключ "wordstat" никогда не появляется в
  словаре `canonical` (нет файла на диске) — обе проверки ВСЕГДА пишут
  `status: "unavailable"`, независимо от `runnable_ids`/`confidence_cap`
  (тот же прецедент, что A07/A16/A25 в `block1.py`).
- `_run_s06` (optional=[wordstat], строки 1071-1137) остаётся runnable по
  одному `seo_queries` — но каждая строка несёт `wordstat_available: false`,
  а финальный `finding: "seasonality_reconciliation"` жёстко капается на
  `confidence: LOW` с `verdict: "cannot_determine_without_wordstat"`
  (каталог §11, "Что Claude не должен утверждать", п.9).

Недостающий шаг: `build_wordstat()` в `src/transform/build_canonical.py` —
читает `wordstat_weekly.parquet`/`wordstat_core_queries.parquet` из
`data/raw/wordstat/`, пишет `data/canonical/wordstat.parquet` по
задокументированной схеме, регистрирует таблицу в `build()`/manifest. Без
этого шага S07/S26 структурно недоступны навсегда, а S06 не может подняться
выше LOW — это ограничение transform-слоя, не compute (`block4_seo.py` в
`allowed_files` этой задачи не входил и не менялся).

---

**AUDIT-pre-existing-failures** — аудит, без правок кода — 2026-07-29.
`pytest tests/ -q --continue-on-collection-errors` на текущем дереве:
**14 failed, 714 passed, 2 collection errors**. Задача 5A (строка ~698
этого файла) уже фиксировала через `git stash`-сравнение ровно **13**
пре-существующих провалов на момент 2026-07-28 (до 5A). Из 14 текущих
провалов + 2 ошибок сборки 13 совпадают с тем списком 1:1; расхождение —
`scipy` (см. ниже).

| test_id | файл | вероятная причина | задача-источник |
|---|---|---|---|
| `test_query_report_dimensions` | `tests/test_direct_2b_patch.py` | `cost_normalized` == `None` вместо ожидаемого рубля — `build_direct_queries` для direct_queries по задокументированному правилу (см. `src/compute/block1.py` докстринг, `build_canonical.py:1131`) намеренно оставляет `cost_normalized` пустым до отдельного апдейта Q01 для direct_queries/geo; тест написан на будущий (ещё не реализованный) контракт | Q01-apply-to-direct-queries-geo (не создана) |
| `test_geo_report_schema` | `tests/test_direct_2b_patch.py` | то же самое (`cost_normalized` для `build_direct_geo`), тот же незакрытый Q01-гэп | Q01-apply-to-direct-queries-geo (не создана) |
| `test_metrika_logs_negotiation_isolates_unsupported_fields` | `tests/test_extract_smoke.py` | `dropped_fields` пуст вместо `{'ym:s:lastSignhasGCLID'}` — поле теперь в статическом списке заведомо-пустых/предфильтруемых полей (`src/extract/metrika_logs.py:48`) и до вызова `evaluate` в тело запроса не попадает, поэтому симулируемый негативный ответ API в тесте никогда не срабатывает; тест и код разошлись после того, как поле перевели в статический список | не установлена (последнее касание файла — `d047032 save before reset`) |
| `test_metrika_logs_backfill_preserves_old_files` | `tests/test_extract_smoke.py` | тот же корень: `entry["dropped_fields"]` пуст по той же причине | не установлена (тот же коммит) |
| `test_gsc_manual_validates_and_writes_same_contract` | `tests/test_extract_smoke.py` | `SourceUnavailable: нет папок YYYY-MM` — тестовый фикстур-хелпер `_write_gsc_manual` пишет файл не в том расположении/формате, который сейчас ожидает `gsc_manual.py` (нужны подпапки `YYYY-MM`); контракт ручной выгрузки разошёлся с тестом | не установлена |
| `test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat` | `tests/test_extract_smoke.py` | то же самое | не установлена |
| `test_gsc_manual_missing_device_column_flags_month` | `tests/test_extract_smoke.py` | то же самое | не установлена |
| `test_webmaster_manual_aggregates_to_popular_contract` | `tests/test_extract_smoke.py` | `SourceUnavailable: файл выгрузки не найден` — аналогичное расхождение фикстуры `_write_wm_manual` с текущим контрактом `webmaster_manual.py` (`_export_path`) | не установлена |
| `test_webmaster_manual_records_no_page_device_breakdown` | `tests/test_extract_smoke.py` | то же самое | не установлена |
| `test_wordstat_queue_cycle_writes_raw_and_manifest` | `tests/test_extract_smoke.py` | `SourceUnavailable: не задан sources.wordstat.folder_id` — задача **wordstat-folder-id-config** (2026-07-22, см. выше) сделала `folder_id` обязательным с fail-fast в `_folder_id()`; фикстура `CONFIG_WS` в этом тестовом файле с тех пор не обновлена и `folder_id` не передаёт | wordstat-folder-id-config (2026-07-22) |
| `test_wordstat_dead_token_raises` | `tests/test_extract_smoke.py` | тот же самый fail-fast до HTTP-вызова из-за отсутствующего `folder_id` в фикстуре — тест не может дойти до проверки dead-token сценария | wordstat-folder-id-config (2026-07-22) |
| `test_lookback_visits_excluded_from_build_visits_aggregation` | `tests/test_metrika_logs_lookback.py` | `assert 2 == 1` — `build_visits` агрегирует визит из `lookback/`-подкаталога вместе с обычным, хотя тест ожидает исключения lookback-файлов из агрегации по `visit_id` | не установлена |
| `test_build_ad_texts_inline_logic_keeps_raw_intact_and_splits_correctly` | `tests/test_transform_direct_normalize.py` | `FileNotFoundError: data/canonical/ad_texts.json` — тест (и его докстринг, см. `tests/test_transform_direct_normalize.py:1-13`) ожидает, что инлайн-код `build()` пишет `canonical/ad_texts.json` + `ad_texts_archived.json`; реальный код (`build_canonical.py:1983-1997`) пишет `ad_texts.parquet`/`ad_texts_archived.parquet` — расхождение JSON vs parquet между докстрингом задачи 4X-direct-cleanup и фактической реализацией | 4X-direct-cleanup |
| — (не тест, ошибка сборки) `ERROR tests/test_block1.py` | `src/compute/block1.py:154` | `ModuleNotFoundError: No module named 'scipy'` — `scipy>=1.11` объявлен в `requirements.txt` с исходного чекпоинта (`ebb44e8`), но не установлен в текущем venv | окружение, не код |
| — (не тест, ошибка сборки) `ERROR tests/test_block3.py` | `src/compute/block3.py` | то же самое — `from scipy import stats` | окружение, не код |
| `test_dispatch_blocks_runs_money_frame_by_default` | `tests/test_money_frame.py` | `ModuleNotFoundError: No module named 'scipy'` — `dispatch_blocks` импортирует `block1`, который падает на импорте `scipy`; это НЕ входит в зафиксированные 13 (задача 5A), появляется только в окружении без установленного `scipy` | окружение, не код |

Итог: **13 из 14** провалов совпадают 1:1 с уже зафиксированным в задаче
5A списком (`test_direct_2b_patch.py` ×2, `test_extract_smoke.py` ×9,
`test_metrika_logs_lookback.py` ×1, `test_transform_direct_normalize.py`
×1) — эти 13 подтверждаются как «известно и приемлемо» (доп. код этой
задачей не менялся, поведение не изменилось со времени 5A). 14-й провал
(`test_money_frame.py`) и 2 ошибки сборки (`test_block1.py`,
`test_block3.py`) — новый пункт, но root cause один и тот же: отсутствие
`scipy` в этом venv, а не регрессия кода; исчезают после `pip install
scipy` (или `pip install -r requirements.txt`).

«Требует задачи» (не «известно и приемлемо», т.к. причина — расхождение
теста и кода, не задокументированный гэп): негоциация metrika_logs
dropped_fields (2 теста), контракт ручных выгрузок gsc/webmaster (5
тестов), lookback-агрегация build_visits (1 тест), ad_texts.json vs
.parquet (1 тест) — итого 9 тестов, где «источник» не установлен из
git-истории и нужен отдельный аудит/задача на каждый файл. `Q01`-гэп по
cost_normalized (2 теста) и wordstat folder_id (2 теста) — уже
задокументированные, ожидаемые гэпы с известным следующим шагом.

---

**AUDIT-live-verification-status** — аудит, без правок кода — 2026-07-29.
Сведены все пункты, помеченные `CODE DONE, live run pending`/mocked-only, и
проверено, закрыты ли они реальным прогоном на pognali.rent с реальными
ключами с тех пор. Источники: `docs/implementation_status.md` (весь файл),
`clients/pognali.rent/.env` (проверено только наличие/непустота переменных,
значения не читались), `clients/pognali.rent/data/raw/manifest.json`,
`clients/pognali.rent/config.yaml`, все `clients/pognali.rent/logs/extract_*.log`
и `compute_*.log`. Самый свежий `extract_*` лог в клиенте —
`extract_20260722_202250.log` (2026-07-22 20:22–20:28); после этой даты
живых `extract`-прогонов не было (последующие задачи 2026-07-23/29 —
диагностика/аудит без новых вызовов внешних API). `.env` содержит непустые
значения для всех шести ключей (`METRIKA_TOKEN`, `DIRECT_TOKEN`,
`GSC_CREDENTIALS_PATH`, `WEBMASTER_TOKEN`, `WORDSTAT_API_KEY`,
`CRUX_API_KEY`).

| Пункт | Было в доке | Найдено | Вердикт |
|---|---|---|---|
| Direct Strategy/BiddingStrategy shape | 2A-direct-strategy: `CODE DONE, live run pending` — «реальный прогон... не выполнен» | `data/raw/manifest.json` → `sources.direct.strategy_field_present=true`, `strategy_field_samples` — 3 реальных объекта (`BiddingStrategyType`: `WB_MAXIMUM_CLICKS`, `HIGHEST_POSITION` ×2), `fetched_at=2026-07-22T17:23:25Z`, тот же прогон, что и `extract_20260722_202250.log`. Фикс из **2A-direct-strategy-fix** (запрос `TextCampaignFieldNames: ["BiddingStrategy"]`) отработал на боевом аккаунте без error 8000. `statistics_field_scope` по-прежнему `"unknown"` — это НЕ проверялось (нужен отдельный экспериментальный вызов, как и указано в исходной записи) и остаётся живым blocker'ом только в этой части. | **Подтверждено живым прогоном** (форма `BiddingStrategy`); `statistics_field_scope` — по-прежнему открыт |
| CrUX real key | 3C-patch: `CODE DONE, live run pending` — «реального `CRUX_API_KEY` в этой сессии нет» | `.env` содержит непустой `CRUX_API_KEY` (mtime 2026-07-22 18:37); `clients/pognali.rent/data/raw/crux/crux.json` создан 2026-07-22 20:23; `manifest.json` → `sources.crux.cwv_field_data_available=true`, `field_data_available_by_target` — 3 из 4 URL с данными, без `error`; лог `extract_20260722_202250.log:95-102` — «crux: готово — cwv_field_data_available=True, записей 4». | **Подтверждено живым прогоном** |
| Wordstat live extraction после dynamics-фикса (WS-2, cloud v2) | wordstat-folder-id-config: реальный `folder_id` не был получен от оператора в той сессии («ответ не получен») | Тот же прогон: `manifest.json` → `sources.wordstat.api_version_used="cloud_search_v2"`, `folder_id="b1ggocts4bcj79ds932l"`, `core_query_rows=39`, `wordstat_calls_made=42`; лог — «wordstat: готово — 39 фраз, 2535 недельных точек, вызовов API: 42», без ошибок/квоты. **Расхождение:** в записи `wordstat-folder-id-config` этого файла указан другой `folder_id` — `ajebnohb0odjms4dgq25` (полученный «от оператора тем же днём») — он НЕ совпадает ни с `clients/pognali.rent/config.yaml` (`folder_id: "b1ggocts4bcj79ds932l"`), ни с манифестом живого прогона. Т.е. либо запись в доке зафиксировала неверное/промежуточное значение, либо `folder_id` был исправлен ещё раз позже без отдельной записи в этом файле — источник расхождения не установлен этим аудитом (только чтение, `config.yaml`/`wordstat.py` вне `allowed_files` этой задачи). | **Подтверждено живым прогоном** (extraction работает), но текстовое значение `folder_id` в записи `wordstat-folder-id-config` этого файла — стale/неверное, требует отдельной проверки у оператора |
| Metrika regionArea негоциация | 2A-patch: уже описано как подтверждённое «после боевого прогона» (2026-07-22) | `manifest.json` → `sources.metrika_logs.region_field="ym:s:regionArea"`, `region_field_verified=true`, `region_field_error=None`, `fetched_at=2026-07-21T22:43:10Z` — совпадает с тем, что уже зафиксировано в записи 2A-patch. | Уже верно задокументировано, обновление не требуется |
| Direct campaigns/geo/queries непустые после TSV-фикса | direct-tsv-report-header-fix: уже помечено `DONE`, с конкретными non-empty счётчиками (1377/1377, 21681/21681, 15253/15253) по реальному сырью клиента | raw-выгрузка того же дня (`extract_20260722_202250.log`): campaigns 1407 строк, queries 15265 строк, geo (через CUSTOM_REPORT) — все 15 месяцев без ошибок. Разница 1407−1377=30 и 15265−15253=12 соответствует ровно количеству отброшенных header/footer-строк (15 файлов×2 и 6 файлов×2) — согласуется с фиксом. | Уже верно задокументировано, обновление не требуется |

**Итог:** 3 из 5 пунктов (`Direct BiddingStrategy shape`, `CrUX`, `Wordstat
dynamics live`) фактически подтверждены реальным прогоном 2026-07-22, но
статус в таблице/тексте этого файла на момент начала этого аудита ещё
показывал `CODE DONE, live run pending`/blocker — обновлено выше (строки
статус-таблицы 2B-patch-2, 3C-patch) и в этой записи. 2 пункта (`regionArea`,
`Direct TSV non-empty`) уже были верно задокументированы как подтверждённые.
Открытые вопросы, не закрытые этим аудитом: (a) `statistics_field_scope`
Direct остаётся `"unknown"`; (b) расхождение `folder_id` Wordstat в записи
`wordstat-folder-id-config` vs фактический `config.yaml`/manifest.

---

**AUDIT-spec-vs-code-drift** — аудит, без правок кода/спеки — 2026-07-29.
Построчная сверка `data-export-spec-v2.md` (единственный формальный контракт
выгрузки) с фактическим `src/extract/*.py` и `src/transform/build_canonical.py`.
Ни спека, ни код не редактировались. `legacy_id`/`config/methodology.yaml` вне
скоупа (не читались специально, только источники a/b по CLAUDE.md §5).

| # | Раздел спеки | Спека говорит | Код фактически делает | Вердикт |
|---|---|---|---|---|
| 1 | §A, `ym:s:screenResolution`/`physicalScreenResolution` | Поле для C21 | `metrika_logs.py`: такого поля не существует (подтверждено API), используются `ym:s:screenWidth`+`ym:s:screenHeight`, собранные в `screen_resolution` в transform | **Код прав, спека устарела** — имена полей в §A не обновлены (в отличие от других полей раздела) |
| 2 | §A, `<clickID полей yclid/gclid если есть>` | Нужен для связки визита с кампанией Директа | `metrika_logs.py`: `yclid` не запрашивается вовсе; `gclid`/`hasGCLID` запрошены и убраны насовсем (100% пусто, нет Google Ads трафика); связка идёт только через `ym:s:lastSignDirectClickOrder` | **Код прав по факту** (эмпирически пусто), но спека всё ещё перечисляет оба поля как желаемые без пометки — стоит уточнить в спеке |
| 3 | §A, `ym:s:ipAddress` | «снят с рассмотрения по решению клиента... не только из-за приватности» (ред. 2, v2-формулировка) | `metrika_logs.py` комментарий: «по-прежнему не запрашивается (приватность важнее, см. **data-export-spec-v1.md**, раздел A)» — ссылается на v1, не v2, и даёт только причину приватности | Расхождение в обосновании (код цитирует устаревшую версию спеки), эффект (поле не запрашивается) совпадает — **синхронизировать комментарий с v2** |
| 4 | §B, goals list: «дата создания/последнего изменения» | Нужно для D02/D03 | `build_canonical.build_goals()`: `created_at`/`updated_at` всегда `None` — «в реальной выгрузке счётчика этих полей нет вовсе (подтверждено на фактическом goals_list.json Pognali)», зафиксировано в `flags["goals_missing_fields"]` | **Код прав, спека устарела** — полей не существует в API; расхождение хотя бы честно всплывает в canonical-манифесте |
| 5 | §C, `campaigns.get`: `StatisticsStartDate, StatisticsEndDate` | Поля таблицы для D08/A01-A03 | `direct.py` `CAMPAIGNS_FIELD_NAMES_ENUM` (дословно взят из текста error 8000 боевого прогона) вообще не содержит таких имён — их нет в валидном enum `campaigns.get` | **Код прав, спека устарела** — поля, видимо, унаследованы из v1/UI-терминологии и не существуют в JSON API v5 |
| 6 | §C, `SEARCH_QUERY_PERFORMANCE_REPORT`: окно не ограничено (весь период 12 мес, как и остальные отчёты) | Подразумевается тем же 12-месячным окном, что и остальные проверки блока 1 | `direct.py` `REPORT_WINDOW_LIMIT_DAYS["SEARCH_QUERY_PERFORMANCE_REPORT"] = 180` — окно обрезается до 180 дней от `today` (API error 4001 на более ранних датах), caveat пишется в `manifest.query_window_caveat`, но **в спеке это ограничение нигде не упомянуто** | **Код прав (эмпирическое ограничение API), спека неполна** — A09-A11/A18 не могут получить полные 12 мес query-уровня независимо от `data_window`, это стоит отразить в §C |
| 7 | §C, «Отчёт по площадкам»: `Placement/AppId, CampaignId, Cost, Clicks, конверсии` | `AppId` как поле | `direct.py` `PLACEMENT_FIELDS = [Placement, AdNetworkType, CampaignId, Cost, Clicks, Conversions]` — нет `AppId`, вместо него `AdNetworkType` (различает сеть/поиск), без комментария о причине замены | Не установлено, кто прав — расхождение **не задокументировано в коде** (в отличие от других полевых замен в этом же файле), нужна проверка на живом аккаунте |
| 8 | §C, «Тексты объявлений + расширения»: `... извлечения (цена/акция/наличие)` | Ожидаются extensions типа price/promotion/availability | `direct.py` `_fetch_ad_texts`: `adextensions.get` запрашивает только `CalloutFieldNames: ["CalloutText"]` (текстовые уточнения) — никаких Price/Promotion/Availability-полей не запрашивается и это нигде не отмечено как ограничение | **Код отклонился от спеки без документирования** — A21-A24 не получают данные о цене/акции/наличии через extensions, хотя спека их требует |
| 9 | §C, «Тексты объявлений»: `... дата последнего изменения` | Нужно поле последнего изменения объявления | `direct.py` `_fetch_ad_texts`: `FieldNames=[Id, CampaignId, AdGroupId, Type, State, Status]`, `TextAdFieldNames=[Title, Title2, Text, Href, DisplayUrlPath]` — поля даты изменения нет вовсе, и нет notes/manifest-флага об этом (в отличие от `Strategy`/`Statistics`, которые получили открытые вопросы) | **Код отклонился от спеки без документирования** — тихий гэп, не как остальные (задокументированные) гэпы этого файла |
| 10 | §C, «Товарный фид»: `offer_id, price, availability, url, дата синхронизации` | Ожидается хотя бы метаданные + путь получения построчных офферов | Докстринг модуля (строка 42-43) утверждает `feeds.get` вызывается («если фида нет — файл не создаётся»), но фактическая `_fetch_feed()` **никогда не вызывает `feeds.get`** — сразу возвращает `[]` с note «требует Ids, список фидов клиента не может быть получен без него» | **Внутреннее противоречие кода** (докстринг модуля обещает то, что реализация не делает) + расхождение со спекой: A25 сейчас не проверяем через этот пайплайн вообще, `feed_used` всегда `False` |
| 11 | §C, ред. 3 «Открытый gap»: `direct_placements` использует старое поле `cost_normalized` в валютном смысле, «требует отдельного патча» | Утверждает расхождение ещё не устранено | `build_direct_placements()` уже пишет `cost_raw`/`cost_rub`/`cost_normalized=None`/`vat_basis_applied=False` — тот же контракт, что у campaigns/queries/geo | **Спека устарела** — gap уже закрыт в коде, ред. 3 не актуализирована |
| 12 | §C, «Ключевые фразы + типы соответствия»: `KeywordId, Phrase, MatchType` | Подразумевает `MatchType` как поле API | `direct.py` `_fetch_keywords`: `keywords.get` не отдаёт `MatchType` полем вовсе — тип соответствия выводится эвристически по операторам в самой фразе (`_keyword_match_type`), задокументировано в коде, но не в спеке | **Код прав по факту**, спека вводит в заблуждение, представляя `MatchType` как прямое поле API |
| 13 | §D, «Популярные запросы... query, page, impressions, clicks, position, ctr, demand» | `page` — обычное поле выгрузки | `webmaster_api.py`: `_fetch_popular` не передаёт никакого page-параметра; докстринг модуля прямо говорит: «отчёт... отдаёт только query-уровень... БЕЗ разбивки по page/device — это ограничение метода» | **Код прав (структурное ограничение API), спека устарела** — §D перечисляет `page` без оговорки для API-режима |
| 14 | §D, то же (внутренняя сверка) | `webmaster_api.py` докстринг утверждает: «То же ограничение зафиксировано в `webmaster_manual.py`» | `webmaster_manual.py` фактически пишет `has_page_column: True`, `page_device_breakdown: True` — ручной формат (`Query|Url|...`) **умеет** page, в отличие от API | **Внутреннее противоречие двух экстракторов одного источника** — докстринг `webmaster_api.py` неверно описывает `webmaster_manual.py` |
| 15 | §D, «demand» (ред. 2 объясняет поле подробно) | `demand` — часть обычной выгрузки популярных запросов | `webmaster_api.py` `QUERY_INDICATORS = [TOTAL_SHOWS, TOTAL_CLICKS, AVG_SHOW_POSITION, AVG_CLICK_POSITION]` — `DEMAND` не запрашивается вовсе в API-режиме; `build_seo_queries_webmaster` читает `indicators.get("DEMAND")`, который для API-режима всегда будет отсутствовать, без caveat об этом | **Код отклонился от спеки без документирования** (для `mode: api`); для `mode: manual` demand поддержан корректно |
| 16 | §F, Wordstat: «частотность по маске и сезонность (**помесячно** за 12 мес, если доступно)», «жёсткие rate limits — очередь с паузами» | Ожидается месячная гранулярность и специфичный quota-цикл с паузами | `wordstat.py` (WS-2, миграция на Cloud Search API v2): весь транспорт v1 (REST, Bearer) заменён на v2 (Api-Key); данные о сезонности — **недельная** (`PERIOD_WEEKLY`) `dynamics`, не месячная; докстринг явно говорит «Отдельного 503-цикла квоты... больше нет» — общий backoff `C.http_request`, не выделенный quota-цикл с паузами | **Код прав (v1 API отключён Яндексом безвозвратно, подтверждено поддержкой)**, но **§F спеки вообще не получил ред.-пометки** о миграции — в отличие от §A/§C/§D/§E/§G1, где реальные расхождения аккуратно зафиксированы «Ред. 2» — это единственный раздел спеки, полностью разошедшийся с кодом без единой пометки |
| 17 | §G1, ред. 2: «список обязан включать все URL с трафиком/расходом из C+D+E за период, покрытие проверяется, а не предполагается» | Полный union без пред-усечения по источнику | `site_crawl.build_url_priority_list()`: каждый источник (`top_spend`, `top_organic_gsc`, `top_organic_webmaster`, `keyword_match`) **предварительно** обрезается до `top_n_each_source=20` **до** объединения и до `max_urls`-усечения; caveat (`result["caveat"]`) фиксирует только финальное `max_urls`-усечение, не это предварительное | **Код отклонился от спеки без разрешения** — для сайтов с >20 URL трафика/расхода на источник объединённый список тихо не совпадает с полным union, и это не видно как caveat (только явное чтение `top_n_each_source` в коде это раскрывает) |
| 18 | §G1, ред. 2: «`robots_directive`... проверять на URL с заведомо известной директивой... `js_content_diff`... проверять на URL с заведомо JS-зависимым контентом... пустое значение чаще признак, что поле не заполняется» | Требуется валидация на known-example перед доверием к пустому значению | `site_crawl.py`: для `js_content_diff` есть эвристика-предупреждение (`attempted>0 and populated==0` → лог), но она глобальная, не «известный JS-URL»; для `robots_directive` **вообще нет** проверки/предупреждения ни на каком уровне | **Требование спеки не реализовано в коде** — обе проверки остаются полностью ручными (аналитик должен делать это сам, что противоречит духу «проверяется, а не предполагается») |

**Итог:** 18 расхождений. Код прав (спека устарела/неточна): #1, 4, 5, 6, 11,
12, 13, 16. Спека права, код тихо отклонился без документирования: #8, 9, 14
(внутреннее противоречие), 15, 17, 18. Не установлено, чья сторона верна
(нужна проверка на живом аккаунте): #7. Формальность без эффекта (комментарий
ссылается не на ту версию спеки): #3, 2 (частично). Ничего не исправлялось —
только сверка, как и требует `task_id AUDIT-spec-vs-code-drift`.

---

**AUDIT-lookback-aggregation-regression** — аудит, без правок кода — 2026-07-29.
Вопрос: `pytest tests/test_metrika_logs_lookback.py::test_lookback_visits_excluded_from_build_visits_aggregation`
падает (`assert 2 == 1`) — это регрессия фильтрации lookback-визитов в
`build()`, или тест не обновлён под контракт «filter-at-write»?

**Вердикт: тест устарел, регрессии в коде нет.**

Изолированный прогон (`pytest tests/test_metrika_logs_lookback.py -v`):
6 passed, 1 failed — падает только эта проверка. Traceback показывает, что
`assert len(df) == 1` (`tests/test_metrika_logs_lookback.py:289`) проверяет
**прямой возврат `bc.build_visits()`** (df до записи в parquet), а не
содержимое `visits.parquet`.

Текущий контракт `build_visits()` (докстринг, `src/transform/build_canonical.py:964-979`,
и явное решение по фильтрации в записи `4X-lookback-canonical-flag`,
`docs/implementation_status.md:55`): функция **намеренно возвращает
объединённый df** (основное окно + lookback, с флагом `is_lookback_only`) —
лукбэк нужен внутри для carry-forward (`resolve_traffic_source`,
T02/T03) и намеренно не фильтруется самой функцией «для тестируемости
эффекта». Фактическую фильтрацию `is_lookback_only == True` перед записью
`visits.parquet` выполняет `build()` (`src/transform/build_canonical.py:1906-1908`,
`report_visits_df = visits_df[visits_df["is_lookback_only"] == False]`) —
эта строка проверена, работает корректно, лукбэк-строки в parquet не
попадают.

`git log` подтверждает порядок событий: `tests/test_metrika_logs_lookback.py`
целиком создан в единственном коммите `d047032` («save before reset»,
2026-07-22 13:14:26) — **до** коммита `17ea03f` (2026-07-22 14:47:25),
который ввёл `is_lookback_only` и контракт filter-at-write. После
`17ea03f` файл `test_metrika_logs_lookback.py` больше не редактировался
(в `670d2e0` тоже не тронут) — тест написан под более старый расчёт
(«build_visits агрегирует лукбэк наружу = баг»,
названия теста — `..._excluded_from_build_visits_aggregation`), который
контракт `4X-lookback-canonical-flag` сознательно заменил на
filter-at-write.

Запись `4X-lookback-canonical-flag` (`docs/implementation_status.md:55`)
прямо называет тесты старого контракта как blocker и перечисляет, что
именно требует обновления — но это **только `tests/test_lookback_wiring_check.py`**
(два теста: `test_build_visits_does_not_see_lookback_subdir_rows`,
`test_force_lookback_backfill_does_not_change_existing_canonical_output`).
Их закрыла последующая задача `4X-lookback-canonical-flag-tests`
(`docs/implementation_status.md:56`). `tests/test_metrika_logs_lookback.py`
— другой файл, в объём той задачи не входил и был пропущен при переходе
на новый контракт.

**Тест-долг, не баг.** Ничего не исправлялось (задание — только диагноз).
Нужна отдельная задача с `tests/test_metrika_logs_lookback.py` в
`allowed_files`, чтобы обновить `test_lookback_visits_excluded_from_build_visits_aggregation`
под контракт filter-at-write (аналогично тому, как `4X-lookback-canonical-flag-tests`
уже сделала для `test_lookback_wiring_check.py`) — либо переименовать/
переписать проверку на `len(df[df["is_lookback_only"] == False]) == 1`,
либо перенести assertion на результат `build()` (записанный `visits.parquet`).

---

**FIX-lookback-test-contract** — 2026-07-30. Переписан
`test_lookback_visits_excluded_from_build_visits_aggregation`
(`tests/test_metrika_logs_lookback.py`) под контракт filter-at-write:
проверяет обе половины — (а) `bc.build_visits()` возвращает и main-, и
lookback-строки с корректным `is_lookback_only`; (б) `bc.build()` пишет в
`visits.parquet` только main-строки (читает реально записанный parquet, а
не промежуточный df). `build_canonical.py` не менялся. `pytest
tests/test_metrika_logs_lookback.py tests/test_lookback_wiring_check.py`
— 14 passed.

---

**AUDIT-manual-export-contract-drift** — аудит, без правок кода — 2026-07-29.
Вопрос: соответствует ли контракт `gsc_manual.py`/`webmaster_manual.py`
(пути, имена файлов, структура папок) `docs/gsc_export_instructions.md` —
или инструкция устарела и живой клиент получит `SourceUnavailable` при
точном следовании ей.

**Вердикт: инструкция и код синхронны; устарели тесты, не инструкция.**
На реальных данных pognali.rent оба ручных источника отрабатывают успешно.

1. `docs/gsc_export_instructions.md` описывает ровно то, что читает
   `gsc_manual.py`: `manual_export_dir/YYYY-MM/` с обязательными
   `Запросы.csv`+`Диаграмма.csv` (+`Страницы.csv`, кроме комбинированного
   contract 3A), опциональными `Устройства.csv`/`Страны.csv`/`Фильтры.csv`,
   и той же логикой определения комбинированного формата по заголовкам
   `column_map["page"]`/`column_map["device"]` в `Запросы.csv`
   (`gsc_manual.py:357-361` ↔ `gsc_export_instructions.md:57-63`). Расхождений
   не найдено.
2. Для Вебмастера отдельного файла-инструкции **нет** (`docs/` содержит
   только `gsc_export_instructions.md` и `implementation_status.md`) — не
   «устарела», а просто отсутствует; контракт `webmaster_manual.py`
   (один wide-файл `manual_export_file`, по умолчанию `webmaster_export.csv`,
   колонки `Query`/`Url` + `YYYY-MM_shows/_clicks/_position/_demand`)
   нигде не задокументирован для оператора вне докстринга модуля.
3. Реальные загруженные выгрузки клиента pognali.rent подтверждают контракт
   кода, не какой-то другой:
   - `clients/pognali.rent/data/raw/gsc/YYYY-MM/` — 16 папок (2025-04…2026-07),
     каждая содержит `Диаграмма.csv`+`Запросы.csv`+`Страницы.csv`+
     `Устройства.csv`+`Страны.csv`+`Фильтры.csv` (+`Вид в поиске.csv`, не
     из `_FILE_MAP`, игнорируется парсером без вреда). `config.yaml`:
     `sources.gsc.manual_export_dir: "data/raw/gsc"` — то же место, что и
     `out_dir` (комментарий в `gsc_manual.py:132-133` явно предусматривает
     это совпадение). `validation_report.json`: `accepted=6813`,
     `rejected=0`, все 16 месяцев обработаны, `source_mode=manual` — реальный
     прогон успешен, деградации до `SourceUnavailable` нет.
   - `clients/pognali.rent/data/raw/webmaster/webmaster_export.csv` — ровно
     то имя файла, что и дефолт `_export_path()`. `validation_report.json`:
     `accepted=421`, `rejected=0`, 18 месяцев, `has_page_column=true`,
     `has_demand_column=true` — тоже успешный прогон.
   - Данные о качестве (не о контракте): у GSC `incomplete_dimensions=true`
     для **всех** 16 месяцев (контракт 3A ни разу не достигнут в проде —
     клиент экспортирует раздельно, page/device везде `unknown`/`""`) и
     `clicks_diagram_vs_queries_mismatch` с отклонением 40–100% почти
     каждый месяц. Не баг парсера — это то, для чего существуют caveat'ы;
     находки S08-S10/S20 для pognali.rent должны оставаться MED/LOW, как и
     предписывает инструкция (`gsc_export_instructions.md:76`).
4. `tests/test_extract_smoke.py` — 5 упавших фикстур (`_write_gsc_manual` →
   `test_gsc_manual_validates_and_writes_same_contract`,
   `test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat`,
   `test_gsc_manual_missing_device_column_flags_month`; `_write_wm_manual` →
   `test_webmaster_manual_aggregates_to_popular_contract`,
   `test_webmaster_manual_records_no_page_device_breakdown`) пишут
   **другой, более старый контракт**, никак не связанный с текущим кодом:
   - `_write_gsc_manual` (`tests/test_extract_smoke.py:985-991`) кладёт
     ОДИН плоский файл `gsc_YYYY-MM.csv` с англ. заголовками
     `query,page,device,clicks,impressions,ctr,position` прямо в
     `manual_export_dir` — без папки `YYYY-MM/`, без `Диаграмма.csv`/
     `Страницы.csv`. Текущий `_month_folders()` ищет **директории** с именем
     `^\d{4}-\d{2}$`; такой директории тест не создаёт вовсе, поэтому
     `extract()` падает на `SourceUnavailable("нет папок YYYY-MM")` до того,
     как дойти до проверяемых assert'ов. Тест также ожидает `meta.yaml` с
     `total_clicks_ui` и `result["clicks_ui_caveats"][0]["total_clicks_ui"]`
     — текущий код такого поля не знает вообще; сверка кликов реализована
     как `_clicks_caveat()` (Диаграмма.csv vs Запросы.csv), а не через
     `meta.yaml`.
   - `_write_wm_manual` (`tests/test_extract_smoke.py:1081-1084`) кладёт по
     ОДНОМУ long-формат файлу на месяц (`webmaster_YYYY-MM.csv`, колонки
     `query,impressions,clicks,position,month`). Текущий `_export_path()`
     ищет ровно один файл с именем `manual_export_file`
     (дефолт `webmaster_export.csv`) в wide-формате — этого имени тест не
     создаёт, поэтому `extract()` падает на
     `SourceUnavailable("файл выгрузки не найден")` до assert'ов.
   - `test_gsc_manual_no_exports_raises_source_unavailable` и
     `test_webmaster_manual_no_exports_raises` (без фикстур, сразу ожидают
     `SourceUnavailable`) проходят и сегодня — совпадение поведения
     случайное, а не признак согласованности контрактов.
5. Не чинилось: код и `docs/gsc_export_instructions.md` не редактировались
   (вне скоупа задачи и не требуется — расхождения не найдено). Тесты не
   редактировались. Нужна отдельная задача с `tests/test_extract_smoke.py`
   в `allowed_files`, чтобы переписать `_write_gsc_manual`/`_write_wm_manual`
   и все 5 зависимых тестов под текущий контракт (папки `YYYY-MM/` с
   срезовыми CSV для GSC; один wide-файл для Webmaster) — по образцу
   реальных файлов pognali.rent, перечисленных в п.3.

---

**FIX-report-wiring** — 2026-07-29.
Закрыт blocker из AUDIT-report-wiring (строка ~1486 этого файла): `run_report`
(`src/pipeline/orchestrator.py`) после гейта `approved_findings_present`
теперь реально вызывает `build_report.build(paths, config, defaults)` с
путями/конфигом клиента вместо строки-заглушки. Ошибка `build()` логируется
(`"report: ОШИБКА сборки отчёта — ..."`) и пробрасывается исключением дальше
(не подменяется на `ok=True`/exit code 0) — `--stage report`/`--stage all`
больше не могут завершиться «успешно» без файла отчёта. `build_report.py` и
его тесты (задачи 7A–7D) не менялись.

Тесты: новый `tests/test_orchestrator_report_wiring.py` (3 теста) —
`run_report` с непустым `findings/approved/` реально создаёт
`diagnostic_report.md` (с содержимым находки), `oral_review_agenda.md` и
`appendix_tables/skipped_checks.csv`; гейт на пустом `approved/` по-прежнему
блокирует (регрессия не допущена); ошибка `build_report.build` пробрасывается
как исключение, а не проглатывается.
`pytest tests/test_orchestrator_report_wiring.py tests/test_orchestrator_analyze_gate.py
tests/test_orchestrator_error_logging.py` — **10 passed**.
Blocker: нет.

---

**FIX-wordstat-canonical** — 2026-07-29.
Замкнут разрыв raw wordstat -> canonical из AUDIT-wordstat-canonical (строка
~1491 этого файла): `build_wordstat()` в `src/transform/build_canonical.py`
читает `data/raw/wordstat/{wordstat_weekly,wordstat_core_queries}.parquet` и
пишет `data/canonical/wordstat.parquet` (LEFT JOIN по `normalized_phrase`;
`month` — вычисляемая колонка из `date`, тот же приём, что `build_direct_geo`;
`purpose` — comma-joined строка вместо list, тот же приём, что
`conditions_raw`). Таблица зарегистрирована в `SCHEMAS` и в `build()`
(строится, если `"wordstat"` есть в `data/raw/manifest.json.sources`).

Blocker (не устранён — вне `allowed_files` этой задачи, `src/compute/
block4_seo.py` не менялся): проверено запуском `block4_seo.run()` с реальным
непустым `wordstat.parquet` в canonical — `_run_s07`/`_run_s26`
(`block4_seo.py:1148`/`2632`) при `"wordstat" in canonical and
_table_nonempty(...)` всё равно пишут `status: "unavailable"` (у обеих
проверок это захардкожено в ОБЕИХ ветках if/else, независимо от таблицы) —
S07/S26 структурно недоступны, даже когда canonical-таблица уже существует.
`_run_s06` (`block4_seo.py:1134`) при том же условии верно поднимает
`wordstat_available` в `True`, но `confidence` строки
`seasonality_reconciliation` захардкожен в `_cap("LOW", confidence_cap)` вне
зависимости от `wordstat_available` — S06 не может подняться выше LOW без
правки этой строки. Требуется отдельная задача с `src/compute/block4_seo.py`
в `allowed_files`, которая реально прочитает колонки новой таблицы (сейчас
там нет ни одного обращения к её содержимому, только проверка на
существование/непустоту) и уберёт оба хардкода.

Тесты: `tests/test_build_canonical.py` — 8 новых (`test_build_wordstat_*`,
`test_build_writes_wordstat_via_orchestrator`,
`test_build_skips_wordstat_when_source_absent`); существующие
`test_s06_reports_trend_and_wordstat_unavailable`,
`test_s07_always_unavailable_wordstat_missing`,
`test_s26_always_unavailable_wordstat_missing` в `tests/test_block4_seo.py`
не менялись и остаются зелёными (они проверяют сценарий «wordstat
отсутствует» — новый transform его не затрагивает).
`pytest tests/test_build_canonical.py` — **136 passed**.
`pytest tests/test_block4_seo.py -k "s06 or s07 or s26"` — **3 passed**.

---

**FIX-block4-seo-wordstat-consumption** — 2026-07-29.
Закрыт blocker из FIX-wordstat-canonical (строка ~1824 этого файла):
`src/compute/block4_seo.py` теперь реально читает колонки `data/canonical/
wordstat.parquet` вместо хардкода.

- `_run_s07`/`_run_s26`: без `canonical["wordstat"]` — по-прежнему
  `unavailable` (данных нет). С таблицей — реальный расчёт через новый общий
  `_gap_demand_candidates()`: кластер спроса = `wordstat` строки
  `scope='gap-specific'` (реальный коммерческий спрос за вычетом junk/general
  — та же классификация, что уже делает extract), материальность —
  `SUM(count) >= _S07_MIN_DEMAND_COUNT` (20, тот же порядок величины, что
  `_MIN_SHOWS_FOR_OPPORTUNITY`); "есть посадочная" — `normalize(phrase)`
  (`src/extract/wordstat_config.normalize`, та же функция, что использует сам
  extract — не задублирована) буквально совпадает с `normalize()` какого-то
  `seo_queries.query`. Несовпадение при материальном спросе -> находка.
  `_run_s26` вызывает ту же функцию (данных для отдельного гео-разреза в
  canonical wordstat нет — регион задаётся на всю выгрузку целиком в
  config, а этот модуль client config не читает, см. его собственный
  контракт) — помечает каждую строку `geo_dimension_available: false`,
  чтобы не выдавать совпадение с S07 за географический анализ.
- `_run_s06`: новая `_reconcile_seasonality()` реально сверяет
  месяцы-аномалии показов `seo_queries` с помесячным `SUM(count)` `wordstat`
  строк `purpose LIKE '%seasonality%'` (единственные фразы, которые extract
  специально отбирает для сезонной кривой). Если Wordstat подтверждает то же
  направление отклонения в том же месяце — `confidence` поднимается до
  `MED` (`verdict: seasonality_explains_anomaly` или
  `anomaly_not_fully_explained_by_seasonality`, тоже MED — сверка состоялась,
  это уже не гипотеза). Без Wordstat — прежнее поведение (`LOW`,
  `cannot_determine_without_wordstat`) не тронуто.

`run()` (диспетчер блока) — `_run_s07`/`_run_s26` теперь принимают `paths`
(нужен для `common.open_duckdb`), сигнатуры и оба call site обновлены.
`build_wordstat()`/`build_canonical.py` не менялись (вне allowed_files).

Тесты (`tests/test_block4_seo.py`): 3 существующих (`test_s06_reports_trend_
and_wordstat_unavailable`, `test_s07_always_unavailable_wordstat_missing`,
`test_s26_always_unavailable_wordstat_missing`) не менялись, остаются
зелёными (сценарий «wordstat отсутствует» не тронут). 5 новых:
`test_s06_confidence_rises_to_med_when_seasonality_confirmed`,
`test_s06_confidence_rises_to_med_when_seasonality_not_confirmed`,
`test_s07_reports_gap_candidates_when_wordstat_available`,
`test_s07_below_min_demand_threshold_not_a_candidate`,
`test_s26_reports_geo_candidates_when_wordstat_available`.
`pytest tests/test_block4_seo.py` — **50 passed**.
`pytest --ignore=tests/test_block1.py --ignore=tests/test_block3.py` (эти два
не собираются в этом окружении, `ModuleNotFoundError: scipy` — не связано с
этой задачей, воспроизводится и на чистом `master`) — **727 passed, 14
failed**. Список этих 14 идентичен `git stash` (полный откат правок этой
задачи) — та же дорожка `test_direct_2b_patch.py` (2), `test_extract_smoke.py`
(8), `test_metrika_logs_lookback.py` (1), `test_money_frame.py` (1),
`test_transform_direct_normalize.py` (1) — предсуществующие падения
окружения, не связанные с этой задачей; регрессий не внесено.
Blocker: нет.

---

**AUDIT-s07-s26-formula-match** — аудит, без правок кода — 2026-07-29.

**Вопрос:** реализуют ли `_run_s07`/`_run_s26`/`_gap_demand_candentats`
(`src/compute/block4_seo.py`, добавлены задачей
FIX-block4-seo-wordstat-consumption) формулу каталога v2, или считают другую
метрику.

**Вердикт: считают другую, смежную метрику. Формуле каталога реализация НЕ
соответствует.**

1. **`site_pages` не читается вообще — не "источник не готов", а "формула
   его не использует".** Каталог v2 (`catalog-proveryaemyh-marketingovyh-
   ugroz-v2.md`, блок 4): S07 — "Сопоставить кластеры Wordstat/GSC **с картой
   страниц**", источник — "Wordstat + GSC + **сайт**"; S26 — "Сопоставить
   гео-спрос, **страницы**, позиции и фактическую зону обслуживания",
   источник — "Wordstat + GSC/Вебмастер + **сайт**". `data-export-spec-v2.md`
   (раздел "Матрица: блок угроз → источники и таблицы") называет это явно:
   `S07, S26 | F (Wordstat) + D/E + G1 (карта страниц) | query cluster` —
   т.е. ТРЕТИЙ обязательный источник, G1 = `site_pages`/`site_crawl`.
   Проверено grep'ом по всему телу `_run_s07` (`block4_seo.py:1314-1361`),
   `_run_s26` (`2823-2872`) и `_gap_demand_candidates`/`_wordstat_gap_demand`/
   `_seo_known_query_set` (`1264-1311`): ни одного упоминания `site_pages`
   или `canonical["site_pages"]` во всех пяти функциях — при том, что та же
   каноническая таблица уже реально используется в этом же файле десятком
   строк выше и ниже (`_load_site_pages_full`, S11-S19/S27,
   `block4_seo.py:602-650`, `1656+`, `2876+`) — то есть таблица доступна и
   используемый паттерн доступа к ней в модуле уже есть, её просто не
   вызвали для S07/S26. Это не деградация "site_crawl не выполнен" (для неё
   в модуле есть отдельный, уже используемый паттерн unavailable/caveat) —
   формула физически не ссылается на карту страниц.
   Фактически вычисляемое условие "релевантной посадочной нет" подменено на
   "ни одна строка `seo_queries.query` не совпадает (после `normalize()`) с
   этой wordstat-фразой" (`_gap_demand_candidates`, `1294-1311`) — то есть
   "эта фраза ни разу не зафиксирована как запрос в отчётах GSC/Вебмастер",
   а НЕ "на сайте нет страницы, релевантной этому спросу". Это разные
   утверждения: страница может существовать и быть вполне релевантной, но
   не ранжироваться ни по одному запросу этого кластера (тогда каталог хочет
   именно эту находку — "спрос есть, посадочная либо есть, но не
   ранжируется, либо её нет вовсе" — сопоставление с G1 позволило бы это
   различить), а собственный код называет свой результат полем
   `has_matching_page` (`_gap_demand_candidates:1309`) — это имя утверждает
   про существование СТРАНИЦЫ то, что функция физически не проверяла (она
   проверяла только текстовое совпадение запроса в отчётах). Для S26
   расхождение больше: каталог явно требует ещё и "позиции" — `_run_s26`
   их не использует вовсе (переиспользует `_gap_demand_candidates` "как
   есть", ноль дополнительной логики сверх `geo_dimension_available: False`,
   уже честно продекларированного в задаче FIX-block4-seo-wordstat-
   consumption).

2. **Порог `SUM(count) >= 20` — нигде вне `block4_seo.py`.**
   `config/defaults.yaml` (полностью прочитан) не содержит ни блока `block4`,
   ни ключа, похожего на демандовый порог для S07/S26 — только
   `data_window_months`, `utm_undefined_threshold`, `significance_alpha`,
   `min_sample_visits`, `goal_inflation_warning`, `currency_round`,
   `manual_source_confidence_cap`, `crux_min_field_data`,
   `transform.traffic_resolve_lookback_days`,
   `transform.seo_queries_min_total_shows`. `_S07_MIN_DEMAND_COUNT = 20`
   (`block4_seo.py:1261`) — захардкоженная константа модуля, обоснованная в
   комментарии рядом только ссылкой "тот же порядок величины, что
   `_MIN_SHOWS_FOR_OPPORTUNITY`" (тоже локальная константа этого же файла,
   `= 20`, строка ~263) — т.е. число не выведено ни из каталога (там числа
   вообще нет ни для одной S-проверки), ни из data-export-spec, ни из
   методологии, ни из конфига — оно скопировано с другого хардкода того же
   модуля "для порядка величины". Важная оговорка: это НЕ уникальное
   отклонение S07/S26 — весь модуль `block4_seo.py` последовательно устроен
   так (собственный докстринг файла, "Пороги-эвристики": "каталог не даёт
   точных чисел... обоснование у каждой константы", ни один из ~30 порогов
   S01-S27 не читается из `config/defaults.yaml`). Так что хардкод сам по
   себе — установленная конвенция файла, а не отдельный дефект именно этой
   реализации; но фиксируется как факт по прямому запросу задачи: число
   нигде не задокументировано как производное от бизнес-требования, это
   произвольная эвристика автора кода.

**Итог:** `_run_s07`/`_run_s26` реализуют реальный, работающий, но ДРУГОЙ
показатель — "коммерческая Wordstat-фраza без единой сопоставимой строки в
seo_queries.query" (query-coverage gap на пересечении Wordstat×GSC/Вебмастер),
а не заявленный каталогом v2 показатель "коммерческий/гео-спрос, для которого
на сайте нет релевантной страницы" (это требует G1/`site_pages`, а для S26 —
ещё и позиций и гео-зоны обслуживания). Поле `has_matching_page` называет
результат этой query-coverage проверки так, будто было подтверждено наличие
страницы — это тоже вводит в заблуждение читателя находки, а не только
пробел в источниках. Не чинилось (вне allowed_files этого аудита) — нужна
отдельная задача с `src/compute/block4_seo.py` в `allowed_files`, которая
добавит реальное сопоставление с `canonical["site_pages"]` (и для S26 —
позиции/гео) либо явно переименует/переопределит находку как
query-coverage-gap, если решено оставить текущую метрику как временный
суррогат.

---

**FIX-s07-site-pages-join** — 2026-07-29. Устраняет пробел, зафиксированный
AUDIT-s07-s26-formula-match (выше), только для S07 (S26 — отдельная задача,
требует ещё позиций/гео). `src/compute/block4_seo.py`: `_gap_demand_candidates`
переименовала поле `has_matching_page` -> `has_matching_query` (честное имя —
функция проверяет только совпадение с seo_queries.query, не существование
страницы; S26 использует ту же функцию, поведение/значения S26 не изменились —
рефакторинг чисто внутри функции, поле нигде не сериализуется у S26). `_run_s07`
получил второй независимый сигнал `has_matching_page` — на любой странице
`canonical["site_pages"]` все слова кластера (после `normalize()`) встречаются
в title, h1 или URL-пути (`_phrase_matches_site_page`/`_site_page_word_sets`,
простое пересечение множеств слов — каталог `catalog-proveryaemyh-
marketingovyh-ugroz-v2.md:263` не даёт более точной формулы). Находка
`commercial_demand_without_landing_page` теперь требует отсутствия совпадения
ПО ОБОИМ сигналам; кластер без query, но с реальной релевантной страницей —
больше не находка (ключевое отличие от прежней реализации). Без
`canonical["site_pages"]` (не только без `wordstat`, как раньше) — S07 явно
`unavailable` с caveat "нет карты страниц" (тот же паттерн, что S11/S18/S19/S27),
тихого fallback на старую query-only логику не осталось. Порог `SUM(count) >=
20` вынесен из хардкода `_S07_MIN_DEMAND_COUNT` в `config/defaults.yaml:
block4_seo.s07_min_demand_count` (с комментарием, ссылка на каталог строка 263);
модульная константа осталась фолбэком на случай отсутствия ключа, S26
по-прежнему читает старый хардкод напрямую (вне scope). `run()`: `_run_s07`
получил параметр `defaults` (S26 не тронут). Тесты — `tests/test_block4_seo.py`:
обновлены 2 существующих S07-теста (добавлена фикстура `site_pages` — контракт
изменился, деградация без неё была бы новым тихим unavailable) + 3 новых:
`test_s07_unavailable_without_site_pages`,
`test_s07_reports_gap_candidates_without_query_or_page_match` (ни query, ни
страница -> находка), `test_s07_page_match_without_query_match_is_not_a_finding`
(страница без query -> НЕ находка, ключевой сценарий промта) —
`pytest tests/test_block4_seo.py` — **52 passed**, 0 failed. Blocker: нет.

---

**AUDIT-s26-geo-data-availability** — 2026-07-30. Диагностика (без правок кода):
физически ли доступны данные для формулы S26 ("Сопоставить гео-спрос, страницы,
позиции и фактическую зону обслуживания", каталог v2 строка 282) — до задачи,
которая тронет `block4_seo.py`.

1. **Wordstat — один регион(-набор) на клиента, не мульти-гео.**
   `src/extract/wordstat.py:_region_ids` читает `config.sources.wordstat.regions`
   как ОДИН список GeoID, передаваемый ЦЕЛИКОМ в каждый вызов `topRequests`/
   `dynamics` (`body["regions"] = [str(r) for r in regions]`, строки 196/286-294,
   416-417, 431-432). У pognali.rent (`clients/pognali.rent/config.yaml:37`)
   это `regions: [75]` (весь Приморский край одним значением) — один агрегат
   спроса на весь регион, а не отдельные срезы по городам/районам, которые
   можно было бы сравнить между собой. Это подтверждает то, что уже
   зафиксировано в самом `block4_seo.py` (docstring `_run_s26`, п.13,
   `geo_dimension_available: false`): canonical wordstat не несёт гео-поле
   на строку. Вывод п.1 задачи: **S26 структурно невозможен как гео-анализ
   без отдельной extract-задачи на мульти-гео Wordstat** (per-регион вызовы
   или колонка региона в `wordstat_weekly`/`wordstat_core_queries`) — это
   пробел в `extract`, а не в `compute`; никакой патч `block4_seo.py` поверх
   существующих данных эту часть формулы не закроет.

2. **"Фактическая зона обслуживания" клиентом не отвечена.**
   В анкете (`clients/_template/inputs/client_answers.yaml`) нет отдельного
   поля "зона обслуживания" — ближайший кандидат, Q04 `capacity_limits`
   (строка 55-57, комментарий явно упоминает "регионы" среди того, что
   бизнес не может обслужить), это список лимитов, а не описание зоны
   покрытия. У pognali.rent (`clients/pognali.rent/inputs/client_answers.yaml`)
   анкета **полностью не заполнена** — файл идентичен `_template`
   (`capacity_limits: []`, все поля null/пустые). Единственный текстовый
   источник гео вообще — `client.geo: "Владивосток / Приморский край"` в
   `config.yaml:7`, это общая формулировка ниши/региона для отчёта, не
   структурированная зона обслуживания по городам/районам.

3. **Вердикт:** S26 **нельзя** закрыть компьют-патчем поверх существующих
   данных. Требуются, до того как трогать `block4_seo.py` под S26 предметно:
   (a) extract-задача на мульти-гео Wordstat (новый транспорт-паттерн —
   per-регион вызовы `topRequests`/`dynamics` или гео-колонка в raw/canonical
   wordstat) — без неё нет самого гео-разреза спроса, только агрегат по
   всему региону; (b) заполненная оператором "зона обслуживания" —
   либо новое поле в `client_answers.yaml`, либо расширение `capacity_limits`
   до структурированного описания (список городов/районов), т.к. текущая
   анкета для pognali.rent пуста. Текущая реализация `_run_s26` (механически
   равна `_run_s07`, см. AUDIT-s07-s26-formula-match/FIX-s07-site-pages-join
   выше) не эквивалентна и не станет эквивалентна каталожной формуле S26 без
   этих двух предпосылок независимо от того, что дальше делается в `compute`.
   Blocker: extract-задача на мульти-гео Wordstat + заполнение зоны
   обслуживания оператором — оба вне scope компьют-слоя.

---

**AUDIT-crm-real-file-ingestion** — 2026-07-30. Диагностика (без правок кода,
без изменений `clients/pognali.rent/data/raw/crm/`): реально ли парсится
настоящий CRM-файл клиента через `src/extract/crm_import.py`. Прогон —
фактический вызов `_read_csv`/`_normalize_row` из кода на реальном файле
(read-only, ничего не записывалось в боевой manifest/data/raw).

**0. Ключевое расхождение до всего остального.** Файл, на который указал
оператор (`clients/pognali.rent/inputs/crm_export.csv`, 74002 байт,
1357 строк данных), — **не** экспорт бронирований аренды авто. Это плоская
выгрузка лидов/сделок из CRM с колонками
`lead_id;created_at;source;utm_source;utm_campaign;stage;is_repeat;deal_amount_rub;closed_at`
(`;`-разделитель, UTF-8 без BOM, точка как десятичный разделитель,
дата `dd.mm.yyyy HH:MM`). Список колонок из задания (код машины, Время
начала/окончания, Суток аренды, Доставка, Приём, Мойка, Повреждения,
Топливо, выручка_чистая, компенсации, выручка_полная, price_mismatch)
описывает какой-то другой источник (похоже на выгрузку бронирований
проката, не на этот CRM-экспорт) — такого файла в `clients/pognali.rent/`
нет ни под одним из проверенных имён. Ниже — таблица по факту того файла,
что есть, а не подгонка под ожидаемый список.

| колонка_клиента (из задания) | прочитана_кодом | тип_совпал | пример_до | пример_после |
|---|---|---|---|---|
| ID | переименовать (файл: `lead_id`, canonical: `phone_or_id`, `column_map` не задан → без правки конфига не читается) | да, при добавлении `column_map` | `"653065"` | `lead_id="653065"`, `lead_kind="id"` (6 цифр — не считается телефоном) |
| Дата создания | переименовать (файл: `created_at`, canonical: `lead_date`, `column_map` не задан) | да, при добавлении `column_map` | `"08.07.2026 11:40"` | `"2026-07-08"` (время отброшено, формат `%d.%m.%Y` уже в `DEFAULT_DATE_FORMATS`) |
| код машины | нет — такой каноничной колонки в `crm_import.py` вообще не существует (`CANONICAL_COLUMNS` = `lead_date, source, phone_or_id, status, amount_rub, is_new_client`) | — | — | — |
| Время начала | нет — не в `CANONICAL_COLUMNS`, в файле такой колонки тоже нет | — | — | — |
| Время окончания | нет — не в `CANONICAL_COLUMNS`; ближайшее по смыслу поле файла `closed_at` код не читает (не входит в `RAW_FIELDS`) | — | `"10.07.2026 12:00"` | (игнорируется полностью) |
| Суток аренды | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Цена | нет отдельной колонки — ближайший аналог `amount_rub` (см. "За аренду" ниже, дублируют друг друга в задании) | — | — | — |
| За аренду | частично — `amount_rub` есть в схеме кода, в файле есть `deal_amount_rub`, но нужен `column_map`; отдельно "Цена" от "За аренду" код не различает — это одно поле `amount_rub` | да, при добавлении `column_map` | `"8200.0"` | `8200.0` (float, разделитель точка — совпадает с суммой в файле построчно, см. п.2 ниже) |
| Доставка | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Приём | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Мойка | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Повреждения | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Топливо | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| Источник клиента | нет, поле пустое | тип не проверить — 0 непустых значений | в файле колонка `source` есть по имени, но **0 из 1357 строк** заполнены | `""` → `source=None` во всех строках |
| Источник | дубль предыдущего (в задании те же данные под другим названием) | — | — | — |
| Источник клиента (норм) | нет — это `source_norm`, считается в `build_canonical.py:normalize_crm_source` из `source`; при пустом `source` на входе будет `unknown`/`null` для всех 1357 строк | — | — | — |
| Источник (норм) | дубль предыдущего | — | — | — |
| выручка_чистая | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| компенсации | нет — не в `CANONICAL_COLUMNS`, в файле нет | — | — | — |
| выручка_полная | нет — не в `CANONICAL_COLUMNS`, в файле нет (в файле одна денежная колонка `deal_amount_rub`, не три) | — | — | — |
| price_mismatch | нет — такой проверки/поля в `crm_import.py` не существует | — | — | — |

Дополнительно (не входит в список задания, но реально есть в файле):
`status`/`stage` — колонка `stage` в файле присутствует по имени, но
**0 из 1357** строк заполнены (как и `source`, `utm_source`, `utm_campaign`,
`is_repeat`). Заполнены только `lead_id`, `created_at`, `deal_amount_rub`,
`closed_at` (1357 из 1357 каждая).

**1. Разделитель/кодировка/числа/даты (raw, до парсинга).** `;`-разделитель
(автоопределение `_sniff_delimiter` угадывает верно), UTF-8 без BOM (не
понадобился откат на `cp1251`), денежное поле — точка как десятичный
разделитель (`"8200.0"`, не `"8200,0"` — нетипично для RU-Excel выгрузки,
похоже на экспорт напрямую из БД/API, а не через Excel), дата
`dd.mm.yyyy HH:MM` (покрыта `DEFAULT_DATE_FORMATS`).

**2. Фактический прогон `_read_csv`/`_normalize_row` (без `column_map`, как
сейчас в `clients/pognali.rent/config.yaml` — там только `sources.crm_csv:
{enabled: false, path: ...}`, ключа верхнего уровня `crm_csv:` с
`column_map` нет вообще):
`accepted=0, rejected_reasons={"bad_date": 1357}` — **все 1357 строк
отбраковываются**, потому что код ищет колонку `lead_date`, а в файле она
называется `created_at`; без `column_map` это в принципе не должно было
парситься, и не парсится.

С добавлением тестового (не сохранённого в конфиг) минимального
`column_map = {lead_date: created_at, phone_or_id: lead_id, amount_rub:
deal_amount_rub}`: `accepted=1357/1357, rejected_reasons={}` — **все
строки проходят**.

**3. Денежные поля на выборке (после применения тестового `column_map`,
`random.seed(42)`, 5 случайных строк из 1357) — совпадение "глазами" с
исходником:**

| RAW `deal_amount_rub` | PARSED `amount_rub` | RAW `created_at` | PARSED `lead_date` |
|---|---|---|---|
| `"20500.0"` | `20500.0` | `"25.04.2025 09:56"` | `"2025-04-25"` |
| `"15700.0"` | `15700.0` | `"14.05.2026 11:19"` | `"2026-05-14"` |
| `"10000.0"` | `10000.0` | `"24.06.2026 14:42"` | `"2026-06-24"` |
| `"4000.0"` | `4000.0` | `"12.11.2025 11:11"` | `"2025-11-12"` |
| `"6600.0"` | `6600.0` | `"18.12.2025 01:11"` | `"2025-12-18"` |

Совпадает точно на всех 5 строках, время в дате корректно отброшено.

**4. Блок L / `block6.py` (лид→сделка, новые/повторные, скорость
обработки) — прямо, без додумывания: `src/compute/block6.py::run` —
`raise NotImplementedError`. **Реализации нет вообще**, ни черновой. Даже
если бы CRM парсился (см. п.2), потреблять результат сейчас некому — блок
не читает `data/canonical/crm.parquet` ни в каком виде. Слой `transform`
(`src/transform/build_canonical.py::build_crm`, строка ~1676) свою часть
контракта имеет — нормализует `status`→`status_norm`,
`source`→`source_norm` — но при пустых `status`/`source` во входном файле
(п.0) на выходе `status_norm`/`source_norm` были бы `unknown`/`null` для
всех 1357 строк, то есть даже при живом `block6.py` проверки 6.2 (новые/
повторные) и атрибуция по источнику из этого конкретного файла не
считались бы — там физически нет данных, а не только нет кода.

**Вердикт:** файл **не грузится как есть** — 0/1357 без правок. Разделитель/
кодировка/числа/даты корректны и не являются причиной; причина — только
несовпадение имён колонок с `CANONICAL_COLUMNS` при пустом `column_map` в
`clients/pognali.rent/config.yaml`. С минимальным `column_map` (3 ключа:
`lead_date`, `phone_or_id`, `amount_rub`) файл грузится **полностью**,
1357/1357, деньги и даты парсятся корректно. Но: (a) `sources.crm_csv.
enabled: false` — источник выключен независимо от парсинга; (b) `source`/
`status`/`is_new_client` в этом файле на 100% пусты — с ним в принципе
недоступны проверки 6.2/6.3 и атрибуция по источнику, даже если включить
источник и дописать `column_map`; (c) большинство колонок, перечисленных в
задании (рентал-специфичные — код машины, сутки аренды, доставка/приём/
мойка/топливо, выручка_чистая/полная, компенсации, price_mismatch), в этом
файле не существует и не входят в схему `crm_import.py` — это, по всей
видимости, требования к другому источнику данных (выгрузка бронирований),
который в репозитории не найден; (d) `block6.py` — пустой стаб,
`NotImplementedError`, потребления результата парсинга нет независимо от
пунктов (a)-(c). Blocker: (1) подтвердить у оператора, существует ли
отдельный файл-экспорт бронирований с рентал-колонками из задания и где он
лежит — то, что было прочитано, им не является; (2) если "CRM" в задании и
есть этот лид/сделка-экспорт — тогда рентал-колонки из задания не входят в
scope этого источника в принципе и вопрос снимается сам собой, но это
нужно явно подтвердить, а не предполагать.

---

**CRM-scope-money-only** — 2026-07-30. Оператор подтвердил: файл из
предыдущего аудита (`lead_id;created_at;source;utm_source;utm_campaign;
stage;is_repeat;deal_amount_rub;closed_at`, 1357 строк) — это и есть
искомый CRM-файл, не заглушка. Рентал-специфичные колонки из задания
(код машины, Суток аренды, выручка_чистая/полная, компенсации,
Доставка/Приём/Мойка/Повреждения/Топливо, price_mismatch) в этом файле
не существуют — реализовано по факту наличных данных, а не по
изначально описанному, но не найденному, набору колонок.

Изменено (`allowed_files`): `src/extract/crm_import.py`,
`clients/pognali.rent/config.yaml`, `tests/test_crm_import.py`.

1. **`clients/pognali.rent/config.yaml`**: `sources.crm_csv.enabled`
   `false -> true`; добавлена секция верхнего уровня `crm_csv:` —
   `column_map` (`lead_date<-created_at`, `phone_or_id<-lead_id`,
   `amount_rub<-deal_amount_rub` — единственные 3 поля, которые вообще
   есть и нужны), `attribution_reliable: false` +
   `attribution_unreliable_reason` (текст: source/stage/is_repeat пусты
   в 1357/1357 строк на 2026-07-30).
2. **`src/extract/crm_import.py`**: `crm_cfg.get("attribution_reliable",
   True)` + `attribution_unreliable_reason` — читаются из конфига (не
   захардкожены, принцип 1 CLAUDE.md), пробрасываются в
   `validation_report.json` и в `manifest.json` как
   `crm_attribution_reliable` (булево, видно
   `degradation.collect_manifest_flags` наравне с остальными флагами
   манифеста, без правок `degradation.py`) + текстовая причина рядом.
   Default `True` — клиенты без этого ключа в конфиге не затронуты.
   `RAW_FIELDS` не менялся — `source`/`status` как были в raw, так и
   остаются (пустыми, но не удалёнными).
3. **`block6.py` не тронут** — остаётся `NotImplementedError`; по
   методологии (`marketing-diagnostics-methodology-v2.md`, §"Блок L")
   это осознанный апселл-статус, не баг, менять не требовалось.
4. **`money_frame.py` не читает CRM вообще** (`grep crm` — пусто) —
   пункт "downstream не падает от отсутствия надёжного source" в задаче
   выполняется тривиально: некому падать, потребителя ниже по потоку
   от CRM пока не существует, кроме самого стаба `block6.py`.

Тесты — `tests/test_crm_import.py` (новый файл, 7 тестов):
без `column_map` реальная схема — 0/1357 принято, `bad_date` на всех
(регрессия аудита); с `column_map` — 1357/1357 принято; денежные поля
и даты точно совпадают на выборке; `source`/`status` не исчезают из
raw-заголовков; `crm_attribution_reliable=false` + причина попадают и
в `validation_report.json`, и в `manifest.json`; default `True` без
флага в конфиге; флаг виден `degradation.collect_manifest_flags`.
`pytest tests/test_crm_import.py tests/test_smoke.py` — **24 passed**,
0 failed (смоук на `_template` — убедиться, что правка конфига
pognali.rent не задевает шаблонного клиента).

Дополнительно — реальный прогон `extract()` на боевом
`clients/pognali.rent/config.yaml` + реальном
`inputs/crm_export.csv`, вывод перенаправлен в scratch-директорию
(`tempfile.mkdtemp`), **боевой `clients/pognali.rent/data/raw/crm/` не
тронут**: `accepted=1357/1357`, `crm_attribution_reliable=false` с
причиной — воспроизводит результат тестов на реальном файле, не только
на фикстуре.

**Вердикт:** денежная сверка подключена и работает (даты + `amount_rub`,
100% строк); атрибуция официально закрыта как непригодная явным
конфиг-флагом + manifest-флагом, а не молчаливым провалом или
удалением колонок. Blocker: нет — задача самодостаточна на имеющихся
данных. Отдельный вопрос (не blocker для этой задачи) остаётся из
прошлого аудита: рентал-специфичные колонки из исходного описания
задания по-прежнему не найдены ни в одном файле репозитория.

**VERIFY-crm-file-actually-present (2026-07-30):** файл с рентал-колонками (ID, Дата создания, код машины...) в репозитории отсутствует; CRM-задачи по pognali.rent выполняются на `clients/pognali.rent/inputs/crm_export.csv` (sha256 3894c6c8..., изменён 2026-07-30 15:15:35 +0300) с заголовком `lead_id;created_at;source;utm_source;utm_campaign;stage;is_repeat;deal_amount_rub;closed_at`.

---

**FIX-s07-verify-normalize-reuse** — аудит, без правок кода — 2026-07-30.

1. **normalize() переиспользуется, отдельной токенизации нет.** `_run_s07`
   (`src/compute/block4_seo.py`) для сопоставления с `site_pages` использует
   `_normalize_words()` (строка ~1340), которая вызывает
   `WC.normalize(text).split()` — то есть `src/extract/wordstat_config.py:
   normalize()`, ту же функцию, что и `_seo_known_query_set()` для стороны
   `seo_queries.query`. `_url_path_word_source()` тоже прогоняет URL-путь
   через `_normalize_words()` (та же `WC.normalize`) перед разбиением на
   слова. Единственное место, где на первый взгляд токенизация происходит
   БЕЗ явного вызова `WC.normalize()` в этой же строке — `_phrase_matches_
   site_page()` делает `normalized_phrase.split()` напрямую, без обёртки в
   `WC.normalize()`. Это не отдельная токенизация: `normalized_phrase`
   приходит из колонки `wordstat.normalized_phrase`, которая сама
   производится через `WC.normalize(phrase)` на этапе extract
   (`src/extract/wordstat.py:341,490`, `norm = WC.normalize(phrase)`) —
   т.е. эта строка уже прошла через `normalize()` до попадания в canonical,
   и `.split()` на уже нормализованной строке эквивалентен повторному
   `WC.normalize(...).split()` (нормализация здесь — lower + схлопывание
   пробелов, идемпотентна). Итог: везде, где сравниваются слова (запрос,
   фраза, title/h1/URL-путь), в цепочке стоит один и тот же `normalize()` —
   отдельной/расходящейся токенизации в `_run_s07` нет.
2. **Риск расхождения — не применим.** Раз отдельной токенизации нет (п.1),
   фиксировать пример расходящегося совпадения (разные окончания слова в
   одной функции и не совпадающие в другой) не требуется — унификация не
   нужна, унифицировано уже сейчас.
3. **Комментарий у `s07_min_demand_count` подтверждён.**
   `config/defaults.yaml:29` — `# S07 (каталог v2 §9, строка 263:
   "Сопоставить кластеры Wordstat/GSC с картой страниц"...` — дословно
   ссылается на каталог v2, строка 263 (проверено прямым чтением файла).

Не чинилось (allowed_files этой задачи — только этот файл). Blocker: нет.

---

**FIX-site-crawl-top20-caveat (2026-07-30).** `build_url_priority_list`
(`src/extract/site_crawl.py`) теперь фиксирует отдельный пер-источниковый
caveat для промежуточного усечения кандидатов до `top_n_each_source` (=20)
ДО объединения списков — раньше в manifest был виден только финальный
caveat усечения по `crawl.max_urls`, и факт отбрасывания части кандидатов
на этапе top-N по источнику терялся. Хелперы `_pages_from_canonical` и
`_pages_from_seo_queries` теперь возвращают `(pages, total_available)`
(число уникальных кандидатов до среза top_n); новый `_source_truncation_caveat`
строит текст в том же формате «что усечено → сколько отброшено → ремарка»,
что и финальный max_urls-caveat, со ссылкой на каталог v2 §G1 ред.2
(«частичное покрытие по построению — не повод для произвольно короткого
списка»). Результат несёт новое поле `source_caveats: list[dict]`
(`source/candidates/kept/dropped/caveat`, только для источников с
`candidates > top_n`); `_record_manifest` пишет его в `extra.source_caveats`.
Порог 20 и логика ранжирования не менялись — только видимость. Источники:
`top_spend` (Директ), `top_organic_gsc` (GSC), `top_organic_webmaster`
(Webmaster). 3 новых теста в `tests/test_site_crawl.py`
(`test_source_caveat_present_when_source_exceeds_top_n`,
`test_no_source_caveat_when_within_top_n`,
`test_source_caveat_absent_without_canonical_data`) — `pytest
tests/test_site_crawl.py` 24 passed. Blocker: нет.

---

**ADD-webmaster-operator-instructions (2026-07-30).** Новый
`docs/webmaster_export_instructions.md` — по аналогии с
`gsc_export_instructions.md`, закрывает пробел, отмеченный в
`AUDIT-manual-export-contract-drift` (п.2 выше): для Вебмастера
инструкции оператору не было вовсе, только докстринг модуля.
Перечитан `src/extract/webmaster_manual.py` перед финализацией — код
не менялся (вне скоупа задачи, только документация). Контракт в
инструкции — один wide-файл `webmaster_export.csv` в
`inputs/manual_exports/webmaster/` (колонки `Query`/`Url` +
`{YYYY-MM}_shows/_position/_demand/_ctr/_clicks`), **без** папок
`YYYY-MM/`: формулировка задания предполагала помесячные папки по
аналогии с GSC, но это не соответствует фактическому контракту
`webmaster_manual.py`/`_export_path()`/тестам/реальной выгрузке
pognali.rent (см. `AUDIT-manual-export-contract-drift`, п.3) — один файл
на весь период, разворот в long делает `4X-webmaster-transform`
(`src/transform/webmaster_popular_queries.py`), а не структура папок.
Инструкция написана под фактический код, расхождение с заданием никак
не скрыто (явно отмечено здесь). Отдельный блок «не путать demand и
shows» — по `data-export-spec-v2.md`, раздел D, ред. 2. Blocker: нет.

---

**AUDIT-metrika-dropped-fields-negotiation** — аудит, без правок кода —
2026-07-30. Установить, жив ли механизм негоциации полей в
`src/extract/metrika_logs.py` или два упавших теста
(`test_metrika_logs_negotiation_isolates_unsupported_fields`,
`test_metrika_logs_backfill_preserves_old_files`) тестируют мёртвый
сценарий. Читал: `metrika_logs.py` (механизм целиком),
`tests/test_extract_smoke.py` (оба теста + точный traceback), записи
2A-patch этого файла.

**Вердикт: тесты устарели под старый список полей — механизм негоциации
рабочий.** Как в двух прошлых аудитах (lookback, gsc/webmaster).

1. Оба теста падают на одном и том же ассерте
   (`test_extract_smoke.py:204` и `:275`):
   `set(result["dropped_fields"]) == {'ym:s:lastSignhasGCLID'}` при
   фактическом `dropped_fields == []`. Симулируемое «плохое» поле теста —
   `_METRIKA_BAD_FIELDS = {"ym:s:lastSignhasGCLID"}`
   (`test_extract_smoke.py:105`).

2. `ym:s:lastSignhasGCLID` патчем 2A-patch убрано из запроса НАСОВСЕМ
   (`metrika_logs.py:48`, докстринг стр. 175–179): его нет ни в
   `PATCH_ADDED_FIELDS`, ни в `PATCH2_ADDED_FIELDS`, значит и в
   `PATCH_CANDIDATE_FIELDS` (стр. 215). Мок `_evaluate_route(..., bad=…)`
   возвращает 400 только если поле реально попало в состав запроса —
   но `lastSignhasGCLID` в состав не попадает, 400 не срабатывает,
   `_negotiate_fields` возвращает `dropped={}`. Симулируемый негативный
   ответ API проверяет поле, которое код больше не запрашивает.

3. Механизм НЕ сломан — у него есть живые кандидаты. `_negotiate_fields`
   вызывается с `PATCH_CANDIDATE_FIELDS` в `_run_full` (стр. 533) и в
   `_run_backfill` (стр. 617); все эти поля реально уходят в
   `logrequests/evaluate`. Эмпирическая проверка (тот же FakeSession, но
   `bad={"ym:s:browser"}` — поле, которое КОД реально запрашивает):
   `dropped_fields == ['ym:s:browser']`,
   `dropped_reasons == {'ym:s:browser': 'Unknown field ...'}`,
   `ym:s:browser` исключён из `available_fields`. Бинарное деление
   (`_find_bad_fields`) изолирует именно отклонённое поле и пишет
   `dropped_fields`/`dropped_reasons`/manifest корректно.

Итог: чинить механизм не нужно. Тесты чинятся заменой
`_METRIKA_BAD_FIELDS` на любое поле из актуального
`PATCH_CANDIDATE_FIELDS` (например `ym:s:browser`) — отдельной задачей
(в скоуп этого аудита правка теста не входит). Это уточняет и закрывает
строку `не установлена` для обоих тестов в записи
`AUDIT-pre-existing-failures` (стр. 1561–1562): источник расхождения —
именно перевод `lastSignhasGCLID` в постоянно-неотправляемые поля
патчем 2A-patch, а не поломка негоциации. Blocker: нет.

---

**FIX-direct-feed-stale-docstring (2026-07-30).** Докстринг
`test_direct_feed_used_writes_parquet` в `tests/test_extract_smoke.py`
описывал отменённый вывод («feeds.get требует Ids явно», error 8000) —
`FIX-feeds-get-contradiction` (см. выше) уже установил обратное:
`SelectionCriteria` не указывается, чтобы получить все фиды аккаунта;
`Ids` обязателен только внутри `SelectionCriteria`, если он передан.
Докстринг переписан под фактическое поведение (пустой ответ feeds.get в
этом тесте — потому что у клиента нет фидов, а не ограничение API).
Логика теста не менялась. `pytest tests/test_extract_smoke.py -k
test_direct_feed_used_writes_parquet` — 1 passed. Blocker: нет.

---

**FIX-ad-texts-parquet-test (2026-07-30).** Закрывает гэп
`ad_texts.json vs .parquet`, зафиксированный в строке
`test_build_ad_texts_inline_logic_keeps_raw_intact_and_splits_correctly`
(стр. 1571, задача `AUDIT-pre-existing-failures`): тест и его докстринг
в `tests/test_transform_direct_normalize.py` ожидали, что инлайн-код
`build()` пишет `canonical/ad_texts.json` + `ad_texts_archived.json`, а
фактический код (`build_canonical.py`, задача 4F-ad-texts-parquet) давно
пишет `ad_texts.parquet`/`ad_texts_archived.parquet` с манифест-флагом
`{"active_count", "archived_count"}`. Переписаны оба теста в этом файле
под .parquet-контракт (чтение через `pd.read_parquet`, проверка
`active_df["ad_id"]`/`archived_df["ad_id"]` вместо парсинга JSON;
второй тест проверяет отсутствие `.parquet`-файлов вместо `.json`),
цель тестов (raw `ad_texts.json` не мутируется; State=="ON" -> active,
всё остальное включая отсутствие State -> archived) сохранена без
изменений. Обновлён и вводный докстринг файла (строки 1-14),
ссылавшийся на устаревший JSON-контракт. `pytest
tests/test_transform_direct_normalize.py` — 5 passed. Blocker: нет.

---

**FIX-gsc-webmaster-smoke-fixtures (2026-07-30).** Закрывает blocker из
`AUDIT-manual-export-contract-drift` (стр. 1781-1817): 5 фикстур в
`tests/test_extract_smoke.py` писали более старый контракт ручных
выгрузок, не связанный с текущим кодом `gsc_manual.py`/
`webmaster_manual.py`. `_write_gsc_manual` (плоский `gsc_YYYY-MM.csv` без
папки `YYYY-MM/`, `meta.yaml`/`total_clicks_ui`) заменён на
`_write_gsc_month` — пишет папку `YYYY-MM/` со срезовыми
`Диаграмма.csv`/`Запросы.csv`(/`Страницы.csv`), формат подтверждён
реальной выгрузкой `clients/pognali.rent/data/raw/gsc/2026-06/`.
`_write_wm_manual` (long-формат, файл на месяц) заменён на запись ОДНОГО
wide-файла `webmaster_export.csv` (`Query,Url,{YYYY-MM}_shows/_position/
_clicks`), формат подтверждён `clients/pognali.rent/data/raw/webmaster/
webmaster_export.csv`. Переписаны под фактическое поведение кода (не
угадывались):
  - `test_gsc_manual_validates_and_writes_same_contract` — комбинированный
    Запросы.csv (Page+Device в строке, contract 3A) -> `device_missing_months
    == []`, page/device в выходном CSV берутся из строки.
  - `test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat` — переписан
    под реальную сверку `_clicks_caveat()` (Диаграмма.csv vs Запросы.csv,
    caveat `clicks_diagram_vs_queries_mismatch`), а не несуществующий
    `meta.yaml: total_clicks_ui`.
  - `test_gsc_manual_missing_device_column_flags_month` — раздельный
    формат (Запросы.csv без Page/Device) требует `Страницы.csv` (иначе
    месяц целиком пропускается с `missing_required_files`); ассерт про
    manifest-заметку сделан регистронезависимым (`"Device"` в тексте, не
    `"device"`).
  - `test_webmaster_manual_aggregates_to_popular_contract` — переписан на
    один wide-файл; ассерты на `manual_no_page_breakdown_policy` и
    заметку «ограничение метода» убраны — этого поля/заметки в текущем
    `webmaster_manual.py` не существует (`has_page_column`/
    `page_device_breakdown` теперь захардкожены `True`, конфиг не
    читается).
  - `test_webmaster_manual_records_no_page_device_breakdown` — так как
    политика конфига в коде отсутствует, тест переименован по смыслу
    (тот же docstring-сценарий, что и в
    `docs/webmaster_export_instructions.md`: пустая колонка `Url` для всех
    строк) и теперь проверяет, что такие строки не отклоняются и
    агрегируются с `page=""`.
`gsc_manual.py`/`webmaster_manual.py`/`docs/gsc_export_instructions.md`/
`docs/webmaster_export_instructions.md` не менялись (вне
`allowed_files`). `pytest tests/test_extract_smoke.py -k
"gsc_manual or webmaster_manual"` — 7 passed (5 починенных + 2
`no_exports`, которые уже проходили). Полный `pytest
tests/test_extract_smoke.py` — 46 passed, 4 failed (те же
pre-existing `test_metrika_logs_negotiation_isolates_unsupported_fields`/
`test_metrika_logs_backfill_preserves_old_files`/
`test_wordstat_queue_cycle_writes_raw_and_manifest`/
`test_wordstat_dead_token_raises` — вне скоупа этой задачи, не
редактировались). Blocker: нет.

---

**AUDIT-cost-normalized-queries-geo-architecture (без правок кода) —
2026-07-30.** Разведка по конфликту, найденному задачей
`FIX-direct-queries-geo-cost-normalized` (та задача была остановлена до
правок — см. её отчёт выше): предполагала гэп в `transform`
(`build_canonical.py`), но там же на неё указывает явный, тестами
закреплённый контракт "null в transform, нормализация — в compute".
Проверено построчно, ничего не менялось.

**(1) Контракт "null в transform" — реальный, преднамеренный, установлен
задачей `4X-direct-normalize-2` (стр. 51):** докстринги
`build_direct_queries`/`build_direct_campaigns`/`build_direct_geo`
(`build_canonical.py`) дословно: `"cost_raw хранится как int64
микрорублей; cost_rub = float64 рублей (валютная конверсия, считается
всегда). cost_normalized = null и vat_basis_applied = False на этом
слое — НДС-нормализацию применяет compute после ответа на Q01"`.
Закреплён тремя проходящими тестами в `tests/test_build_canonical.py`
(стр. 461-547): `test_build_direct_queries_cost_rub_always_computed_cost_normalized_null`,
`test_build_direct_campaigns_cost_rub_always_computed_cost_normalized_null`,
`test_build_direct_geo_cost_rub_always_computed_cost_normalized_null` —
каждый явно требует `pd.isna(row["cost_normalized"])` и
`vat_basis_applied == False`, плюс комментарий над ними (стр. 461-468):
`"cost_normalized — НДС-нормализация; на слое transform всегда null...
их заполняет compute после Q01... Не путать с costs.parquet, где
cost_normalized/cost_status считаются уже здесь, в transform"`.

**(2) Факт-проверка compute-слоя: нормализация НЕ реализована нигде.**
`src/compute/block1.py` — единственный потребитель
`direct_queries.cost_normalized`/`direct_geo.cost_normalized`/
`direct_campaigns.cost_normalized`/`direct_placements.cost_normalized`
(A09-A11, A12, A15, A18-A26 и разрезы по дате/устройству/фразе/гео) —
везде читает `cost_normalized` через голый DuckDB
`SUM(cost_normalized) FILTER (WHERE cost_normalized IS NOT NULL)` прямо
из этих таблиц (строки 419-421, 903, 912, 981, 1050, 1156, 1451/1456,
1532/1539, 1620, 1664, 1710, 1772, 1854 и др.) — **нет ни одного вызова
`_vat_lookup`/`_apply_vat_to_rows`/любой другой функции, которая брала
бы `finance.vat_basis_by_source` и применяла его к `cost_raw`/`cost_rub`
этих четырёх таблиц.** `grep -n "vat_basis_by_source\|_vat_lookup\|
_apply_vat_to_rows\|vat_included" src/compute/block1.py` находит только
три упоминания — все в докстринге (строки 42, 93, 99), ни одного в теле
функций. Единственное место, где `_vat_lookup`/`_apply_vat_to_rows`
реально вызываются, — `build_costs` (`build_canonical.py`, задача 4B),
и это заполняет `costs.parquet`, а не отчётные Direct-таблицы; A02
единственная из A01-A26 читает деньги из `costs` (`FROM costs WHERE
source_tag = 'direct'`, строка 421) — там `cost_normalized` уже
настоящий (НДС-корректный). A09-A11 и остальные, что требуют
`direct_queries`/`direct_campaigns`/`direct_geo`/`direct_placements`,
получают гарантированно `NULL` на реальных данных клиента — сам
докстринг `block1.py` (строки 90-102) это признаёт открытым текстом:
`"Для direct_queries/direct_campaigns cost_normalized в текущем
состоянии пайплайна всегда null... Это значит, что на реальных данных
клиента прямо сейчас A09–A11 будут писать явную деградацию по деньгам,
пока эта отдельная задача не закрыта — это осознанное следствие
правила, а не баг данного модуля."`

**(3) Откуда взялась формулировка "заполняется в compute".** Она
появилась в задаче `4X-direct-normalize-2` (2026-07-22, стр. 51) —
на тот момент `src/compute/block1.py` был пустой заглушкой
(`raise NotImplementedError`, задача явно это констатирует: `"src/
compute/block1.py (и все прочие src/compute/block0..6.py) — пустые
заглушки... НИЧЕГО не читают ни из costs, ни из
direct_queries/campaigns/geo"`) — формулировка была декларацией
намерения на будущее, а не описанием существующего кода. Когда задача
`5D` (2026-07-28, стр. 847) реально реализовала `block1.py`, она
унаследовала эту формулировку в комментариях (стр. 90-99), но саму
нормализацию не добавила — она читает `cost_normalized` как готовое
поле, ожидая, что его кто-то заполнит, и открыто документирует
получающуюся деградацию как ожидаемую, а не как баг.

**Вердикт: гэп Q01 для direct_queries/direct_campaigns/direct_geo/
direct_placements реально ОТКРЫТ** — нормализация не реализована ни в
`transform`, ни в `compute`. Ранее в `AUDIT-pre-existing-failures`
(стр. 1559-1560) он был описан только со стороны двух упавших тестов
`test_direct_2b_patch.py::test_query_report_dimensions`/
`::test_geo_report_schema` (эти два теста, впрочем, ожидают СТАРУЮ,
дообновлённую 4X-direct-normalize-2 семантику — `cost_normalized ==
cost_raw/1_000_000`, то есть валютную конверсию без НДС, что само по
себе не тот же контракт, что "нормализация по vat_basis_by_source";
скорее всего эти два теста — сироты, забытые при переименовании
4X-direct-normalize-2, а не спецификация будущего поведения). Ссылка
`build_canonical.py:1131` в записи `AUDIT-pre-existing-failures` —
устаревший номер строки (файл с тех пор вырос), фактическое место —
докстринг `build_direct_queries` вокруг строки 1162-1169 на момент
этого аудита.

**Куда чинить, когда возьмутся за реализацию:** по контракту,
установленному `4X-direct-normalize-2` и тремя тестами
`test_build_canonical.py` выше, — **в `src/compute/block1.py`** (по
месту фактического использования `cost_normalized`), не в
`build_canonical.py`. Задача `FIX-direct-queries-geo-cost-normalized`
(предполагавшая правку в transform) закрыта неверной — правильная
версия этой задачи должна называть `src/compute/block1.py` (плюс,
возможно, общий helper для `direct_campaigns`/`direct_placements`,
которые несут тот же контракт и то же незакрытое ограничение, хотя
исходная задача называла только `direct_queries`/`direct_geo`) в
`allowed_files`, а не `src/transform/build_canonical.py`. Правка кода
и тестов не производилась (allowed_files этого аудита — только этот
файл). Blocker: нет, только маршрутизация следующей задачи.

---

**AUDIT-cost-normalized-formula-for-queries-geo (без правок кода) —
2026-07-30.** Повторный вопрос (какую формулу писать в `block1.py`:
полную НДС-нормализацию по `vat_basis_by_source` или чистую конвертацию
`cost_raw/1_000_000`) и что делать с 2 падающими тестами
`test_direct_2b_patch.py::test_query_report_dimensions`/
`::test_geo_report_schema`. Подтверждает вердикт
`AUDIT-cost-normalized-queries-geo-architecture` (стр. выше) с двумя
недостающими фактами, которые тот аудит не проверял напрямую.

**(1) Провенанс 2 тестов — по git-истории, не по предположению.**
`git log --oneline -- tests/test_direct_2b_patch.py` — один коммит
(`e18f88e`, 2026-07-20), тест не менялся с тех пор. Ломающее
переименование `cost_normalized` (валютная конверсия) →
`cost_rub`/`cost_normalized` (НДС-семантика, null до Q01) внесено
коммитом `d047032 "save before reset"` (2026-07-22 13:14) — тем самым
"защитным коммитом перед reset --hard", что описан в CLAUDE.md
(«Дисциплина параллельных сессий»); задача `4X-direct-normalize-2`
(запись выше, стр. 51) документирует именно эту правку и явно
признаёт тесты сломанными, но не трогает их (вне `allowed_files`).
Итог: оба теста написаны до переименования, для старой семантики
(`cost_normalized == cost_raw/1_000_000`), и с тех пор ни разу не
обновлялись — это сироты, а не спецификация целевого поведения.

**(2) Клиентские данные: `vat_basis_by_source` у pognali.rent.**
`clients/pognali.rent/inputs/client_answers.yaml: finance.vat_basis_by_source`
— три записи (`direct`, `seo`, "Яндекс Бизнес"), у всех
`vat_included: true`. База НДС одинакова по всем источникам на этом
клиенте — обе формулы дают числа, отличающиеся на один и тот же
постоянный множитель 1.2 (не ноль), но не создающие кросс-источникового
искажения (то, ради чего вообще существует D06). На будущем клиенте со
смешанными `vat_included` (часть true, часть false/null) две формулы
разойдутся уже структурно — правильная НДС-нормализация обязана
понижать/размечать по источнику (`cost_status`: gross/net/
vat_basis_unknown, как в `_apply_vat_to_rows` для `costs.parquet`), а
чистая конвертация эту разницу молча стирает.

**(3) Вердикт.** Формула для `block1.py` — полная НДС-нормализация по
`vat_basis_by_source` (та же логика, что `_vat_lookup`/
`_apply_vat_to_rows` в `build_costs`, применённая к `cost_rub` четырёх
Direct-таблиц), НЕ чистая конвертация. Основания: `data-export-spec-v2.md`
(строки 74-84, правило D06/D07 — явно "никогда не выводить
cost_normalized из cost_raw/cost_rub автоматической формулой без ответа
Q01"), каталог v2 D06 (тип A+Q, требует сверки по источнику, не одну
общую формулу), и уже закреплённый контракт `4X-direct-normalize-2`
(3 проходящих теста в `test_build_canonical.py`, ожидающие
`cost_normalized is null`/`vat_basis_applied=False` на слое transform).
`build_canonical.py` трогать не нужно — он уже корректен (подтверждено
предыдущим аудитом, п. 1-2 записи выше); недостающая часть —
реализация нормализации в `src/compute/block1.py`, которой сейчас нет
вообще (см. ту же запись, п. 2).

**(4) Что делать с 2 тестами.** Обновить, не оставлять как есть — они
закрепляют невалидный (дорелизный) контракт и будут маскировать
регресс, если кто-то по ошибке вернёт валютную конверсию в
`cost_normalized` на слое transform. `test_query_report_dimensions`/
`test_geo_report_schema` должны проверять `cost_rub ==
pytest.approx(N)` вместо `cost_normalized`, плюс
`row["cost_normalized"] is None` и `row["vat_basis_applied"] is False`
— по образцу трёх тестов
`test_build_direct_*_cost_rub_always_computed_cost_normalized_null` в
`test_build_canonical.py`. Правка кода и тестов не производилась
(allowed_files этой задачи — только этот файл). Blocker: нет.

**FIX-block1-cost-normalization — 2026-07-30.** Реализована отложенная
Q01-нормализация `cost_normalized` для `direct_queries`/`direct_geo`/
`direct_campaigns`/`direct_placements` в `src/compute/block1.py`, по
вердикту двух аудитов выше (полная НДС-формула по `vat_basis_by_source`,
не чистая конвертация; `build_canonical.py` не тронут).

**(1) Выбор варианта переиспользования — (а), прямой импорт.** `block1.py`
импортирует `_apply_vat_to_rows`/`_vat_lookup` напрямую из
`src/transform/build_canonical.py` — вторая независимая реализация
формулы не написана. Обоснование выбора (а) вместо (б, вынос в
`src/compute/common.py`/`src/shared/vat.py`): в `block1.py` уже есть
ровно такой же прецедент — импорт `is_brand_query` из того же
`build_canonical.py` (использован в A17/is_brand). Слои `transform`/
`compute` в CLAUDE.md неизменяемы по данным (`raw -> canonical ->
metrics -> ...`, каждый этап читает выход предыдущего), не по границам
импорта кода — межслойный импорт чистой функции без побочных эффектов
это правило не нарушает. `build_costs` (transform, вызывает
`_vat_lookup`/`_apply_vat_to_rows` для `costs.parquet`) не тронута.

**(2) Источник ответа на Q01 — `inputs/client_answers.yaml`, НЕ
`config.yaml`.** Побочная находка при чтении `build_canonical.build()`
(за пределами `allowed_files`, не исправлялась): `build()` берёт
`vat_basis_by_source` из `config.get("finance")`, а `config` там —
результат `load_client_config()` = только `config.yaml`; секции
`finance` нет ни у одного клиента (`clients/*/config.yaml`,
`clients/_template/config.yaml`) — она лежит в
`inputs/client_answers.yaml: finance.vat_basis_by_source`. Похоже,
`costs.parquet.cost_normalized` в реальном прогоне пайплайна тоже всегда
null (`vat_basis` пуст на этом пути) — отдельный, вне-scope этой задачи
гэп в transform (D06 в `block0.py` уже читает правильный источник —
`common.load_inputs(paths)["client_answers"]["finance"].
vat_basis_by_source`). `block1.py` реализован по образцу D06 (правильный
источник), не по образцу `build_canonical.build()` (сломанный источник) —
иначе моя нормализация тоже была бы недостижима на реальных данных.
Возможный отдельный тикет: `build_canonical.build()` должен брать
`vat_basis_by_source` из `inputs/client_answers.yaml`, а не из
`config.get("finance")`.

**(3) Реализация.** `_direct_vat_multiplier(paths)` — вызывает
`_apply_vat_to_rows` на пробной строке `{"source_tag": "direct",
"cost_raw": 1.0}` и возвращает получившийся `cost_normalized` как
множитель (1/1.2, 1.0 или `None`, если база НДС для `"direct"` не
указана). `_open_duckdb_with_direct_vat(paths, canonical)` — открывает
соединение через `common.open_duckdb` и, если множитель известен,
переопределяет view каждой из 4 таблиц (`CREATE OR REPLACE VIEW ... AS
SELECT * REPLACE (multiplier * cost_rub AS cost_normalized, true AS
vat_basis_applied) FROM read_parquet(...)`) — источник `read_parquet`
берётся из уже известного пути `canonical[table]`, а не из имени view,
чтобы не создать самоссылающееся определение. Один патч на уровне view,
а не правка ~15 SQL-запросов в A09-A15/A17-A19 по отдельности — при
`multiplier is None` view не трогается, `cost_normalized` остаётся null
(деградация, как раньше). Заменены вызовы `common.open_duckdb(paths)` на
`_open_duckdb_with_direct_vat(paths, canonical)` только там, где функция
реально агрегирует `cost_normalized` из этих 4 таблиц: A09, A10, A11,
A12, A13, A14, A15, A17, A18, A19 (проверено построчным гепом каждой
`_run_aXX`, не списком из промта — промт называл "A04-A11, A18-A20";
фактически A04-A08 берут деньги уже из нормализованного в transform
`costs.parquet` через `_campaign_costs`, а не из этих 4 таблиц, и A20 не
читает cost_normalized вовсе — обе группы в патче не нуждаются и его не
получили).

**(4) Тесты.** `tests/test_direct_2b_patch.py::test_query_report_dimensions`/
`::test_geo_report_schema` обновлены по плану п.4 аудита выше: assert на
`cost_rub`, плюс `pd.isna(cost_normalized)`/`vat_basis_applied == False`
на слое transform (не тронут). Новый файл
`tests/test_block1_direct_vat_normalization.py` (5 тестов) — фикстуры
пишут `direct_queries`/`direct_placements` в реальном контракте
transform (`cost_normalized=None`) плюс `inputs/client_answers.yaml`;
проверяют `cost_normalized_rub` при `vat_included=true` (÷1.2) и `false`
(без деления), что смена `vat_included` даёт кратно предсказуемый
результат (`net == gross * 1.2`), деградацию до null без
`client_answers.yaml`, и что подмена работает не только для
`direct_queries` (A15/`direct_placements`). `pytest tests/` — 844
passed, 4 failed; все 4 падения в `tests/test_extract_smoke.py`
(wordstat/metrika_logs backfill) — не затронутые этой задачей модули,
воспроизводятся и без правок (`git status` показывает эти файлы уже
модифицированными до начала задачи, вне `allowed_files`). Blocker: нет.

**AUDIT-vat-basis-source-path-critical — 2026-07-30.** Подтверждено:
`build_costs()`/`build()` в `build_canonical.py` читают
`vat_basis_by_source` из `config.get("finance")` (`config` = только
`config.yaml`, см. `load_client_config`) — секции `finance` нет ни у
одного клиента, только в `inputs/client_answers.yaml` (данные Q01
реально заполнены у pognali.rent: direct/seo/Яндекс Бизнес — все
`vat_included: true`). `build()` вообще не принимает параметр `inputs`.
Прямое доказательство на реальных данных: `costs.parquet` (1377 строк) —
100% `cost_status="vat_basis_unknown"`, `cost_normalized` NULL везде;
нормализация не применялась ни разу. Тесты `test_costs_vat_*` в
`test_build_canonical.py` не ловят баг, т.к. сами кладут
`vat_basis_by_source` в `config["finance"]` (тот же ошибочный путь, что
и прод) — маскировка контракта, а не проверка его. Ранее уже
зафиксировано как побочная находка в `FIX-block1-cost-normalization`
(этот файл, п.2) — `block1.py` реализован по образцу `block0.py` D06
(правильный источник — `common.load_inputs()["client_answers"]`), в обход
`build_canonical.build()`, поэтому патч A09/A10/A11/A12/A13/A14/A15/
A17/A18/A19 (via `direct_queries`/`direct_geo`/`direct_campaigns`/
`direct_placements`) не унаследовал баг. Однако A04/A05/A06/A08 берут
деньги через `_campaign_costs()` → `costs.parquet.cost_normalized`
напрямую (не через патч `_open_duckdb_with_direct_vat`) — при 100% NULL
`_money()` возвращает None для каждой группы кампаний, что даёт "нет
данных"/degraded, а не неверные числа. `money_frame.py` агрегирует
`cost_normalized_rub` из JSON A-проверок — наследует null транзитивно
везде, где к нему стекаются A04-A08.

Отдельная находка: D06/D07 (единственные проверки, которые должны были
поймать ровно это расхождение — `answer_not_applied` при
`expected_cost_status != actual_cost_status`) в реальном прогоне
pognali.rent в `degradation_report.json` помечены `skipped`, причина
`missing: ["client_answers"]` — деградация посчитала источник
`client_answers` недоступным, хотя файл `inputs/client_answers.yaml` с
заполненным Q01 на диске существует. Похоже на устаревший прогон
compute/degradation, сделанный до заполнения Q01-Q07 аналитиком, без
повторного прогона после — не расследовано в рамках этой задачи (вне
`files_to_read`). Также замечено (не расследовано): `source` в Q01
client_answers.yaml — `"seo"`/`"Яндекс Бизнес"`, а `source_tag` в
costs.parquet — `"seo_fee"`/`"yandex_business"` (см.
`_VALID_COST_SOURCE_TAGS`); при прямом сопоставлении по строке эти
записи не совпадут даже после починки основного пути — отдельный риск.

Диагноз: баг подтверждён, ничего не исправлялось (задача диагностическая).
Задетые места: `costs.parquet.cost_normalized/cost_status` (все клиенты,
все прошлые прогоны), A04/A05/A06/A08, всё, что суммирует их
`cost_normalized_rub` в `money_frame.py`, и любой уже собранный `report`,
опирающийся на эти проверки. Возможный тикет на будущее (не в этой
задаче): `build_canonical.build()` должен читать `vat_basis_by_source`
из `inputs/client_answers.yaml` тем же путём, что уже использует D06 в
`block0.py`; заодно свести словарь `source_tag`/`source` к одному имени
между Q01 и costs.parquet. Blocker: нет (диагностика завершена).

**AUDIT-block0-client-answers-wiring-and-source-tag-mismatch — 2026-07-30.**
Два независимых диагноза перед `FIX-vat-basis-wiring`, не чинилось.

**Вопрос 1 — почему D06/D07 skipped.** Причина — **(б), баг того же
класса, что и `build()`, но в другом месте**, НЕ (а) порядок операций.
`_run_d06`/`_run_d07` (`block0.py`) сами по себе корректны — читают
`common.load_inputs(paths)["client_answers"]`, и `load_inputs()`
(`common.py:67-81`) корректно резолвит `inputs/*.yaml` по имени файла
без расширения (`client_answers.yaml` -> ключ `"client_answers"`) —
путь совпадает с реальным
`clients/pognali.rent/inputs/client_answers.yaml`. Проблема — ДО этого,
в гейте `run()` (`block0.py:815,819`: `if "D06" in runnable_ids and
"costs" in canonical`). `runnable_ids` строится в
`degradation.build_degradation_report()` из `available_tables_from_manifest(manifest)`
(`degradation.py:218-236`), а `manifest`, который получает эта функция —
**`data/raw/manifest.json`** (пишет `orchestrator.run_compute()`,
`orchestrator.py:496`: `manifest = manifest_mod.load_manifest(paths.raw)`),
т.е. манифест extract-стадии по API-источникам, а не что-либо, что
смотрит в `inputs/`. `available_tables_from_manifest()` берёт таблицы
из `manifest["sources"][*]["canonical_tables"]` плюс
`manifest.get("input_tables", [])` — и **ничто в кодовой базе никогда
не пишет `input_tables`**: `grep -rn "input_tables" src/` даёт только
чтение в `degradation.py:234` и дефолт `[]` в `manifest.py:37`
(`load_manifest` при отсутствии файла). Реальный
`clients/pognali.rent/data/raw/manifest.json` подтверждает:
`"input_tables": []`, `sources` содержит только `metrika_reports,
metrika_logs, wordstat, direct, gsc, site_crawl, webmaster, crux` —
`client_answers` там в принципе не может появиться. `requires:
[costs, client_answers]` у D06/D07 (`config/methodology.yaml:127,138`)
поэтому не выполняется никогда, независимо от содержимого
`client_answers.yaml`. Точный `reason` из
`clients/pognali.rent/data/metrics/degradation_report.json` (запись
skipped): `"missing": ["client_answers"], "reason": "нет источника:
анкета клиента (inputs/client_answers.yaml)"`.
Даты файлов: `client_answers.yaml` изменён 2026-07-30 14:35,
`degradation_report.json` — 2026-07-29 14:01 (старее), `data/raw/manifest.json`
— 2026-07-22 20:28. Порядок дат сам по себе выглядит как версия (а)
("прогон был до анкеты, нужен новый прогон") — но это **ложное
впечатление**: повторный прогон `compute` прямо сейчас использовал бы
тот же `data/raw/manifest.json` от 2026-07-22 (extract не перезапускался)
и тот же путь `available_tables_from_manifest()` без `input_tables` —
результат не изменится. Заметка о `_MANUAL_TABLES`
(`degradation.py:74-76`, содержит `client_answers`/`webvisor_findings`/
`crm`/`manual_serp`) и `_SOURCE_LABELS` (строка 52) показывает, что
доступность этих источников по дизайну ДОЛЖНА определяться — но
механизм (запись в `input_tables` по факту наличия файла в `inputs/`)
нигде не реализован. Затронуты не только D06/D07: любая будущая
проверка с `requires` на `client_answers`/`webvisor_findings`/`crm`/
`manual_serp` будет skipped всегда, на любом клиенте.

**Вопрос 2 — совпадение source_tag.** `_vat_lookup()`
(`build_canonical.py:477-491`) сопоставляет по точной строке после
`.strip()` — без `.lower()`, без транслитерации/маппинга
кириллица→snake_case:

| source (Q01, `client_answers.yaml`) | source_tag (costs.parquet) | совпадёт как есть |
|---|---|---|
| `"direct"` | `"direct"` (TSV Директа, `build_costs` хардкодит `source_tag="direct"`) | **да** |
| `"seo"` | `"seo_fee"` (из `costs_manual.seo_fee_rub_month`) | **нет** |
| `"Яндекс Бизнес"` | `"yandex_business"` (из `costs_manual.other[].source_tag`, см. `_VALID_COST_SOURCE_TAGS`) | **нет** |

Подтверждено: даже после починки пути `config` → `inputs` в `build()`,
НДС применится только к `source_tag="direct"` (единственное точное
совпадение) — `"seo"`/`"Яндекс Бизнес"` останутся
`vat_basis_unknown` из-за несовпадения имён, а не из-за отсутствия
ответа. На практике сейчас это не проявляется на реальных данных:
`costs.parquet` pognali.rent содержит только `source_tag="direct"`
(1377/1377 строк) — `costs_manual` для этого клиента сейчас не задан,
поэтому строк `seo_fee`/`yandex_business`/`agency_fee` в данных нет;
риск станет видимым, как только у любого клиента появятся
`costs_manual`-фиксы с этими тегами.

Диагноз завершён, правок не вносилось. Blocker: нет.

**AUDIT-input-tables-blast-radius — 2026-07-30.** Полный периметр
check_id, чей `requires` в `config/methodology.yaml` ссылается на
источник input-категории, и которые физически не могли стать
`runnable=true` ни разу ни на одном клиенте — подтверждено по
`clients/pognali.rent/data/metrics/degradation_report.json` реального
прогона (все 12 ниже — в `skipped`, ни один не в `runnable_check_ids`).
Правок не вносилось.

Из четырёх токенов в `_MANUAL_TABLES`/`_SOURCE_LABELS`
(`degradation.py:74-76,43-56`: `client_answers`, `webvisor_findings`,
`crm`, `manual_serp`) реестр `methodology.yaml` фактически ссылается
(`requires`/`optional`) только на `client_answers` и `webvisor_findings`
— `crm` и `manual_serp` не встречаются ни в одном `requires`/`optional`
ни одной из 100 проверок (мёртвые записи в справочнике деградации,
зарезервированы на будущее). `manual_serp` в S25 (задача 5bC) — это
только внутреннее поле результата
(`manual_serp_check_required=true`), не гейт деградации — S25 не
затронут.

| check_id | requires-источник | reason из реального прогона | ранее записано как «работает»/«готово»? |
|---|---|---|---|
| D06 | client_answers | нет источника: анкета клиента (inputs/client_answers.yaml) | **да** — 5B (эта же папка, п. «D01/D04/D05/D06 гейтятся штатно») |
| D07 | client_answers | нет источника: анкета клиента (inputs/client_answers.yaml) | **да** — 5C («Все шесть проверок работают строго в пределах requires») |
| T06 | client_answers | нет источника: анкета клиента (inputs/client_answers.yaml) | **да** — 5F, описан как рабочий агрегат без оговорки о недостижимости гейта |
| C03 | site_crawl | нет источника: обход сайта (ручная техническая проверка) | нет — 5G п.(2) явно документирует разрыв `site_crawl.py:CANONICAL_TABLES=["pages"]` vs реальной `site_pages` |
| C08 | site_crawl | (то же) | нет — тот же пункт 5G |
| C11 | site_crawl | (то же) | нет — тот же пункт 5G |
| C14 | site_crawl | (то же) | нет — 5H наследует формулировку 5G (не переформулирована явно для C14, но ссылается на тот же паттерн) |
| C17 | site_crawl | (то же) | нет — 5H, тот же паттерн |
| C23 | site_crawl | (то же) | нет — 5H, тот же паттерн |
| S15 | site_crawl | (то же) | нет — 5bB прямо цитирует разрыв («тот же приём, что C03/C08/C11/C14/C17... эти ID никогда не станут runnable») |
| S18 | site_crawl | (то же) | нет — тот же пункт 5bB |
| S19 | site_crawl | (то же) | нет — тот же пункт 5bB |

Итог: **3 из 12** (D06, D07, T06) — единственные, ранее задним числом
описанные как работающие/гейтящиеся штатно без указания, что
`requires: [client_answers]` структурно недостижим (см.
`AUDIT-input-tables-blast-radius` выше — `input_tables` в
`data/raw/manifest.json` нигде не заполняется). **9 из 12**
(C03/C08/C11/C14/C17/C23/S15/S18/S19, все — `requires: [site_crawl]`)
— наоборот, уже на момент реализации (5G/5H/5bB) явно и корректно
задокументированы как «никогда не станет runnable через автоматическую
деградацию» из-за отдельного, независимого несовпадения имён
(`site_crawl.py: CANONICAL_TABLES=["pages"]` против канонической
таблицы `site_pages`) — это НЕ то же расхождение, что баг
`input_tables`, а третий, самостоятельный источник того же класса
поломки (имя в `requires` не совпадает с тем, что реально попадает в
`available_set`).

Не гейтятся токенами input-категории и корректно работают в обход
`requires`/`optional` (проверено по `runnable_check_ids` — все три
runnable): **C13, C24** (5H, `requires=[visits]`, `client_answers`
читается в теле проверки напрямую, минуя degradation) и **T09** (5F,
`requires=[visits]`, `client_answers` — необязательное обогащение).
D03 skipped, но по независимой причине (`requires: [goals]`, `goals`
реально недоступен для pognali.rent) — не связано с этим аудитом.

Диагноз завершён, правок не вносилось. Blocker: нет.

**FIX-input-tables-manifest-gate — 2026-07-30.** Закрыт баг из
`AUDIT-input-tables-blast-radius` выше: `manifest["input_tables"]` теперь
заполняется в `run_extract` (`src/pipeline/orchestrator.py`,
`_detect_input_tables` + `manifest_mod.update_global`) на основании
фактического наличия непустого `inputs/client_answers.yaml` клиента —
`degradation.available_tables_from_manifest` (не менялся, уже читал это поле
корректно) теперь реально видит `client_answers` как доступный источник.
Поле перезаписывается на каждом прогоне extract целиком (идемпотентность,
принцип 2) — включая пустой список, если анкета отсутствует/пуста, не
только когда она заполнена. Расширение на `manual_form_tests`/
`webvisor_findings` сознательно не сделано — вне скоупа задачи, отдельный
вопрос.

Интеграционный тест (не юнит на функцию — тот же урок, что и с VAT-багом):
`tests/test_orchestrator_input_tables_gate.py`, через реальный
`run_extract` -> `run_compute` -> `degradation_report.json`. Подтверждено:
без анкеты D06/D07/T06 остаются skipped с честной причиной
(`missing: [client_answers]`); с заполненной `client_answers.yaml`
(и заранее выгруженным `costs` для D06/D07) все три становятся
`runnable=true`.

Попутно обнаружено: `tests/test_crux.py` использует минимальный дублёр
`ClientPaths` без атрибута `.inputs` для прогона `run_extract` напрямую —
`_detect_input_tables` сделан устойчивым к этому через `getattr(paths,
"inputs", None)`, чтобы не расширять скоуп правкой файла вне
`allowed_files`. Полный `pytest` прогнан: 4 предсуществующих падения в
`tests/test_extract_smoke.py` (metrika_logs negotiation/backfill, wordstat
queue/dead-token) не связаны с этой задачей — воспроизводятся и без правки
(отдельные незакоммиченные изменения в репозитории, вне `allowed_files`).

**AUDIT-c14-requires-decision — 2026-07-30.** Вопрос: должен ли C14 требовать
`visits`, как остальные проверки группы A+G2 матрицы `data-export-spec-v2.md`
строка 212 (`C12–C16, C18, C19, C25 | A (поведение) + G2 (ручной аудит)`), или
`requires: [site_crawl]`, реализованный в 5H, — осознанное решение.

Подтверждено: **осознанное решение, не упущение.** Источники:

- Каталог v2 (§8, приоритет "a" по CLAUDE.md п.5) типизирует C14 как **тип
  "B"** (полностью ручная, 4/5), а не "A+B" — той же строкой, что C03/C08/C11
  /C17/C23 (тоже "B"). C15/C16/C18/C25, напротив, типизированы в каталоге v2
  как **"A+B"**. То есть сам каталог v2 разводит C14 и группу C15/C16/C18/C25
  по типу проверяемости ещё до матрицы data-export-spec.
- `src/compute/block3.py:1198-1200` (докстринг `_run_c14`) прямо фиксирует
  причину: "Тип B (полностью ручная, как C03/C08/C11), плюс optional
  webvisor_findings — единственная из manual-only проверок 5H с обогащением".
- Запись 5H (выше, п. «C14/C17/C23 — полностью ручные (тип B, как
  C03/C08/C11), гейт site_pages в canonical») явно объясняет выбор: C14
  отнесена к той же группе, что C03/C08/C11 (5G) и C17/C23 (5H), а не к группе
  C15/C16/C18/C25 (тоже 5H, `_run_manual_form_tests_fallback`, разрыв (9) —
  CTA/попапы/классификация страниц не хранятся ни в `site_pages`, ни в
  `visits`).
- Матрица `data-export-spec-v2.md:212` — источник приоритета "b" (поля/
  контракты выгрузки) — группирует C14 вместе с C15/C16/C18/C19/C25 под
  общим "A (поведение) + G2"; это огрублённая сводная группировка на уровне
  диапазона ID, которая расходится с более точной построковой типизацией
  каталога v2. По приоритету CLAUDE.md п.5 (a > b) для вопроса типа
  проверяемости (`type_default`) конкретного ID каталог v2 старше матрицы —
  реализация 5H следует каталогу, что и задокументировано в самой записи 5H
  (не задним числом).

Расхождение между строкой 212 матрицы и типом C14 в каталоге v2 — реальный,
но уже названный и разрешённый конфликт источников, а не новый. Правок в
`config/methodology.yaml` или `src/compute/block3.py` не требуется и не
вносилось. Diagnostic завершён, вопрос закрыт. Blocker: нет.
Blocker: нет.

**FIX-input-tables-manifest-gate (расширенная версия) — 2026-07-30.** Закрыт
второй, самостоятельный источник недостижимости `runnable=true` из
`AUDIT-input-tables-blast-radius`: для C03/C08/C11/C17/C23 (`requires:
[site_crawl]`) — несовпадение имени `site_crawl.py: CANONICAL_TABLES=
["pages"]` vs реальной канонической таблицы `site_pages`
(`AUDIT-c-checks-required-source-mismatch`, она же разрыв (2)/(3) в докстринге
`src/compute/block3.py`). Это отдельный баг от того, что чинила первая версия
задачи (там — незаполнение `manifest["input_tables"]` для `client_answers`,
здесь — сам `requires`-токен указывал на источник, для которого деградация
структурно никогда не соберёт `available`).

Правки:
- `config/methodology.yaml`: C03, C08, C11, C17, C23 — `requires: [site_crawl]`
  -> `requires: [manual_form_tests]`. `optional` (webvisor_findings у C03/C08,
  visits у C23) не менялся. C14 сознательно НЕ тронут — отдельный вопрос,
  предварительно закрытый выше в `AUDIT-c14-requires-decision` (там речь про
  выбор между `visits` и `site_crawl`, не про `manual_form_tests`); менять его
  requires в эту задачу не входило.
- `src/pipeline/orchestrator.py`: `INPUT_TABLE_FILES` расширен с
  `client_answers` на `manual_form_tests` -> `manual_form_tests.yaml`.
  `webvisor_findings`/`crm`/`manual_serp` сознательно не добавлены — ни один
  `requires` на них не ссылается (только `optional`, которое runnable не
  гейтит), а `crm`/`manual_serp` — мёртвые записи справочника по итогам
  `AUDIT-input-tables-blast-radius`.
- `src/pipeline/degradation.py`: `manual_form_tests` добавлен в `_MANUAL_TABLES`
  и `_SOURCE_LABELS`. Без этого `table_source_modes()` трактовал бы
  `manual_form_tests` как `api` по умолчанию, и `confidence_cap` в
  `degradation_report.json` для этих пяти проверок остался бы `HIGH` вместо
  `MED` — расхождение с тем, что сами функции `block3.py` (`_manual_pattern_rows`
  /`_manual_conclusions_rows`) и так жёстко капают строки до `MED`. Это не было
  явно перечислено в списке «Действия» промта, но необходимо для того, чтобы
  сам факт нового `requires`-токена был корректно классифицирован как
  manual-источник — иначе `degradation_report.json` содержал бы неверный
  `confidence_cap` на уровне проверки при корректных строках находок.
- `src/compute/block3.py`: диспетчерское условие для C03/C08/C11/C17/C23
  изменено с `"C0X" in runnable_ids and "site_pages" in canonical` на
  `"C0X" in runnable_ids` — тот же приём, что уже применялся к C01/C02 (crux)
  и T06 (client_answers). Тела `_run_manual_only_check`/`_run_c14` не менялись.
  Обновлены докстринг модуля (заголовочная таблица проверок, разрыв (2)) и
  докстринг `_run_manual_only_check` — они больше не описывают `site_pages`
  как инфраструктурную предпосылку для этих пяти ID.

Тесты (интеграционные через реальный orchestrator, тот же урок, что с
VAT-багом и в первой версии задачи):
`tests/test_orchestrator_input_tables_gate.py` — новый сценарий
`test_gate_opens_when_manual_form_tests_filled_without_site_crawl`: без
единого source с `canonical_tables=["site_pages"]`/`["pages"]` в манифесте
C03/C08/C11/C17/C23 становятся `runnable=true` от одного заполненного
`inputs/manual_form_tests.yaml`; `test_gate_stays_closed_without_manual_form_tests`
подтверждает честную причину `missing: [manual_form_tests]` при пустом файле.
`tests/test_block3.py`: `test_c03_c08_c11_c17_c23_run_without_site_crawl` —
все пять пишут артефакты без `site_pages` в canonical; переписаны (не
удалены) `test_c11_without_site_pages_not_dispatched` и
`test_c17_without_site_pages_not_dispatched`, которые раньше фиксировали
именно баг (проверка НЕ считается без site_pages) как ожидаемое поведение —
явно заданное ломающее изменение контракта (CLAUDE.md, протокол микрозадач
п.9). `tests/test_degradation.py`:
`test_manual_form_tests_required_caps_confidence_at_med` — прямая проверка
`_MANUAL_TABLES`-правки.

Прогон: `pytest tests/test_degradation.py tests/test_orchestrator_input_tables_gate.py
tests/test_block3.py tests/test_methodology_goals_requires.py` — 57 passed.
Полный `pytest tests/` — 850 passed, те же 4 предсуществующих падения в
`tests/test_extract_smoke.py` (metrika_logs negotiation/backfill, wordstat
queue/dead-token), что и в первой версии задачи — не связаны, вне
`allowed_files`. Blocker: нет.

---

## task FIX-site-crawl-canonical-tables-rename (2026-07-30)

`requires: [site_crawl]` — токен имени extract-источника, а не канонической
таблицы; `available_tables_from_manifest` собирает множество из
`canonical_tables` каждого источника, а `CANONICAL_TABLES` в
`src/extract/site_crawl.py` был `["pages"]` — ни разу не совпадал ни с
`"site_crawl"` (в methodology.yaml), ни с реальными именами канонических
таблиц, которые пишет `build_canonical.py` (`site_pages`, `site_link_graph`).
S15/S18/S19 были невыполнимы (`runnable=false`) при любом манифесте.

Полный `grep "requires:.*site_crawl"` по methodology.yaml нашёл 4 вхождения,
не 3: S15, S18, S19 и C14. C04/C05/C24/S11/S12/S13/S16/S17/S27 из матрицы
G1 data-export-spec-v2.md используют site_crawl только через `optional`
(S16, S17) либо не используют вовсе (C04/C05/C24 — `requires: [visits]`;
S11/S12/S13/S27 — `requires: [seo_queries]`) — не трогались, `requires: [site_crawl]`
у них и не было.

Действия:
- `src/extract/site_crawl.py`: `CANONICAL_TABLES` → `["site_pages", "site_link_graph"]`
  (было `["pages"]` — не совпадало ни с чем).
- `config/methodology.yaml`: `requires: [site_crawl]` →
  для S15 — `[site_pages]` (redirect_chain/final_url — поля site_pages);
  для S18, S19 — `[site_link_graph]` (внутренний граф ссылок, глубина от
  главной, страницы-сироты — по data-export-spec-v2.md, §G1-матрица:
  «Внутренний граф ссылок... — S18, S19», отдельно от «Цепочки редиректов...
  — C05, S15»). Отклонение от буквального текста промта («requires:
  [site_pages] для каждого») — обоснование: source-of-truth
  data-export-spec-v2.md разводит эти два ID по разным таблицам; S15 не
  использует граф, S18/S19 не используют redirect_chain напрямую.
- C03/C08/C11/C17/C23 (`manual_form_tests`) и C14 (`requires: [site_crawl]`,
  осознанно оставлена отдельным аудитом) — не тронуты, регрессии нет
  (подтверждено grep + regression-тестом).
- `optional: [site_crawl]` у S16/S17/C15 (та же проблема токена, но у
  `optional`, не `requires`) — вне скоупа задачи, не тронуто, зафиксировано
  здесь как известный смежный дефект для отдельной задачи.

Тесты: новый `tests/test_site_crawl_canonical_requires.py` — S15 требует
`site_pages`, S18/S19 требуют `site_link_graph`; S15/S18/S19 становятся
`runnable=true` при `available={"site_pages", "site_link_graph"}` и
`runnable=false` при пустом `available`; регрессия — C03/C08/C11/C17/C23
всё ещё на `manual_form_tests`, C14 всё ещё на `[site_crawl]`.

Прогон: `pytest tests/test_site_crawl_canonical_requires.py
tests/test_methodology_goals_requires.py tests/test_site_crawl.py
tests/test_site_crawl_pages.py tests/test_site_crawl_bfs.py` — 125 passed.
`pytest tests/ -k "methodology or degradation"` (без scipy-сломанных
`test_block1*.py`/`test_block3.py`, вне `allowed_files`, предсуществующая
проблема окружения) — 28 passed. Blocker: нет.

---

## task VERIFY-site-link-graph-table-exists (2026-07-30)

Только факт, без изменений кода. `site_link_graph` — реально строящаяся
таблица, не фантомное второе имя по образцу прежнего `["pages"]`.

Подтверждено: `build_site_link_graph()` (`src/transform/build_canonical.py:1668`)
читает `data/raw/site_crawl/link_graph.parquet`, дедуплицирует, вызывается
в `run_transform` (`build_canonical.py:2140-2143`), пишет
`canonical_dir/site_link_graph.parquet`, таблица есть в `SCHEMAS`.
`src/extract/site_crawl.py` реально собирает граф на extract-этапе (BFS по
внутренним ссылкам → `link_graph.parquet`, `site_crawl.py:1057-1058`).
`data-export-spec-v2.md` §G1 (строка 150) даёт требование «Внутренний граф
ссылок... — S18, S19», но буквального имени `site_link_graph` в спеке нет —
имя таблицы взято из кода, а не процитировано из спеки; это не расхождение,
просто источник имени. Физически `clients/pognali.rent/data/canonical/
site_link_graph.parquet` существует (21771 байт, прогон 2026-07-23,
присутствует в `manifest.json → tables`). `site_link_graph` — часть
исправления задачи `FIX-site-crawl-canonical-tables-rename` (см. выше), не
повтор прежнего бага. S18/S19 разблокированы под этим именем реально, не
только на бумаге. Blocker: нет.

---

## task CHECKPOINT-full-pipeline-e2e (2026-07-30)

Сквозная проверка всего конвейера на pognali.rent после серии P0/P1/P2 задач.
Диагностика и живой прогон на боевых данных клиента (не scratch-копия —
идемпотентная перезапись собственного слоя каждой стадией допустима
принципом 2, а часть цели проверки — подтвердить факт работы на реальных
данных, не на фикстурах).

### Часть 1 — регрессия

`pytest tests/ -q --continue-on-collection-errors` — **4 failed, 862 passed**.
Совпадает с задокументированным baseline (`AUDIT-pre-existing-failures` +
все P2-фиксы этой сессии): `tests/test_extract_smoke.py::
test_metrika_logs_negotiation_isolates_unsupported_fields`,
`::test_metrika_logs_backfill_preserves_old_files`,
`::test_wordstat_queue_cycle_writes_raw_and_manifest`,
`::test_wordstat_dead_token_raises`. `test_direct_2b_patch.py` ×2 из
прежнего baseline теперь зелёные (`FIX-block1-cost-normalization` уже
прогнан ранее). Новых незадокументированных падений нет.

### Часть 2 — живой прогон

**intake** — OK. Предупреждение `data_window не содержит поля mode`
ожидаемо (флат-формат `clients/pognali.rent/config.yaml`, обратная
совместимость по CLAUDE.md).

**extract** — при первом прогоне **упал целиком** (не деградация одного
источника, а падение всей стадии — именно то, что чек-лист требовал
расследовать отдельно, не списывать на «предсуществующее»). Корневая
причина: `StageLogger.__call__` (`src/pipeline/orchestrator.py:93-97`)
вызывает голый `print(message)`; консоль этой сессии — кодовая страница
`cp1251` (по умолчанию для RU-локали Windows), а `webmaster_manual.py:115`
логирует символ `×` (U+00D7, «уникальных пар query×page»), которого нет в
cp1251 → `UnicodeEncodeError`, необработанный, роняет весь `run_extract`
после того, как `webmaster_manual` уже успешно посчитал данные (лог
успевает написать «файлов 421, страниц 18, demand=True» и падает на
следующей строке). Файл лога (`open(..., encoding="utf-8")`) от бага не
страдает — ловит его только консольный `print`. Затронуты все источники
после webmaster в порядке диспетчеризации (gsc/wordstat/crux/crm/
site_crawl ни разу не были вызваны в упавшем прогоне). Не исправлено —
`src/pipeline/orchestrator.py` вне `allowed_files` этой задачи; для этого
прогона использован обходной путь без правки кода:
`PYTHONIOENCODING=utf-8 PYTHONUTF8=1` перед `python run.py`. Реальный фикс
(`StageLogger.__call__` → `print(message, file=sys.stdout)` с явной
UTF-8-обёрткой или `errors="replace"`) — отдельная задача, не входила в
`allowed_files` чекпоинта. Blocker для документации, не для прогона.

С обходным путём — **extract прошёл полностью**: `выгружено 9, недоступно
0, пропущено 0` (metrika_reports 148 строк, metrika_logs 34227 строк
(пропуск повторной выгрузки — patch_date уже закрыт), direct 1407 строк
(запросы — 0 строк, `SEARCH_QUERY_PERFORMANCE_REPORT` реально вернул
данные только за один день 2026-01-31 при окне 2025-04-07..2026-06-30 —
не расследовано, вне скоупа этой задачи, из-за чего `direct_queries.parquet`
в этом прогоне не перезаписан и в canonical/ остался устаревший файл от
2026-07-23 — не путать с актуальными таблицами, отдельный вопрос на
будущее), webmaster_manual 421, gsc_manual 6813, wordstat 2405 (37 фраз,
40 вызовов API), crux 3 (cwv_field_data_available=true), crm_import 1357,
site_crawl 21 страница/2989 рёбер графа — с ожидаемым частичным
вырождением BFS по не-HTML/таймаутящимся ассетам (`skipped_non_html`/
`ReadTimeout` на картинках — не источник целиком, только отдельные URL,
это управляемая деградация принципа 4, не падение). Автосверка Logs↔Reports
— **OK, 30/0/0** (все 15 месяцев по визитам и охвату <0.5% расхождения).

Проверка манифест-флагов из промта — все подтверждены фактом прогона:
- `input_tables: ["client_answers", "manual_form_tests"]` — фикс
  `FIX-input-tables-manifest-gate` реально работает на боевых данных, не
  только в тестах.
- `sources.crm.crm_attribution_reliable: false` (не top-level поле, как
  буквально сформулировано в промте, а вложенное в `sources.crm` —
  расхождение с формулировкой промта, не с фактом; значение и текст
  причины совпадают с `config.yaml: crm_csv.attribution_unreliable_reason`).
- `sources.direct.ad_extensions_price_fields_available: false`,
  `ad_extensions_caveat.affected_checks: ["A24"]` — подтверждено.
- `sources.site_crawl.source_caveats` — присутствует ровно там, где
  кандидатов >20: `top_organic_webmaster` (26 кандидатов, оставлено 20,
  отброшено 6).

**transform** — OK. `построено 14 таблиц`: visits, goals, costs,
direct_campaigns, direct_geo, direct_placements, campaign_strategies,
ad_texts, ad_texts_archived, seo_queries, **wordstat**, crm, site_pages,
site_link_graph. `wordstat.parquet` подтверждён физически (20828 байт,
свежий таймстамп прогона).

**compute** — при первом прогоне **block4 (SEO, S01-S27) молча не
считался**: `block4: not_implemented` в выводе стадии, ни одного `s*.json`
в `data/metrics/` не появилось — не «unavailable по всем строкам», как
предполагал промт, а полное отсутствие расчёта, ни разу, ни в одном
реальном прогоне. Корневая причина: `BLOCK_MODULE_NAMES`
(`src/compute/common.py:229-232`) диспетчеризует модуль `src.compute.
block4` — это заглушка **старой** нумерации методологии (докстринг «Блок
4 — атрибуция», проверки 4.1/4.2, `raise NotImplementedError`,
`src/compute/block4.py`), а не `src/compute/block4_seo.py`, где реально
реализованы S01-S27 (задачи 5bA/5bB/5bC и все последующие S-фиксы этой
сессии — `AUDIT-s07-s26-formula-match`, `FIX-site-crawl-canonical-tables-
rename` и т.д. — были протестированы юнит-тестами напрямую, но никогда не
проходили через реальный `dispatch_blocks`). Файл `block4.py` — мёртвый
код той же природы, что `write_ad_texts_archive` в `AUDIT-input-tables-
blast-radius`, только с более серьёзным следствием: он не просто не
вызывается — он **вызывается вместо** правильного модуля и глотает
ошибку через `except NotImplementedError`, поэтому баг ничем не сигналил
о себе, кроме статуса в логе стадии, который никто не читал построчно.

Правка (по решению аналитика — вне `allowed_files`, подтверждено явно
перед внесением): `src/compute/common.py` — `BLOCK_MODULE_NAMES`:
`"block4"` → `"block4_seo"`. Однострочная правка, `block4.py` не удалён
(вне скоупа, отдельный вопрос — мёртвый файл старой нумерации, может
пригодиться для будущей атрибуции 4.1/4.2 или подлежит удалению отдельной
задачей). Регрессия: полный `pytest tests/` после правки — **4 failed,
862 passed**, те же 4 предсуществующих падения, ни одного нового.

После фикса — **compute: выполнимо 91/100, пропущено 9**,
`block4_seo: ok`. `s01..s19,s21..s27` (23 файла; `s20` в skipped —
см. ниже) реально посчитаны на боевых данных. Точечная проверка глубины
расчёта (не только факт существования файла):
- `s06.json` — `monthly_shows_trend`/`monthly_shows_anomaly` с реальными
  помесячными show/click рядами (13 месяцев) и обнаруженными аномалиями
  (spike/drop против медианы).
- `s07.json` — `summary` (`clusters_evaluated: 31`,
  `query_gap_candidate_count: 14`) + построчные
  `commercial_demand_without_landing_page` с реальным `demand_total` из
  Wordstat.
- `s26.json` — `summary` + `geo_demand_without_landing_page` с реальными
  `demand_total`; `geo_dimension_available: false` — ожидаемое,
  задокументированное ранее (`AUDIT-s07-s26-formula-match`) структурное
  ограничение (гео-срез Wordstat недоступен), не баг и не деградация
  этого прогона.

Итог: `wordstat.parquet` + `block4_seo` + `s07`-site_pages-join реально
работают вместе на реальных данных клиента, не только на фикстурах —
именно то, что просил проверить промт, но обнаруженный по пути дефект
диспетчера был серьёзнее, чем можно было предположить (не «строки
unavailable», а «весь блок ни разу не считался»).

**Побочное открытие (не в списке промта, не расследовано глубоко, вне
скоупа):** тем же классом бага, что и `block4`/`write_ad_texts_archive`
(`AUDIT-input-tables-blast-radius`) — `crux` реально экстрагируется
(`data/raw/crux/`, `cwv_field_data_available=true`), но
`src/transform/build_canonical.py` не содержит вообще никакой логики
построения канонической таблицы `crux` (`grep -c crux` → 0 в этом файле).
`config/methodology.yaml` требует `requires: [crux]` у C01, C02, S20
(строки 606, 617, 1091) — эти три проверки структурно недостижимы
(`runnable=true` никогда), подтверждено `degradation_report.json` этого
прогона: `C01`/`C02`/`S20` в `skipped`, `missing: ["crux"]`. Не входило в
`AUDIT-input-tables-blast-radius` (тот аудит покрывал `client_answers`/
`webvisor_findings`/`crm`/`manual_serp`/`site_crawl`, не `crux`) — четвёртый
независимый экземпляр того же класса бага, найден только благодаря этому
чекпоинту. Правок не вносилось (вне `allowed_files` и вне явного решения
аналитика по этому конкретному пункту — только `block4` был согласован).
Кандидат на отдельную `FIX`-задачу.

Остальные 6 из 9 skipped — без сюрпризов, ожидаемые ограничения источников
на этом клиенте: `D02`/`D03` (`missing: [goals]` — цели `count`/`sum`-типа
Метрики недоступны для сверки, независимо от `_run_d0x`), `A01`/`A02`/`A03`
(`missing: [campaign_strategies]` — ручная стратегия кампаний не заполнена
клиентом), `C14` (`missing: [site_crawl]` — осознанное решение
`AUDIT-c14-requires-decision`, не баг).

**D06/D07 — реальная валидация фикса `FIX-input-tables-manifest-gate` на
боевых данных, с неожиданным положительным эффектом.** Оба теперь
`runnable=true` (не в skipped). D06 **поймал** ровно то расхождение,
которое предсказывал `AUDIT-vat-basis-source-path-critical`:
`expected_cost_status: "gross"` (из `client_answers.yaml`, Q01 отвечен) vs
`actual_cost_status: "vat_basis_unknown"` (баг пути `vat_basis_by_source`
всё ещё не исправлен) → `answer_not_applied: true`. Т.е. система теперь
корректно детектирует свой собственный незакрытый баг вместо того, чтобы
молчать о нём (раньше D06 был `skipped` и не мог его увидеть). D07 —
`possible_double_counted_budget`, `direct_total_rub: 492661.44`,
`yandex_business_total_rub: 0.0`, `both_present: false` — сдвоенного учёта
бюджета на этом клиенте нет (Яндекс Бизнес не подключён).

**analyze/report** — по решению аналитика **не запускались** в рамках
этого чекпоинта: `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` не заданы в
этой сессии, а `analyze` — реальный платный вызов LLM по ~91 выполнимой
проверке; запускать вслепую без ключа или молча искать обходной путь не
стали. `findings/draft/`, `findings/approved/`, `report/` для
pognali.rent по-прежнему пусты после этого чекпоинта — гейт перед report
не снят. Требует отдельного прогона с доступным ключом.

### Часть 3 — сверка открытых пунктов

Все заранее известные открытые вопросы подтверждены этим прогоном ровно
так, как задокументированы, без сюрпризов:
- **S26** — структурно ограничен (`geo_dimension_available: false`),
  как и предсказано `AUDIT-s07-s26-formula-match`; численно теперь виден
  впервые (см. выше), но природа ограничения не изменилась.
- **cost_normalized-гэп** (`AUDIT-vat-basis-source-path-critical`) —
  подтверждён на 100% боевых строк: `costs.parquet` (1377 строк) —
  `cost_status="vat_basis_unknown"` и `cost_normalized IS NULL` во всех
  без исключения строках. Фикс `FIX-block1-cost-normalization` (отдельный
  путь через `block1.py`, в обход `build_canonical.build()`) закрывает
  A09-A15/A17-A19; A04/A05/A06/A08 (прямые читатели `costs.parquet`)
  по-прежнему получают `None`/деградацию по деньгам — не регрессия этого
  чекпоинта, тот же задокументированный гэп.
- **`statistics_field_scope: "unknown"`** (`sources.direct`) —
  подтверждён как есть, без изменений.

Два пункта, обнаруженных этим чекпоинтом и **не** входивших в список
известных открытых вопросов промта — оба задокументированы выше:
диспетчерский баг `block4`/`block4_seo` (исправлен по ходу задачи, с
явного согласия) и мёртвая логика построения канонической `crux`-таблицы
(не исправлена, кандидат на отдельную задачу).

### Итоговая таблица

| Этап | Статус | Подтверждено фактом прогона |
|---|---|---|
| pytest (полный) | прошёл, baseline без изменений | 4 failed / 862 passed, состав совпадает с baseline |
| intake | прошёл | таблица источников, warning про data_window ожидаем |
| extract | упал целиком → починен обходным путём (без правки кода) | `UnicodeEncodeError` в `StageLogger.__call__`/`webmaster_manual.py:115`; с `PYTHONIOENCODING=utf-8` — 9/9 источников, сверка Logs↔Reports OK 30/0/0 |
| transform | прошёл | 14 таблиц, включая `wordstat.parquet` |
| compute | деградировал ожидаемо (9/9 известных случаев) + один неожиданный полный провал блока, исправлен | `block4`→`block4_seo` фикс подтверждён: 91/100 runnable, S01-S27 (кроме S20) реально посчитаны с содержательными данными |
| analyze | не запускался (решение аналитика) | нет `ANTHROPIC_API_KEY` в сессии |
| report | не запускался (гейт: `findings/approved/` пуст) | ожидаемо — analyze не выполнялся |

### Файлы реального отчёта этого прогона

`report/` пуст — report не собирался (см. выше). Артефакты этого прогона,
которые можно открыть:
- `clients/pognali.rent/data/raw/manifest.json`,
  `clients/pognali.rent/data/raw/reconciliation.json`
- `clients/pognali.rent/data/canonical/*.parquet` (14 файлов + 2
  устаревших сироты прошлых схем — `direct_queries.parquet`,
  `geo.parquet`, не перезаписаны в этом прогоне, см. выше про
  `SEARCH_QUERY_PERFORMANCE_REPORT`)
- `clients/pognali.rent/data/metrics/degradation_report.json`,
  `metrics_summary.json`, `d*.json`/`a*.json`/`t*.json`/`c*.json`/
  `s*.json` (91 выполнимых проверок)
- `clients/pognali.rent/logs/*_20260730_*.log` (intake/extract/transform/
  compute этого прогона)

Blocker: нет для завершённой части (Части 1-2 через compute). Открыт
явный, согласованный с аналитиком blocker для analyze/report — нужен
`ANTHROPIC_API_KEY` для продолжения. Изменённый файл вне `allowed_files`:
`src/compute/common.py` (однострочный фикс диспетчера, внесён по прямому
согласию аналитика в ходе задачи, не самовольное расширение скоупа).

**MIGRATE-analyze-openai** DONE — 2026-07-30. Слой analyze переведён с Anthropic на OpenAI Responses API: `openai`, `client.responses.create`, `OPENAI_API_KEY` только из project environment, `gpt-5.6-terra` по умолчанию; strict JSON Schema, rejected-артефакты и ручной report gate сохранены. Целевые тесты проходят без реального API.
**FIX-analyze-proxyapi-integration** DONE — 2026-07-31. Analyze читает только `PROXYAPI_API_KEY`; `ANALYZE_LLM_BASE_URL` имеет ProxyAPI default, fallback на OpenAI отсутствует; модель и Responses API не менялись.
**FIX-d07-q02-field-contract** DONE — 2026-08-01. D07 поддерживает canonical Q02 и legacy-алиас, фиксирует конфликты и malformed ввод без подмены нулём.
**FIX-s06-gsc-direction** DONE — 2026-08-01. S06 считает сезонность только по помесячным GSC total_shows и avg_show_position; Wordstat подтверждает её лишь при совпадении направления спроса и показов без ухудшения позиции. Вебмастер для динамики пишет явный `unavailable`; добавлены positive/negative/direction-conflict/missing-W сценарии.
**FIX-seo-queries-month-key** DONE — 2026-08-01. Natural key `seo_queries` включает `month`, поэтому месяцы одного query×page не схлопываются до page-level агрегатов S09/S24; S05 остаётся явным `unavailable`, пока в canonical нет кластера запросов.
**FIX-s04-device-manual-degradation** DONE — 2026-08-01. S04 считает CTR только по известным device-срезам и исключает `unknown`; без известного device пишет единственный `manual_required` с MED без CTR-прокси и проблемных строк.
**FIX-s09-s24-gsc-page-degradation** DONE — 2026-08-01. S09 не создаёт overlap-кандидаты при пустой GSC page-dimension; S24 не итоги и кандидаты без непустого GSC page за два месяца; оба пишут единственный `manual_required` с MED.
**FIX-t03-t10-contract-degradation** DONE — 2026-08-01. T03/T04/T08/T10 остановлены реестром без обязательных входов; T09 хранит только контекст аномалии и без любого из журнала изменений/независимого ряда имеет `unavailable_for_cause`.
**FIX-t03-t10-downstream-gap** DONE — 2026-08-01. Analyze исключает unavailable/unavailable_for_cause и channel_anomaly_context из кандидатов и validation; report не публикует сохранённые запрещённые finding и явно показывает limitation T09 без причинного вывода.
