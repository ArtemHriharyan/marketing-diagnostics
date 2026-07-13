"""Смоук-тесты слоя extract на моках HTTP (без реальных токенов).

Проверяют инвариант этапа extract: каждый экстрактор на фикстурах-ответах API
корректно пишет сырьё в data/raw/<source>/ и запись в manifest.json, а на
мёртвом токене поднимает AuthError с кодом «источник недоступен» (принцип 4).

Реальная сеть не трогается: session мокается объектом FakeSession, который
отдаёт заранее заготовленные ответы по URL. Токены — фиктивные.
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extract import _common as C  # noqa: E402
from src.extract import direct, metrika_logs, metrika_reports  # noqa: E402
from src.extract import crm_import, wordstat  # noqa: E402
from src.extract import gsc_api, gsc_manual  # noqa: E402
from src.extract import webmaster_api, webmaster_manual  # noqa: E402
from src.extract import crux  # noqa: E402
from src.pipeline import manifest as manifest_mod  # noqa: E402


# ── Тестовые дублёры HTTP ──────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, status_code=200, *, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("нет JSON в ответе")
        return self._json


class FakeSession:
    """Отдаёт ответы по совпадению подстроки в URL; запоминает вызовы.

    routes — список (predicate(method, url), response | callable(call_index)).
    Для одного и того же маршрута можно вернуть разные ответы по номеру вызова
    (напр. logrequest: сначала 'processing', потом 'processed').
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self._per_route_counts = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for idx, (pred, responder) in enumerate(self.routes):
            if pred(method, url):
                n = self._per_route_counts.get(idx, 0)
                self._per_route_counts[idx] = n + 1
                return responder(n) if callable(responder) else responder
        raise AssertionError(f"нет мока для {method} {url}")


def _contains(*needles):
    return lambda method, url: all(n in url for n in needles)


class Paths:
    """Мини-дублёр ClientPaths: экстрактору нужны .raw и .root (ручные экспорты)."""

    def __init__(self, raw: Path, root: Path | None = None):
        self.raw = raw
        # raw = <root>/data/raw -> root по умолчанию на два уровня выше.
        self.root = root if root is not None else raw.parent.parent


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "data" / "raw", root=tmp_path)


CONFIG_METRIKA = {
    "sources": {"metrika": {"enabled": True, "counter_id": 12345}},
    "data_window": {"date_from": "2026-05-01", "date_to": "2026-06-30"},
}
CONFIG_DIRECT = {
    "sources": {"direct": {"enabled": True, "client_login": "test-login"}},
    "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"},
}
ENV = {"METRIKA_TOKEN": "fake-metrika", "DIRECT_TOKEN": "fake-direct"}
NO_SLEEP = lambda _sec: None


# ── metrika_logs ───────────────────────────────────────────────────────────
# Пробные поля патча, которые API может отклонить (имена сверены с документацией;
# isRobotPro и lastSignGCLID/lastSignhasGCLID — GCLID-зонды, статус по evaluate).
_METRIKA_BAD_FIELDS = {"ym:s:isRobotPro", "ym:s:lastSignGCLID", "ym:s:lastSignhasGCLID"}


def _evaluate_route(get_session, bad=frozenset()):
    """Мок logrequests/evaluate: 400 «Unknown field» если в составе есть bad-поле."""
    def responder(_n):
        fields = get_session().calls[-1][2]["params"]["fields"].split(",")
        offending = [f for f in fields if f in bad]
        if offending:
            msg = f"Unknown field in the request: {offending[0]} for the source visits"
            return FakeResponse(status_code=400, json_data={
                "errors": [{"error_type": "invalid_parameter", "message": msg}],
                "code": 400, "message": msg})
        return FakeResponse(json_data={"log_request_evaluation": {"possible": True}})
    return (lambda m, u: m == "GET" and u.endswith("/logrequests/evaluate"), responder)


def test_metrika_logs_writes_raw_and_manifest(paths):
    """Полный цикл Logs API: evaluate -> create -> poll -> download -> csv.gz + manifest."""
    header = "\t".join(metrika_logs.VISIT_FIELDS)
    part_text = header + "\n" + "\t".join(["v1", "c1", "2026-05-02 10:00:00"] +
                                          ["x"] * (len(metrika_logs.VISIT_FIELDS) - 3)) + "\n"

    def poll_responder(n):
        status = "created" if n == 0 else "processed"
        parts = [] if n == 0 else [{"part_number": 0}]
        return FakeResponse(json_data={"log_request": {
            "request_id": 777, "status": status, "parts": parts}})

    box = {}
    routes = [
        _evaluate_route(lambda: box["session"]),  # все поля валидны (bad=пусто)
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": 777, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith("/logrequest/777"), poll_responder),
        (_contains("/part/0/download"), FakeResponse(text=part_text)),
    ]
    session = FakeSession(routes)
    box["session"] = session

    result = metrika_logs.extract(
        CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP,
    )

    # Два месячных чанка (май, июнь) -> два запроса, по одной части.
    src_dir = paths.raw / "metrika_logs"
    gz_files = sorted(src_dir.glob("*.csv.gz"))
    assert len(gz_files) == 2
    with gzip.open(gz_files[0], "rt", encoding="utf-8") as fh:
        assert fh.read().startswith("ym:s:visitID")

    assert result["rows"] == 2  # по одной строке данных на чанк
    manifest = manifest_mod.load_manifest(paths.raw)
    entry = manifest["sources"]["metrika_logs"]
    assert entry["rows"] == 2
    assert entry["canonical_tables"] == ["visits"]
    assert entry["date_from"] == "2026-05-01" and entry["date_to"] == "2026-06-30"
    assert "fetched_at" in entry

    # Патч: новые поля согласованы и зафиксированы в манифесте.
    assert result["patch_backfill"] is False
    assert entry["patch_date"] == metrika_logs.PATCH_DATE
    assert entry["schema_version"] == metrika_logs.SCHEMA_VERSION
    for new_field in ("ym:s:lastTrafficSource", "ym:s:browser", "ym:s:operatingSystem",
                      "ym:s:screenWidth", "ym:s:screenHeight",
                      "ym:s:regionCountry", "ym:s:regionCity"):
        assert new_field in metrika_logs.VISIT_FIELDS
        assert new_field in entry["patch_fields"]
    # Наивная и last-significant модели атрибуции — ОБЕ (для T02).
    assert "ym:s:lastTrafficSource" in metrika_logs.VISIT_FIELDS
    assert "ym:s:lastsignTrafficSource" in metrika_logs.VISIT_FIELDS
    # Все поля валидны в этом моке -> ничего не отброшено.
    assert entry["dropped_fields"] == []


