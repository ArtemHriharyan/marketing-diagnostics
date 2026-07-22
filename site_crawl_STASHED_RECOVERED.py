"""Экстрактор: сайт-кролер — очередь URL + сбор свойств страниц.

Задача 3.5A — построение очереди URL без HTTP-запросов.
Задача 3.5B — детерминированный HTTP-обход и запись pages.parquet.
Задача 3.5C — JS-diff (raw vs rendered), внутренние ссылки, BFS,
               link_graph.parquet.

Контракт:
    Читает   — config.crawl_seed_urls, config.crawl.max_urls, config.crawl.base_url,
               config.sources.crux.key_urls; опционально — канонические таблицы
               GSC/Webmaster (страницы по кликам) и Direct (страницы по расходу).
    Пишет    — data/raw/site_crawl/url_queue.json
               data/raw/site_crawl/pages.parquet   (если base_url доступен)
               data/raw/site_crawl/link_graph.parquet  (если BFS даёт рёбра)
               manifest.json
    Деградация — опционален; при отсутствии base_url HTTP-обход пропускается.
    LLM      — не используется (принцип 3).

pages.parquet schema (PAGES_SCHEMA):
    url, http_status, redirect_chain, final_url, canonical_url,
    robots_directive, in_sitemap, title, description, h1, crawled_at,
    js_content_diff

link_graph.parquet schema (LINK_GRAPH_SCHEMA):
    from_url, to_url, depth_from_home

redirect_chain   — JSON-список промежуточных URL (history.url) до финального.
js_content_diff  — JSON-строка {raw_link_count, rendered_link_count,
                   links_only_in_rendered, text_changed} или null.
                   Заполняется только если config.crawl.headless=true и
                   playwright установлен.
crawled_at       — ISO-8601 UTC.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

SCRIPT_VERSION = "0.3.0"
SOURCE = "site_crawl"
CANONICAL_TABLES: list[str] = ["pages"]

DEFAULT_MAX_URLS = 30
CRAWL_TIMEOUT_SEC = 15
MAX_BFS_DEPTH = 3

# Колонки выходной таблицы pages — строгий контракт (3.5B/3.5C).
PAGES_SCHEMA: list[str] = [
    "url",
    "http_status",
    "redirect_chain",
    "final_url",
    "canonical_url",
    "robots_directive",
    "in_sitemap",
    "title",
    "description",
    "h1",
    "crawled_at",
    "js_content_diff",
]

# Колонки выходной таблицы link_graph (3.5C).
LINK_GRAPH_SCHEMA: list[str] = ["from_url", "to_url", "depth_from_home"]

# После 4D GSC/Webmaster объединены в одну таблицу seo_queries.parquet;
# источник различается колонкой source ("gsc" | "webmaster").
_SEO_QUERIES_FILE = "seo_queries.parquet"
_PAGE_COL_SEO = "page"
_SOURCE_COL_SEO = "source"
_CLICKS_COL_SEO = "total_clicks"
_SOURCE_GSC = "gsc"
_SOURCE_WM = "webmaster"

# Колонка страницы в Директ-расходах.
# ПРИМЕЧАНИЕ: costs.parquet (build_costs) сейчас campaign-level — колонки
# entry_page там нет и не будет без отдельной пер-страничной выгрузки Директа.
# Поиск ниже корректно деградирует в [] (page_col not in df.columns), это не
# баг site_crawl — см. docs/implementation_status.md, задача 3.5-patch.
_PAGE_COL_COST = "entry_page"
_COST_COL = "cost"


# ── HTML-парсеры (stdlib) ─────────────────────────────────────────────────────

class _MetaParser(HTMLParser):
    """Извлекает title, description, h1, canonical, robots из HTML без зависимостей."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.description: str | None = None
        self.h1: str | None = None
        self.canonical_url: str | None = None
        self.robots_directive: str | None = None
        self._in_title = False
        self._in_h1 = False
        self._title_buf: list[str] = []
        self._h1_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = {k.lower(): v for k, v in attrs}
        tag = tag.lower()
        if tag == "title" and self.title is None:
            self._in_title = True
            self._title_buf = []
        elif tag == "h1" and self.h1 is None:
            self._in_h1 = True
            self._h1_buf = []
        elif tag == "meta":
            name = (d.get("name") or "").lower()
            if name == "description" and self.description is None:
                raw = (d.get("content") or "").strip()
                self.description = raw or None
            elif name == "robots" and self.robots_directive is None:
                raw = (d.get("content") or "").strip()
                self.robots_directive = raw or None
        elif tag == "link":
            rel = (d.get("rel") or "").lower()
            if rel == "canonical" and self.canonical_url is None:
                raw = (d.get("href") or "").strip()
                self.canonical_url = raw or None

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._in_title:
            self._in_title = False
            text = "".join(self._title_buf).strip()
            self.title = text or None
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
            text = "".join(self._h1_buf).strip()
            self.h1 = text or None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        elif self._in_h1:
            self._h1_buf.append(data)


