"""Тесты wordstat_config: classify() и normalize() (task_id WS-0).

Сценарии:
    1. entries пуст -> classify всегда None (флаг manifest — забота вызывающей
       стороны, здесь проверяем только сам контракт "пусто -> None").
    2. совпадение junk и general по подстроке.
    3. регистр и пробелы не ломают сопоставление (normalize).
    4. normalize() — общий модуль, не продублирован (импортируется из
       wordstat_config, доступен как единая точка сравнения).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import wordstat_config as WC  # noqa: E402


def _entries():
    return [
        {"phrase": "конкурент-бренд", "scope": "junk", "reason": "конкурент"},
        {"phrase": "что такое", "scope": "general", "reason": "инфозапрос"},
    ]


# ── 1. Пустой стоп-лист ──────────────────────────────────────────────────────
def test_classify_empty_entries_always_none():
    assert WC.classify("аренда авто", []) is None
    assert WC.classify("конкурент-бренд аренда", []) is None


def test_load_stopwords_missing_file_returns_empty(tmp_path):
    assert WC.load_stopwords(tmp_path / "нет-такого.yaml") == []


# ── 2. Совпадение junk и general ─────────────────────────────────────────────
def test_classify_matches_junk_by_substring():
    assert WC.classify("аренда авто конкурент-бренд владивосток", _entries()) == "junk"


def test_classify_matches_general_by_substring():
    assert WC.classify("что такое аренда авто", _entries()) == "general"


def test_classify_no_match_returns_none():
    assert WC.classify("аренда авто владивосток", _entries()) is None


# ── 3. Регистр и пробелы ─────────────────────────────────────────────────────
def test_classify_case_insensitive_and_extra_whitespace():
    entries = [{"phrase": "Конкурент-Бренд", "scope": "junk", "reason": "конкурент"}]
    assert WC.classify("  АРЕНДА   Конкурент-БРЕНД  авто  ", entries) == "junk"


def test_normalize_collapses_whitespace_and_lowercases():
    assert WC.normalize("  Аренда   Авто  ") == "аренда авто"
    assert WC.normalize("") == ""
    assert WC.normalize(None) == ""


# ── 4. Первое совпадение побеждает при пересечении подстрок ─────────────────
def test_classify_first_matching_entry_wins():
    entries = [
        {"phrase": "авто", "scope": "general", "reason": "широкое"},
        {"phrase": "конкурент-бренд", "scope": "junk", "reason": "конкурент"},
    ]
    assert WC.classify("аренда авто конкурент-бренд", entries) == "general"


# ── load_stopwords читает entries из YAML ────────────────────────────────────
def test_load_stopwords_reads_entries(tmp_path):
    path = tmp_path / "wordstat_stopwords.yaml"
    path.write_text(
        "entries:\n"
        "  - phrase: \"конкурент\"\n"
        "    scope: \"junk\"\n"
        "    reason: \"тест\"\n"
        "    added_by: \"analyst\"\n"
        "    added_at: \"2026-07-21\"\n",
        encoding="utf-8",
    )
    entries = WC.load_stopwords(path)
    assert len(entries) == 1
    assert entries[0]["phrase"] == "конкурент"
    assert WC.classify("конкурент aренда", entries) == "junk"


def test_load_stopwords_empty_entries_key(tmp_path):
    path = tmp_path / "wordstat_stopwords.yaml"
    path.write_text("entries: []\n", encoding="utf-8")
    assert WC.load_stopwords(path) == []
