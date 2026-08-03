# AUDIT-P0 — инвентаризация пайплайна (read-only)

Дата: 2026-08-03. Ветка: `main`, HEAD `429aa33`.
Метод: чтение кода, а не документации. Каждый статус подтверждён строкой кода
или артефактом реального прогона `clients/pognali.rent/data/metrics/`.
Оценка качества кода не производилась.

Окружение проверки: `.venv/Scripts/python.exe` (Python 3.14.6).

---

## 1. Слои: подключение и статус

| Слой | Модуль | В оркестраторе | Статус | Доказательство |
|------|--------|:--------------:|--------|----------------|
| intake | `src/pipeline/orchestrator.py::run_intake` | да | **частично** | `run.py:32-33` → `orch.run_intake`. Валидация `data_window` работает (`orchestrator.py:293`). Ping источников НЕ выполняется: `orchestrator.py:282-285` — `# TODO(extract): вызвать лёгкий ping`, колонка «доступен» всегда `"?"`/`"-"` (`orchestrator.py:285`) |
| extract | `src/extract/_common.py` | — (библиотека) | работает | `http_request` + ретраи; используется всеми API-модулями |
| extract | `metrika_logs.py` | да | работает | `EXTRACTORS["metrika"]` `orchestrator.py:371`; `def extract(`, `requests.`/`http_request` в модуле; реальный raw у pognali.rent |
| extract | `metrika_reports.py` | да | работает | `EXTRACTORS["metrika"]` `orchestrator.py:371` |
| extract | `direct.py` | да | работает | `EXTRACTORS["direct"]` `orchestrator.py:372` |
| extract | `wordstat.py` (+`wordstat_config.py`) | да | работает | `EXTRACTORS["wordstat"]` `orchestrator.py:373` |
| extract | `crux.py` | да | работает | `EXTRACTORS["crux"]` `orchestrator.py:374` |
| extract | `crm_import.py` | да | работает | `EXTRACTORS["crm_csv"]` `orchestrator.py:375` |
| extract | `gsc_manual.py` / `gsc_api.py` | да | работает | `MODE_DISPATCH` `orchestrator.py:382`, выбор `_modules_for_source` `:385-391` (дефолт `manual`) |
| extract | `webmaster_manual.py` / `webmaster_api.py` | да | работает | там же, `orchestrator.py:382-391` |
| extract | `site_crawl.py` | да (условно) | работает | `orchestrator.py:465` — вызывается при `config.crawl.base_url` |
| extract | сверка Logs↔Reports | да | работает | `orchestrator.py:494` → `_run_metrika_reconciliation` → `scripts/verify_metrika.py` |
| transform | `build_canonical.py` | да | работает | `orchestrator.py:535` `build_canonical.build(...)`; 21 parquet в `clients/pognali.rent/data/canonical/` |
| transform | `direct_normalize.py` | да (через build) | работает | подключён `4X-direct-wiring` в `build()`/`SCHEMAS` |
| transform | `webmaster_popular_queries.py` | да (через build) | работает | `reshape_popular_queries_wide_to_long` вызывается из `build_canonical` |
| compute | `common.py` | да | работает | `orchestrator.py:573` `dispatch_blocks`, `:574` `build_metrics_summary` |
| compute | `block0..block6` и др. | да | см. §2 | `common.py:234-238` `BLOCK_MODULE_NAMES` |
| compute | `degradation.py` | да | работает | `orchestrator.py:559` `build_degradation_report`; в прогоне `counts={total:100, runnable:86, skipped:14}` |
| compute | `manifest.py` | да | работает | `orchestrator.py:309, 436, 556` |
| analyze | `draft_findings.py` | да | работает (кодом), **боевого результата нет** | `orchestrator.py:608` `draft_findings.draft(...)`; LLM-вызов `draft_findings.py:696 _call_llm`, `:717 from openai import OpenAI`, base_url `:621` = ProxyAPI. Однако `clients/pognali.rent/findings/draft/` **пуст** — ни одного черновика не записано |
| analyze | `schemas.py`, `validate_findings.py` | да | работает | вызываются из `draft()`: `schemas.validate_finding`, `validate_findings_mod.validate_finding_evidence` |
| report | `build_report.py` | да | работает (кодом), **никогда не запускался** | `orchestrator.py:640` `build_report.build(...)`; гейт `:629 approved_findings_present`. `clients/*/findings/approved/` и `clients/*/report/` пусты у всех клиентов |