def test_metrika_logs_negotiation_isolates_unsupported_fields(paths):
    """evaluate отклоняет isRobotPro/lastSignGCLID/lastSignhasGCLID -> бинарное деление изолирует именно их."""
    cfg = {**CONFIG_METRIKA,
           "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}}

    logged: list[str] = []
    box = {}
    routes = [
        _evaluate_route(lambda: box["session"], bad=_METRIKA_BAD_FIELDS),
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": 42, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith("/logrequest/42"),
         FakeResponse(json_data={"log_request": {"request_id": 42, "status": "processed",
                                                 "parts": [{"part_number": 0}]}})),
        (_contains("/part/0/download"),
         FakeResponse(text="ym:s:visitID\tym:s:browser\nv1\tchrome\n")),
    ]
    session = FakeSession(routes)
    box["session"] = session

    result = metrika_logs.extract(cfg, ENV, paths, session=session,
                                  sleeper=NO_SLEEP, log=logged.append)

    # Изолированы именно неподдерживаемые поля, а не весь пакет новых.
    assert set(result["dropped_fields"]) == _METRIKA_BAD_FIELDS
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    # Валидные поля остались доступны.
    for good in ("ym:s:lastTrafficSource", "ym:s:browser", "ym:s:screenWidth",
                 "ym:s:regionCity"):
        assert good in entry["available_fields"]
    # Причина отклонения записана по каждому dropped-полю.
    for bad in _METRIKA_BAD_FIELDS:
        assert bad in entry["dropped_reasons"]
        assert "Unknown field" in entry["dropped_reasons"][bad]
        assert bad not in entry["patch_fields"]
    # Безопасный лог: отклонённый состав виден, токен/Authorization — нет.
    joined = "\n".join(logged)
    assert "evaluate отклонил" in joined
    assert "fake-metrika" not in joined and "OAuth" not in joined


