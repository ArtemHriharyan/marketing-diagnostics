# marketing-diagnostics

Локальный пайплайн диагностики маркетинга для малого бизнеса. Python 3.11+.

Архитектурные принципы и описание слоёв — в [CLAUDE.md](CLAUDE.md).

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
python run.py acme --stage intake      # проверка конфига и живости токенов
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

```bash
pytest tests/
```
