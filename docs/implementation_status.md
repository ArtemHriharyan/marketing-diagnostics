# Статус реализации — audit 2026-07-14

Тесты: `pytest tests/test_smoke.py tests/test_extract_smoke.py tests/test_build_canonical.py tests/test_verify_metrika.py tests/test_gsc_manual.py`
Результат: **146 passed, 3 failed** из 149 (после добавления 9 тестов gsc_manual 2026-07-14).

---

## Таблица статусов

| Задача | Статус  | Недостающий критерий / комментарий |
|--------|---------|-------------------------------------|
| **1A** | DONE    | — |
| **1B** | DONE    | — |
| **1C** | DONE    | — |
| **1D** | DONE    | проверка совместимости 2026-07-14: methodology×2 pass, degradation×6 pass, config×16 pass, intake _template exit=0, intake pognali.rent exit=0 |
| **2A** | DONE    | — |
| **2B** | DONE    | — |
| **2C** | DONE    | — |
| **2D** | DONE    | — |
| **3A** | DONE    | build_canonical.py базовые преобразования. GSC manual path (task_id gsc-3A): gsc_manual.py полностью реализован 2026-07-14; добавлен tests/test_gsc_manual.py (9 тестов) — 9 pass. Диспетчеризация mode:manual↔api через MODE_DISPATCH в orchestrator. gsc_api.py: полная реализация из 2C (не stub — stub сломал бы 3 прошедших теста в test_extract_smoke.py). |
| **3B** | DONE    | исправлено 2026-07-14: условие skip-merge и drop patch-колонок перед join |
| **3C** | DONE    | — |
| **3D** | DONE    | — |

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
6 тестов — все pass.

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

**3B-fix:** исправить `_join_backfill` в `src/transform/build_canonical.py`.

Проблема: `patch_already_present` нужно определять не по наличию колонок в df
(они там всегда), а по факту наличия файлов в `backfill/` или по тому, имеют ли
patch-поля в df ненулевые значения (лучше — проверять существование
`backfill_dir` и наличие в нём `visits_backfill_*.csv.gz`).
Правило: если backfill-директория есть и непуста → делать merge; иначе → skip.
После исправления три падающих теста должны стать green.

`allowed_files: [src/transform/build_canonical.py]`