class _LinkParser(HTMLParser):
    """Извлекает href из <a> тегов."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() == "a":
            d = {k.lower(): v for k, v in attrs}
            href = (d.get("href") or "").strip()
            if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                self.hrefs.append(href)


class _TextParser(HTMLParser):
    """Извлекает видимый текст (без script/style/noscript) для JS-diff."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            s = data.strip()
            if s:
                self._parts.append(s)

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def _parse_page_meta(html_text: str) -> dict[str, Any]:
    """Разобрать HTML и вернуть dict с title/description/h1/canonical_url/robots_directive."""
    parser = _MetaParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return {
        "title": parser.title,
        "description": parser.description,
        "h1": parser.h1,
        "canonical_url": parser.canonical_url,
        "robots_directive": parser.robots_directive,
    }


def _visible_text(html: str) -> str:
    """Вернуть видимый текст HTML без тегов script/style/noscript."""
    parser = _TextParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.text


# ── Работа со ссылками ────────────────────────────────────────────────────────

def _extract_links(
    html_text: str,
    page_url: str,
    base_url: str,
) -> dict[str, list[str]]:
    """Извлечь и классифицировать ссылки из HTML.

    Возвращает {"internal": [...], "external": [...]}.
    "internal" — уникальные абсолютные URL того же домена, что base_url.
    "external" — уникальные абсолютные URL сторонних доменов.
    Фрагменты (#anchor), mailto:, tel:, javascript: — игнорируются.
    """
    parser = _LinkParser()
    try:
        parser.feed(html_text)
    except Exception:
        pass

    base_netloc = urlparse(base_url).netloc
    internal: list[str] = []
    external: list[str] = []
    seen: set[str] = set()

    for href in parser.hrefs:
        abs_url = urljoin(page_url, href).split("#")[0]
        if not abs_url or abs_url in seen:
            continue
        seen.add(abs_url)
        if urlparse(abs_url).netloc == base_netloc:
            internal.append(abs_url)
        else:
            external.append(abs_url)

    return {"internal": internal, "external": external}


# ── Sitemap ───────────────────────────────────────────────────────────────────

