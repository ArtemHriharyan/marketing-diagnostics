"""Тесты задачи 3.5C: _extract_links, compute_js_diff, crawl_bfs, write_link_graph_parquet.

Сценарии:
  - JS-only контент (текст/ссылки только в rendered HTML)
  - Классификация внутренних/внешних ссылок
  - Обнаружение цикла ссылок в BFS
  - Ограничение глубины BFS (max_depth=3)
  - Схема link_graph.parquet
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extract.site_crawl import (
    LINK_GRAPH_SCHEMA,
    MAX_BFS_DEPTH,
    _extract_links,
    _visible_text,
    compute_js_diff,
    crawl_bfs,
    write_link_graph_parquet,
)

BASE = "https://example.com"

# ── HTML-фикстуры ─────────────────────────────────────────────────────────────

_HTML_WITH_LINKS = """<html><body>
<a href="/about">About</a>
<a href="/contact">Contact</a>
<a href="https://external.com/page">External</a>
<a href="https://other.org/">Other</a>
<a href="#anchor">Anchor</a>
<a href="mailto:hi@example.com">Email</a>
<a href="tel:+7123">Phone</a>
</body></html>"""

_HTML_NO_LINKS = """<html><head><title>Empty</title></head><body><p>No links here.</p></body></html>"""

_HTML_JS_ONLY_LINKS = """<html><body>
<a href="/visible-raw">Raw link</a>
</body></html>"""

_HTML_JS_RENDERED_EXTRA = """<html><body>
<a href="/visible-raw">Raw link</a>
<nav><a href="/js-nav-1">JS Nav 1</a><a href="/js-nav-2">JS Nav 2</a></nav>
<p>Dynamic content loaded by JavaScript that makes text longer.</p>
</body></html>"""

_HTML_LOTS_OF_TEXT = """<html><body>
<p>""" + ("Текст " * 200) + """</p>
</body></html>"""


# ── _extract_links: внутренние/внешние ───────────────────────────────────────

def test_extract_links_classifies_internal():
    result = _extract_links(_HTML_WITH_LINKS, f"{BASE}/", BASE)
    internal = result["internal"]
    assert f"{BASE}/about" in internal
    assert f"{BASE}/contact" in internal


def test_extract_links_classifies_external():
    result = _extract_links(_HTML_WITH_LINKS, f"{BASE}/", BASE)
    external = result["external"]
    assert "https://external.com/page" in external
    assert "https://other.org/" in external


def test_extract_links_skips_anchors():
    result = _extract_links(_HTML_WITH_LINKS, f"{BASE}/", BASE)
    all_links = result["internal"] + result["external"]
    assert not any("#anchor" in u for u in all_links)


def test_extract_links_skips_mailto_and_tel():
    result = _extract_links(_HTML_WITH_LINKS, f"{BASE}/", BASE)
    all_links = result["internal"] + result["external"]
    assert not any("mailto:" in u for u in all_links)
    assert not any("tel:" in u for u in all_links)


def test_extract_links_no_duplicates():
    html = """<html><body>
    <a href="/page">Link 1</a>
    <a href="/page">Link 2</a>
    <a href="/page/">Link 3</a>
    </body></html>"""
    result = _extract_links(html, f"{BASE}/", BASE)
    # /page and /page/ differ after stripping fragment but urljoin keeps them as-is;
    # duplicates of the exact same resolved URL are removed.
    pages = [u for u in result["internal"] if "/page" in u]
    # /page and /page/ are distinct URLs, but /page appears twice → deduplicated to one
    assert pages.count(f"{BASE}/page") == 1


def test_extract_links_resolves_relative_paths():
    html = """<html><body><a href="about.html">About</a></body></html>"""
    result = _extract_links(html, f"{BASE}/dir/page.html", BASE)
    # urljoin("https://example.com/dir/page.html", "about.html") → ".../dir/about.html"
    assert f"{BASE}/dir/about.html" in result["internal"]


def test_extract_links_empty_html_returns_empty():
    result = _extract_links("", f"{BASE}/", BASE)
    assert result["internal"] == []
    assert result["external"] == []


# ── _visible_text ─────────────────────────────────────────────────────────────

def test_visible_text_strips_scripts():
    html = "<html><body><script>var x=1;</script><p>Hello</p></body></html>"
    assert "var x" not in _visible_text(html)
    assert "Hello" in _visible_text(html)


def test_visible_text_strips_style():
    html = "<html><head><style>.foo{color:red}</style></head><body>Text</body></html>"
    assert ".foo" not in _visible_text(html)
    assert "Text" in _visible_text(html)


# ── compute_js_diff ───────────────────────────────────────────────────────────

def test_js_diff_returns_none_when_no_rendered():
    result = compute_js_diff(_HTML_JS_ONLY_LINKS, None, BASE)
    assert result is None


def test_js_diff_detects_js_only_links():
    diff = compute_js_diff(_HTML_JS_ONLY_LINKS, _HTML_JS_RENDERED_EXTRA, BASE)
    assert diff is not None
    only_rendered = diff["links_only_in_rendered"]
    assert f"{BASE}/js-nav-1" in only_rendered
    assert f"{BASE}/js-nav-2" in only_rendered


def test_js_diff_raw_link_not_in_links_only_rendered():
    diff = compute_js_diff(_HTML_JS_ONLY_LINKS, _HTML_JS_RENDERED_EXTRA, BASE)
    assert f"{BASE}/visible-raw" not in diff["links_only_in_rendered"]


def test_js_diff_link_counts():
    diff = compute_js_diff(_HTML_JS_ONLY_LINKS, _HTML_JS_RENDERED_EXTRA, BASE)
    assert diff["raw_link_count"] == 1
    assert diff["rendered_link_count"] == 3


def test_js_diff_text_changed_true_when_rendered_longer():
    diff = compute_js_diff(_HTML_NO_LINKS, _HTML_LOTS_OF_TEXT, BASE)
    assert diff is not None
    assert diff["text_changed"] is True


def test_js_diff_text_changed_false_when_similar():
    same = _HTML_NO_LINKS
    diff = compute_js_diff(same, same, BASE)
    assert diff is not None
    assert diff["text_changed"] is False


def test_js_diff_no_js_links_links_only_rendered_empty():
    diff = compute_js_diff(_HTML_NO_LINKS, _HTML_NO_LINKS, BASE)
    assert diff["links_only_in_rendered"] == []


# ── BFS: вспомогательный мок ─────────────────────────────────────────────────

class _MockResp:
    def __init__(self, status: int, text: str, url: str) -> None:
        self.status_code = status
        self.text = text
        self.url = url
        self.history: list = []


class _MockBfsSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages

    def get(self, url: str, **kwargs) -> _MockResp:
        text = self._pages.get(url, "")
        status = 200 if text else 404
        return _MockResp(status, text, url)


def _make_page(*hrefs: str) -> str:
    links = "".join(f'<a href="{h}">{h}</a>' for h in hrefs)
    return f"<html><body>{links}</body></html>"


# ── crawl_bfs: базовый обход ─────────────────────────────────────────────────

def test_bfs_discovers_depth1_links():
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a", f"{BASE}/b"),
        f"{BASE}/a": _make_page(),
        f"{BASE}/b": _make_page(),
    })
    edges = crawl_bfs(BASE, session)
    to_urls = {e["to_url"] for e in edges}
    assert f"{BASE}/a" in to_urls
    assert f"{BASE}/b" in to_urls


def test_bfs_edge_depth_from_home_correct():
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a"),
        f"{BASE}/a": _make_page(f"{BASE}/b"),
        f"{BASE}/b": _make_page(),
    })
    edges = crawl_bfs(BASE, session)
    depth_map = {e["to_url"]: e["depth_from_home"] for e in edges}
    assert depth_map[f"{BASE}/a"] == 1
    assert depth_map[f"{BASE}/b"] == 2


# ── crawl_bfs: цикл ссылок ───────────────────────────────────────────────────

def test_bfs_cycle_does_not_loop():
    """A→B→A цикл: BFS завершается без зависания."""
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a"),
        f"{BASE}/a": _make_page(f"{BASE}/b"),
        f"{BASE}/b": _make_page(f"{BASE}/a"),  # цикл обратно на /a
    })
    edges = crawl_bfs(BASE, session, max_depth=5)
    # Цикл не должен вызвать бесконечный обход; /a встречается дважды в рёбрах,
    # но в очередь добавляется только один раз.
    visited_from = [e["from_url"] for e in edges]
    assert visited_from.count(f"{BASE}/a") == 1  # /a обходится ровно один раз


def test_bfs_cycle_edge_still_recorded():
    """Ребро на уже посещённый URL должно попасть в граф."""
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a", f"{BASE}/b"),
        f"{BASE}/a": _make_page(f"{BASE}/b"),  # /b уже в очереди, но ребро записывается
        f"{BASE}/b": _make_page(),
    })
    edges = crawl_bfs(BASE, session)
    # /b должно встречаться как to_url дважды: от home и от /a
    b_edges = [e for e in edges if e["to_url"] == f"{BASE}/b"]
    assert len(b_edges) == 2


# ── crawl_bfs: ограничение глубины ───────────────────────────────────────────

def test_bfs_depth_limit_default_is_3():
    assert MAX_BFS_DEPTH == 3


def test_bfs_stops_at_max_depth():
    """Страница на глубине max_depth не обходится; её ссылки не записываются."""
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/d1"),
        f"{BASE}/d1": _make_page(f"{BASE}/d2"),
        f"{BASE}/d2": _make_page(f"{BASE}/d3"),
        f"{BASE}/d3": _make_page(f"{BASE}/d4"),  # глубина 3, не обходится
        f"{BASE}/d4": _make_page(),
    })
    edges = crawl_bfs(BASE, session, max_depth=3)
    to_urls = {e["to_url"] for e in edges}
    assert f"{BASE}/d3" in to_urls       # ребро до глубины 3 записывается
    assert f"{BASE}/d4" not in to_urls   # глубина 4 — не достигается


def test_bfs_max_depth_1_only_home_links():
    """max_depth=1: только рёбра с глубиной 1 (прямые ссылки от home)."""
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a"),
        f"{BASE}/a": _make_page(f"{BASE}/b"),
        f"{BASE}/b": _make_page(),
    })
    edges = crawl_bfs(BASE, session, max_depth=1)
    assert all(e["depth_from_home"] == 1 for e in edges)
    to_urls = {e["to_url"] for e in edges}
    assert f"{BASE}/b" not in to_urls


# ── crawl_bfs: внешние ссылки ────────────────────────────────────────────────

def test_bfs_excludes_external_links():
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/internal", "https://external.com/page"),
        f"{BASE}/internal": _make_page(),
    })
    edges = crawl_bfs(BASE, session)
    to_urls = {e["to_url"] for e in edges}
    assert "https://external.com/page" not in to_urls
    assert f"{BASE}/internal" in to_urls


# ── crawl_bfs: ошибки соединения ─────────────────────────────────────────────

def test_bfs_network_error_does_not_crash():
    class _ErrSession:
        def get(self, url: str, **kwargs):
            if url == f"{BASE}/":
                raise ConnectionError("timeout")
            return _MockResp(200, "", url)

    edges = crawl_bfs(BASE, _ErrSession())
    assert edges == []


def test_bfs_404_page_skipped():
    session = _MockBfsSession({
        f"{BASE}/": _make_page(f"{BASE}/a"),
        # /a → 404 (not in _pages)
    })
    edges = crawl_bfs(BASE, session)
    # /a is in edges (from home), but /a's own children aren't (404)
    assert any(e["to_url"] == f"{BASE}/a" for e in edges)


# ── write_link_graph_parquet ──────────────────────────────────────────────────

def test_write_link_graph_parquet_schema(tmp_path):
    pytest.importorskip("pandas")
    import pandas as pd

    edges = [
        {"from_url": f"{BASE}/", "to_url": f"{BASE}/a", "depth_from_home": 1},
        {"from_url": f"{BASE}/a", "to_url": f"{BASE}/b", "depth_from_home": 2},
    ]
    out = write_link_graph_parquet(edges, tmp_path)
    assert out.exists()
    df = pd.read_parquet(out)
    assert list(df.columns) == LINK_GRAPH_SCHEMA


def test_write_link_graph_parquet_depth_dtype(tmp_path):
    pytest.importorskip("pandas")
    import pandas as pd

    edges = [{"from_url": f"{BASE}/", "to_url": f"{BASE}/a", "depth_from_home": 1}]
    write_link_graph_parquet(edges, tmp_path)
    df = pd.read_parquet(tmp_path / "link_graph.parquet")
    assert str(df["depth_from_home"].dtype) in ("Int64", "int64")


def test_write_link_graph_parquet_row_count(tmp_path):
    pytest.importorskip("pandas")
    import pandas as pd

    edges = [
        {"from_url": f"{BASE}/", "to_url": f"{BASE}/a", "depth_from_home": 1},
        {"from_url": f"{BASE}/", "to_url": f"{BASE}/b", "depth_from_home": 1},
        {"from_url": f"{BASE}/a", "to_url": f"{BASE}/c", "depth_from_home": 2},
    ]
    write_link_graph_parquet(edges, tmp_path)
    df = pd.read_parquet(tmp_path / "link_graph.parquet")
    assert len(df) == 3


def test_write_link_graph_parquet_empty(tmp_path):
    pytest.importorskip("pandas")
    import pandas as pd

    out = write_link_graph_parquet([], tmp_path)
    df = pd.read_parquet(out)
    assert list(df.columns) == LINK_GRAPH_SCHEMA
    assert len(df) == 0
