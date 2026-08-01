"""Тесты слоя analyze: подключение OpenAI Responses API в draft_findings.py.

Вызов API OpenAI мокается целиком (_MockResponses/_MockClient) — сеть не
трогаем, реальный ключ/модель не нужны. Сценарии:

1. _call_llm: формирует ожидаемый запрос Responses API и парсит output_text.
2. _resolve_llm_model: по умолчанию DEFAULT_LLM_MODEL, переопределяется через
   project env (ANALYZE_LLM_MODEL), не через clients/<name>/.env — у _Paths
   в этих тестах вовсе нет .env_file, так что подмена через client-секреты
   структурно невозможна.
3. draft(): валидные находки модели пишутся как findings/draft/F-<блок>-<nn>.yaml
   с последовательной нумерацией внутри блока.
4. draft(): находки, не прошедшие schemas.validate_finding, отбрасываются без
   повторного вызова модели (ровно один responses.create за прогон).
5. draft(): не больше schemas.MAX_FINDINGS_PER_RUN находок пишется, даже если
   модель вернула больше — лишние отбрасываются, вызов модели остаётся один.
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
    """Минимальная замена ClientPaths (см. tests/test_money_frame.py).

    Намеренно без .env_file — секреты клиента (clients/<name>/.env) к
    вызову модели отношения не имеют (см. докстринг draft_findings.py).
    """

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
    ]
}

CONFIG = {
    "client": {"name": "Клиент Тест", "niche": "аренда авто", "geo": "Москва"},
    "brand_terms": ["тестбренд"],
    "data_window": {"mode": "explicit", "date_from": "2025-07-01", "date_to": "2026-06-30"},
}


def _write_degradation(paths: _Paths, checks: list[dict]) -> None:
    paths.metrics.mkdir(parents=True, exist_ok=True)
    report = {
        "runnable_check_ids": [c["check_id"] for c in checks if c.get("runnable")],
        "skipped": [],
        "checks": checks,
        "counts": {"total": len(checks)},
    }
    (paths.metrics / "degradation_report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def _write_metrics(paths: _Paths, name: str, rows: list[dict]) -> None:
    """data/metrics/<name>.json — source_file, который сверяет задача 6C
    (validate_findings_mod.validate_finding_evidence)."""
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / f"{name}.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )


def _evidence_metrics_row(check_id: str, confidence: str) -> dict:
    """Строка metrics, подтверждающая числа evidence по умолчанию в _finding_dict
    (10 000 ₽ / 0 конверсий / 6 месяцев) — нужна после задачи 6C, где
    draft() сверяет числа находки с data/metrics/<check_id>.json."""
    return {
        "check_id": check_id,
        "cost_normalized_rub": 10000.0,
        "net_conversions": 0,
        "period_months": 6,
        "confidence": confidence,
    }


class _MockResponses:
    """Подмена client.responses — capture пишет kwargs каждого вызова."""

    def __init__(self, findings_payload: list[dict], capture: list[dict] | None = None):
        self._payload = findings_payload
        self.capture = capture if capture is not None else []

    def create(self, **kwargs):
        self.capture.append(kwargs)
        text = json.dumps({"findings": self._payload}, ensure_ascii=False)
        return types.SimpleNamespace(output_text=text)


class _MockClient:
    def __init__(self, findings_payload: list[dict], capture: list[dict] | None = None):
        self.responses = _MockResponses(findings_payload, capture)


def _finding_dict(check_id: str, **overrides) -> dict:
    base = dict(
        check_id=check_id,
        name="Название находки",
        status="подтверждена",
        confidence="MED",
        significant=True,
        period="2025-07..2026-06",
        segment=None,
        data_source="Директ + Метрика",
        evidence="Кампания 'Х' потратила 10 000 ₽, чистых конверсий 0 за 6 месяцев",
        control_metric=None,
        what_is_distorted="Бюджет уходит на кампанию без результата",
        money_category="potentially_excludable_spend",
        money_amount_rub=10000.0,
        money_not_assessable=False,
        assumptions=[],
        recommended_action="Остановить или пересобрать кампанию",
        how_to_measure="Сравнить CPA/конверсии после изменения за аналогичный период",
        what_cannot_be_concluded="Нельзя утверждать, что кампания никогда не сработает",
        source_check_ids=[],
    )
    base.update(overrides)
    return base


# ── 1. _call_llm: форма запроса + разбор ответа ─────────────────────────────

def test_call_llm_sends_expected_request_and_parses_response():
    capture: list[dict] = []
    client = _MockClient([_finding_dict("A04")], capture)

    result = draft_findings._call_llm(
        "системный промт",
        {"known_check_ids": ["A04", "D01"], "analysis_candidates": {}},
        client=client,
    )

    assert result == {"findings": [_finding_dict("A04")]}
    assert len(capture) == 1
    kwargs = capture[0]
    assert kwargs["model"] == draft_findings.DEFAULT_LLM_MODEL
    assert kwargs["max_output_tokens"] == draft_findings.LLM_MAX_TOKENS
    assert kwargs["instructions"] == "системный промт"
    assert kwargs["text"]["format"]["type"] == "json_schema"
    assert kwargs["text"]["format"]["name"] == "analyze_findings"
    assert kwargs["text"]["format"]["strict"] is True
    assert kwargs["text"]["format"]["schema"]["required"] == ["findings"]
    item_schema = kwargs["text"]["format"]["schema"]["properties"]["findings"]["items"]
    assert set(item_schema["required"]) == set(item_schema["properties"])
    properties = item_schema["properties"]
    assert properties["status"]["enum"] == sorted(schemas.STATUS_VALUES)
    assert properties["confidence"]["enum"] == sorted(schemas.CONFIDENCE_VALUES)
    assert set(properties["money_category"]["enum"]) == {None, *schemas.MONEY_CATEGORY_VALUES}
    assert properties["check_id"]["enum"] == ["A04", "D01"]
    assert len(kwargs["input"]) == 1
    assert kwargs["input"][0]["role"] == "user"
    assert '"analysis_candidates"' in kwargs["input"][0]["content"]


def test_call_llm_fails_clearly_without_proxyapi_api_key(monkeypatch):
    monkeypatch.delenv(draft_findings.PROXYAPI_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")

    import pytest

    with pytest.raises(RuntimeError, match="PROXYAPI_API_KEY"):
        draft_findings._call_llm("системный промт", {"metrics": {}})


def test_call_llm_creates_client_with_proxyapi_key_and_default_base_url(monkeypatch):
    captured: dict[str, object] = {}

    class _MockOpenAI:
        def __init__(self, *, api_key, base_url, timeout, max_retries, **_private):
            captured.update(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
            self.responses = _MockResponses([])

    monkeypatch.setenv(draft_findings.PROXYAPI_API_KEY_ENV_VAR, "test-proxyapi-key")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_MockOpenAI))

    assert draft_findings._call_llm("системный промт", {"metrics": {}}) == {"findings": []}
    assert captured == {
        "api_key": "test-proxyapi-key",
        "base_url": draft_findings.DEFAULT_LLM_BASE_URL,
        "timeout": draft_findings.LLM_TIMEOUT_SECONDS,
        "max_retries": draft_findings.LLM_MAX_RETRIES,
    }


def test_call_llm_uses_base_url_override(monkeypatch):
    captured: dict[str, object] = {}

    class _MockOpenAI:
        def __init__(self, *, api_key, base_url, **_private):
            captured.update(api_key=api_key, base_url=base_url)
            self.responses = _MockResponses([])

    monkeypatch.setenv(draft_findings.PROXYAPI_API_KEY_ENV_VAR, "test-proxyapi-key")
    monkeypatch.setenv(draft_findings.LLM_BASE_URL_ENV_VAR, "https://proxy.example/v1")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_MockOpenAI))

    assert draft_findings._call_llm("системный промт", {"metrics": {}}) == {"findings": []}
    assert captured == {
        "api_key": "test-proxyapi-key",
        "base_url": "https://proxy.example/v1",
    }


# ── 2. _resolve_llm_model: project env, не client env ──────────────────────

def test_resolve_llm_model_defaults_without_env(monkeypatch):
    monkeypatch.delenv(draft_findings.LLM_MODEL_ENV_VAR, raising=False)
    assert draft_findings._resolve_llm_model() == draft_findings.DEFAULT_LLM_MODEL


def test_resolve_llm_model_reads_project_env_override(monkeypatch):
    monkeypatch.setenv(draft_findings.LLM_MODEL_ENV_VAR, "gpt-5.6-sol")
    assert draft_findings._resolve_llm_model() == "gpt-5.6-sol"


# ── 3. draft(): валидные находки -> F-<блок>-<nn>.yaml ──────────────────────

def test_draft_writes_valid_findings_with_block_numbered_filenames(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [
        {"check_id": "D01", "runnable": True, "confidence_cap": "HIGH"},
        {"check_id": "A04", "runnable": True, "confidence_cap": "MED"},
    ])
    _write_metrics(paths, "d01", [_evidence_metrics_row("D01", "HIGH")])
    _write_metrics(paths, "a04", [_evidence_metrics_row("A04", "MED")])
    payload = [_finding_dict("D01", confidence="HIGH"), _finding_dict("A04", confidence="MED")]

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient(payload))

    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME, "F-D-01.yaml", "F-A-01.yaml"]

    import yaml

    written = (paths.findings_draft / "F-D-01.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(written)
    assert data["check_id"] == "D01"
    assert data["confidence"] == "HIGH"


def test_draft_numbers_multiple_findings_in_same_block_sequentially(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "A04", "runnable": True, "confidence_cap": "MED"}])
    _write_metrics(paths, "a04", [_evidence_metrics_row("A04", "MED")])
    payload = [_finding_dict("A04"), _finding_dict("A04", name="Другая проблема")]

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient(payload))

    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME, "F-A-01.yaml", "F-A-02.yaml"]


def test_draft_sends_only_p06_candidates_to_llm(tmp_path, monkeypatch):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "A04", "runnable": True, "confidence_cap": "MED"}])
    _write_metrics(paths, "a04", [_evidence_metrics_row("A04", "MED")])
    paths.metrics.mkdir(parents=True, exist_ok=True)
    (paths.metrics / "analysis_candidates.json").write_text(
        json.dumps({
            "columns": ["check_id", "row_role", "candidate", "payload"],
            "rows": [["A04", "candidate", True, {"cost": 5000.0}]],
            "coverage": {"checks_calculated": 1},
        }),
        encoding="utf-8",
    )
    _write_metrics(paths, "a19", [{"details": "RAW_SENTINEL" * 100}])
    capture: list[dict] = []
    loaded_stems: list[str] = []
    original_loader = draft_findings._load_json_artifact

    def tracked_loader(current_paths, stem):
        loaded_stems.append(stem)
        return original_loader(current_paths, stem)

    monkeypatch.setattr(draft_findings, "_load_json_artifact", tracked_loader)

    draft_findings.draft(
        paths, CONFIG, METHODOLOGY, client=_MockClient([_finding_dict("A04")], capture)
    )

    sent_pack = json.loads(capture[0]["input"][0]["content"].partition("\n\n")[2])
    assert sent_pack["analysis_candidates"]["rows"][0][0] == "A04"
    assert "metrics" not in sent_pack
    assert "RAW_SENTINEL" not in json.dumps(sent_pack)
    assert "a04" in loaded_stems
    assert "a19" not in loaded_stems


# ── 4. Невалидные находки отбрасываются в rejected/ без повторного вызова
#      модели (задача 6C: и schema-, и evidence-нарушения) ──────────────────

def test_draft_drops_invalid_findings_without_regenerating(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "A04", "runnable": True, "confidence_cap": "MED"}])
    _write_metrics(paths, "a04", [_evidence_metrics_row("A04", "MED")])
    payload = [
        _finding_dict("A04", significant=False),  # невалидна: significant=false запрещено
        _finding_dict("A04"),                      # валидна
    ]
    capture: list[dict] = []

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient(payload, capture))

    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME, "F-A-01.yaml"]
    assert len(capture) == 1  # ни одного повторного вызова модели

    rejected = sorted((paths.findings_draft / "rejected").glob("*.yaml"))
    assert len(rejected) == 1
    import yaml

    rejected_data = yaml.safe_load(rejected[0].read_text(encoding="utf-8"))
    assert any("significant=false" in reason for reason in rejected_data["reasons"])


# ── 5. Лимит MAX_FINDINGS_PER_RUN соблюдается ───────────────────────────────

def test_draft_applies_limit_after_grouping_repeated_segments(tmp_path):
    paths = _Paths(tmp_path)
    _write_degradation(paths, [{"check_id": "A04", "runnable": True, "confidence_cap": "MED"}])
    _write_metrics(paths, "a04", [_evidence_metrics_row("A04", "MED")])
    payload = [
        _finding_dict("A04", segment=f"segment-{index}")
        for index in range(schemas.MAX_FINDINGS_PER_RUN + 1)
    ]
    capture: list[dict] = []

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient(payload, capture))

    finding_names = [n for n in names if n != draft_findings.INPUT_PACK_ARTIFACT_NAME]
    assert finding_names == ["F-A-01.yaml"]
    assert len(capture) == 1

    import yaml

    grouped = yaml.safe_load((paths.findings_draft / "F-A-01.yaml").read_text(encoding="utf-8"))
    assert "segment-0" in grouped["segment"]
    assert f"segment-{schemas.MAX_FINDINGS_PER_RUN}" in grouped["segment"]