def _parse_sitemap_xml(xml_text: str) -> set[str]:
    """Извлечь URL из sitemap XML (обычный и index-формат). Нормализует trailing slash."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return set()
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: set[str] = set()
    for loc in root.findall(".//sm:loc", ns):
        if loc.text:
            u = loc.text.strip()
            urls.add(u.rstrip("/") or "/")
    return urls


def fetch_sitemap(
    base_url: str,
    session: Any,
    timeout: int = CRAWL_TIMEOUT_SEC,
) -> set[str]:
    """Скачать /sitemap.xml и вернуть множество нормализованных URL. При ошибке — пустое."""
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    try:
        resp = session.get(sitemap_url, timeout=timeout, allow_redirects=True)
        if getattr(resp, "status_code", None) != 200:
            return set()
        return _parse_sitemap_xml(resp.text)
    except Exception:
        return set()


# ── robots.txt ────────────────────────────────────────────────────────────────

def _parse_robots_txt(text: str) -> list[dict[str, Any]]:
    """Разобрать robots.txt на группы {agents: [...], rules: [(kind, path), ...]}.

    kind — "disallow" | "allow" (в нижнем регистре, как в файле).
    Группа — блок последовательных User-agent строк + следующие за ними
    Allow/Disallow до следующего User-agent (RFC 9309).
    """
    groups: list[dict[str, Any]] = []
    current_agents: list[str] = []
    current_rules: list[tuple[str, str]] = []
    rule_seen = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "user-agent":
            if rule_seen:
                groups.append({"agents": current_agents, "rules": current_rules})
                current_agents = []
                current_rules = []
                rule_seen = False
            current_agents.append(value.lower())
        elif key in ("disallow", "allow"):
            current_rules.append((key, value))
            rule_seen = True

    if current_agents or current_rules:
        groups.append({"agents": current_agents, "rules": current_rules})
    return groups


def _select_robots_rules(
    groups: list[dict[str, Any]], user_agent: str = "*"
) -> list[tuple[str, str]]:
    """Выбрать правила для user_agent; иначе — группа '*'; иначе — пусто."""
    ua = user_agent.lower()
    for g in groups:
        if ua in g["agents"]:
            return g["rules"]
    for g in groups:
        if "*" in g["agents"]:
            return g["rules"]
    return []


def _is_path_disallowed(path: str, rules: list[tuple[str, str]]) -> bool:
    """Проверить путь по правилам Disallow/Allow (побеждает самое длинное совпадение)."""
    best_len = -1
    best_disallow = False
    for kind, pattern in rules:
        if pattern == "":
            # Disallow: (пусто) означает «разрешено всё» — не блокирует.
            continue
        if path.startswith(pattern) and len(pattern) > best_len:
            best_len = len(pattern)
            best_disallow = kind == "disallow"
    return best_disallow


def fetch_robots_txt(
    base_url: str,
    session: Any,
    timeout: int = CRAWL_TIMEOUT_SEC,
) -> list[tuple[str, str]]:
    """Скачать /robots.txt и вернуть правила Disallow/Allow для user-agent "*".

    При отсутствии файла, ошибке сети или ином статусе — пустой список
    (краулинг не блокируется, деградация мягкая, как у sitemap).
    """
    robots_url = base_url.rstrip("/") + "/robots.txt"
    try:
        resp = session.get(robots_url, timeout=timeout, allow_redirects=True)
        if getattr(resp, "status_code", None) != 200:
            return []
        text = getattr(resp, "text", "") or ""
        groups = _parse_robots_txt(text)
        return _select_robots_rules(groups, "*")
    except Exception:
        return []


def _get_header(headers: Any, name: str) -> str | None:
    """Регистронезависимый доступ к HTTP-заголовку (requests уже нечувствителен
    к регистру, но мок-объекты в тестах используют обычный dict)."""
    if not headers:
        return None
    try:
        val = headers.get(name)
        if val:
            return val
    except AttributeError:
        return None
    name_lower = name.lower()
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


# ── Headless-рендеринг (опционально) ─────────────────────────────────────────

def _render_headless(url: str, timeout: int = CRAWL_TIMEOUT_SEC) -> str | None:
    """Рендеринг страницы через headless Chromium (playwright).

    Возвращает HTML после выполнения JS или None, если playwright не установлен
    либо произошла любая ошибка (мягкая деградация).
    Требует: pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


# ── JS-diff ───────────────────────────────────────────────────────────────────

_JS_DIFF_TEXT_THRESHOLD = 100  # минимальная разница в символах для text_changed=True


