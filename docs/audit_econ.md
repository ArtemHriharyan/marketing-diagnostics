# AUDIT-ECON — цепочка "CRM + расходы -> экономика клиента -> отчёт"

Режим READ-ONLY. Клиент для фактических данных — `pognali.rent` (единственный
с реальной CRM-выгрузкой).

## 1. EXTRACT

**1.1** Существует: `src/extract/crm_import.py`. Вызывается оркестратором:
`src/pipeline/orchestrator.py:375` — `"crm_csv": ["crm_import"]` (маппинг
`config.sources.crm_csv` -> экстрактор).

**1.2** Контракт `CANONICAL_COLUMNS` (`src/extract/crm_import.py:54-56`):
`lead_date, source, phone_or_id, status, amount_rub, is_new_client`.
`phone_or_id`: если ≥10 цифр — хэшируется (SHA-256), сырой телефон в
canonical не попадает (`crm_import.py:312-324`).

**1.3** Факт (pognali.rent):
`data/raw/crm/leads.parquet` + `validation_report.json`. Из
`validation_report.json`: `total_rows: 1357, accepted: 1357, rejected: 0`,
`columns_seen: [lead_id, created_at, source, utm_source, utm_campaign, stage,
is_repeat, deal_amount_rub, closed_at]`. Диапазон дат из
`data/raw/manifest.json` (`sources.crm`): `date_from: "2024-12-20"`,
`date_to: "2026-07-08"`.

**1.4** Manifest пишется (`crm_import.py:346-364`, `_record_manifest`):
`canonical_tables: ["crm"]`, `rows`, `date_from/date_to`, плюс
`crm_attribution_reliable: false` и `crm_attribution_unreliable_reason`.
Фактическое значение reason (`data/raw/manifest.json`, `sources.crm`):
"source/stage/is_repeat присутствуют по имени, но пусты в 100% строк
(1357/1357)... Блок L (лид->деньги, атрибуция) по этому клиенту остаётся
закрыт как апселл, не деградация." Явного поля `source_mode`/`completeness`
как отдельных ключей нет — есть `rows`, `rejected`, `canonical_tables`,
`crm_attribution_reliable`.

## 2. КЛЮЧ АТРИБУЦИИ

**Вывод: L0** — только общая стоимость (весь расход / все записи), доказано
фактическими данными, а не документацией.

Доказательство:
- `clients/pognali.rent/config.yaml:56` — `attribution_reliable: false`.
- `clients/pognali.rent/inputs/crm_export.csv` — колонки `source`,
  `utm_source`, `utm_campaign`, `stage`, `is_repeat` присутствуют, но
  фактически пусты у всех 1357 строк (проверено: первые строки файла имеют
  пустые `source/utm_source/utm_campaign/stage/is_repeat`; клиентский
  `attribution_unreliable_reason` в конфиге фиксирует это как 100%/1357).
  Заполненность поля `source`: **0%**.
- `phone_or_id` замаплен на `lead_id` (`config.yaml:53-55`,
  `column_map.phone_or_id: "lead_id"`) — значения вида `653065`, не телефон
  и не `client_id`/`yclid`/`gclid`, поэтому классифицируются как `id`, а не
  `phone` (`crm_import.py:312-324`, `_classify_key`), JOIN с `visits` по
  этому ключу невозможен в принципе (в `visits.parquet` такого ключа нет).
- В canonical `crm.parquet` схема (`build_canonical.py:2071-2096`,
  `build_crm`) не несёт ни `source` (сырое поле не сохраняется, только
  `source_norm`, который в данных этого клиента пуст->`"unknown"`), ни
  ключа для JOIN с визитами.
- Единственная реализованная реальная связка (`acquisition_economics.py`)
  использует режимы `crm_share_estimate` (фиксированная доля 0.9,
  сконфигурированная клиентом, не вычисленная) и `tracked_funnel` (визиты
  Метрики напрямую, без CRM) — не JOIN CRM↔visits.