Вывод по сквозному прогону: реально доходил до `compute` включительно
(191 файл в `clients/pognali.rent/data/metrics/`). Слои `analyze` и `report`
кодом подключены, но выходных артефактов нет ни у одного клиента.

---

## 2. `src/compute/` — блоки и check_id

### 2.1 Что реально диспетчеризуется

`common.py:234-238`:

```
BLOCK_MODULE_NAMES = ("block0","block1","block2","funnels","seasonality",
                      "block3","block4_seo","block5","block6",
                      "cost_summary","acquisition_economics","money_frame",
                      "candidates")
```

| Модуль | В `BLOCK_MODULE_NAMES` | Статус | Доказательство |
|--------|:----------------------:|--------|----------------|
| `block0.py` | да | работает, D01–D12 | `block0.py::run` — 12 веток `if "D01".."D12" in runnable_ids` |
| `block1.py` | да | работает, A01–A26 | `block1.py::run` — 26 веток |
| `block2.py` | да | работает, T01–T10 (4 из них — заглушки, §2.3) | `block2.py::run` |
| `block3.py` | да | работает, C01–C25 | `block3.py::run` |
| `block4_seo.py` | да | работает, S01–S27 | `block4_seo.py::run` |
| **`block4.py`** | **НЕТ** | **мёртвый код** | `block4.py:21 raise NotImplementedError`; имя отсутствует в `BLOCK_MODULE_NAMES`. Легаси-нумерация 4.1/4.2 (атрибуция) |
| **`block5.py`** | да | **заглушка** | `block5.py:26 raise NotImplementedError`; в прогоне `metrics_summary.block_status.block5 = "not_implemented"` |
| **`block6.py`** | да | **заглушка** | `block6.py:21 raise NotImplementedError`; `block_status.block6 = "not_implemented"` |
| `funnels.py` | да | работает | `block_status.funnels = "ok"`, есть `funnels.json` |
| `seasonality.py` | да | работает | `block_status.seasonality = "ok"`, есть `seasonality.json` |
| `cost_summary.py` | да | работает | `block_status = "ok"`, есть `cost_summary.json` |
| `acquisition_economics.py` | да | работает | `block_status = "ok"`, есть `acquisition_economics.json` |
| `money_frame.py` | да | работает | `block_status = "ok"`, есть `money_frame.json`, `findings_registry.json` |
| `candidates.py` | да | работает | `block_status = "ok"`, есть `analysis_candidates.json` |

`block5.py` / `block6.py` описывают легаси-проверки 5.1–5.6 / 6.1–6.3 старой
нумерации; ни одного check_id схемы `D/A/T/C/S` они не несут. Их присутствие
в `BLOCK_MODULE_NAMES` даёт только строку `not_implemented` в
`metrics_summary`, ни одной проверки из 100 они не закрывают.

### 2.2 Итог по check_id

`config/methodology.yaml` — **100 записей**: D=12, A=26, T=10, C=25, S=27
(подсчёт по `checks[*].id`).

* Диспетчеризовано (есть ветка `if "<ID>" in runnable_ids` в `run()`): **100 / 100**.
* Из них **89** имеют хоть какую-то условную (зависящую от данных) реализацию.
* **11** захардкожены в `unavailable` при любых входных данных (§2.3).
* Из 89 ещё **11** — только транспорт ручного YAML, без детерминированного расчёта (§2.4).
* **Детерминированный расчёт по данным: 78 / 100.**

Подтверждение на реальном прогоне (`clients/pognali.rent/data/metrics/`):
90 артефактов `<id>.json`, из них **61 с данными**, **29 только `status: unavailable`**.
Список unavailable в прогоне:
`A04 A05 A06 A07 A09 A10 A11 A12 A13 A16 A21 A25 A26 C03 C08 C11 C15 C16 C17
C18 C19 C22 C23 C25 S05 T03 T04 T08 T10`.