def compute_js_diff(
    raw_html: str,
    rendered_html: str | None,
    base_url: str,
    page_url: str | None = None,
) -> dict[str, Any] | None:
    """Сравнить внутренние ссылки и видимый текст raw HTML vs headless-рендеринга.

    Возвращает None если rendered_html is None (playwright недоступен).
    Иначе:
        raw_link_count        — кол-во внутренних ссылок в raw HTML
        rendered_link_count   — кол-во внутренних ссылок в rendered HTML
        links_only_in_rendered — ссылки, добавленные JS (нет в raw)
        text_changed          — True если видимый текст вырос/убыл более чем
                                на _JS_DIFF_TEXT_THRESHOLD символов
    """
    if rendered_html is None:
        return None

    purl = page_url or (base_url.rstrip("/") + "/")
    raw_links = set(_extract_links(raw_html, purl, base_url)["internal"])
    rendered_links = set(_extract_links(rendered_html, purl, base_url)["internal"])

    raw_text_len = len(_visible_text(raw_html))
    rendered_text_len = len(_visible_text(rendered_html))
    text_changed = abs(rendered_text_len - raw_text_len) > _JS_DIFF_TEXT_THRESHOLD

    return {
        "raw_link_count": len(raw_links),
        "rendered_link_count": len(rendered_links),
        "links_only_in_rendered": sorted(rendered_links - raw_links),
        "text_changed": text_changed,
    }


# ── HTTP-обход ────────────────────────────────────────────────────────────────

def _to_absolute(url: str, base_url: str) -> str:
    """Превратить относительный путь в абсолютный URL."""
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    base = base_url.rstrip("/")
    if not url.startswith("/"):
        url = "/" + url
    return base + url


def _combine_robots_directive(
    meta_robots: str | None,
    x_robots_tag: str | None,
    robots_txt_disallowed: bool,
) -> str | None:
    """Свести meta robots + X-Robots-Tag + robots.txt в одно строковое поле.

    Единственный источник (обычно meta) даёт исходную строку без изменений
    (совместимость со старым контрактом). При нескольких источниках —
    объединение через "; ".
    """
    parts: list[str] = []
    if meta_robots:
        parts.append(meta_robots)
    if x_robots_tag:
        parts.append(f"x-robots-tag: {x_robots_tag}")
    if robots_txt_disallowed:
        parts.append("robots.txt: disallow")
    return "; ".join(parts) if parts else None