Что нужно запросить у клиента для L1/L2: реально заполняемое поле
источника/utm в CRM на момент создания сделки, либо `client_id`/click ID/
телефон в открытом виде для матчинга по хэшу с визитами.

## 3. TRANSFORM

**3.1** Есть: `build_crm()` (`src/transform/build_canonical.py:2071-2096`).
Создаёт `crm.parquet` со схемой: `lead_date, source_norm, status_norm,
amount_rub, is_new_client, phone_hash`. Строки без даты отбрасываются
(`lead_date is None: continue`, строка 2084-2085).

**3.2** JOIN CRM с visits/costs: **не найдено** нигде в
`build_canonical.py` (единственные `_join_*` записи в файле — `visits_backfill`,
`wordstat_core_metadata`; ни одной с участием `crm`). Ветка `build()` для
`crm` (`build_canonical.py:2810-2814`) просто пишет `crm.parquet` как есть,
без merge.

**3.3** Canonical-таблица "расход+результат по источнику/кампании за
период в одной строке" — **не найдено**. `costs.parquet` и `crm.parquet` —
раздельные таблицы без общего ключа кампании/источника на уровне CRM.

## 4. COMPUTE

**4.1** ID блока L в `config/methodology.yaml`: **не найдено** (grep по
файлу — 0 совпадений `crm`). Реестр знает только D01-D12, A01-A26, T01-T10,
C01-C25, S01-S27 — подтверждает CLAUDE.md. Блок L документирован как
отдельный апселл в `marketing-diagnostics-methodology-v2.md:147-171`
("Блок L — лид → деньги (CRM) — без изменений... остаётся апселлом").

**4.2** `money_frame.py` (`src/compute/money_frame.py`) на вход берёт уже
посчитанные `data/metrics/{aXX,cXX}.json` (docstring, строки 1-58) —
**не** читает `crm.parquet` и не считает по CRM. На выход —
`money_frame.csv/json` + `findings_registry.csv/json`: список объектов
`{check_id, money_category, amount_rub, confidence, confidence_cap,
segment, description, source_check_ids, scenario, kind}` плюс
`category_total` по 4 категориям (`_money_item`, строки 227-246;
структура `rows_out`, строки 484-536).

**4.3** По каждой из пяти величин:
- общий расход за период — есть, `src/compute/cost_summary.py:149-227`
  (`build_cost_summary`, `monthly_total_rows`).
- расход по источникам — есть, `cost_summary.py:206-222`
  (`channel_totals`/`component_totals`).
- число сделок из CRM — есть, `src/compute/acquisition_economics.py:176-177`
  (`_crm_summary`, `COUNT(*) FROM crm`).
- стоимость сделки — есть, но не как отдельный проверяемый check (`type=A`),
  а как сервисная метрика: `acquisition_economics.py:332-458`
  (`compute_acquisition_economics`, `value_rub = numerator/denominator`),
  при `crm_share_estimate` знаменатель — доля от общего числа CRM-записей
  (не JOIN), при `tracked_funnel` — счёт визитов по цели, CRM не участвует.
- стоимость веб-конверсии по источникам (канальный CPL/CPA из CRM) — **нет**:
  для этого нужен JOIN crm↔visits по источнику, которого нет (см. §2, §3.2).

**4.4** Попадают в `data/metrics/`: `cost_summary.json`,
`acquisition_economics.json` (у pognali фактически существуют и заполнены —
`total_revenue_rub: 24281956.0`, `record_count: 1357`,
`models[].value_rub` для `estimated_site_booking` и `tracked_direct_booking`).
В `metrics_summary.json` (`data/metrics/metrics_summary.json:74-88`,
`block_status`) — `"cost_summary": "ok"`, `"acquisition_economics": "ok"`,
`"money_frame": "ok"`, но `"block5": "not_implemented"`,
`"block6": "not_implemented"` (`src/compute/block6.py:21` —
`raise NotImplementedError`). `metrics_summary.json` сам по себе НЕ несёт
денежных ключей — только counts/block_status/artifacts/seo_confidence_cap.

