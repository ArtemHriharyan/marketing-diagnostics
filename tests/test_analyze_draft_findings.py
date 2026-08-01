"""Тесты слоя analyze (задача 6A): src/analyze/draft_findings.py + schemas.py.

Сценарии для детерминированной части (задача 6A):

1. build_input_pack: собирает metrics/inputs/degradation/client_context/
   known_check_ids/confidence_ceilings/money_categories.
2. Input pack целиком JSON-сериализуем (это тело запроса к API).
3. draft(): при пустом ответе модели пишет ровно один аудиторский артефакт
   в findings/draft/ (не находку), возвращает его имя; содержимое парсится
   обратно. Вызов API подменён мок-клиентом (_MockClient) — задача 6B
   подключает реальный вызов, см. tests/test_analyze_draft_findings_llm.py
   для сценариев с находками.
4. build_system_prompt: содержит все обязательные запреты текстом.
5. schemas.Finding/validate_finding: валидная находка не даёт нарушений;
   каждое нарушение (significant=false, confidence > cap, money_category
   вне 4 категорий, money_category+money_not_assessable одновременно,
   money_amount_rub без категории, неверный формат/незарегистрированный
   check_id) детектируется по отдельности.
6. validate_findings_batch: лимит 12 находок за прогон.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analyze import draft_findings, schemas  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_money_frame.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.metrics = root / "data" / "metrics"
        self.inputs = root / "inputs"
        self.findings_draft = root / "findings" / "draft"
        self.config_file = root / "config.yaml"


METHODOLOGY = {
    "checks": [
        {"id": "D01", "name": "Ключевая цель срабатывает несколько раз в одном визите"},
        {"id": "A04", "name": "Кампания расходует деньги и не даёт ни одной чистой конверсии"},
        {"id": "C06", "name": "Большой отвал между открытием и отправкой формы"},
        {"id": "S06", "name": "Сезонность объясняет SEO-аномалию"},
    ]
}

DEFAULTS = {
    "min_sample_visits": 500,
    "significance_alpha": 0.05,
    "currency_round": 0,
    "manual_source_confidence_cap": "MED",
}

CONFIG = {
    "client": {"name": "Клиент Тест", "niche": "аренда авто", "geo": "Москва"},
    "brand_terms": ["тестбренд"],
    "data_window": {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"},
}


def _write_json(directory: Path, name: str, data) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


CANDIDATE_COLUMNS = [
    "check_id", "row_role", "candidate", "status", "confidence", "significant", "payload",
]


def _write_candidates(paths: _Paths, rows: list[list], coverage: dict | None = None) -> None:
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": CANDIDATE_COLUMNS,
        "rows": rows,
        "coverage": coverage or {"checks_calculated": 2},
    })


def _write_degradation(paths: _Paths, checks: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {
        "runnable_check_ids": [c["check_id"] for c in checks if c.get("runnable")],
        "skipped": [],
        "checks": checks,
        "counts": {"total": len(checks), "runnable": sum(1 for c in checks if c.get("runnable")), "skipped": 0},
    }
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


# ── 1+2. build_input_pack: сбор + JSON-сериализуемость ─────────────────────

def test_build_input_pack_collects_all_sections(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [
        {"check_id": "D01", "runnable": True, "confidence_cap": "HIGH", "type_effective": "A"},
        {"check_id": "A04", "runnable": True, "confidence_cap": "MED", "type_effective": "A"},
    ])
    _write_candidates(paths, [
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}],
        ["S06", "candidate", True, "ok", "MED", True, {"trend": "down"}],
    ])
    _write_json(paths.metrics, "funnels", {"booking": {"open": 100, "submit": 45}})
    _write_json(paths.metrics, "acquisition_economics", {"models": [{"value_rub": 2500.0}]})
    _write_json(paths.metrics, "seasonality", {"peaks": ["2026-06"]})
    _write_json(paths.metrics, "a04", [{"raw": "x" * 10_000}])

    paths.inputs.mkdir(parents=True)
    (paths.inputs / "client_answers.yaml").write_text(
        "business:\n  avg_check_rub: 5000\n  comment: ''\n", encoding="utf-8"
    )

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["client_context"]["name"] == "Клиент Тест"
    assert pack["client_context"]["niche"] == "аренда авто"
    assert pack["check_names"]["A04"].startswith("Кампания расходует")
    assert set(pack["known_check_ids"]) == {"D01", "A04", "C06", "S06"}
    assert pack["coverage"]["included_check_ids"] == ["C06", "S06"]
    assert set(pack["compact_context"]) == {"funnels", "acquisition_economics", "seasonality"}
    assert "metrics" not in pack
    assert "raw" not in json.dumps(pack, ensure_ascii=False)

    assert pack["inputs"]["client_answers"]["business"]["avg_check_rub"] == 5000
    assert "comment" not in pack["inputs"]["client_answers"]["business"]

    assert pack["constraints"]["source_cap_by_check"] == {"D01": "HIGH", "A04": "MED"}
    assert pack["constraints"]["sample_size_rule"]["min_sample_visits"] == 500
    assert pack["constraints"]["money_categories"] == dict(schemas.MONEY_CATEGORIES)
    assert pack["audit"]["input_pack_bytes"] < pack["audit"]["byte_cap"]


def test_build_input_pack_missing_sources_are_empty_not_broken(tmp_path):
    paths = _Paths(tmp_path)
    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["analysis_candidates"]["rows"] == []
    assert pack["compact_context"] == {}
    assert pack["inputs"] == {}
    assert pack["degradation"]["rows"] == []
    assert pack["constraints"]["source_cap_by_check"] == {}


def test_build_input_pack_filters_unavailable_rows_not_mixed_artifact(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [
        ["S06", "candidate", True, "ok", "MED", True, {"source": "gsc+wordstat"}],
        ["S06", "candidate", True, "unavailable", "LOW", False, {"source": "webmaster"}],
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}],
    ])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    rows = pack["analysis_candidates"]["rows"]
    assert [row[0] for row in rows] == ["S06", "C06"]
    assert pack["coverage"]["candidates_detected"] == 3
    assert pack["coverage"]["candidates_excluded"] == 1
    assert pack["excluded_candidates"][0]["reason"] == "unavailable_row"


def test_input_pack_round_trips_through_json(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "D01", "runnable": True, "confidence_cap": "HIGH"}])
    _write_candidates(paths, [["D01", "candidate", True, "ok", "HIGH", True, {"value": 1}]])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    pack["system_prompt"] = draft_findings.build_system_prompt(DEFAULTS)

    serialized = json.dumps(pack, ensure_ascii=False)
    restored = json.loads(serialized)
    assert restored == pack


def test_build_input_pack_uses_only_p06_candidates_not_raw_metric_arrays(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}]])
    _write_json(paths.metrics, "a19", [{"details": "RAW_SENTINEL" * 1000}])
    _write_json(paths.metrics, "c21", [{"details": "RAW_SENTINEL" * 1000}])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert "RAW_SENTINEL" not in json.dumps(pack, ensure_ascii=False)
    assert pack["analysis_candidates"]["rows"][0][0] == "C06"


def test_build_input_pack_byte_cap_is_deterministic_and_audited(tmp_path):
    paths = _Paths(tmp_path)
    rows = [
        ["C06", "candidate", True, "ok", "LOW", False, {"detail": "x" * 1200, "n": i}]
        for i in range(8)
    ]
    rows.append([
        "S06", "candidate", True, "ok", "HIGH", True,
        {"money_amount_rub": 10000.0, "detail": "x" * 1200},
    ])
    _write_candidates(paths, rows)
    defaults = {**DEFAULTS, "analyze_input_pack_byte_cap": 4500}

    first = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, defaults)
    second = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, defaults)

    assert first == second
    assert first["audit"]["input_pack_bytes"] <= 4500
    assert first["coverage"]["candidates_excluded"] > 0
    assert any(item["reason"] == "byte_cap" for item in first["excluded_candidates"])
    assert "S06" in first["coverage"]["included_check_ids"]


def test_pognali_fixture_compacts_funnels_and_keeps_every_candidate(tmp_path):
    paths = _Paths(tmp_path)
    candidate_columns = [*CANDIDATE_COLUMNS, "row_ref", "context_refs"]
    candidate_rows = [
        [
            "A04", "detail", False, "ok", "MED", True,
            {"detail": "x" * 2500}, f"detail-{index}", [],
        ]
        for index in range(45)
    ]
    candidate_rows.extend([
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}, "c06", []],
        ["S06", "candidate", True, "ok", "MED", True, {"trend": "down"}, "s06", []],
        ["A04", "candidate", True, "ok", "MED", True, {"cost_rub": 5000}, "a04", []],
    ])
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": candidate_columns,
        "rows": candidate_rows,
        "coverage": {"checks_calculated": 3},
    })
    segments = [
        {
            "dimension": "landing_page",
            "segment": f"/cars/{index}",
            "stages": [
                {"stage_id": "form_open", "visits": 100 + index},
                {"stage_id": "form_submit", "visits": 40 + index},
            ],
            "transitions": [{
                "from_stage": "form_open", "to_stage": "form_submit",
                "rate": 0.4, "gap_visits": 60,
            }],
        }
        for index in range(420)
    ]
    funnels = {
        "funnels": [{
            "id": "booking",
            "stages": [
                {"stage_id": "form_open", "visits": 3002},
                {"stage_id": "form_submit", "visits": 634},
            ],
            "segments": {"landing_page": segments},
        }]
    }
    assert len(json.dumps(funnels, ensure_ascii=False).encode("utf-8")) > 90_000
    _write_json(paths.metrics, "funnels", funnels)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    projection = pack["compact_context"]["funnels"]
    funnel_columns = projection["funnels"]["columns"]
    funnel_row = projection["funnels"]["rows"][0]
    segment_projection = funnel_row[funnel_columns.index("segments")]["landing_page"]
    assert segment_projection["columns"]
    assert len(segment_projection["rows"]) == len(segments)
    assert pack["audit"]["byte_cap"] == 100_000
    assert pack["audit"]["input_pack_bytes"] < 100_000
    assert pack["coverage"]["included_check_ids"] == ["A04", "C06", "S06"]
    assert pack["coverage"]["candidates_omitted"] == 0
    assert pack["excluded_candidates"] == []


# ── 3. draft(): аудиторский артефакт всегда пишется первым ─────────────────
# Задача 6B подключила вызов модели (см. tests/test_analyze_draft_findings_llm.py
# для сценариев с находками) — здесь только проверяем, что при пустом ответе
# модели (findings: []) draft() по-прежнему пишет ровно аудиторский артефакт,
# а не «находку». Реальный вызов API подменяется мок-клиентом.

class _MockResponses:
    def __init__(self, findings_payload):
        self._payload = findings_payload

    def create(self, **kwargs):
        text = json.dumps({"findings": self._payload}, ensure_ascii=False)
        return types.SimpleNamespace(output_text=text)


class _MockClient:
    def __init__(self, findings_payload):
        self.responses = _MockResponses(findings_payload)


def test_draft_writes_single_audit_artifact_not_a_finding(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "D01", "runnable": True, "confidence_cap": "HIGH"}])

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient([]))

    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME]
    written = list(paths.findings_draft.glob("*"))
    assert len(written) == 1
    assert written[0].name.startswith("_")  # не похоже на карточку находки

    content = json.loads(written[0].read_text(encoding="utf-8"))
    assert "system_prompt" in content
    assert "client_context" in content


# ── 4. build_system_prompt: обязательные запреты ────────────────────────────

def test_system_prompt_contains_all_required_bans():
    prompt = draft_findings.build_system_prompt(DEFAULTS)

    lowered = prompt.lower()
    assert "п.п." in prompt and "%" in prompt
    assert "significant" in lowered
    assert "12" in prompt  # MAX_FINDINGS_PER_RUN
    assert "обвин" in lowered  # "обвинять"
    assert "confidence_cap" in prompt or "потолок" in lowered
    assert "check_id" in prompt
    for category in schemas.MONEY_CATEGORIES:
        assert category in prompt


# ── 5. schemas.Finding / validate_finding ────────────────────────────────────

def _valid_finding(**overrides) -> schemas.Finding:
    base = dict(
        check_id="A04",
        name="Кампания расходует деньги и не даёт ни одной чистой конверсии",
        status="подтверждена",
        confidence="MED",
        significant=True,
        period="2025-07..2026-06",
        data_source="Директ + Метрика",
        evidence="Кампания 'Х' потратила 10 000 ₽, чистых конверсий 0 за 6 месяцев",
        what_is_distorted="Бюджет уходит на кампанию без результата",
        recommended_action="Остановить или пересобрать кампанию",
        how_to_measure="Сравнить CPA/конверсии после изменения за аналогичный период",
        what_cannot_be_concluded="Нельзя утверждать, что кампания никогда не сработает",
        money_category="potentially_excludable_spend",
        money_amount_rub=10000.0,
    )
    base.update(overrides)
    return schemas.Finding(**base)


def test_valid_finding_has_no_errors():
    finding = _valid_finding()
    errors = schemas.validate_finding(finding, known_ids=schemas.known_check_ids(METHODOLOGY), confidence_cap="MED")
    assert errors == []


def test_significant_false_is_rejected():
    finding = _valid_finding(significant=False)
    errors = schemas.validate_finding(finding)
    assert any("significant=false" in e for e in errors)


def test_confidence_above_cap_is_rejected():
    finding = _valid_finding(confidence="HIGH")
    errors = schemas.validate_finding(finding, confidence_cap="MED")
    assert any("превышает потолок источника" in e for e in errors)


def test_client_high_bypasses_source_cap():
    finding = _valid_finding(confidence=schemas.CLIENT_CONFIDENCE)
    errors = schemas.validate_finding(finding, confidence_cap="MED")
    assert errors == []


def test_invalid_money_category_is_rejected():
    finding = _valid_finding(money_category="total_grand_sum")
    errors = schemas.validate_finding(finding)
    assert any("не входит в 4 категории" in e for e in errors)


def test_money_category_and_not_assessable_together_is_rejected():
    finding = _valid_finding(money_not_assessable=True)  # money_category уже задана в base
    errors = schemas.validate_finding(finding)
    assert any("взаимоисключающие" in e for e in errors)


def test_money_amount_without_category_is_rejected():
    finding = _valid_finding(money_category=None, money_not_assessable=True)
    finding.money_amount_rub = 500.0  # категория снята, но сумма осталась
    errors = schemas.validate_finding(finding)
    assert any("money_amount_rub" in e for e in errors)


def test_bad_check_id_format_is_rejected():
    finding = _valid_finding(check_id="2.2")
    errors = schemas.validate_finding(finding)
    assert any("формату нового реестра" in e for e in errors)


def test_check_id_not_in_registry_is_rejected():
    finding = _valid_finding(check_id="A99")
    errors = schemas.validate_finding(finding, known_ids=schemas.known_check_ids(METHODOLOGY))
    assert any("отсутствует в реестре" in e for e in errors)


def test_empty_required_field_is_rejected():
    finding = _valid_finding(evidence="")
    errors = schemas.validate_finding(finding)
    assert any("'evidence'" in e for e in errors)


# ── 6. validate_findings_batch: лимит 12 находок ────────────────────────────

def test_batch_over_limit_is_rejected():
    findings = [_valid_finding(check_id="A04") for _ in range(13)]
    errors = schemas.validate_findings_batch(findings, methodology=METHODOLOGY)
    assert any("максимум 12" in e for e in errors)


def test_batch_within_limit_and_valid_has_no_errors():
    findings = [_valid_finding() for _ in range(3)]
    errors = schemas.validate_findings_batch(
        findings, methodology=METHODOLOGY, confidence_caps={"A04": "MED"}
    )
    assert errors == []
