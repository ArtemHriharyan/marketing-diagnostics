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
    _write_json(paths.metrics, "a04", [
        {"check_id": "A04", "campaign_id": "c1", "cost_normalized_rub": 10000.0,
         "zero_conversion_campaign": True, "confidence": "MED"},
    ])
    # Служебные артефакты не должны попасть в metrics-пакет как обычные проверки.
    _write_json(paths.metrics, "metrics_summary", {"counts": {"total": 2}})

    paths.inputs.mkdir(parents=True)
    (paths.inputs / "client_answers.yaml").write_text(
        "business:\n  avg_check_rub: 5000\n", encoding="utf-8"
    )

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["client_context"]["name"] == "Клиент Тест"
    assert pack["client_context"]["niche"] == "аренда авто"
    assert pack["methodology_check_names"]["A04"].startswith("Кампания расходует")
    assert set(pack["known_check_ids"]) == {"D01", "A04", "C06"}

    assert "a04" in pack["metrics"]
    assert "metrics_summary" not in pack["metrics"]
    assert "degradation_report" not in pack["metrics"]

    assert pack["inputs"]["client_answers"]["business"]["avg_check_rub"] == 5000

    assert pack["degradation"]["runnable_check_ids"] == ["D01", "A04"]
    assert pack["confidence_ceilings"]["source_cap_by_check"] == {"D01": "HIGH", "A04": "MED"}
    assert pack["confidence_ceilings"]["sample_size_rule"]["min_sample_visits"] == 500
    assert pack["confidence_ceilings"]["sample_size_rule"]["significance_alpha"] == 0.05

    assert pack["money_categories"] == dict(schemas.MONEY_CATEGORIES)
    assert pack["max_findings_per_run"] == schemas.MAX_FINDINGS_PER_RUN


def test_build_input_pack_missing_sources_are_empty_not_broken(tmp_path):
    paths = _Paths(tmp_path)
    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["metrics"] == {}
    assert pack["inputs"] == {}
    assert pack["degradation"]["checks"] == []
    assert pack["confidence_ceilings"]["source_cap_by_check"] == {}


def test_build_input_pack_excludes_non_finding_metrics(tmp_path):
    paths = _Paths(tmp_path)
    _write_json(paths.metrics, "t03", [{"check_id": "T03", "status": "unavailable"}])
    _write_json(paths.metrics, "t09", {
        "summary": {"status": "unavailable_for_cause"},
        "anomalies": [{"finding": "channel_anomaly_context"}],
    })

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert "t03" not in pack["metrics"]
    assert "t09" not in pack["metrics"]


def test_input_pack_round_trips_through_json(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "D01", "runnable": True, "confidence_cap": "HIGH"}])
    _write_json(paths.metrics, "d01", [{"check_id": "D01", "confidence": "HIGH"}])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    pack["system_prompt"] = draft_findings.build_system_prompt(DEFAULTS)

    serialized = json.dumps(pack, ensure_ascii=False)
    restored = json.loads(serialized)
    assert restored == pack


def test_build_input_pack_projects_only_reportable_a19_a20_c21_rows(tmp_path):
    paths = _Paths(tmp_path)
    a19_rows = [
        {"campaign": "high", "cpc_anomalously_high": True, "cost": 1500.0},
        {"campaign": "normal", "cpc_anomalously_high": False, "cost": 500.0},
    ]
    a20_rows = [
        {"campaign": "low", "anomalously_low_ctr": True, "ctr": 0.01},
        {"campaign": "normal", "anomalously_low_ctr": False, "ctr": 0.04},
    ]
    c21_rows = [
        {"segment": "bad", "segment_underperforms_baseline": True, "sessions": 20},
        {"segment": "control", "is_baseline": True, "sessions": 100},
        {"segment": "other", "segment_underperforms_baseline": False, "sessions": 80},
    ]
    _write_json(paths.metrics, "a19", a19_rows)
    _write_json(paths.metrics, "a20", a20_rows)
    _write_json(paths.metrics, "c21", c21_rows)
    _write_json(paths.metrics, "a04", [{"check_id": "A04", "some_bool": False}])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert json.loads((paths.metrics / "a19.json").read_text(encoding="utf-8")) == a19_rows
    assert json.loads((paths.metrics / "a20.json").read_text(encoding="utf-8")) == a20_rows
    assert json.loads((paths.metrics / "c21.json").read_text(encoding="utf-8")) == c21_rows
    assert pack["metrics"]["a19"] == {
        "summary": {
            "total_rows": 2,
            "candidate_rows": 1,
            "context_rows": 0,
            "candidate_flag": "cpc_anomalously_high",
        },
        "candidates": [a19_rows[0]],
    }
    assert pack["metrics"]["a20"]["candidates"] == [a20_rows[0]]
    assert pack["metrics"]["c21"]["candidates"] == [c21_rows[0]]
    assert pack["metrics"]["c21"]["context"] == [c21_rows[1]]
    assert pack["metrics"]["a04"] == [{"check_id": "A04", "some_bool": False}]


def test_build_input_pack_omits_rows_when_projected_artifact_has_no_candidates(tmp_path):
    paths = _Paths(tmp_path)
    _write_json(paths.metrics, "a19", [{"cpc_anomalously_high": False}])
    _write_json(paths.metrics, "a20", [{"anomalously_low_ctr": False}])
    _write_json(paths.metrics, "c21", [{"is_baseline": True, "segment_underperforms_baseline": False}])

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["metrics"]["a19"] == {
        "summary": {
            "total_rows": 1,
            "candidate_rows": 0,
            "context_rows": 0,
            "candidate_flag": "cpc_anomalously_high",
        }
    }
    assert pack["metrics"]["a20"]["summary"]["candidate_rows"] == 0
    assert pack["metrics"]["c21"] == {
        "summary": {
            "total_rows": 1,
            "candidate_rows": 0,
            "context_rows": 1,
            "candidate_flag": "segment_underperforms_baseline",
        }
    }


def test_llm_metric_projection_is_deterministic_and_substantially_smaller(tmp_path):
    paths = _Paths(tmp_path)
    rows = [
        {"campaign": f"normal-{i}", "cpc_anomalously_high": False, "details": "x" * 200}
        for i in range(50)
    ]
    rows.append({"campaign": "high", "cpc_anomalously_high": True, "details": "x" * 200})
    _write_json(paths.metrics, "a19", rows)

    first_pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    second_pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert first_pack == second_pack
    assert len(json.dumps(first_pack["metrics"]["a19"])) < len(json.dumps(rows)) / 2


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
