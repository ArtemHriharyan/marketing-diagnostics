"""Тесты HTTP-обхода страниц: _parse_page_meta, fetch_sitemap, crawl_pages, pages.parquet.

Фикстурный мини-сайт реализован через MockSession/MockResponse без сетевых запросов.
Сценарии: 200, redirect (301→200), 404, canonical, robots noindex,
          наличие/отсутствие в sitemap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extract.site_crawl import (
    PAGES_SCHEMA,
    _is_path_disallowed,
    _parse_page_meta,
    _parse_robots_txt,
    _parse_sitemap_xml,
    _resolve_base_url,
    _select_robots_rules,
    _to_absolute,
    crawl_pages,
    fetch_robots_txt,
    fetch_sitemap,
    write_pages_parquet,
)


# ── Вспомогательные классы для мок-сессии ────────────────────────────────────

class MockResponse:
    """Минимальный аналог requests.Response для тестов."""

    def __init__(
        self,
        status_code: int,
        text: str = "",
        url: str = "",
        history: list | None = None,
        content_type: str = "text/html; charset=utf-8",
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.history = history or []
        self.headers = {"content-type": content_type, **(headers or {})}


class MockSession:
    """Заглушка requests.Session: возвращает заранее заданные ответы по URL."""

    def __init__(self, responses: dict[str, MockResponse]) -> None:
        self._responses = responses

    def get(self, url: str, **kwargs) -> MockResponse:
        if url in self._responses:
            return self._responses[url]
        return MockResponse(404, "", url)


# ── HTML-фикстуры мини-сайта ──────────────────────────────────────────────────

_HTML_200 = """<!DOCTYPE html>
<html>
<head>
  <title>Аренда авто во Владивостоке</title>
  <meta name="description" content="Лучшая аренда автомобилей без водителя">
  <link rel="canonical" href="https://example.com/">
  <meta name="robots" content="index, follow">
</head>
<body>
  <h1>Аренда авто без водителя</h1>
</body>
</html>"""

_HTML_NOINDEX = """<!DOCTYPE html>
<html>
<head>
  <title>Тех-страница</title>
  <meta name="robots" content="noindex, nofollow">
</head>
<body><h1>Служебная</h1></body>
</html>"""

_HTML_NON_SELF_CANONICAL = """<!DOCTYPE html>
<html>
<head>
  <title>Дублирующая страница</title>
  <link rel="canonical" href="https://example.com/">
</head>
<body><h1>Дубль</h1></body>
</html>"""

_HTML_MINIMAL = """<html><head><title>Простая страница</title></head>
<body><h1>Заголовок</h1></body></html>"""

_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com</loc></url>
  <url><loc>https://example.com/rooms</loc></url>
</urlset>"""

_ROBOTS_TXT_MINI = """User-agent: *
Disallow: /private/
"""

BASE = "https://example.com"


@pytest.fixture()
def mini_site_session() -> MockSession:
    """Сессия, имитирующая мини-сайт со всеми сценариями."""
    redirect_intermediate = MockResponse(301, "", url=f"{BASE}/old-path")
    response_200 = MockResponse(200, _HTML_200, url=f"{BASE}/")

    # Для редиректа: history содержит промежуточный ответ, финальный status=200.
    redirect_final = MockResponse(
        200, _HTML_200, url=f"{BASE}/",
        history=[redirect_intermediate],
    )

    return MockSession({
        f"{BASE}/sitemap.xml": MockResponse(200, _SITEMAP_XML, url=f"{BASE}/sitemap.xml",
                                             content_type="application/xml"),
        f"{BASE}/robots.txt": MockResponse(200, _ROBOTS_TXT_MINI, url=f"{BASE}/robots.txt",
                                            content_type="text/plain"),
        f"{BASE}/": response_200,
        f"{BASE}/rooms": MockResponse(200, _HTML_MINIMAL, url=f"{BASE}/rooms"),
        f"{BASE}/redirect": redirect_final,
        f"{BASE}/noindex": MockResponse(200, _HTML_NOINDEX, url=f"{BASE}/noindex"),
        f"{BASE}/dup": MockResponse(200, _HTML_NON_SELF_CANONICAL, url=f"{BASE}/dup"),
        f"{BASE}/private/page": MockResponse(200, _HTML_MINIMAL, url=f"{BASE}/private/page"),
        f"{BASE}/x-robots": MockResponse(
            200, _HTML_MINIMAL, url=f"{BASE}/x-robots",
            headers={"X-Robots-Tag": "noindex"},
        ),
        # /missing не добавлен → MockSession вернёт 404
    })