## 5. REPORT (ключевой разрыв)

**5.1** `build_report.py:build()` (строки 730-763) читает из
`metrics_dir` только: `degradation_report.json` и `metrics_summary.json`
(строки 735-736), плюс `t09.json` для формулировки причин деградации
(строка 738). Обращений к `money_frame.json`, `acquisition_economics.json`
или `cost_summary.json` в файле — **0** (grep по трём именам не дал
совпадений в `src/report/build_report.py`).

**5.2** Механизм вывода величин, НЕ являющихся approved-находкой:
**не найдено**. Единственный вход отчёта по находкам —
`load_approved_findings(paths.findings_approved)` (строка 732,
читает только `findings/approved/*.yaml`). Денежные метрики достигают
отчёта только если аналитик вручную оформил их как finding и одобрил —
косвенно, через `src/analyze/draft_findings.py:75`, где
`cost_summary`/`acquisition_economics` перечислены как допустимые входы
LLM-слоя analyze (значит могут попасть в текст находки), но не как
отдельная таблица/секция отчёта.

**5.3** Гейт (`src/pipeline/orchestrator.py:107-116`,
`report_gate_message`) блокирует запуск стадии `report` целиком при
пустом `findings/approved/` — это блокирует ВСЁ, включая гипотетическую
экономическую секцию, если бы она существовала. Отдельного пути вывода
базовых экономических таблиц в обход гейта — нет.

**5.4** Секция "экономика/денежная рамка" как отдельная страница —
**не найдено**. Фактические секции `report/diagnostic_report.md` (pognali):
Вердикт, Резюме, План действий, Ключевые находки, "Что не удалось
проверить и почему", Приложение (доп. находки + "SEO-ядро — не
посчитано"), Сноски, Глоссарий. Денежные суммы попадают в отчёт только
внутри текста конкретных находок (например A23, C06), не как сводная
таблица `money_frame`/`acquisition_economics`.

## 6. ВЕРДИКТ

**Цепочка обрывается первым на слое REPORT**: посчитанные экономические
величины (`cost_summary.json`, `acquisition_economics.json`,
`money_frame.json`) существуют и корректны по данным pognali, но
`build_report.py` их не читает — путь от чисел к странице отчёта
отсутствует физически, а не деградирован.

| Слой | Статус | Чего не хватает | Объём |
|---|---|---|---|
| extract (crm) | готов | — | — |
| transform (crm.parquet) | частично | нет JOIN crm↔visits/costs (ключа для него и нет) | — |
| compute (cost_summary, acquisition_economics) | готов | канальный CPL по CRM (нужен ключ атрибуции) | L (данные клиента) |
| compute (block L / block6.py) | отсутствует | реализация; `raise NotImplementedError` | M |
| report | отсутствует | чтение money_frame/acquisition_economics/cost_summary, отдельная секция | S |

Невозможно посчитать на текущих данных клиента (pognali.rent):
- канальный CPL/CPA по CRM (L1/L2) — не хватает **данных клиента**:
  заполняемое поле источника/utm в CRM на момент лида, или client_id/
  телефон в открытом виде для склейки с визитами.
- Блок L целиком (лид->сделка, новые/повторные из CRM, скорость
  обработки) — не хватает **кода**: `src/compute/block6.py` —
  `NotImplementedError`; сам блок не зарегистрирован в
  `config/methodology.yaml` (нет ID).
- Вывод уже посчитанной экономики (`acquisition_economics`,
  `cost_summary`, `money_frame`) в финальный отчёт как отдельной секции —
  не хватает **кода** в `src/report/build_report.py` (чтение файлов +
  секция); данные для этого уже готовы.
