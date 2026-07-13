"""Генерация черновиков находок из метрик и качественных входов.

Контракт:
    Читает   — data/metrics/* (артефакты compute), data/metrics/degradation_report.json,
               inputs/client_answers.yaml, inputs/webvisor_findings.yaml,
               config/methodology.yaml (названия проверок для привязки находок).
    Пишет    — findings/draft/*.yaml. Каждая находка: id проверки, формулировка
               (русский), опора на числа, уровень уверенности
               (client-HIGH / MED / …), рекомендация.
    LLM      — ДА, только здесь. Модель формулирует текст находки поверх уже
               посчитанных детерминированных чисел; сами числа не выдумываются.
    Гейт     — вывод идёт в draft/, НЕ в approved/. Перенос — ручной, аналитиком.

    ПОТОЛКИ (обязательны к соблюдению; LLM их только понижает, не повышает):
      * degradation_report.checks[*].confidence_cap — верхняя граница уверенности
        находки по этой проверке (MED, если задействован manual-источник). Это
        второй потолок поверх исходного из compute: итоговая уверенность =
        min(compute-уверенность, confidence_cap).
      * degradation_report.checks[*].type_effective — тип находки (A|B|Q) уже
        после пост-хок понижения; analyze берёт его как есть, не «повышает».
"""

from __future__ import annotations

from typing import Any


def draft(paths: Any, config: dict[str, Any], methodology: dict[str, Any]) -> list[str]:
    """Сформировать черновики находок в findings/draft/; вернуть их имена. TODO."""
    raise NotImplementedError
