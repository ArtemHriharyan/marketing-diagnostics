"""Схема и загрузчик clients/<name>/inputs/wordstat_stopwords.yaml.

Контракт:
    Читает   — inputs/wordstat_stopwords.yaml (entries: phrase, scope,
               reason, added_by, added_at).
    Отдаёт   — classify(phrase, entries) -> "junk" | "general" | None.
    LLM      — не используется.

wordstat.py эту логику не дублирует, а вызывает classify() отсюда —
единственное место, где живёт правило сопоставления стоп-слов.

Правила classify() (см. task_id WS-0):
    - сопоставление по подстроке (entry.phrase внутри normalize(phrase)),
      регистронезависимо, через normalize() ниже;
    - entries пуст (новый клиент, темплейт не заполнен) -> classify всегда
      возвращает None и НЕ блокирует пайплайн; вызывающая сторона обязана
      зафиксировать в manifest wordstat_stopwords_empty=true, чтобы это было
      видно в отчёте о полноте данных, а не терялось молча;
    - "junk" вырезается из gap_candidates и seasonality_candidates;
      "general" — только из gap_candidates (см. WS-1 п.2b).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_VALID_SCOPES = ("junk", "general")


def normalize(text: str | None) -> str:
    """lower + trim + схлопнуть пробелы — единая точка сравнения текста запросов.

    Используется здесь и в дедупе target_queries (WS-1) — не дублировать эту
    логику второй копией, импортировать normalize() отсюда.
    """
    if not text:
        return ""
    return " ".join(str(text).lower().split())


def load_stopwords(path: Path) -> list[dict[str, Any]]:
    """Прочитать entries из wordstat_stopwords.yaml. Файл или поле отсутствует -> []."""
    import yaml

    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("entries") or [])


def classify(phrase: str, entries: list[dict[str, Any]]) -> str | None:
    """"junk" | "general" | None по первому совпадению подстроки в entries.

    entries пуст -> None для любой фразы (см. правило пустого стоп-листа в
    докстринге модуля). Порядок entries значим: побеждает первое совпадение.
    """
    if not entries:
        return None
    haystack = normalize(phrase)
    if not haystack:
        return None
    for entry in entries:
        needle = normalize(entry.get("phrase"))
        if not needle or needle not in haystack:
            continue
        scope = entry.get("scope")
        if scope in _VALID_SCOPES:
            return scope
    return None