def crawl_pages(
    urls: list[str],
    base_url: str,
    sitemap_urls: set[str],
    *,
    session: Any,
    log: Any = None,
    timeout: int = CRAWL_TIMEOUT_SEC,
    headless: bool = False,
    robots_rules: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Обойти список URL и собрать свойства страниц.

    Возвращает список dict по схеме PAGES_SCHEMA.
    Ошибки соединения не роняют пайплайн: http_status=0, мета-поля=None.
    headless=True включает JS-рендеринг через playwright (требует playwright install).
    robots_rules — правила Disallow/Allow для user-agent "*" из fetch_robots_txt;
    robots_directive сводит meta robots + X-Robots-Tag заголовок + robots.txt.
    """
    log = log or (lambda _: None)
    pages: list[dict[str, Any]] = []
    robots_rules = robots_rules or []

    for url in urls:
        abs_url = _to_absolute(url, base_url)
        crawled_at = datetime.now(timezone.utc).isoformat()

        path = urlparse(abs_url).path or "/"
        robots_txt_disallowed = _is_path_disallowed(path, robots_rules)

        record: dict[str, Any] = {
            "url": url,
            "http_status": None,
            "redirect_chain": "[]",
            "final_url": abs_url,
            "canonical_url": None,
            "robots_directive": _combine_robots_directive(None, None, robots_txt_disallowed),
            "in_sitemap": False,
            "title": None,
            "description": None,
            "h1": None,
            "crawled_at": crawled_at,
            "js_content_diff": None,
        }

        try:
            resp = session.get(abs_url, timeout=timeout, allow_redirects=True)
            status = getattr(resp, "status_code", None)
            record["http_status"] = status

            # Цепочка редиректов: промежуточные URL из history (без финального).
            history = getattr(resp, "history", []) or []
            chain = [getattr(r, "url", "") for r in history]
            record["redirect_chain"] = json.dumps(chain, ensure_ascii=False)

            final_url = getattr(resp, "url", abs_url) or abs_url
            record["final_url"] = final_url

            # Проверка наличия в sitemap по нормализованному final_url.
            norm = (final_url or "").rstrip("/") or "/"
            record["in_sitemap"] = norm in sitemap_urls

            x_robots_tag = _get_header(getattr(resp, "headers", None), "X-Robots-Tag")

            # Парсинг HTML: для 2xx ответов.
            meta_robots: str | None = None
            if status is not None and 200 <= status < 300:
                text = getattr(resp, "text", "") or ""
                if text:
                    meta = _parse_page_meta(text)
                    meta_robots = meta.pop("robots_directive", None)
                    record.update(meta)

                    if headless:
                        rendered = _render_headless(final_url, timeout)
                        diff = compute_js_diff(text, rendered, base_url, final_url)
                        record["js_content_diff"] = (
                            json.dumps(diff, ensure_ascii=False) if diff is not None else None
                        )

            record["robots_directive"] = _combine_robots_directive(
                meta_robots, x_robots_tag, robots_txt_disallowed
            )

        except Exception as exc:
            log(f"{SOURCE}: ошибка при обходе {url}: {type(exc).__name__}")
            record["http_status"] = 0

        log(f"{SOURCE}: {url} → {record['http_status']}")
        pages.append(record)

    return pages


def write_pages_parquet(pages: list[dict[str, Any]], out_dir: Path) -> Path:
    """Записать список страниц в pages.parquet по схеме PAGES_SCHEMA."""
    import pandas as pd

    out = Path(out_dir) / "pages.parquet"
    df = pd.DataFrame(pages, columns=PAGES_SCHEMA)
    df["http_status"] = pd.to_numeric(df["http_status"], errors="coerce").astype("Int64")
    df["in_sitemap"] = df["in_sitemap"].astype(bool)
    df.to_parquet(out, index=False)
    return out


# ── BFS по внутренним ссылкам ─────────────────────────────────────────────────

def crawl_bfs(
    base_url: str,
    session: Any,
    *,
    max_depth: int = MAX_BFS_DEPTH,
    log: Any = None,
    timeout: int = CRAWL_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """BFS по внутренним ссылкам от главной страницы до max_depth включительно.

    Алгоритм:
      - глубина 0 — home (base_url + "/")
      - глубина depth_from_home — рёбра из страниц на глубине depth_from_home-1
      - страницы на глубине max_depth не обходятся (их дочерние рёбра не записываются)

    Циклы устраняются через множество visited (нормализованный URL без trailing slash).

    Возвращает список dict {from_url, to_url, depth_from_home}.
    """
    log = log or (lambda _: None)

    home = base_url.rstrip("/") + "/"

    def _norm(u: str) -> str:
        return u.rstrip("/") or u

    visited: set[str] = {_norm(home)}
    queue: deque[tuple[str, int]] = deque([(home, 0)])
    edges: list[dict[str, Any]] = []

    while queue:
        current_url, depth = queue.popleft()

        if depth >= max_depth:
            continue

        try:
            resp = session.get(current_url, timeout=timeout, allow_redirects=True)
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                continue
            html = getattr(resp, "text", "") or ""
        except Exception as exc:
            log(f"{SOURCE}: BFS ошибка {current_url}: {type(exc).__name__}")
            continue

        links = _extract_links(html, current_url, base_url)
        for link in links["internal"]:
            edges.append({
                "from_url": current_url,
                "to_url": link,
                "depth_from_home": depth + 1,
            })
            norm_link = _norm(link)
            if norm_link not in visited:
                visited.add(norm_link)
                queue.append((link, depth + 1))

    log(f"{SOURCE}: BFS завершён, {len(edges)} рёбер")
    return edges


def write_link_graph_parquet(edges: list[dict[str, Any]], out_dir: Path) -> Path:
    """Записать link_graph.parquet по схеме LINK_GRAPH_SCHEMA."""
    import pandas as pd

    out = Path(out_dir) / "link_graph.parquet"
    df = pd.DataFrame(edges, columns=LINK_GRAPH_SCHEMA)
    df["depth_from_home"] = pd.to_numeric(
        df["depth_from_home"], errors="coerce"
    ).astype("Int64")
    df.to_parquet(out, index=False)
    return out


# ── resolve_base_url ──────────────────────────────────────────────────────────

def _resolve_base_url(config: dict[str, Any]) -> str | None:
    """Определить базовый URL сайта для HTTP-обхода.

    Приоритет: config.crawl.base_url → config.sources.webmaster.host_id.
    host_id формата «https:example.com:443» → «https://example.com».
    """
    crawl_cfg = config.get("crawl") or {}
    base = crawl_cfg.get("base_url")
    if base:
        return str(base).rstrip("/")

    wm = ((config.get("sources") or {}).get("webmaster") or {})
    host_id = wm.get("host_id")
    if host_id and isinstance(host_id, str):
        parts = host_id.split(":")
        if len(parts) >= 2:
            scheme = parts[0].lower()
            domain = parts[1].lstrip("/")
            if scheme in ("http", "https") and domain:
                return f"{scheme}://{domain}"

    return None


# ── Публичный API ─────────────────────────────────────────────────────────────

def resolve_max_urls(config: dict[str, Any], default: int = DEFAULT_MAX_URLS) -> int:
    """max_urls из config.crawl.max_urls, иначе ``default``."""
    crawl_cfg = (config.get("crawl") or {})
    val = crawl_cfg.get("max_urls")
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def build_url_priority_list(
    config: dict[str, Any],
    canonical_dir: Path | None = None,
    *,
    max_urls: int | None = None,
    top_n_each_source: int = 20,
) -> dict[str, Any]:
    """Построить приоритетный список URL без HTTP-запросов.

    Возвращает словарь::

        {
            "urls":            list[str],  # итоговый список, ≤ max_urls
            "total_candidates": int,       # до усечения
            "truncated":       bool,
            "caveat":          str | None, # заполнен при усечении
            "url_sources":     dict[str, str],  # url -> откуда взят
        }
    """
    effective_max = max_urls if max_urls is not None else resolve_max_urls(config)

    seen: dict[str, str] = {}  # url -> источник (первый выигрывает)

    def _add(url: str, source_label: str) -> None:
        url = url.strip()
        if not url:
            return
        # Normalise trailing slash but preserve bare "/" root.
        normalised = url.rstrip("/") or "/"
        if normalised not in seen:
            seen[normalised] = source_label

    # 1. Явные seed URL из config.
    for u in (config.get("crawl_seed_urls") or []):
        _add(str(u), "explicit_seed")

    # 2. Ключевые посадочные URL из CrUX-конфига.
    crux_cfg = ((config.get("sources") or {}).get("crux") or {})
    for u in (crux_cfg.get("key_urls") or []):
        _add(str(u), "crux_key_url")

    # 3–5. Страницы из канонических таблиц (если доступны).
    if canonical_dir is not None:
        canonical_dir = Path(canonical_dir)

        # 3. По расходу Директа.
        for url in _pages_from_canonical(
            canonical_dir, "costs.parquet", _PAGE_COL_COST, _COST_COL, top_n_each_source
        ):
            _add(url, "top_spend")

        # 4а. Органика GSC.
        for url in _pages_from_seo_queries(canonical_dir, _SOURCE_GSC, top_n_each_source):
            _add(url, "top_organic_gsc")

        # 4б. Органика Webmaster (запасной вариант, если GSC нет).
        for url in _pages_from_seo_queries(canonical_dir, _SOURCE_WM, top_n_each_source):
            _add(url, "top_organic_webmaster")

        # 5. Страницы с ключевыми словами из wordstat_seeds.
        seeds = [str(s).lower() for s in (config.get("wordstat_seeds") or []) if str(s).strip()]
        if seeds:
            for url in _pages_matching_keywords(
                canonical_dir, seeds, top_n_each_source
            ):
                _add(url, "keyword_match")

    all_candidates = list(seen.keys())
    total = len(all_candidates)
    truncated = total > effective_max
    final_urls = all_candidates[:effective_max]

    caveat: str | None = None
    if truncated:
        dropped = total - effective_max
        caveat = (
            f"Очередь усечена до {effective_max} URL (crawl.max_urls). "
            f"Отброшено {dropped} кандидатов. "
            "Увеличьте crawl.max_urls в config.yaml, если нужно покрыть больше страниц."
        )

    return {
        "urls": final_urls,
        "total_candidates": total,
        "truncated": truncated,
        "caveat": caveat,
        "url_sources": {u: seen[u] for u in final_urls},
    }


def extract(
    config: dict[str, Any],
    env: dict[str, str],
    paths: Any,
    *,
    defaults: dict[str, Any] | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Записать очередь URL, выполнить HTTP-обход страниц и BFS граф ссылок.

    Если config.crawl.base_url (или webmaster.host_id) не задан — HTTP-обход
    и BFS пропускаются, pages.parquet и link_graph.parquet не создаются.
    config.crawl.headless=true включает JS-рендеринг (требует playwright).
    """
    from . import _common as C

    log = log or (lambda _msg: None)

    canonical_dir: Path | None = None
    try:
        canonical_dir = Path(paths.canonical) if paths.canonical else None
    except AttributeError:
        pass

    result = build_url_priority_list(config, canonical_dir)
    log(
        f"{SOURCE}: кандидатов {result['total_candidates']}, "
        f"итого в очереди {len(result['urls'])}"
        + (f" (усечено, кавет: {result['caveat']!r})" if result["truncated"] else "")
    )

    out_dir = C.reset_dir(C.source_dir(paths, SOURCE))
    _dump(out_dir / "url_queue.json", result)

    pages: list[dict[str, Any]] = []
    bfs_edges: list[dict[str, Any]] = []
    headless_stats: dict[str, Any] | None = None
    base_url = _resolve_base_url(config)
    headless = bool((config.get("crawl") or {}).get("headless", False))

    if base_url and result["urls"]:
        try:
            import requests as _requests

            session = _requests.Session()
            session.headers["User-Agent"] = "marketing-diagnostics-crawler/1.0"
            sitemap_urls = fetch_sitemap(base_url, session)
            log(f"{SOURCE}: sitemap содержит {len(sitemap_urls)} URL")

            robots_rules = fetch_robots_txt(base_url, session)
            log(f"{SOURCE}: robots.txt содержит {len(robots_rules)} правил (user-agent *)")

            pages = crawl_pages(
                result["urls"],
                base_url,
                sitemap_urls,
                session=session,
                log=log,
                headless=headless,
                robots_rules=robots_rules,
            )
            if pages:
                write_pages_parquet(pages, out_dir)
                log(f"{SOURCE}: pages.parquet записан, {len(pages)} страниц")

            if headless and pages:
                attempted = sum(
                    1 for p in pages
                    if p.get("http_status") is not None and 200 <= p["http_status"] < 300
                )
                populated = sum(1 for p in pages if p.get("js_content_diff"))
                headless_stats = {"attempted": attempted, "diff_populated": populated}
                if attempted > 0 and populated == 0:
                    log(
                        f"{SOURCE}: headless включён, но js_content_diff пуст на всех "
                        f"{attempted} проверенных страницах — либо сайт SSR (пустой diff "
                        "корректен), либо playwright/chromium недоступен в этой среде "
                        "(pip install playwright && playwright install chromium). "
                        "Различить эти случаи без ручного подтверждения нельзя — "
                        "см. docs/implementation_status.md, задача 3.5-patch."
                    )

            # BFS от главной страницы (3.5C).
            bfs_edges = crawl_bfs(base_url, session, log=log)
            if bfs_edges:
                write_link_graph_parquet(bfs_edges, out_dir)
                log(f"{SOURCE}: link_graph.parquet записан, {len(bfs_edges)} рёбер")

        except Exception as exc:
            log(f"{SOURCE}: HTTP-обход пропущен: {exc}")
    else:
        log(f"{SOURCE}: base_url не задан — HTTP-обход пропущен")

    manifest = _record_manifest(
        paths, result, crawled=len(pages), bfs_edges=len(bfs_edges),
        headless=headless, headless_stats=headless_stats,
    )
    log(f"{SOURCE}: url_queue.json записан, {len(result['urls'])} URL")

    return {
        "source": SOURCE,
        "rows": len(result["urls"]),
        "canonical_tables": CANONICAL_TABLES,
        "manifest": manifest,
        "pages_crawled": len(pages),
        "bfs_edges": len(bfs_edges),
        **result,
    }


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _pages_from_canonical(
    canonical_dir: Path,
    table_file: str,
    page_col: str,
    sort_col: str,
    top_n: int,
) -> list[str]:
    """Топ-N уникальных URL из parquet-таблицы, отсортированных по убыванию sort_col."""
    path = canonical_dir / table_file
    if not path.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path, columns=[c for c in [page_col, sort_col] if c])
        if page_col not in df.columns:
            return []
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False)
        pages = df[page_col].dropna().astype(str).unique().tolist()
        result_pages = []
        for p in pages[:top_n]:
            p = p.strip()
            if p:
                result_pages.append(p.rstrip("/") or "/")
        return result_pages
    except Exception:
        return []


