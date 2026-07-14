"""Тесты каркаса кролера: build_url_priority_list и вспомогательные функции."""

import json
from pathlib import Path

import pytest

from src.extract.site_crawl import (
    DEFAULT_MAX_URLS,
    build_url_priority_list,
    resolve_max_urls,
)


# ── resolve_max_urls ─────────────────────────────────────────────────────────

def test_resolve_max_urls_returns_default_when_not_set():
    assert resolve_max_urls({}) == DEFAULT_MAX_URLS


def test_resolve_max_urls_reads_from_config():
    config = {"crawl": {"max_urls": 50}}
    assert resolve_max_urls(config) == 50


def test_resolve_max_urls_ignores_invalid_value():
    config = {"crawl": {"max_urls": "bad"}}
    assert resolve_max_urls(config) == DEFAULT_MAX_URLS


def test_resolve_max_urls_uses_provided_default():
    assert resolve_max_urls({}, default=10) == 10


# ── build_url_priority_list — без канонических данных ───────────────────────

def test_empty_config_returns_empty_list():
    result = build_url_priority_list({})
    assert result["urls"] == []
    assert result["total_candidates"] == 0
    assert result["truncated"] is False
    assert result["caveat"] is None


def test_seed_urls_included_first():
    config = {"crawl_seed_urls": ["/", "/booking", "/faq"]}
    result = build_url_priority_list(config)
    assert result["urls"][:3] == ["/", "/booking", "/faq"]


def test_crux_key_urls_added_after_seeds():
    config = {
        "crawl_seed_urls": ["/"],
        "sources": {"crux": {"key_urls": ["/rooms", "/contacts"]}},
    }
    result = build_url_priority_list(config)
    assert result["urls"][0] == "/"
    assert "/rooms" in result["urls"]
    assert "/contacts" in result["urls"]


def test_no_duplicate_urls():
    config = {
        "crawl_seed_urls": ["/booking", "/booking"],
        "sources": {"crux": {"key_urls": ["/booking"]}},
    }
    result = build_url_priority_list(config)
    assert result["urls"].count("/booking") == 1


def test_trailing_slash_normalised():
    config = {"crawl_seed_urls": ["/booking/", "/booking"]}
    result = build_url_priority_list(config)
    assert len(result["urls"]) == 1
    assert result["urls"][0] == "/booking"


def test_url_sources_reflect_origin():
    config = {
        "crawl_seed_urls": ["/"],
        "sources": {"crux": {"key_urls": ["/contacts"]}},
    }
    result = build_url_priority_list(config)
    assert result["url_sources"]["/"] == "explicit_seed"
    assert result["url_sources"]["/contacts"] == "crux_key_url"


# ── truncation caveat ────────────────────────────────────────────────────────

def test_truncation_at_max_urls():
    seeds = [f"/page-{i}" for i in range(40)]
    config = {"crawl_seed_urls": seeds, "crawl": {"max_urls": 10}}
    result = build_url_priority_list(config)
    assert len(result["urls"]) == 10
    assert result["truncated"] is True
    assert result["total_candidates"] == 40


def test_caveat_set_when_truncated():
    seeds = [f"/p{i}" for i in range(35)]
    config = {"crawl_seed_urls": seeds, "crawl": {"max_urls": 30}}
    result = build_url_priority_list(config)
    assert result["truncated"] is True
    assert result["caveat"] is not None
    assert "30" in result["caveat"]
    assert "5" in result["caveat"]  # 35 - 30 = 5 dropped


def test_no_caveat_when_within_limit():
    config = {"crawl_seed_urls": ["/", "/about"], "crawl": {"max_urls": 30}}
    result = build_url_priority_list(config)
    assert result["truncated"] is False
    assert result["caveat"] is None


def test_max_urls_argument_overrides_config():
    seeds = [f"/p{i}" for i in range(20)]
    config = {"crawl_seed_urls": seeds, "crawl": {"max_urls": 30}}
    result = build_url_priority_list(config, max_urls=5)
    assert len(result["urls"]) == 5


# ── с каноническими данными (parquet) ────────────────────────────────────────

@pytest.fixture()
def canonical_dir(tmp_path):
    """Создать минимальные parquet-таблицы в tmp_path."""
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas недоступен")

    costs = pd.DataFrame({
        "entry_page": ["/rooms", "/promo", "/contacts"],
        "cost": [5000.0, 3000.0, 1000.0],
    })
    costs.to_parquet(tmp_path / "costs.parquet", index=False)

    gsc = pd.DataFrame({
        "page": ["/rooms", "/faq", "/booking"],
        "clicks": [200, 150, 100],
    })
    gsc.to_parquet(tmp_path / "seo_queries_gsc.parquet", index=False)

    return tmp_path


def test_top_spend_pages_added(canonical_dir):
    config = {}
    result = build_url_priority_list(config, canonical_dir)
    assert "/rooms" in result["urls"]
    assert "/promo" in result["urls"]


def test_top_organic_gsc_pages_added(canonical_dir):
    config = {}
    result = build_url_priority_list(config, canonical_dir)
    assert "/faq" in result["urls"]
    assert "/booking" in result["urls"]


def test_explicit_seeds_stay_first_when_canonical_present(canonical_dir):
    config = {"crawl_seed_urls": ["/special"]}
    result = build_url_priority_list(config, canonical_dir)
    assert result["urls"][0] == "/special"
    assert result["url_sources"]["/special"] == "explicit_seed"


def test_keyword_match_pages_added(tmp_path):
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas недоступен")

    # top_n_each_source=20 по умолчанию: страницы 0–19 захватываются как top_organic_webmaster.
    # Страница 20 (/arenda-avto) имеет clicks=1 (ранг 21) — ниже порога и попадает
    # в список только через keyword_match.
    rows = [{"page": f"/page-{i}", "clicks": 100 - i} for i in range(20)]
    rows.append({"page": "/arenda-avto", "clicks": 1})
    wm = pd.DataFrame(rows)
    wm.to_parquet(tmp_path / "seo_queries_webmaster.parquet", index=False)

    config = {"wordstat_seeds": ["arenda"]}
    result = build_url_priority_list(config, tmp_path)
    assert "/arenda-avto" in result["urls"]
    assert result["url_sources"]["/arenda-avto"] == "keyword_match"


def test_missing_canonical_dir_does_not_crash():
    config = {"crawl_seed_urls": ["/"]}
    result = build_url_priority_list(config, canonical_dir=None)
    assert result["urls"] == ["/"]


def test_missing_parquet_file_in_canonical_dir_skipped(tmp_path):
    config = {"crawl_seed_urls": ["/"]}
    result = build_url_priority_list(config, tmp_path)
    assert "/" in result["urls"]
