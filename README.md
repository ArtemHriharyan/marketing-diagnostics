# marketing-diagnostics

Локальный пайплайн диагностики маркетинга для малого бизнеса. Python 3.11+.

Архитектурные принципы и описание слоёв — в [CLAUDE.md](CLAUDE.md).

> [!WARNING]
> Проект находится в стадии стабилизации. До закрытия P1-проблем из
> [реестра текущих рисков](docs/current_risks.md) результаты нельзя выпускать
> клиенту как воспроизводимый диагностический отчёт.

Правила передачи данных во внешний LLM и действующие ограничения описаны в
[политике безопасности и конфиденциальности](docs/security-and-privacy.md).

## Установка

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# *nix:
source .venv/bin/activate

pip install -r requirements.txt
```

## Новый клиент

```bash
cp -r clients/_template clients/acme        # или скопировать вручную
```

1. Заполнить `clients/acme/config.yaml` (счётчики, логины, цели, ручные расходы).
2. Скопировать `clients/acme/.env.example` в `clients/acme/.env` и вписать токены.
3. Заполнить анкету `clients/acme/inputs/client_answers.yaml` на установочном
   созвоне.
4. По мере анализа заполнять `clients/acme/inputs/webvisor_findings.yaml`.

## Запуск этапов

```bash
python run.py acme --stage intake      # предварительная проверка конфигурации
python run.py acme --stage extract     # выгрузка сырых данных в data/raw/
python run.py acme --stage transform   # raw -> data/canonical/*.parquet
python run.py acme --stage compute      # canonical -> data/metrics/
python run.py acme --stage analyze      # черновики находок в findings/draft/
# --- ручной шаг: проверить черновики, утверждённые перенести в findings/approved/ ---
python run.py acme --stage report       # сборка отчёта в report/

python run.py acme --stage all          # всё подряд с остановкой на гейтах
```

## Важное

- `report` не запустится, пока `findings/approved/` пуст — это осознанный гейт.
- Отсутствие источника не роняет пайплайн: непокрытые проверки уходят в
  `data/metrics/degradation_report.json` и затем в раздел отчёта
  «Что не удалось проверить».
- Секреты — только в `clients/<name>/.env`, который не коммитится.

## Тесты

Единый запуск тестов в Windows — через скрипт ниже. Он создаёт временные каталоги
в рабочем дереве, направляет в `tmp/` переменные `TMP` и `TEMP`, а pytest — в
`.pytest_tmp/`; системная `%TEMP%` и `.pytest_cache/` не используются. При
невозможности создать эти каталоги скрипт завершится до запуска тестов.

```bash
powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1 tests/
```

Дополнительные аргументы pytest передаются после пути: например,
`powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1 tests/ -q`.

Фикстуры для внешних TSV/CSV/JSON источников (Директ, GSC, Вебмастер,
Метрика) обязаны основываться на реальном байтовом образце ответа API/экспорта,
сохранённом в `tests/fixtures/samples/`, а не писаться вручную по документации
или предположению о формате. Минимум один интеграционный тест на источник
должен парсить этот реальный образец целиком — юнит-тесты на синтетических
данных дополняют, но не заменяют эту проверку.