# ── _parse_page_meta ──────────────────────────────────────────────────────────

def test_parse_meta_extracts_title():
    meta = _parse_page_meta(_HTML_200)
    assert meta["title"] == "Аренда авто во Владивостоке"


def test_parse_meta_extracts_description():
    meta = _parse_page_meta(_HTML_200)
    assert meta["description"] == "Лучшая аренда автомобилей без водителя"


def test_parse_meta_extracts_h1():
    meta = _parse_page_meta(_HTML_200)
    assert meta["h1"] == "Аренда авто без водителя"


def test_parse_meta_extracts_canonical():
    meta = _parse_page_meta(_HTML_200)
    assert meta["canonical_url"] == "https://example.com/"


def test_parse_meta_extracts_robots():
    meta = _parse_page_meta(_HTML_200)
    assert meta["robots_directive"] == "index, follow"


def test_parse_meta_noindex_robots():
    meta = _parse_page_meta(_HTML_NOINDEX)
    assert meta["robots_directive"] == "noindex, nofollow"


def test_parse_meta_non_self_canonical():
    meta = _parse_page_meta(_HTML_NON_SELF_CANONICAL)
    assert meta["canonical_url"] == "https://example.com/"


def test_parse_meta_empty_html_returns_nones():
    meta = _parse_page_meta("")
    assert meta["title"] is None
    assert meta["h1"] is None
    assert meta["description"] is None
    assert meta["canonical_url"] is None
    assert meta["robots_directive"] is None


# ── _parse_sitemap_xml ────────────────────────────────────────────────────────

def test_parse_sitemap_returns_urls():
    urls = _parse_sitemap_xml(_SITEMAP_XML)
    assert "https://example.com" in urls
    assert "https://example.com/rooms" in urls


def test_parse_sitemap_strips_trailing_slash():
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/rooms/</loc></url>
    </urlset>"""
    urls = _parse_sitemap_xml(xml)
    assert "https://example.com/rooms" in urls
    assert "https://example.com/rooms/" not in urls


def test_parse_sitemap_invalid_xml_returns_empty():
    assert _parse_sitemap_xml("not xml at all") == set()


# ── fetch_sitemap ─────────────────────────────────────────────────────────────

def test_fetch_sitemap_returns_urls(mini_site_session):
    urls = fetch_sitemap(BASE, mini_site_session)
    assert "https://example.com" in urls
    assert "https://example.com/rooms" in urls


def test_fetch_sitemap_404_returns_empty():
    session = MockSession({})  # нет sitemap.xml → 404
    urls = fetch_sitemap(BASE, session)
    assert urls == set()


def test_fetch_sitemap_network_error_returns_empty():
    class ErrSession:
        def get(self, url, **kwargs):
            raise ConnectionError("network down")

    assert fetch_sitemap(BASE, ErrSession()) == set()


# ── robots.txt ────────────────────────────────────────────────────────────────

_ROBOTS_TXT = """User-agent: *
Disallow: /admin/
Disallow: /cart
Allow: /cart/share

User-agent: Yandex
Disallow: /yandex-only/