def _pages_from_seo_queries(
    canonical_dir: Path,
    source_value: str,
    top_n: int,
) -> list[str]:
    """Топ-N уникальных URL по total_clicks для source='gsc'|'webmaster'
    из объединённой таблицы seo_queries.parquet (после 4D GSC и Webmaster
    больше не пишутся отдельными файлами)."""
    path = canonical_dir / _SEO_QUERIES_FILE
    if not path.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(
            path, columns=[_PAGE_COL_SEO, _SOURCE_COL_SEO, _CLICKS_COL_SEO]
        )
        if _PAGE_COL_SEO not in df.columns or _SOURCE_COL_SEO not in df.columns:
            return []
        df = df[df[_SOURCE_COL_SEO] == source_value]
        if df.empty:
            return []
        if _CLICKS_COL_SEO in df.columns:
            ranked = df.groupby(_PAGE_COL_SEO)[_CLICKS_COL_SEO].sum()
            ranked = ranked.sort_values(ascending=False)
            pages = ranked.index.tolist()
        else:
            pages = df[_PAGE_COL_SEO].dropna().astype(str).unique().tolist()
        result_pages = []
        for p in pages[:top_n]:
            p = str(p).strip()
            if p:
                result_pages.append(p.rstrip("/") or "/")
        return result_pages
    except Exception:
        return []


