"""DET-1 — воспроизводимость слоя compute.

Три инварианта:
  1) два прогона на неизменной фикстуре дают побайтово идентичные файлы во
     всём data/metrics/ (при PYTHONHASHSEED по умолчанию и при =0);
  2) добавление новой кампании не меняет evidence_id у существующих строк;
  3) все context_refs разрешаются в evidence_id того же прогона.

Фикстура синтетическая и самодостаточная: канонические таблицы пишутся по
реальным SCHEMAS (src/transform/build_canonical.py), чтобы блоки шли теми же
SQL-путями, что и на клиентских данных.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402

from src.compute import common  # noqa: E402
from src.pipeline import degradation as degradation_mod  # noqa: E402
from src.pipeline import orchestrator as orchestrator_mod  # noqa: E402
from src.transform.build_canonical import write_canonical_table  # noqa: E402


# ── Фикстура клиента ────────────────────────────────────────────────────────
class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_compute_common.py)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.raw = self.root / "data" / "raw"
        self.canonical = self.root / "data" / "canonical"
        self.metrics = self.root / "data" / "metrics"
        self.inputs = self.root / "inputs"
        self.config_file = self.root / "config.yaml"


_DEVICES = ("desktop", "mobile", "tablet")
_SOURCE_GROUPS = ("ad", "organic", "direct", "referral")
_PAGES = ("/", "/catalog", "/cars", "/contacts")
_CAMPAIGNS = (("101", "Поиск"), ("102", "РСЯ"), ("103", "Бренд"))
_EXTRA_CAMPAIGN = ("104", "Новая кампания")
_QUERIES = ("аренда авто", "прокат машин", "автопрокат владивосток", "погнали")
_START = date(2026, 1, 1)


def _visits(count: int = 240) -> pd.DataFrame:
    rows = []
    for i in range(count):
        day = _START + timedelta(days=i % 60)
        rows.append({
            "visit_id": f"v{i:04d}",
            "client_id": f"c{i % 37:03d}",
            "dt": pd.Timestamp(day) + pd.Timedelta(hours=i % 24),
            "date": day,
            "device": _DEVICES[i % 3],
            "source_group": _SOURCE_GROUPS[i % 4],
            "utm_source_raw": "yandex" if i % 4 == 0 else (None if i % 5 else "Yandex"),
            "source_final": _SOURCE_GROUPS[i % 4],
            "is_ad": i % 4 == 0,
            "entry_page": _PAGES[i % 4],
            "form_open": i % 3 == 0,
            "form_submit": i % 7 == 0,
            "call_click": i % 11 == 0,
            "messenger_click": i % 13 == 0,
            "form_submit_count": 2 if i % 7 == 0 else 0,
            "form_open_count": 3 if i % 3 == 0 else 0,
            "call_click_count": 1 if i % 11 == 0 else 0,
            "messenger_click_count": 1 if i % 13 == 0 else 0,
            "is_new_user": i % 2 == 0,
            "utm_medium_raw": "cpc" if i % 4 == 0 else None,
            "utm_campaign_raw": _CAMPAIGNS[i % 3][0] if i % 4 == 0 else None,
            "utm_term_raw": None,
            "direct_click_order": None,
            "click_id": None,
            "last_traffic_source_naive": "ad" if i % 4 == 0 else "direct",
            "browser": "chrome",
            "os": "windows",
            "screen_width": 1920,
            "screen_height": 1080,
            "screen_resolution": "1920x1080",
            "region_country": "Россия",
            "region_city": "Владивосток",
            "last_sign_traffic_source_raw": "ad" if i % 4 == 0 else "direct",
            "source_group_resolved": _SOURCE_GROUPS[i % 4],
            "traffic_source_resolved": i % 4 == 0,
        })
    return pd.DataFrame(rows)


def _goals() -> pd.DataFrame:
    return pd.DataFrame([
        {"goal_id": "1", "name": "Открытие формы", "type": "action", "url_pattern": None,
         "conditions_raw": None, "created_at": pd.Timestamp(_START),
         "updated_at": pd.Timestamp(_START)},
        {"goal_id": "2", "name": "Отправка формы", "type": "action", "url_pattern": None,
         "conditions_raw": None, "created_at": pd.Timestamp(_START),
         "updated_at": pd.Timestamp(_START)},
    ])


def _visit_goals(visits: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, visit_id in enumerate(visits["visit_id"]):
        if i % 3 == 0:
            rows.append({"visit_id": visit_id, "goal_id": "1", "achievement_count": 3})
        if i % 7 == 0:
            rows.append({"visit_id": visit_id, "goal_id": "2", "achievement_count": 2})
    return pd.DataFrame(rows)


def _campaign_list(extra_campaign: bool) -> tuple[tuple[str, str], ...]:
    return _CAMPAIGNS + ((_EXTRA_CAMPAIGN,) if extra_campaign else ())


def _costs(extra_campaign: bool) -> pd.DataFrame:
    rows = []
    for index, (campaign_id, campaign_name) in enumerate(_campaign_list(extra_campaign)):
        for day_offset in range(0, 60, 10):
            day = _START + timedelta(days=day_offset)
            cost = 1000.0 + 100 * index + day_offset
            rows.append({
                "date": day, "source_tag": "direct",
                "campaign_id": campaign_id, "campaign_name": campaign_name,
                "cost_raw": cost, "cost_normalized": cost, "cost_status": "net",
                "clicks": 50 + index, "impressions": 1000 + 10 * index,
            })
    return pd.DataFrame(rows)


def _direct_campaigns(extra_campaign: bool) -> pd.DataFrame:
    rows = []
    for index, (campaign_id, campaign_name) in enumerate(_campaign_list(extra_campaign)):
        for day_offset in range(0, 60, 10):
            for device in _DEVICES:
                rows.append({
                    "date": _START + timedelta(days=day_offset),
                    "campaign_id": campaign_id, "campaign_name": campaign_name,
                    "device": device,
                    "cost_raw": 1000000 + 1000 * index, "cost_rub": 1000.0 + index,
                    "cost_normalized": 1000.0 + index, "vat_basis_applied": True,
                    "clicks": 20 + index, "impressions": 400 + index,
                    "conversions_all": 2 + index,
                })
    return pd.DataFrame(rows)


def _direct_queries(extra_campaign: bool) -> pd.DataFrame:
    rows = []
    for index, (campaign_id, campaign_name) in enumerate(_campaign_list(extra_campaign)):
        for query_index, query in enumerate(_QUERIES):
            rows.append({
                "date": _START + timedelta(days=10 * index),
                "campaign_id": campaign_id, "campaign_name": campaign_name,
                "ad_group_id": f"g{index}",
                "query": query,
                "match_type": "SYNONYM" if query_index % 2 else "EXACT",
                "device": _DEVICES[query_index % 3],
                "cost_raw": 500000 + 1000 * query_index, "cost_rub": 500.0 + query_index,
                "cost_normalized": 500.0 + query_index, "vat_basis_applied": True,
                "clicks": 10 + query_index, "impressions": 300 + 10 * query_index,
                "conversions_all": 1 + query_index,
            })
    return pd.DataFrame(rows)


def _direct_placements(extra_campaign: bool) -> pd.DataFrame:
    rows = []
    for index, (campaign_id, _name) in enumerate(_campaign_list(extra_campaign)):
        for placement in ("search.yandex.ru", "mail.ru", "avito.ru"):
            rows.append({
                "placement": placement,
                "ad_network_type": "SEARCH" if placement.startswith("search") else "AD_NETWORK",
                "campaign_id": campaign_id,
                "cost_raw": 200000 + 1000 * index, "cost_rub": 200.0 + index,
                "cost_normalized": 200.0 + index, "vat_basis_applied": True,
                "clicks": 5 + index, "conversions_all": 1,
            })
    return pd.DataFrame(rows)


def _direct_geo(extra_campaign: bool) -> pd.DataFrame:
    rows = []
    for index, (campaign_id, campaign_name) in enumerate(_campaign_list(extra_campaign)):
        for location_id, location in (("1", "Владивосток"), ("2", "Хабаровск")):
            rows.append({
                "date": _START, "month": "2026-01",
                "campaign_id": campaign_id, "campaign_name": campaign_name,
                "location_of_presence_id": location_id,
                "location_of_presence_name": location,
                "device": "desktop",
                "cost_raw": 300000 + 1000 * index, "cost_rub": 300.0 + index,
                "cost_normalized": 300.0 + index, "vat_basis_applied": True,
                "clicks": 7 + index, "impressions": 100 + index, "conversions_all": 1,
            })
    return pd.DataFrame(rows)


def _campaign_status(extra_campaign: bool) -> pd.DataFrame:
    return pd.DataFrame([
        {"campaign_id": campaign_id, "state": "ON", "status": "ACCEPTED",
         "status_payment": "ALLOWED", "status_clarification": None,
         "observed_at": "2026-03-01T00:00:00", "source": "direct_api",
         "requested_states": "ON,SUSPENDED,ARCHIVED"}
        for campaign_id, _name in _campaign_list(extra_campaign)
    ])


def _seo_queries() -> pd.DataFrame:
    rows = []
    for query_index, query in enumerate(_QUERIES):
        for page_index, page in enumerate(_PAGES):
            for source in ("gsc", "webmaster"):
                for month in ("2026-01", "2026-02"):
                    rows.append({
                        "query": query, "page": page, "source": source, "month": month,
                        "device": _DEVICES[page_index % 3],
                        "total_shows": 100 + 10 * query_index + page_index,
                        "total_clicks": 5 + query_index,
                        "avg_show_position": 3.0 + page_index,
                        "is_brand": query == "погнали",
                        "source_mode": "api", "completeness": "full",
                        "ctr": 0.05 + 0.01 * query_index, "demand": 500 + query_index,
                    })
    return pd.DataFrame(rows)


def _site_pages() -> pd.DataFrame:
    return pd.DataFrame([
        {"url": page, "http_status": 200 if index else 301,
         "redirect_chain": "" if index else "/,/home,/",
         "final_url": page, "canonical_url": page, "robots_directive": "index,follow",
         "in_sitemap": True, "title": f"Заголовок {page}",
         "description": f"Описание {page}", "h1": f"H1 {page}",
         "crawled_at": "2026-03-01T00:00:00", "js_content_diff": None}
        for index, page in enumerate(_PAGES)
    ])


def _site_link_graph() -> pd.DataFrame:
    return pd.DataFrame([
        {"from_url": "/", "to_url": page, "depth_from_home": 1}
        for page in _PAGES if page != "/"
    ])


def _wordstat() -> pd.DataFrame:
    rows = []
    for query_index, query in enumerate(_QUERIES):
        for month_index, month in enumerate(("2026-01", "2026-02")):
            rows.append({
                "phrase": query, "normalized_phrase": query,
                "date": _START + timedelta(days=30 * month_index), "month": month,
                "count": 1000 + 100 * query_index, "share": 0.1,
                "purpose": "seasonality,gap", "seed_mask": query,
                "scope": "gap-specific", "top_requests_count": 10,
            })
    return pd.DataFrame(rows)


def build_fixture(root: Path, *, extra_campaign: bool = False) -> tuple[_Paths, list[str]]:
    """Собрать самодостаточную фикстуру клиента в root."""
    paths = _Paths(root)
    paths.canonical.mkdir(parents=True, exist_ok=True)
    paths.inputs.mkdir(parents=True, exist_ok=True)
    paths.raw.mkdir(parents=True, exist_ok=True)

    visits = _visits()
    tables = {
        "visits": visits,
        "visit_goals": _visit_goals(visits),
        "goals": _goals(),
        "costs": _costs(extra_campaign),
        "direct_campaigns": _direct_campaigns(extra_campaign),
        "direct_queries": _direct_queries(extra_campaign),
        "direct_placements": _direct_placements(extra_campaign),
        "direct_geo": _direct_geo(extra_campaign),
        "campaign_status": _campaign_status(extra_campaign),
        "seo_queries": _seo_queries(),
        "site_pages": _site_pages(),
        "site_link_graph": _site_link_graph(),
        "wordstat": _wordstat(),
    }
    for name, frame in tables.items():
        write_canonical_table(frame, name, paths.canonical / f"{name}.parquet")

    paths.config_file.write_text(yaml.safe_dump({
        "client": "det_fixture",
        "brand_terms": ["погнали"],
        "goals": {"form_open": ["1"], "form_submit": ["2"]},
        "sources": {"direct": {"enabled": True, "macro_goals": ["2"]}},
    }, allow_unicode=True), encoding="utf-8")
    (paths.inputs / "client_answers.yaml").write_text(
        yaml.safe_dump({"client_facts": {}}, allow_unicode=True), encoding="utf-8"
    )
    return paths, sorted(tables)


# ── Прогон compute ──────────────────────────────────────────────────────────
def run_compute(root: str, extra_campaign: str = "0") -> None:
    """Повторить run_compute без логирования: degradation -> блоки -> summary."""
    paths, tables = build_fixture(Path(root), extra_campaign=extra_campaign == "1")
    paths.metrics.mkdir(parents=True, exist_ok=True)
    methodology = orchestrator_mod.load_methodology()
    defaults = orchestrator_mod.load_defaults()
    config = orchestrator_mod.load_client_config(paths)
    report = degradation_mod.build_degradation_report(
        methodology, available=tables, config=config, defaults=defaults
    )
    common.write_json_atomic(paths.metrics / "degradation_report.json", report)
    dispatch_result = common.dispatch_blocks(paths, defaults, report)
    common.write_json_atomic(
        paths.metrics / "metrics_summary.json",
        common.build_metrics_summary(report, dispatch_result),
    )


def _run_compute_subprocess(
    root: Path, hashseed: str | None, *, extra_campaign: bool = False
) -> Path:
    env = dict(os.environ)
    if hashseed is None:
        env.pop("PYTHONHASHSEED", None)
    else:
        env["PYTHONHASHSEED"] = hashseed
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         " from tests.test_compute_determinism import run_compute;"
         " run_compute(sys.argv[2], sys.argv[3])",
         str(REPO_ROOT), str(root), "1" if extra_campaign else "0"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    return _Paths(root).metrics


def _metric_files(metrics_dir: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(metrics_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }


def _load_rows(metrics_dir: Path) -> dict[str, list[dict]]:
    reserved = {"analysis_candidates", "degradation_report", "metrics_summary"}
    out: dict[str, list[dict]] = {}
    for path in sorted(metrics_dir.glob("*.json")):
        if path.stem in reserved:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            out[path.stem] = [row for row in payload if isinstance(row, dict)]
    return out


def _dimensions(row: dict) -> str:
    """Ключ содержания строки — то, от чего обязан зависеть evidence_id."""
    return json.dumps(
        common._evidence_dimension_items(row, include_bools=True), ensure_ascii=False
    )


# ── 1. Побайтовая воспроизводимость ─────────────────────────────────────────
@pytest.mark.parametrize("hashseed", [None, "0"], ids=["default_hashseed", "hashseed_0"])
def test_two_runs_produce_byte_identical_metrics(tmp_path, hashseed):
    first = _run_compute_subprocess(tmp_path / "run1", hashseed)
    second = _run_compute_subprocess(tmp_path / "run2", hashseed)

    files_a, files_b = _metric_files(first), _metric_files(second)
    assert sorted(files_a) == sorted(files_b)
    assert len(files_a) > 50, "фикстура не породила артефактов — проверять нечего"

    differing = sorted(name for name in files_a if files_a[name] != files_b[name])
    assert differing == []


# ── 2. Новая кампания не сдвигает evidence_id существующих строк ────────────
def test_new_campaign_does_not_change_existing_evidence_ids(tmp_path):
    base = _run_compute_subprocess(tmp_path / "base", None)
    extra = _run_compute_subprocess(tmp_path / "extra", None, extra_campaign=True)

    base_rows, extra_rows = _load_rows(base), _load_rows(extra)
    assert base_rows, "нет артефактов для сверки"

    compared = 0
    for artifact, rows in base_rows.items():
        if artifact not in extra_rows:
            continue
        after = {
            _dimensions(row): row["evidence_id"]
            for row in extra_rows[artifact] if row.get("evidence_id")
        }
        for row in rows:
            if not row.get("evidence_id"):
                continue  # артефакты вне candidate-контракта (money_frame и т.п.)
            key = _dimensions(row)
            if key not in after:
                continue
            compared += 1
            assert row["evidence_id"] == after[key], (
                f"{artifact}: evidence_id строки изменился от появления новой кампании"
            )
    assert compared > 100, f"сверено слишком мало строк: {compared}"


def test_assign_evidence_ids_is_position_independent():
    rows = [
        {"check_id": "A07", "campaign_id": "101", "cost_rub": 100.0},
        {"check_id": "A07", "campaign_id": "102", "cost_rub": 200.0},
    ]
    before = common.assign_evidence_ids("a07", rows)
    # новая кампания в начале списка + изменившиеся метрики соседей
    shifted = [
        {"check_id": "A07", "campaign_id": "104", "cost_rub": 50.0},
        {"check_id": "A07", "campaign_id": "101", "cost_rub": 111.0},
        {"check_id": "A07", "campaign_id": "102", "cost_rub": 222.0},
    ]
    after = common.assign_evidence_ids("a07", shifted)
    assert before == after[1:]
    assert all(evidence_id.startswith("a07:") for evidence_id in before)
    assert len(set(after)) == len(after)


def test_evidence_id_distinguishes_rows_that_differ_only_by_boolean_dimension():
    rows = [
        {"check_id": "S01", "finding": "by_source", "source": "gsc", "is_brand": True},
        {"check_id": "S01", "finding": "by_source", "source": "gsc", "is_brand": False},
    ]
    ids = common.assign_evidence_ids("s01", rows)
    assert len(set(ids)) == 2


def test_evidence_label_is_human_readable_and_not_used_as_ref():
    row = {"check_id": "D08", "campaign_id": "119193036", "campaign_name": "Поиск",
           "total_cost_rub": 1.0}
    label = common.evidence_label(row)
    assert label.startswith("D08 · ")
    assert "campaign_name=Поиск" in label
    # label не является идентификатором: он не участвует в вычислении id
    assert common.assign_evidence_ids("d08", [row]) == common.assign_evidence_ids(
        "d08", [{**row, "evidence_label": label}]
    )


# ── 3. Все перекрёстные ссылки разрешаются ──────────────────────────────────
def test_all_context_refs_resolve_to_existing_evidence_ids(tmp_path):
    metrics = _run_compute_subprocess(tmp_path / "refs", None)
    rows_by_artifact = _load_rows(metrics)

    known = {
        row["evidence_id"]
        for rows in rows_by_artifact.values() for row in rows
        if row.get("evidence_id")
    }
    refs = [
        (artifact, ref)
        for artifact, rows in rows_by_artifact.items()
        for row in rows for ref in (row.get("context_refs") or [])
    ]
    assert refs, "фикстура не породила ни одной перекрёстной ссылки"
    assert [item for item in refs if item[1] not in known] == []

    payload = json.loads((metrics / "analysis_candidates.json").read_text(encoding="utf-8"))
    columns = payload["columns"]
    id_index = columns.index("evidence_id")
    refs_index = columns.index("context_refs")
    candidate_ids = {row[id_index] for row in payload["rows"]}
    unresolved = [
        ref for row in payload["rows"] for ref in (row[refs_index] or [])
        if ref not in candidate_ids
    ]
    assert unresolved == []


# ── 4. Порядок строк задан измерениями, а не порядком агрегации ─────────────
def test_group_by_queries_are_ordered_by_dimensions():
    """Ни один GROUP BY в src/compute/ не остаётся без ORDER BY.

    Исключения — подзапросы, из которых наружу уходит скаляр (COUNT поверх
    сгруппированного набора): там порядок групп не наблюдаем.
    """
    scalar_subqueries = {
        ("block2.py", "GROUP BY client_id"),
        ("block4_seo.py", "GROUP BY page HAVING"),
    }
    offenders = []
    for path in sorted((REPO_ROOT / "src" / "compute").glob("*.py")):
        lines = path.read_text(encoding="utf-8").split("\n")
        for index, line in enumerate(lines):
            if "GROUP BY" not in line:
                continue
            # ORDER BY может стоять на следующей строке того же SQL-литерала
            if any("ORDER BY" in tail for tail in lines[index:index + 3]):
                continue
            if any(
                path.name == name and marker in line
                for name, marker in scalar_subqueries
            ):
                continue
            offenders.append(f"{path.name}:{index + 1}")
    assert offenders == []