Sitemap: https://example.com/sitemap.xml
"""


def test_parse_robots_txt_groups_by_user_agent():
    groups = _parse_robots_txt(_ROBOTS_TXT)
    agents = [g["agents"] for g in groups]
    assert ["*"] in agents
    assert ["yandex"] in agents


def test_parse_robots_txt_ignores_comments_and_sitemap():
    groups = _parse_robots_txt(_ROBOTS_TXT)
    star_rules = _select_robots_rules(groups, "*")
    assert ("disallow", "/admin/") in star_rules
    assert not any(k == "sitemap" for k, _ in star_rules)


def test_select_robots_rules_falls_back_to_star():
    groups = _parse_robots_txt(_ROBOTS_TXT)
    rules = _select_robots_rules(groups, "googlebot")
    assert ("disallow", "/admin/") in rules


def test_select_robots_rules_specific_agent():
    groups = _parse_robots_txt(_ROBOTS_TXT)
    rules = _select_robots_rules(groups, "yandex")
    assert rules == [("disallow", "/yandex-only/")]


def test_is_path_disallowed_matches_prefix():
    rules = [("disallow", "/admin/")]
    assert _is_path_disallowed("/admin/users", rules) is True


def test_is_path_disallowed_allows_unmatched_path():
    rules = [("disallow", "/admin/")]
    assert _is_path_disallowed("/catalog/", rules) is False


def test_is_path_disallowed_allow_overrides_more_specific_disallow():
    rules = [("disallow", "/cart"), ("allow", "/cart/share")]
    assert _is_path_disallowed("/cart/share", rules) is False
    assert _is_path_disallowed("/cart", rules) is True


def test_is_path_disallowed_empty_disallow_means_allow_all():
    rules = [("disallow", "")]
    assert _is_path_disallowed("/anything", rules) is False


def test_fetch_robots_txt_parses_star_rules(mini_site_session):
    rules = fetch_robots_txt(BASE, mini_site_session)
    assert ("disallow", "/private/") in rules


def test_fetch_robots_txt_404_returns_empty():
    session = MockSession({})
    assert fetch_robots_txt(BASE, session) == []


def test_fetch_robots_txt_network_error_returns_empty():
    class ErrSession:
        def get(self, url, **kwargs):
            raise ConnectionError("network down")

    assert fetch_robots_txt(BASE, ErrSession()) == []


# ── _to_absolute ──────────────────────────────────────────────────────────────

def test_to_absolute_relative_path():
    assert _to_absolute("/rooms", BASE) == "https://example.com/rooms"


def test_to_absolute_already_absolute():
    assert _to_absolute("https://other.com/page", BASE) == "https://other.com/page"


def test_to_absolute_root_slash():
    assert _to_absolute("/", BASE) == "https://example.com/"


# ── crawl_pages — фикстурный мини-сайт ───────────────────────────────────────

@pytest.fixture()
def sitemap_urls() -> set[str]:
    return _parse_sitemap_xml(_SITEMAP_XML)


def _crawl(urls: list[str], session: MockSession, sitemap: set[str]) -> list[dict]:
    return crawl_pages(urls, BASE, sitemap, session=session)


def test_crawl_200_status(mini_site_session, sitemap_urls):
    pages = _crawl(["/"], mini_site_session, sitemap_urls)
    assert pages[0]["http_status"] == 200


def test_crawl_200_populates_metadata(mini_site_session, sitemap_urls):
    pages = _crawl(["/"], mini_site_session, sitemap_urls)
    p = pages[0]
    assert p["title"] == "Аренда авто во Владивостоке"
    assert p["description"] == "Лучшая аренда автомобилей без водителя"
    assert p["h1"] == "Аренда авто без водителя"
    assert p["canonical_url"] == "https://example.com/"
    assert p["robots_directive"] == "index, follow"


def test_crawl_404_status(mini_site_session, sitemap_urls):
    pages = _crawl(["/missing"], mini_site_session, sitemap_urls)
    assert pages[0]["http_status"] == 404
    assert pages[0]["title"] is None


def test_crawl_redirect_records_chain(mini_site_session, sitemap_urls):
    pages = _crawl(["/redirect"], mini_site_session, sitemap_urls)
    p = pages[0]
    assert p["http_status"] == 200
    chain = json.loads(p["redirect_chain"])
    assert len(chain) == 1
    assert chain[0] == f"{BASE}/old-path"


def test_crawl_redirect_sets_final_url(mini_site_session, sitemap_urls):
    pages = _crawl(["/redirect"], mini_site_session, sitemap_urls)
    assert pages[0]["final_url"] == f"{BASE}/"


def test_crawl_no_redirect_empty_chain(mini_site_session, sitemap_urls):
    pages = _crawl(["/"], mini_site_session, sitemap_urls)
    chain = json.loads(pages[0]["redirect_chain"])
    assert chain == []


def test_crawl_canonical_non_self(mini_site_session, sitemap_urls):
    pages = _crawl(["/dup"], mini_site_session, sitemap_urls)
    p = pages[0]
    assert p["canonical_url"] == "https://example.com/"


def test_crawl_robots_noindex(mini_site_session, sitemap_urls):
    pages = _crawl(["/noindex"], mini_site_session, sitemap_urls)
    assert pages[0]["robots_directive"] == "noindex, nofollow"


def test_crawl_robots_directive_none_when_no_signal(mini_site_session, sitemap_urls):
    pages = _crawl(["/rooms"], mini_site_session, sitemap_urls)
    assert pages[0]["robots_directive"] is None


def test_crawl_x_robots_tag_header_sets_directive(mini_site_session, sitemap_urls):
    pages = crawl_pages(["/x-robots"], BASE, sitemap_urls, session=mini_site_session)
    assert pages[0]["robots_directive"] == "x-robots-tag: noindex"


def test_crawl_robots_txt_disallow_sets_directive(mini_site_session, sitemap_urls):
    rules = fetch_robots_txt(BASE, mini_site_session)
    pages = crawl_pages(
        ["/private/page"], BASE, sitemap_urls,
        session=mini_site_session, robots_rules=rules,
    )
    assert pages[0]["robots_directive"] == "robots.txt: disallow"


def test_crawl_robots_txt_allowed_path_not_flagged(mini_site_session, sitemap_urls):
    rules = fetch_robots_txt(BASE, mini_site_session)
    pages = crawl_pages(
        ["/rooms"], BASE, sitemap_urls,
        session=mini_site_session, robots_rules=rules,
    )
    assert pages[0]["robots_directive"] is None


def test_crawl_combines_meta_and_robots_txt(mini_site_session, sitemap_urls):
    rules = [("disallow", "/noindex")]
    pages = crawl_pages(
        ["/noindex"], BASE, sitemap_urls,
        session=mini_site_session, robots_rules=rules,
    )
    assert pages[0]["robots_directive"] == "noindex, nofollow; robots.txt: disallow"


def test_crawl_robots_txt_disallow_recorded_even_on_network_error(sitemap_urls):
    """robots.txt блокирует по пути — это не требует успешного HTTP-запроса к странице."""
    class ErrSession:
        def get(self, url, **kwargs):
            raise ConnectionError("timeout")

    pages = crawl_pages(
        ["/private/page"], BASE, sitemap_urls,
        session=ErrSession(), robots_rules=[("disallow", "/private/")],
    )
    assert pages[0]["robots_directive"] == "robots.txt: disallow"


def test_crawl_in_sitemap_true(mini_site_session, sitemap_urls):
    # /rooms → final_url = https://example.com/rooms → есть в sitemap
    pages = _crawl(["/rooms"], mini_site_session, sitemap_urls)
    assert pages[0]["in_sitemap"] is True


def test_crawl_not_in_sitemap(mini_site_session, sitemap_urls):
    # /noindex не указан в sitemap
    pages = _crawl(["/noindex"], mini_site_session, sitemap_urls)
    assert pages[0]["in_sitemap"] is False


def test_crawl_network_error_sets_status_zero(sitemap_urls):
    class ErrSession:
        def get(self, url, **kwargs):
            raise ConnectionError("timeout")

    pages = crawl_pages(["/"], BASE, sitemap_urls, session=ErrSession())
    assert pages[0]["http_status"] == 0
    assert pages[0]["title"] is None


def test_crawl_crawled_at_is_iso_utc(mini_site_session, sitemap_urls):
    import re
    pages = _crawl(["/"], mini_site_session, sitemap_urls)
    ts = pages[0]["crawled_at"]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)


def test_crawl_url_field_preserves_original(mini_site_session, sitemap_urls):
    pages = _crawl(["/rooms"], mini_site_session, sitemap_urls)
    assert pages[0]["url"] == "/rooms"


# ── crawl_pages: жёсткий таймаут (3.5-hang-fix) ────────────────────────────────
# Эмулирует сервер, медленно вытекающий телом мелкими чанками дольше read-таймаута,
# но без полного бездействия сокета — классический requests-гочер, из-за которого
# scalar timeout= не срабатывает (см. docs/implementation_status.md, 3.5-hang-diag).

class _SlowMockResponse:
    """Мок ответа, чьё .text выполняется дольше hard_timeout."""

    def __init__(self, delay: float, url: str = "") -> None:
        self.status_code = 200
        self.url = url
        self.history: list = []
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self._delay = delay

    @property
    def text(self) -> str:
        import time
        time.sleep(self._delay)
        return "<html><body>too slow</body></html>"

    def close(self) -> None:
        pass


class _SlowMockSession:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    def get(self, url: str, **kwargs) -> _SlowMockResponse:
        return _SlowMockResponse(self._delay, url=url)


def test_crawl_pages_hard_timeout_returns_instead_of_hanging(sitemap_urls):
    """Тело течёт дольше hard_timeout — crawl_pages не зависает, возвращает
    управление в пределах hard_timeout, а не delay сервера."""
    import time

    session = _SlowMockSession(delay=2.0)
    start = time.monotonic()
    pages = crawl_pages(["/"], BASE, sitemap_urls, session=session, hard_timeout=0.2)
    elapsed = time.monotonic() - start

    assert pages[0]["http_status"] == 0
    assert elapsed < 1.5


def test_crawl_pages_continues_after_hard_timeout_on_one_url(mini_site_session, sitemap_urls):
    """Один URL уходит в hard_timeout — обход остальной очереди не прерывается."""

    class _MixedSession:
        def __init__(self, slow_url: str, fallback: Any) -> None:
            self._slow_url = slow_url
            self._fallback = fallback

        def get(self, url: str, **kwargs):
            if url == self._slow_url:
                return _SlowMockResponse(2.0, url=url)
            return self._fallback.get(url, **kwargs)

    session = _MixedSession(f"{BASE}/", mini_site_session)
    pages = crawl_pages(
        ["/", "/rooms"], BASE, sitemap_urls, session=session, hard_timeout=0.2,
    )
    assert pages[0]["http_status"] == 0
    assert pages[1]["http_status"] == 200


# ── crawl_pages: Content-Type skip (3.5-hang-fix) ──────────────────────────────

def test_crawl_pages_skips_image_content_type_without_parsing_body(sitemap_urls):
    """Content-Type image/* — тело не парсится как HTML: http_status сохраняется,
    но title/h1 остаются None (мета не извлекается из бинарного содержимого)."""
    session = MockSession({
        f"{BASE}/photo.jpg": MockResponse(
            200, "not-real-html-binary-garbage", url=f"{BASE}/photo.jpg",
            content_type="image/jpeg",
        ),
    })
    pages = crawl_pages(["/photo.jpg"], BASE, sitemap_urls, session=session)
    p = pages[0]
    assert p["http_status"] == 200
    assert p["title"] is None
    assert p["h1"] is None


def test_crawl_pages_skips_pdf_content_type(sitemap_urls):
    session = MockSession({
        f"{BASE}/doc.pdf": MockResponse(
            200, "%PDF-1.4 binary", url=f"{BASE}/doc.pdf", content_type="application/pdf",
        ),
    })
    pages = crawl_pages(["/doc.pdf"], BASE, sitemap_urls, session=session)
    assert pages[0]["http_status"] == 200
    assert pages[0]["title"] is None


# ── write_pages_parquet ───────────────────────────────────────────────────────

def test_write_pages_parquet_schema(tmp_path, mini_site_session, sitemap_urls):
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas недоступен")

    pages = _crawl(["/", "/missing", "/redirect"], mini_site_session, sitemap_urls)
    out = write_pages_parquet(pages, tmp_path)

    assert out.exists()
    df = pd.read_parquet(out)
    assert list(df.columns) == PAGES_SCHEMA


def test_write_pages_parquet_dtypes(tmp_path, mini_site_session, sitemap_urls):
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas недоступен")

    pages = _crawl(["/", "/missing"], mini_site_session, sitemap_urls)
    write_pages_parquet(pages, tmp_path)
    df = pd.read_parquet(tmp_path / "pages.parquet")

    assert df["in_sitemap"].dtype == bool
    # http_status — nullable integer (Int64)
    assert str(df["http_status"].dtype) in ("Int64", "int64")


def test_write_pages_parquet_row_count(tmp_path, mini_site_session, sitemap_urls):
    try:
        import pandas as pd
    except ImportError:
        pytest.skip("pandas недоступен")

    urls = ["/", "/rooms", "/missing"]
    pages = _crawl(urls, mini_site_session, sitemap_urls)
    write_pages_parquet(pages, tmp_path)
    df = pd.read_parquet(tmp_path / "pages.parquet")
    assert len(df) == len(urls)


# ── _resolve_base_url ─────────────────────────────────────────────────────────

def test_resolve_base_url_from_crawl_config():
    config = {"crawl": {"base_url": "https://site.ru"}}
    assert _resolve_base_url(config) == "https://site.ru"


def test_resolve_base_url_strips_trailing_slash():
    config = {"crawl": {"base_url": "https://site.ru/"}}
    assert _resolve_base_url(config) == "https://site.ru"


def test_resolve_base_url_from_webmaster_host_id():
    config = {"sources": {"webmaster": {"host_id": "https:pognali.rent:443"}}}
    assert _resolve_base_url(config) == "https://pognali.rent"


def test_resolve_base_url_none_when_missing():
    assert _resolve_base_url({}) is None