### 2.3 Захардкожены в `unavailable` независимо от данных (11)

| ID | Код | Что делает |
|----|-----|------------|
| A07 | `block1.py:982 _run_a07(metrics_dir)` | только `_write_unavailable` |
| A16 | `block1.py:1675 _run_a16(metrics_dir)` | только `_write_unavailable` |
| A25 | `block1.py:2202 _run_a25(metrics_dir)` | только `_write_unavailable` |
| T03 | `block2.py:519-522` | тело = `_write_contract_unavailable(..., "referer_domains")` |
| T04 | `block2.py:526-531` | тело = `_write_contract_unavailable(..., "goal_attribution_models")` |
| T08 | `block2.py:758-762` | тело = `_write_contract_unavailable(..., "visit_campaign_map")` |
| T10 | `block2.py:857-858` | тело = `_write_contract_unavailable(..., "referer_domains и referral_geo")` |
| C19 | `block3.py:1432-1440` | `_write_unavailable` прямо в `run()`, без функции-раннера |
| C22 | `block3.py:1451-1462` | `_write_unavailable` прямо в `run()` |
| C24 | `block3.py:1249-1274 _run_c24` | обе ветки if/else пишут `unavailable` |
| S05 | `block4_seo.py:1190-1195 _run_s05` | только `_write_unavailable` |

### 2.4 Только транспорт ручного YAML, без автоматической части (11)

Считают, только если аналитик заполнил `inputs/*.yaml`; собственной
детерминированной арифметики по canonical нет.

| ID | Код | Источник |
|----|-----|----------|
| C03, C08, C11, C17, C23 | `block3.py:657 _run_manual_only_check` | `inputs/manual_form_tests.yaml` |
| C15, C16, C18, C25 | `block3.py:1302 _run_manual_form_tests_fallback` | `inputs/manual_form_tests.yaml`, `automatic_component="unavailable"` |
| C20 | `block3.py:1156 _run_c20` | `inputs/webvisor_findings.yaml` |
| C14 | `block3.py:1276 _run_c14` | `inputs/manual_form_tests.yaml` (обязателен), webvisor — enrichment |

---

## 3. Расхождения `docs/implementation_status.md` с кодом

Файл 3416 строк, 73 строки таблицы статусов. Большая часть записей честна и
сама фиксирует структурные разрывы (A07/A16/A25, C19/C22/C24, S26 и т.д.).
Ниже — только то, где статус расходится с текущим кодом.

### 3.1 `5F` = DONE «Бизнес-логика T01–T10», но T03/T04/T08/T10 — заглушки (регрессия)

Запись `5F` (DONE, 2026-07-28) подробно описывает реализацию:
«T03 автоматически считает только частоту/долю разрывов сессии», «T10 —
эвристика по client_id … повторяемость визитов (>= 5,
`_T10_MIN_VISITS_FOR_SPAM_CANDIDATE`)», «T04 (визит-уровневые ad-конверсии
Метрики против `goal_conv_<id>` Директа …)».

Код на HEAD ничего из этого не содержит:
* `block2.py:519-522` — `_run_t03` = один вызов `_write_contract_unavailable`;
* `block2.py:526-531` — `_run_t04` = то же;
* `block2.py:758-762` — `_run_t08` = то же;
* `block2.py:857-858` — `_run_t10` = то же;
* `_T10_MIN_VISITS_FOR_SPAM_CANDIDATE` в файле отсутствует.

Логика была удалена коммитом `58ff2fb` («fix: checkpoint diagnostic contract
stabilization», `git show --stat 58ff2fb` → `src/compute/block2.py | 431 +++-----------`).
Ни строки `_write_contract_unavailable`, ни `referer_domains`,
`goal_attribution_models`, `visit_campaign_map` в `implementation_status.md`
не встречаются ни разу — удаление не задокументировано, статус `5F` не понижен.
**4 из 10 T-проверок фактически не реализованы при статусе DONE.**

### 3.2 `block4.py` числится модулем блока, но не вызывается ничем