def _pages_matching_keywords(
    canonical_dir: Path,
    seeds: list[str],
    top_n: int,
) -> list[str]:
    """Страницы GSC/Webmaster (seo_queries.parquet), URL которых содержит
    слово из seeds."""
    path = canonical_dir / _SEO_QUERIES_FILE
    if not path.exists():
        return []
    matched: list[str] = []
    try:
        import pandas as pd
        df = pd.read_parquet(path, columns=[_PAGE_COL_SEO])
        pages = df[_PAGE_COL_SEO].dropna().astype(str).unique()
        for page in pages:
            page_lower = page.lower()
            if any(seed in page_lower for seed in seeds):
                url = page.strip().rstrip("/")
                if url not in matched:
                    matched.append(url)
            if len(matched) >= top_n:
                break
    except Exception:
        return matched[:top_n]
    return matched[:top_n]


def _record_manifest(
    paths: Any,
    result: dict[str, Any],
    crawled: int = 0,
    bfs_edges: int = 0,
    headless: bool = False,
    headless_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..pipeline import manifest as manifest_mod

    extra: dict[str, Any] = {
        "source_mode": "deterministic",
        "total_candidates": result["total_candidates"],
        "urls_queued": len(result["urls"]),
        "truncated": result["truncated"],
        "pages_crawled": crawled,
        "bfs_edges": bfs_edges,
        "headless_enabled": headless,
    }
    if result["caveat"]:
        extra["caveat"] = result["caveat"]
    if headless_stats is not None:
        extra["headless_pages_attempted"] = headless_stats["attempted"]
        extra["headless_diff_populated"] = headless_stats["diff_populated"]

    return manifest_mod.update_source(
        Path(paths.raw), SOURCE,
        date_from="", date_to="",
        rows=len(result["urls"]),
        script_version=SCRIPT_VERSION,
        canonical_tables=CANONICAL_TABLES,
        extra=extra,
    )


def _dump(path: Path, obj: Any) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
