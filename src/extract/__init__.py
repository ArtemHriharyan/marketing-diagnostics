"""Слой extract: по одному модулю на источник. БЕЗ вызовов LLM.

Каждый модуль обязан предоставлять две функции:
    ping(config, env) -> bool           лёгкая проверка живости токена для intake
    extract(config, env, paths) -> dict  выгрузка в data/raw/<source>/,
                                         возврат метаданных для manifest.json
"""