def test_metrika_logs_backfill_preserves_old_files(paths):
    """Окно уже выгружено ДО патча -> довыгрузка полей в подкаталог backfill/."""
    cfg = {**CONFIG_METRIKA,
           "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}}

    # Пред-патчевое состояние: старый visits_* файл + запись манифеста без patch_date.
    src = paths.raw / "metrika_logs"
    src.mkdir(parents=True)
    old_file = src / "visits_2026-06-01_2026-06-30_part000.csv.gz"
    with gzip.open(old_file, "wt", encoding="utf-8") as fh:
        fh.write("ym:s:visitID\nv1\n")
    old_bytes = old_file.read_bytes()
    manifest_mod.update_source(
        paths.raw, "metrika_logs",
        date_from="2026-06-01", date_to="2026-06-30", rows=1,
        script_version="0.2.0", canonical_tables=["visits"],
    )

    box = {}
    routes = [
        _evaluate_route(lambda: box["session"], bad=_METRIKA_BAD_FIELDS),
        (lambda m, u: m == "POST" and u.endswith("/logrequests"),
         FakeResponse(json_data={"log_request": {"request_id": 888, "status": "created"}})),
        (lambda m, u: m == "GET" and u.endswith("/logrequest/888"),
         FakeResponse(json_data={"log_request": {"request_id": 888, "status": "processed",
                                                 "parts": [{"part_number": 0}]}})),
        (_contains("/part/0/download"),
         FakeResponse(text="ym:s:visitID\tym:s:lastTrafficSource\nv1\tdirect\n")),
    ]
    session = FakeSession(routes)
    box["session"] = session

    result = metrika_logs.extract(cfg, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["patch_backfill"] is True
    # Старый файл слоя raw НЕ тронут (неизменность слоя).
    assert old_file.read_bytes() == old_bytes
    # Backfill-файлы лежат в ПОДКАТАЛОГЕ backfill/ (вне глоба верхнего уровня).
    assert sorted(src.glob("visits_backfill_*.csv.gz")) == []
    backfill_files = sorted((src / "backfill").glob("visits_backfill_*.csv.gz"))
    assert len(backfill_files) == 1
    with gzip.open(backfill_files[0], "rt", encoding="utf-8") as fh:
        assert fh.read().startswith("ym:s:visitID")
    # verify_metrika/transform глобят только верхний уровень -> backfill их не ломает.
    assert sorted(src.glob("*.csv.gz")) == [old_file]

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["metrika_logs"]
    assert entry["patch_backfill"] is True
    assert entry["patch_date"] == metrika_logs.PATCH_DATE
    assert entry["schema_version"] == metrika_logs.SCHEMA_VERSION
    assert entry["backfill_dir"] == "metrika_logs/backfill"
    assert "ym:s:lastTrafficSource" in entry["patch_fields"]
    assert "ym:s:visitID" not in entry["patch_fields"]  # ключ склейки — не «поле патча»
    # Неподдерживаемые поля изолированы и в backfill.
    assert set(entry["dropped_fields"]) == _METRIKA_BAD_FIELDS


def test_metrika_logs_dead_token_raises_auth_error(paths):
    """401 на любом шаге -> AuthError с кодом «источник недоступен»."""
    routes = [(lambda m, u: True, FakeResponse(status_code=401))]
    session = FakeSession(routes)

    with pytest.raises(C.AuthError) as exc:
        metrika_logs.extract(CONFIG_METRIKA, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE
    assert "METRIKA" in str(exc.value)


def test_metrika_logs_missing_token_raises_before_network(paths):
    """Нет METRIKA_TOKEN в .env -> AuthError без единого запроса."""
    session = FakeSession([])
    with pytest.raises(C.AuthError):
        metrika_logs.extract(CONFIG_METRIKA, {}, paths, session=session, sleeper=NO_SLEEP)
    assert session.calls == []


# ── metrika_reports ────────────────────────────────────────────────────────
def test_metrika_reports_writes_slices_and_manifest(paths):
    """Цели + помесячные срезы источников/целей пишутся как raw JSON + manifest."""
    goals = {"goals": [{"id": 101, "name": "Форма"}, {"id": 102, "name": "Звонок"}]}
    stat = {"data": [{"dimensions": [], "metrics": [100, 5, 2]}], "totals": [100, 5, 2]}

    routes = [
        (_contains("/goals"), FakeResponse(json_data=goals)),
        (_contains("/stat/v1/data"), FakeResponse(json_data=stat)),
    ]
    session = FakeSession(routes)

    result = metrika_reports.extract(CONFIG_METRIKA, ENV, paths, session=session)

    src_dir = paths.raw / "metrika_reports"
    assert (src_dir / "goals_list.json").exists()
    goals_by_month = json.loads((src_dir / "goals_by_month.json").read_text("utf-8"))
    sources_by_month = json.loads((src_dir / "sources_by_month.json").read_text("utf-8"))
    # Окно май+июнь -> два месяца в каждом срезе.
    assert [m["month"] for m in goals_by_month] == ["2026-05-01", "2026-06-01"]
    assert len(sources_by_month) == 2

    assert result["goals"] == 2
    manifest = manifest_mod.load_manifest(paths.raw)
    assert manifest["sources"]["metrika_reports"]["canonical_tables"] == ["visits"]

    # Токен нигде не утёк в сохранённое сырьё.
    for f in src_dir.glob("*.json"):
        assert "fake-metrika" not in f.read_text("utf-8")


def test_metrika_reports_dead_token_raises(paths):
    routes = [(lambda m, u: True, FakeResponse(status_code=403))]
    session = FakeSession(routes)
    with pytest.raises(C.AuthError) as exc:
        metrika_reports.extract(CONFIG_METRIKA, ENV, paths, session=session)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE


# ── direct ─────────────────────────────────────────────────────────────────
def _campaign_tsv():
    return ("CampaignId\tCampaignName\tCost\tClicks\tImpressions\tDate\n"
            "1\tПоиск\t5000000\t10\t200\t2026-06-01\n"
            "1\tПоиск\t3000000\t7\t150\t2026-06-02\n")


def _query_tsv():
    return ("Query\tCampaignId\tAdGroupId\tCost\tClicks\tConversions\n"
            "купить окна\t1\t11\t2000000\t3\t1\n")


def _placement_tsv():
    return ("Placement\tAdNetworkType\tCampaignId\tCost\tClicks\tConversions\n"
            "avito.ru\tAD_NETWORK\t1\t1000000\t2\t0\n")


def _campaign_tsv_with_lost_is():
    # Кампания с показами>0 и непустым LostImpressionShare (для A07).
    return ("CampaignId\tCampaignName\tCost\tClicks\tImpressions\tDate"
            "\tWeightedImpressions\tLostImpressionShare\n"
            "1\tПоиск\t5000000\t10\t200\t2026-06-01\t180\t0.35\n")


def _direct_routes(box, *, campaign_tsv=None, strategies=None, feeds=None,
                   keywords=None):
    """Полный набор HTTP-моков Директа (reports + все json-эндпоинты v5).

    ``box["session"]`` заполняется вызывающим кодом после создания FakeSession —
    ReportsResponder читает из него тело последнего запроса, чтобы вернуть TSV
    нужного отчёта.
    """
    campaign_tsv = campaign_tsv or _campaign_tsv()
    if strategies is None:
        strategies = [{"Id": 1, "Name": "Поиск", "TextCampaign": {
            "BiddingStrategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}}}]
    feeds = [] if feeds is None else feeds
    if keywords is None:
        keywords = [{"Id": 501, "CampaignId": 1, "AdGroupId": 11, "Keyword": "аренда авто"}]

    def reports(n):
        _m, _u, kwargs = box["session"].calls[-1]
        rt = kwargs["json"]["params"]["ReportType"]
        if rt == "CAMPAIGN_PERFORMANCE_REPORT":
            body = campaign_tsv
        elif rt == "SEARCH_QUERY_PERFORMANCE_REPORT":
            body = _query_tsv()
        else:  # CUSTOM_REPORT -> площадки
            body = _placement_tsv()
        if n == 0:
            return FakeResponse(status_code=202, headers={"retryIn": "0"})
        return FakeResponse(status_code=200, text=body)

    return [
        (_contains("/reports"), reports),
        (_contains("/campaigns"), FakeResponse(json_data={"result": {"Campaigns": strategies}})),
        (_contains("/adgroups"), FakeResponse(json_data={"result": {"AdGroups": [
            {"Id": 11, "CampaignId": 1, "Name": "grp", "RegionIds": [213]}]}})),
        (_contains("/bidmodifiers"), FakeResponse(json_data={"result": {"BidModifiers": []}})),
        (_contains("/adextensions"), FakeResponse(json_data={"result": {"AdExtensions": []}})),
        (_contains("/ads"), FakeResponse(json_data={"result": {"Ads": [
            {"Id": 101, "CampaignId": 1, "AdGroupId": 11,
             "TextAd": {"Title": "Аренда", "Text": "Погнали"}}]}})),
        (_contains("/keywords"), FakeResponse(json_data={"result": {"Keywords": keywords}})),
        (_contains("/feeds"), FakeResponse(json_data={"result": {"Feeds": feeds}})),
    ]


def test_direct_writes_reports_strategies_and_manifest(paths):
    """Все отчёты Директа пишутся; manifest фиксирует cost_basis и приёмочные флаги."""
    box = {}
    session = FakeSession(_direct_routes(box))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    src_dir = paths.raw / "direct"
    assert (src_dir / "campaign_performance.tsv").read_text("utf-8").startswith("CampaignId")
    assert (src_dir / "search_query_performance.tsv").exists()
    assert (src_dir / "placements" / "placement_performance.tsv").exists()
    strat = json.loads((src_dir / "campaign_strategies.json").read_text("utf-8"))
    assert strat[0]["Id"] == 1
    # Новые отчёты патча.
    targeting = json.loads((src_dir / "campaign_targeting.json").read_text("utf-8"))
    assert targeting["ad_groups"][0]["RegionIds"] == [213]
    ad_texts = json.loads((src_dir / "ad_texts.json").read_text("utf-8"))
    assert ad_texts["ads"][0]["Id"] == 101
    assert (src_dir / "keywords.parquet").exists()
    # Фида нет -> файл не создаётся.
    assert not (src_dir / "product_feed.parquet").exists()

    assert result["campaign_rows"] == 2 and result["query_rows"] == 1
    assert result["placement_rows"] == 1 and result["keyword_rows"] == 1
    assert result["cost_basis"] == "net_no_vat"
    assert result["feed_used"] is False

    manifest = manifest_mod.load_manifest(paths.raw)
    entry = manifest["sources"]["direct"]
    assert entry["cost_basis"] == "net_no_vat"
    assert entry["cost_micros_per_rub"] == 1_000_000
    assert entry["canonical_tables"] == ["costs", "direct_queries"]
    # Приёмочные флаги (у фикстуры базового отчёта нет LostImpressionShare и State).
    assert entry["campaign_report_has_lost_impression_share"] is False
    assert entry["archived_campaigns_retrievable"] is False
    assert entry["feed_used"] is False


def test_direct_lost_impression_share_flag_true(paths):
    """Есть непустой LostImpressionShare у кампании с показами>0 -> флаг true (A07)."""
    box = {}
    session = FakeSession(_direct_routes(box, campaign_tsv=_campaign_tsv_with_lost_is()))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["campaign_report_has_lost_impression_share"] is True
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["campaign_report_has_lost_impression_share"] is True


def test_direct_archived_campaigns_retrievable_flag_true(paths):
    """campaigns.get вернул ARCHIVED-кампанию -> archived_campaigns_retrievable=true (D08)."""
    strategies = [
        {"Id": 1, "Name": "Поиск", "State": "ON", "TextCampaign": {
            "BiddingStrategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}}},
        {"Id": 2, "Name": "Старая", "State": "ARCHIVED", "TextCampaign": {
            "BiddingStrategy": {"Search": {"BiddingStrategyType": "AVERAGE_CPA"}}}},
    ]
    box = {}
    session = FakeSession(_direct_routes(box, strategies=strategies))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["archived_campaigns_retrievable"] is True
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["archived_campaigns_retrievable"] is True


def test_direct_feed_used_writes_parquet(paths):
    """Есть товарный фид -> product_feed.parquet + manifest.feed_used=true (A25)."""
    feeds = [{"Id": 77, "Name": "Каталог", "BusinessType": "RETAIL",
              "UrlFeedParameters": {"Url": "https://pognali.rent/feed.yml"},
              "UpdateStatus": {"LastUpdate": "2026-06-27T03:00:00Z"}}]
    box = {}
    session = FakeSession(_direct_routes(box, feeds=feeds))
    box["session"] = session

    result = direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)

    assert result["feed_used"] is True
    assert (paths.raw / "direct" / "product_feed.parquet").exists()
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["direct"]
    assert entry["feed_used"] is True


def test_direct_keyword_match_type_classification():
    """Тип соответствия ключевой фразы выводится по операторам Директа."""
    assert direct._keyword_match_type("аренда авто") == "broad"
    assert direct._keyword_match_type('"аренда авто"') == "exact"
    assert direct._keyword_match_type("[аренда авто]") == "exact"
    assert direct._keyword_match_type("!аренда +авто") == "exact"
    assert direct._keyword_match_type("") == "unknown"


def test_direct_has_lost_impression_share_helper():
    """_has_lost_impression_share: непустое значение у строки с Impressions>0."""
    assert direct._has_lost_impression_share(_campaign_tsv_with_lost_is(), True) is True
    # Поля не запрашивались (API не принял) -> всегда false.
    assert direct._has_lost_impression_share(_campaign_tsv_with_lost_is(), False) is False
    # Колонки нет в базовом отчёте -> false.
    assert direct._has_lost_impression_share(_campaign_tsv(), True) is False


def test_direct_dead_token_raises(paths):
    routes = [(lambda m, u: True, FakeResponse(status_code=401))]
    session = FakeSession(routes)
    with pytest.raises(C.AuthError) as exc:
        direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE
    assert "DIRECT" in str(exc.value)


def test_direct_reports_error_513_raises_clear_message(paths):
    """Reports API отдаёт ошибку HTTP 400 + {"error":{error_code:"513"}} (строка).

    Должно упасть внятным SourceUnavailable про «логин не подключён», а НЕ
    записать error-JSON как TSV-отчёт.
    """
    err = {"error": {"error_code": "513", "error_string": "логин не подключён",
                     "error_detail": "", "request_id": "1"}}
    routes = [(_contains("/reports"), FakeResponse(status_code=400, json_data=err,
                                                   text=json.dumps(err)))]
    session = FakeSession(routes)
    with pytest.raises(C.SourceUnavailable) as exc:
        direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE
    assert "513" in str(exc.value)
    # Ничего «сыро-ошибочного» на диск не легло.
    assert not (paths.raw / "direct" / "campaign_performance.tsv").exists()


def test_direct_campaigns_error_58_raises_registration(paths):
    """campaigns.get отдаёт HTTP 200 + {"error":{error_code:58}} — не успех.

    Отчёты в этом сценарии успешны, но стратегии упираются в error 58 -> падаем
    внятным сообщением про незавершённую регистрацию (доступ к API не выдан).
    """
    err58 = {"error": {"error_code": 58, "error_string": "Незавершённая регистрация"}}
    routes = [
        (_contains("/reports"), FakeResponse(status_code=200, text=_campaign_tsv())),
        (_contains("/campaigns"), FakeResponse(status_code=200, json_data=err58,
                                               text=json.dumps(err58))),
    ]
    session = FakeSession(routes)
    with pytest.raises(C.SourceUnavailable) as exc:
        direct.extract(CONFIG_DIRECT, ENV, paths, session=session, sleeper=NO_SLEEP)
    assert "58" in str(exc.value)


def test_direct_ping_false_on_error_58(paths):
    """ping не должен считать HTTP 200 с телом error за живой источник."""
    err58 = {"error": {"error_code": 58, "error_string": "Незавершённая регистрация"}}
    routes = [(_contains("/campaigns"), FakeResponse(status_code=200, json_data=err58,
                                                     text=json.dumps(err58)))]
    session = FakeSession(routes)
    # ping создаёт свою requests.Session; подменяем через monkeypatch не нужно —
    # проверяем _raise_for_api_error напрямую на фейковом ответе.
    assert direct._api_error(FakeResponse(json_data=err58)) is not None
    with pytest.raises(C.SourceUnavailable):
        direct._raise_for_api_error(FakeResponse(json_data=err58), "ping")


# ── общая обвязка: ретраи и окно ───────────────────────────────────────────
def test_http_retries_on_5xx_then_succeeds():
    """5xx -> экспоненциальный бэкофф и повтор; на 3-й попытке успех."""
    slept = []

    def responder(n):
        return FakeResponse(status_code=500) if n < 2 else FakeResponse(json_data={"ok": True})

    session = FakeSession([(lambda m, u: True, responder)])
    resp = C.http_request(session, "GET", "http://x/y", source="t",
                          sleeper=slept.append)
    assert resp.json() == {"ok": True}
    assert len(session.calls) == 3        # две неудачи + успех
    assert len(slept) == 2                # два ожидания между попытками


def test_http_429_respects_retry_after():
    """429 -> ждём Retry-After, затем успех."""
    slept = []

    def responder(n):
        return (FakeResponse(status_code=429, headers={"Retry-After": "7"})
                if n == 0 else FakeResponse(json_data={"ok": True}))

    session = FakeSession([(lambda m, u: True, responder)])
    C.http_request(session, "GET", "http://x", source="t", sleeper=slept.append)
    assert slept == [7.0]


def test_http_5xx_exhausted_raises_source_unavailable():
    """Постоянный 5xx -> SourceUnavailable после MAX_ATTEMPTS попыток."""
    session = FakeSession([(lambda m, u: True, FakeResponse(status_code=503))])
    with pytest.raises(C.SourceUnavailable):
        C.http_request(session, "GET", "http://x", source="t", sleeper=lambda _s: None)
    assert len(session.calls) == C.MAX_ATTEMPTS


def test_resolve_window_months_back():
    """Без явных дат окно = months назад от опорной даты."""
    cfg = {"data_window": {"months": 3}}
    dfrom, dto = C.resolve_window(cfg, today=date(2026, 7, 8))
    assert dto == date(2026, 7, 8)
    assert dfrom == date(2026, 4, 8)


def test_month_chunks_splits_by_calendar_month():
    chunks = C.month_chunks(date(2026, 5, 15), date(2026, 7, 3))
    assert chunks == [
        (date(2026, 5, 15), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 3)),
    ]


# ── gsc_api (mode: api) ──────────────────────────────────────────────────────
CONFIG_GSC = {
    "sources": {"gsc": {"enabled": True, "site_url": "https://pognali.rent/",
                        "raw_format": "csv"}},
    "data_window": {"date_from": "2026-05-01", "date_to": "2026-06-30"},
}
ENV_GSC = {"GSC_CREDENTIALS_PATH": "/fake/sa.json"}


def test_gsc_writes_monthly_files_and_manifest(paths):
    """searchAnalytics.query: помесячные срезы (query,page,device) -> csv + manifest."""
    row = {"keys": ["аренда авто", "https://pognali.rent/cars", "DESKTOP"],
           "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 3.2}
    routes = [(_contains("searchAnalytics/query"),
               FakeResponse(json_data={"rows": [row]}))]
    session = FakeSession(routes)

    result = gsc_api.extract(CONFIG_GSC, ENV_GSC, paths,
                         session=session, access_token="fake-token", sleeper=NO_SLEEP)

    src_dir = paths.raw / "gsc"
    csv_files = sorted(src_dir.glob("*.csv"))
    assert len(csv_files) == 2                        # май + июнь -> два файла
    head = csv_files[0].read_text("utf-8").splitlines()[0]
    assert head.startswith("month,query,page,device")
    assert result["rows"] == 2                        # по одной строке на месяц

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["canonical_tables"] == ["seo_queries"]
    assert entry["engine"] == "google"


def test_gsc_paginates_by_start_row(paths):
    """Полная страница (ROW_LIMIT) -> тянем следующую по startRow, затем стоп."""
    full_page = [{"keys": ["q", "u", "MOBILE"], "clicks": 1, "impressions": 2,
                  "ctr": 0.5, "position": 1.0}] * gsc_api.ROW_LIMIT

    def responder(n):
        # Первый вызов на месяц — полная страница, второй — «добивка» из 1 строки.
        return FakeResponse(json_data={"rows": full_page if n % 2 == 0 else full_page[:1]})

    session = FakeSession([(_contains("searchAnalytics/query"), responder)])
    cfg = {**CONFIG_GSC, "data_window": {"date_from": "2026-06-01", "date_to": "2026-06-30"}}

    result = gsc_api.extract(cfg, ENV_GSC, paths,
                         session=session, access_token="fake-token", sleeper=NO_SLEEP)
    # Один месяц: страница полная -> вторая страница -> стоп. Два запроса.
    assert len(session.calls) == 2
    assert result["rows"] == gsc_api.ROW_LIMIT + 1


def test_gsc_dead_token_raises(paths):
    routes = [(lambda m, u: True, FakeResponse(status_code=401))]
    session = FakeSession(routes)
    with pytest.raises(C.AuthError) as exc:
        gsc_api.extract(CONFIG_GSC, ENV_GSC, paths,
                    session=session, access_token="fake-token", sleeper=NO_SLEEP)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE


# ── webmaster_api (mode: api) ────────────────────────────────────────────────
CONFIG_WM = {
    "sources": {"webmaster": {"enabled": True, "host_id": "https:pognali.rent:443"}},
    "data_window": {"date_from": "2026-05-01", "date_to": "2026-06-30"},
}
ENV_WM = {"WEBMASTER_TOKEN": "fake-wm"}


def test_webmaster_writes_queries_history_and_notes(paths):
    """Популярные запросы + история пишутся; усечение истории -> honest note."""
    popular = {"queries": [{"query_id": "q1", "query_text": "аренда авто",
                            "indicators": {"TOTAL_SHOWS": 1000, "TOTAL_CLICKS": 50,
                                           "AVG_SHOW_POSITION": 3.1,
                                           "AVG_CLICK_POSITION": 2.2}}], "count": 1}
    # История начинается позже запрошенного 2026-05-01 -> ряд усечён слева.
    history = {"indicators": {"TOTAL_SHOWS": [{"date": "2026-06-01", "value": 900}],
                              "TOTAL_CLICKS": [{"date": "2026-06-01", "value": 40}]}}
    routes = [
        (lambda m, u: m == "GET" and u.endswith("/user"),
         FakeResponse(json_data={"user_id": 555})),
        (_contains("search-queries/popular"), FakeResponse(json_data=popular)),
        (_contains("all/history"), FakeResponse(json_data=history)),
    ]
    session = FakeSession(routes)

    result = webmaster_api.extract(CONFIG_WM, ENV_WM, paths, session=session)

    src_dir = paths.raw / "webmaster"
    pop = json.loads((src_dir / "search_queries_popular.json").read_text("utf-8"))
    assert pop[0]["query_text"] == "аренда авто"
    assert (src_dir / "search_queries_history.json").exists()
    assert result["rows"] == 1

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["canonical_tables"] == ["seo_queries"]
    # Две заметки: общее ограничение API + факт усечения ряда слева.
    assert len(entry["notes"]) == 2
    assert any("усеч" in n for n in entry["notes"])


def test_webmaster_dead_token_raises(paths):
    routes = [(lambda m, u: True, FakeResponse(status_code=403))]
    session = FakeSession(routes)
    with pytest.raises(C.AuthError) as exc:
        webmaster_api.extract(CONFIG_WM, ENV_WM, paths, session=session)
    assert exc.value.exit_code == C.EXIT_SOURCE_UNAVAILABLE


# ── gsc_manual (mode: manual — ручная выгрузка CSV) ──────────────────────────
CONFIG_GSC_MANUAL = {
    "sources": {"gsc": {"enabled": True, "mode": "manual", "raw_format": "csv",
                        "manual_export_dir": "inputs/manual_exports/gsc"}},
}


def _write_gsc_manual(paths, name, text, meta=None):
    """Положить ручную выгрузку gsc_YYYY-MM.csv (+ опц. meta.yaml) в inputs/."""
    manual_dir = paths.root / "inputs" / "manual_exports" / "gsc"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / name).write_text(text, encoding="utf-8")
    if meta is not None:
        (manual_dir / (Path(name).stem + ".meta.yaml")).write_text(meta, encoding="utf-8")


def test_gsc_manual_validates_and_writes_same_contract(paths):
    """Норма: ручной CSV -> gsc_YYYY-MM.csv в контракте transform + manifest manual."""
    _write_gsc_manual(paths, "gsc_2026-05.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://pognali.rent/cars,DESKTOP,10,100,4.2%,3.1\n"
        "прокат машин,https://pognali.rent/,MOBILE,5,80,,7.0\n"
        ",https://pognali.rent/x,DESKTOP,1,10,1%,9.0\n",       # пустой query -> reject
    )

    result = gsc_manual.extract(CONFIG_GSC_MANUAL, {}, paths)

    src_dir = paths.raw / "gsc"
    out = src_dir / "gsc_2026-05.csv"
    lines = out.read_text("utf-8").splitlines()
    assert lines[0].startswith("month,query,page,device")   # тот же контракт, что у API
    assert result["accepted"] == 2 and result["rejected"] == 1
    assert result["rejected_reasons"] == {"missing_query": 1}
    # CTR из процентов приведён к доле; месяц взят из имени файла.
    first = lines[1].split(",")
    assert first[0] == "2026-05"
    assert first[6] == "0.042"                               # 4.2% -> 0.042

    report = json.loads((src_dir / "validation_report.json").read_text("utf-8"))
    assert report["source_mode"] == "manual"
    assert report["completeness"] == "unverified"
    assert report["device_missing_months"] == []

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["canonical_tables"] == ["seo_queries"]
    assert entry["source_mode"] == "manual"
    assert entry["completeness"] == "unverified"


def test_gsc_manual_total_clicks_ui_mismatch_becomes_caveat(paths):
    """Расхождение суммы clicks с total_clicks_ui > 10% -> caveat в отчёте."""
    _write_gsc_manual(paths, "gsc_2026-06.csv",
        "query,page,device,clicks,impressions,ctr,position\n"
        "аренда авто,https://pognali.rent/cars,DESKTOP,80,1000,8%,3.1\n"
        "прокат авто,https://pognali.rent/,MOBILE,5,80,6%,7.0\n",   # сумма clicks = 85
        meta="total_clicks_ui: 100\n",                             # UI: 100 -> расхождение 15%
    )

    result = gsc_manual.extract(CONFIG_GSC_MANUAL, {}, paths)

    caveats = result["clicks_ui_caveats"]
    assert len(caveats) == 1
    assert caveats[0]["month"] == "2026-06"
    assert caveats[0]["total_clicks_ui"] == 100
    assert caveats[0]["sum_clicks"] == 85
    assert caveats[0]["deviation_pct"] == 15.0

    report = json.loads((paths.raw / "gsc" / "validation_report.json").read_text("utf-8"))
    assert report["clicks_ui_caveats"][0]["deviation_pct"] == 15.0
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert any("total_clicks_ui" in n for n in entry["notes"])


def test_gsc_manual_missing_device_column_flags_month(paths):
    """Нет колонки device в экспорте -> device=unknown, месяц исключён из S20."""
    _write_gsc_manual(paths, "gsc_2026-05.csv",
        "query,page,clicks,impressions,ctr,position\n"          # без device
        "аренда авто,https://pognali.rent/cars,10,100,4%,3.1\n",
    )

    result = gsc_manual.extract(CONFIG_GSC_MANUAL, {}, paths)

    assert result["device_missing_months"] == ["2026-05"]
    assert result["accepted"] == 1                              # строку НЕ отбрасываем
    row = (paths.raw / "gsc" / "gsc_2026-05.csv").read_text("utf-8").splitlines()[1]
    assert row.split(",")[3] == "unknown"                       # device -> unknown
    entry = manifest_mod.load_manifest(paths.raw)["sources"]["gsc"]
    assert entry["device_missing_months"] == ["2026-05"]
    assert any("device" in n for n in entry["notes"])


def test_gsc_manual_no_exports_raises_source_unavailable(paths):
    with pytest.raises(C.SourceUnavailable):
        gsc_manual.extract(CONFIG_GSC_MANUAL, {}, paths)


# ── webmaster_manual (mode: manual — ручная выгрузка «Популярные запросы») ────
CONFIG_WM_MANUAL = {
    "sources": {"webmaster": {"enabled": True, "mode": "manual",
                              "manual_export_dir": "inputs/manual_exports/webmaster"}},
}


def _write_wm_manual(paths, name, text):
    manual_dir = paths.root / "inputs" / "manual_exports" / "webmaster"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / name).write_text(text, encoding="utf-8")


def test_webmaster_manual_aggregates_to_popular_contract(paths):
    """Норма: помесячные CSV -> search_queries_popular.json в контракте transform."""
    _write_wm_manual(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,600,30,3.0,2026-05\n"
        "прокат машин,100,5,7.0,2026-05\n",
    )
    _write_wm_manual(paths, "webmaster_2026-06.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,400,20,5.0,2026-06\n",
    )

    result = webmaster_manual.extract(CONFIG_WM_MANUAL, {}, paths)

    popular = json.loads(
        (paths.raw / "webmaster" / "search_queries_popular.json").read_text("utf-8")
    )
    # «аренда авто» отсортирован первым (больше показов) и агрегирован за 2 месяца.
    top = popular[0]
    assert top["query_text"] == "аренда авто"
    assert top["indicators"]["TOTAL_SHOWS"] == 1000        # 600 + 400
    assert top["indicators"]["TOTAL_CLICKS"] == 50         # 30 + 20
    # Позиция — средневзвешенная по показам: (3*600 + 5*400) / 1000 = 3.8.
    assert top["indicators"]["AVG_SHOW_POSITION"] == pytest.approx(3.8)
    assert result["rows"] == 2

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["webmaster"]
    assert entry["canonical_tables"] == ["seo_queries"]
    assert entry["source_mode"] == "manual"
    assert entry["page_device_breakdown"] is False
    assert entry["manual_no_page_breakdown_policy"] == "degrade"   # дефолт
    # Ограничение метода зафиксировано явно (и для API тоже, не только ручного).
    assert any("ограничение метода" in n for n in entry["notes"])


def test_webmaster_manual_records_no_page_device_breakdown(paths):
    """Экспорт без page/device -> зафиксировано ограничение + политика из конфига."""
    _write_wm_manual(paths, "webmaster_2026-05.csv",
        "query,impressions,clicks,position,month\n"
        "аренда авто,600,30,3.0,2026-05\n",
    )
    cfg = {"sources": {"webmaster": {
        "enabled": True, "mode": "manual",
        "manual_export_dir": "inputs/manual_exports/webmaster",
        "manual_no_page_breakdown_policy": "aggregate"}}}

    result = webmaster_manual.extract(cfg, {}, paths)

    assert result["page_device_breakdown"] is False
    assert result["manual_no_page_breakdown_policy"] == "aggregate"
    report = json.loads(
        (paths.raw / "webmaster" / "validation_report.json").read_text("utf-8")
    )
    assert report["page_device_breakdown"] is False
    assert "aggregate" in report["policy_effect"]
    assert report["manual_no_page_breakdown_policy"] == "aggregate"


def test_webmaster_manual_no_exports_raises(paths):
    with pytest.raises(C.SourceUnavailable):
        webmaster_manual.extract(CONFIG_WM_MANUAL, {}, paths)


# ── crux (Chrome UX Report API) ──────────────────────────────────────────────
CONFIG_CRUX = {
    "sources": {"crux": {"enabled": True, "api_key_env": "CRUX_API_KEY",
                         "origin": "https://pognali.rent",
                         "key_urls": ["https://pognali.rent/cars"]}},
}
ENV_CRUX = {"CRUX_API_KEY": "fake-crux-key"}


def _crux_record(origin="https://pognali.rent"):
    return {"record": {
        "key": {"origin": origin},
        "metrics": {
            "largest_contentful_paint": {"percentiles": {"p75": 2500}},
            "cumulative_layout_shift": {"percentiles": {"p75": "0.08"}},
            "interaction_to_next_paint": {"percentiles": {"p75": 180}},
        },
    }}


def test_crux_writes_field_data_when_present(paths):
    """Есть полевые данные: origin + ключевой URL -> cwv_field_data_available=true."""
    routes = [(_contains("records:queryRecord"),
               FakeResponse(json_data=_crux_record()))]
    session = FakeSession(routes)

    result = crux.extract(CONFIG_CRUX, ENV_CRUX, paths, session=session)

    assert result["cwv_field_data_available"] is True
    data = json.loads((paths.raw / "crux" / "crux.json").read_text("utf-8"))
    assert data["cwv_field_data_available"] is True
    # origin + один ключевой URL = две записи.
    assert len(data["records"]) == 2
    origin_rec = data["records"][0]
    assert origin_rec["target_type"] == "origin"
    assert origin_rec["p75"]["largest_contentful_paint"] == 2500

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["crux"]
    assert entry["cwv_field_data_available"] is True
    assert entry["source_mode"] == "api"


def test_crux_missing_field_data_is_normal_not_error(paths):
    """Нет данных (404): НЕ падаем, пишем cwv_field_data_available=false и идём дальше."""
    routes = [(_contains("records:queryRecord"), FakeResponse(status_code=404))]
    session = FakeSession(routes)

    result = crux.extract(CONFIG_CRUX, ENV_CRUX, paths, session=session)

    assert result["cwv_field_data_available"] is False
    # origin пуст -> веерных запросов по URL не делаем: ровно один вызов.
    assert len(session.calls) == 1
    data = json.loads((paths.raw / "crux" / "crux.json").read_text("utf-8"))
    assert data["cwv_field_data_available"] is False
    assert data["records"][0]["field_data_available"] is False

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["crux"]
    assert entry["cwv_field_data_available"] is False
    assert any("порог" in n or "лаборатор" in n for n in entry["notes"])


def test_crux_missing_api_key_raises(paths):
    session = FakeSession([])
    with pytest.raises(C.SourceUnavailable):
        crux.extract(CONFIG_CRUX, {}, paths, session=session)
    assert session.calls == []


# ── wordstat ─────────────────────────────────────────────────────────────────
CONFIG_WS = {"wordstat_seeds": ["аренда авто", "прокат машин"], "wordstat_geo": [10231]}
ENV_WS = {"WORDSTAT_TOKEN": "fake-ws"}


def test_wordstat_queue_cycle_writes_raw_and_manifest(paths):
    """Очередь Wordstat: create -> list(Done) -> get -> delete -> raw + manifest."""
    ws_responses = {
        "CreateNewWordstatReport": {"data": 111},
        "GetWordstatReportList": {"data": [{"ReportID": 111, "StatusReport": "Done"}]},
        "GetWordstatReport": {"data": [
            {"Phrase": "аренда авто", "GeoID": [10231],
             "SearchedWith": [{"Phrase": "аренда авто", "Shows": 1000}]},
            {"Phrase": "прокат машин", "GeoID": [10231],
             "SearchedWith": [{"Phrase": "прокат машин", "Shows": 500}]},
        ]},
        "DeleteWordstatReport": {"data": 1},
    }

    def responder(n):
        # Wordstat шлёт тело как UTF-8 байты (data=), а не json= (квирк v4).
        method = json.loads(session.calls[-1][2]["data"].decode("utf-8"))["method"]
        return FakeResponse(json_data=ws_responses[method])

    session = FakeSession([(_contains("/v4/json"), responder)])

    result = wordstat.extract(CONFIG_WS, ENV_WS, paths, session=session, sleeper=NO_SLEEP)

    data = json.loads((paths.raw / "wordstat" / "wordstat.json").read_text("utf-8"))
    assert [d["Phrase"] for d in data] == ["аренда авто", "прокат машин"]
    assert result["rows"] == 2
    assert result["geo"] == [10231]

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["wordstat"]
    assert entry["canonical_tables"] == ["wordstat"]
    assert entry["geo"] == [10231]

    # Регрессия: кириллица уходит реальными UTF-8 байтами, а не \uXXXX
    # (иначе legacy v4 отвечает 501 «Request encoding is not UTF8»).
    create_body = next(k["data"] for _m, _u, k in session.calls
                       if b"CreateNewWordstatReport" in k["data"])
    assert "аренда авто".encode("utf-8") in create_body
    assert b"\\u0430" not in create_body


def test_wordstat_dead_token_raises(paths):
    """error_code 53 в теле ответа -> AuthError (легаси v4 не отдаёт 401)."""
    routes = [(lambda m, u: True,
               FakeResponse(json_data={"error_code": 53, "error_str": "bad token"}))]
    session = FakeSession(routes)
    with pytest.raises(C.AuthError):
        wordstat.extract(CONFIG_WS, ENV_WS, paths, session=session, sleeper=NO_SLEEP)


def test_wordstat_no_seeds_raises(paths):
    with pytest.raises(C.SourceUnavailable):
        wordstat.extract({"wordstat_seeds": []}, ENV_WS, paths,
                         session=FakeSession([]), sleeper=NO_SLEEP)


# ── crm_import ───────────────────────────────────────────────────────────────
_CRM_CSV = (
    "Дата;Источник;Телефон;Статус;Сумма;Новый\n"
    "07.05.2026;Яндекс;+7 (999) 123-45-67;успешно;15 000,50;да\n"
    "2026-05-08;Google;ORDER-123;отказ;0;нет\n"
    "не дата;direct;+79990000000;в работе;100;да\n"
    "09.05.2026;seo;;успешно;50;нет\n"
    "10.05.2026;seo;+79991112233;неизвестный;abc;да\n"
)


def _crm_config(csv_path):
    return {
        "sources": {"crm_csv": {"enabled": True, "path": str(csv_path),
                                "raw_format": "csv"}},
        "crm_csv": {
            "column_map": {"lead_date": "Дата", "source": "Источник",
                           "phone_or_id": "Телефон", "status": "Статус",
                           "amount_rub": "Сумма", "is_new_client": "Новый"},
            "status_map": {"успешно": "won", "отказ": "lost", "в работе": "in_progress"},
            "hash_salt": "s3cret",
        },
    }


def test_crm_import_validates_normalizes_and_reports(paths, tmp_path):
    """CSV -> нормализованные leads + validation_report; сырой телефон не утекает."""
    csv_path = tmp_path / "crm_export.csv"
    csv_path.write_text(_CRM_CSV, encoding="utf-8")

    result = crm_import.extract(_crm_config(csv_path), {}, paths)

    src_dir = paths.raw / "crm"
    leads_text = (src_dir / "leads.csv").read_text("utf-8")
    # Принято 2 строки: телефон-лид (won) и id-лид (lost).
    assert result["accepted"] == 2
    assert result["rejected"] == 3
    assert result["rejected_reasons"] == {"bad_date": 1, "missing_key": 1, "bad_amount": 1}

    # Сырой телефон нигде в выгрузке (только хэш).
    assert "999" not in leads_text and "+7" not in leads_text
    assert "won" in leads_text and "lost" in leads_text
    assert "ORDER-123" in leads_text          # id-лид кладём как есть

    report = json.loads((src_dir / "validation_report.json").read_text("utf-8"))
    assert report["total_rows"] == 5
    assert report["accepted"] == 2
    assert report["warnings"].get("unknown_status") == 1

    entry = manifest_mod.load_manifest(paths.raw)["sources"]["crm"]
    assert entry["canonical_tables"] == ["crm"]
    assert entry["rejected"] == 3


def test_crm_import_phone_hash_is_stable_and_salted(paths, tmp_path):
    """Один и тот же телефон -> один и тот же хэш; соль меняет хэш."""
    csv_path = tmp_path / "crm.csv"
    csv_path.write_text(
        "Дата;Источник;Телефон;Статус;Сумма;Новый\n"
        "07.05.2026;a;+7 999 123-45-67;успешно;10;да\n"
        "07.05.2026;b;8 (999) 1234567;успешно;20;нет\n",
        encoding="utf-8",
    )
    crm_import.extract(_crm_config(csv_path), {}, paths)
    lines = (paths.raw / "crm" / "leads.csv").read_text("utf-8").splitlines()[1:]
    hashes = [ln.split(",")[4] for ln in lines]          # колонка phone_hash
    # 79991234567 в двух записях (7… и 8…) -> одинаковый хэш.
    assert hashes[0] and hashes[0] == hashes[1]


def test_crm_import_missing_file_raises(paths):
    with pytest.raises(C.SourceUnavailable):
        crm_import.extract(
            {"sources": {"crm_csv": {"path": "/nope/does_not_exist.csv"}}},
            {}, paths,
        )
