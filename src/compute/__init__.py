"""Слой compute: canonical -> metrics. Детерминированно, БЕЗ вызовов LLM.

Один модуль на блок методологии (block0..block6). Каждый блок реализует
проверки своего блока из config/methodology.yaml и пишет артефакты (csv + json)
в data/metrics/. Запускаются только проверки, чьи requires удовлетворены
(см. src.pipeline.degradation); остальные попадают в degradation_report.json.

DuckDB выполняет запросы поверх parquet-файлов data/canonical/ без сервера.
"""