`CLAUDE.md` («Структура каталогов») и docstring-и говорят о `block0..block6`.
Фактически `common.py:234-238` не содержит `"block4"` — вместо него
`"block4_seo"`. `src/compute/block4.py:21 raise NotImplementedError` —
файл не импортируется и не вызывается ни из одного места
(`grep block4` вне `block4_seo` даёт только сам файл).
Документ это признаёт в записи `CHECKPOINT-full-pipeline-e2e`, но файл
оставлен и в дереве, и в описании структуры проекта.

Смежно: `block5.py` / `block6.py` формально в диспетчере, но возвращают
`not_implemented` и не закрывают ни одного из 100 check_id — а
`CLAUDE.md` перечисляет их как слой расчёта метрик.

### 3.3 intake: `ping()` реализован в 10 экстракторах, но оркестратором не вызывается

`docs/implementation_status.md` многократно описывает `ping()` как рабочий
(`test_ping_true_with_valid_config_and_key`, `ping()` даёт осмысленный
True/False и т.д.), и функции действительно есть:
`crm_import.py, crux.py, direct.py, gsc_api.py, gsc_manual.py, metrika_logs.py,
metrika_reports.py, webmaster_api.py, webmaster_manual.py, wordstat.py`.

Но `run_intake` их не зовёт — `orchestrator.py:282-285`:
`# TODO(extract): вызвать лёгкий ping соответствующего модуля extract.`
и `orchestrator.py:285`: `available = "?" if enabled else "-"`.
`grep "ping("` по `orchestrator.py` и `run.py` — 0 совпадений.
Стадия `intake` заявленную функцию «источник → доступен/нет» не выполняет.

### 3.4 Прочее (статус доклада корректен, но результата нет)

* `P10` = **BLOCKED** — соответствует реальности: `findings/draft/` пуст у всех клиентов.
* `3.5-patch`, `4X-metrika-lookback`, `4X-direct-normalize`, `4X-direct-normalize-2` = **PARTIAL** — заявлены честно.
* Запись `AUDIT-report-wiring` («`run_report` НЕ вызывает `build_report.build()`») **устарела**: на HEAD `orchestrator.py:640` вызов есть. Опровержения в документе нет — читатель, дошедший до этой записи, получит ложный вывод.

---

## 4. Зависимости окружения

Импортированы все 39 top-level модулей `src/**` + `scripts/verify_metrika`
интерпретатором `.venv/Scripts/python.exe` — **0 ошибок импорта**.

| Пакет | `.venv` | Системный Python 3.14 | Где нужен |
|-------|:-------:|:---------------------:|-----------|
| `openai` | **есть** | нет | `draft_findings.py:717`, импорт ленивый (внутри `_call_llm`) |
| `scipy` | **есть** | нет | `block1.py:162`, `block3.py:197` — импорт на уровне модуля, без scipy эти два блока не импортируются вовсе |
| `duckdb`, `pyarrow`, `pandas`, `pyyaml`, `requests`, `playwright` | есть | — | — |
| **`tiktoken`** | **НЕТ** | нет | **нигде в `src/`** — 0 совпадений в коде; в `requirements.txt` тоже отсутствует |

Выводы:
1. Известная проблема «падают `openai` и `tiktoken`» **не подтверждается** для
   рабочего окружения: под `.venv` `openai` импортируется, а `tiktoken` в коде
   не используется ни разу (ссылки на слово есть только внутри
   `.venv/Lib/site-packages/openai/**` в текстах докстрингов). Устанавливать
   `tiktoken` не нужно; отсутствие пакета ничего не ломает.
2. Реальная ловушка — **не тот интерпретатор**. Под системным
   `C:\Users\Artem\AppData\Local\Python\pythoncore-3.14-64\python.exe`
   падают `src.compute.block1` и `src.compute.block3`
   (`ModuleNotFoundError: No module named 'scipy'`), а также `openai`.
   Именно это зафиксировано в `implementation_status.md` как
   «эти два не собираются в этом окружении, `ModuleNotFoundError: scipy`» —
   ограничение окружения, не кода. Запускать только через `.venv`.
