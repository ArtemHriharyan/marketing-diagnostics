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
        ["D01", "candidate", True, "ok", "HIGH", True, {"repeats": 3}],
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
    # PACK-1: реестры сужены до задействованных проверок прогона — A04
    # кандидатов не дал, поэтому в check_names/known_check_ids его нет
    assert pack["check_names"]["C06"].startswith("Большой отвал")
    assert set(pack["known_check_ids"]) == {"D01", "C06", "S06"}
    assert "A04" not in pack["check_names"]
    assert pack["coverage"]["included_check_ids"] == ["C06", "D01", "S06"]
    assert set(pack["compact_context"]) == {"funnels", "acquisition_economics", "seasonality"}
    assert "metrics" not in pack
    assert "raw" not in json.dumps(pack, ensure_ascii=False)

    assert pack["inputs"]["client_answers"]["business"]["avg_check_rub"] == 5000
    assert "comment" not in pack["inputs"]["client_answers"]["business"]

    assert pack["constraints"]["source_cap_by_check"] == {"D01": "HIGH"}
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


def test_build_input_pack_prioritizes_candidates_over_optional_context(tmp_path):
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
    paths.inputs.mkdir(parents=True)
    (paths.inputs / "optional_context.yaml").write_text(
        "comment: '" + "я" * 80_000 + "'\n", encoding="utf-8"
    )

    first = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    second = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert first == second
    assert first["audit"]["final_serialized_bytes"] < 100_000
    assert first["coverage"]["candidates_omitted"] == 0
    assert first["excluded_candidates"] == []
    assert "inputs.optional_context" in first["audit"]["omitted_context"]
    assert "S06" in first["coverage"]["included_check_ids"]


def _union_schema_candidate_rows() -> tuple[list[str], list[list]]:
    """638 кандидатов в union-схеме ~200 колонок, ~10 заполненных на строку.

    Так выглядит реальный analysis_candidates.json (P06): колонки — объединение
    полей ВСЕХ типов проверок методологии, поэтому в каждой конкретной строке
    заполнены единицы полей, а остальные несут null. Прежняя фикстура строила
    8 плотных колонок и потому не воспроизводила основной вклад в размер
    пакета — null-overhead union-схемы (ломающее изменение фикстуры, PACK-1).
    """
    base_columns = [
        *CANDIDATE_COLUMNS, "candidate_reason", "segment", "row_ref", "context_refs",
    ]
    # +190 полей чужих типов проверок — в строках C06/S06 они всегда null
    filler_columns = [f"metric_{index:03d}" for index in range(190)]
    columns = [*base_columns, *filler_columns]
    assert len(columns) >= 200

    rows = []
    for index in range(319):
        c06 = {
            "check_id": "C06", "row_role": "candidate", "candidate": True,
            "status": "ok", "confidence": "HIGH", "significant": True,
            "payload": {"rate": 0.45, "gap_visits": 60, "metric": "open_to_submit"},
            "candidate_reason": "funnel_gap",
            "segment": f"landing_page=/cars/{index}",
            "row_ref": f"c06-{index}", "context_refs": [],
        }
        s06 = {
            "check_id": "S06", "row_role": "candidate", "candidate": True,
            "status": "ok", "confidence": "MED", "significant": True,
            "payload": {"trend": "down", "demand_index": 0.82, "metric": "seasonality"},
            "candidate_reason": "seasonality_conflict",
            "segment": f"month=2026-{index % 12 + 1:02d}",
            "row_ref": f"s06-{index}", "context_refs": [],
        }
        for decoded in (c06, s06):
            rows.append([decoded.get(column) for column in columns])
    assert len(rows) == 638
    assert all(sum(1 for v in row if v is not None) <= 11 for row in rows)
    return columns, rows


def test_real_p10_stress_pack_keeps_638_candidates_under_final_cap(tmp_path):
    paths = _Paths(tmp_path)
    candidate_columns, candidate_rows = _union_schema_candidate_rows()
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": candidate_columns,
        "rows": candidate_rows,
        "coverage": {"checks_calculated": 2, "artifacts": [{"raw": "x" * 5_000}]},
    })
    funnels = {
        "funnels": [{
            "id": "booking",
            "totals": {"form_open": 3002, "form_submit": 634},
            "transitions": [{
                "from_stage": "form_open", "to_stage": "form_submit", "rate": 0.2112,
            }],
            "gaps": [{"stage": "form_open", "next_stage": "form_submit", "visits": 2368}],
            "anomalies": [{"code": "late_without_early", "visits": 14}],
            "segments": [
                {"segment": f"/cars/{index}", "raw": "x" * 400}
                for index in range(420)
            ],
            "qa_details": ["RAW_FUNNEL_SENTINEL" * 1000],
        }]
    }
    assert len(json.dumps(funnels, ensure_ascii=False).encode("utf-8")) > 100_000
    _write_json(paths.metrics, "funnels", funnels)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    projection = pack["compact_context"]["funnels"]
    funnel_columns = projection["funnels"]["columns"]
    funnel_row = projection["funnels"]["rows"][0]
    assert set(funnel_columns) == {"id", "totals", "transitions", "gaps", "anomalies"}
    assert "RAW_FUNNEL_SENTINEL" not in json.dumps(projection, ensure_ascii=False)

    candidate_projection = pack["analysis_candidates"]
    assert candidate_projection["columns"] == [
        "check_id", "candidate_reason", "candidate_count", "common", "segments",
    ]
    assert sum(row[2] for row in candidate_projection["rows"]) == 638
    assert all(row[4]["columns"] and row[4]["rows"] for row in candidate_projection["rows"])
    # 190 полей чужих типов проверок не доехали до модели как null-балласт
    assert all(row[3] and not any(v is None for v in row[3].values())
               for row in candidate_projection["rows"])
    assert all("metric_000" not in row[3] and "metric_000" not in row[4]["columns"]
               for row in candidate_projection["rows"])

    system_prompt = draft_findings.build_system_prompt(DEFAULTS)
    final_size = len(json.dumps(
        {**pack, "system_prompt": system_prompt},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8"))
    assert pack["audit"]["byte_cap"] == draft_findings.INPUT_PACK_BYTE_CAP
    assert final_size == pack["audit"]["final_serialized_bytes"]
    assert final_size < 100_000
    assert pack["coverage"]["included_check_ids"] == ["C06", "S06"]
    assert pack["coverage"]["candidates_included"] == 638
    assert pack["coverage"]["candidates_omitted"] == 0
    assert pack["excluded_candidates"] == []
    # PACK-2: клиент масштаба pognali.rent проходит без единой обрезки групп
    assert pack["audit"]["truncated_candidate_groups"] == []
    assert "candidates_aggregated" not in pack["coverage"]
    assert all(
        "tail_aggregate" not in row[4]
        for row in candidate_projection["rows"]
    )


# ── 2b. PACK-2: последний эшелон byte-cap вместо исключения ────────────────

def _oversized_candidate_rows() -> tuple[list[str], list[list]]:
    """Клиент, заведомо не влезающий в byte-cap: 1276 кандидатов, длинные URL.

    Отличие от _union_schema_candidate_rows — вдвое больше строк, реалистично
    длинные сегменты (URL с ЧПУ) и лишнее поле в каждой строке, из-за чего
    одно только кандидатное ядро уже не влезает в byte-cap. Это НЕ «удвоенный
    pognali»: дублирование строк реального клиента даёт ~46 800 байт и обрезку
    не запускает вовсе — здесь нагрузка создаётся длиной строк, а не только их
    числом. До PACK-2 такой клиент ронял стадию ValueError'ом.
    """
    base_columns = [
        *CANDIDATE_COLUMNS, "candidate_reason", "segment", "row_ref", "context_refs",
    ]
    filler_columns = [f"metric_{index:03d}" for index in range(190)]
    columns = [*base_columns, *filler_columns]

    slug = "arenda-avtomobilya-bez-voditelya-v-moskve-nedorogo"
    rows = []
    for index in range(638):
        c06 = {
            "check_id": "C06", "row_role": "candidate", "candidate": True,
            "status": "ok", "confidence": "HIGH", "significant": True,
            "payload": {
                "rate": 0.45, "gap_visits": 60 + index, "metric": "open_to_submit",
            },
            "candidate_reason": "funnel_gap",
            "segment": f"landing_page=/cars/{slug}/{index}",
            "row_ref": f"c06-{slug}-{index}", "context_refs": [],
        }
        s06 = {
            "check_id": "S06", "row_role": "candidate", "candidate": True,
            "status": "ok", "confidence": "MED", "significant": True,
            "payload": {
                "trend": "down", "demand_index": 0.82, "metric": "seasonality",
                "visits": 10 + index,
            },
            "candidate_reason": "seasonality_conflict",
            "segment": f"query=/{slug}-{index}",
            "row_ref": f"s06-{slug}-{index}", "context_refs": [],
        }
        for decoded in (c06, s06):
            rows.append([decoded.get(column) for column in columns])
    assert len(rows) == 1276
    return columns, rows


def _audit_by_check_id(entries: list[dict]) -> dict[str, dict]:
    """Индекс записей audit.truncated_candidate_groups по check_id.

    Записи двух форм (обычная — `check_id`, объединённая второго эшелона —
    `merged_check_ids`), поэтому ключи читаются через
    `draft_findings.audit_entry_check_ids`, а не из `entry["check_id"]`.
    """
    return {
        check_id: entry
        for entry in entries
        for check_id in draft_findings.audit_entry_check_ids(entry)
    }


def _write_oversized_client(paths: _Paths) -> None:
    columns, rows = _oversized_candidate_rows()
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": columns, "rows": rows, "coverage": {"checks_calculated": 2},
    })


def test_oversized_client_truncates_instead_of_raising(tmp_path):
    """Клиент, заведомо не влезающий в cap, обрезается, а не роняет стадию.

    Фикстура (_oversized_candidate_rows) — 1276 кандидатов с удлинёнными URL в
    сегментах и лишним полем в строке; нагружает byte-cap длиной строк, а не
    только их числом. Удвоенный по строкам pognali таким клиентом не является
    (~46 800 байт, обрезка не срабатывает ни разу).
    """
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    truncations = pack["audit"]["truncated_candidate_groups"]
    assert truncations, "ожидалась хотя бы одна зафиксированная обрезка"
    assert pack["audit"]["final_serialized_bytes"] < pack["audit"]["byte_cap"]
    assert pack["audit"]["byte_cap_exceeded"] is False

    # Обрезка видна модели: и в audit, и в самой группе кандидатов
    top_n = draft_findings.resolve_candidate_top_n(DEFAULTS)
    # записи audit гетерогенны (check_id либо merged_check_ids) — читаются
    # только через аксессор, он возвращает список в обеих формах
    for entry in truncations:
        assert set(draft_findings.audit_entry_check_ids(entry)) <= {"C06", "S06"}
        assert entry["candidates_sent"] == top_n
        assert entry["candidates_aggregated"] > 0
        assert entry["criterion"] in {"payload.gap_visits", "payload.visits"}
    aggregated_total = sum(e["candidates_aggregated"] for e in truncations)
    assert pack["coverage"]["candidates_aggregated"] == aggregated_total

    truncated_ids = {
        check_id for entry in truncations for check_id in draft_findings.audit_entry_check_ids(entry)
    }
    for row in pack["analysis_candidates"]["rows"]:
        if row[0] not in truncated_ids:
            continue
        tail = row[4]["tail_aggregate"]
        assert tail["truncated_candidates"] > 0
        assert len(row[4]["rows"]) == top_n
        assert tail["truncated_sum"] >= tail["truncated_max"] >= tail["truncated_min"]

    # top-N выбраны по влиянию: отброшенные значения не выше оставленных
    c06_rows = [row for row in pack["analysis_candidates"]["rows"] if row[0] == "C06"][0]
    gap_index = c06_rows[4]["columns"].index("payload.gap_visits")
    kept_values = [row[gap_index] for row in c06_rows[4]["rows"]]
    assert min(kept_values) > c06_rows[4]["tail_aggregate"]["truncated_max"]


def test_truncation_is_byte_identical_across_runs(tmp_path):
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)

    first = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    second = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    dump = lambda pack: json.dumps(pack, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    assert dump(first) == dump(second)
    assert first["audit"]["truncated_candidate_groups"] == \
        second["audit"]["truncated_candidate_groups"]


def test_candidate_top_n_and_impact_keys_come_from_defaults(tmp_path):
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)

    pack = draft_findings.build_input_pack(
        paths, CONFIG, METHODOLOGY,
        {**DEFAULTS, "analyze_candidate_top_n": 5,
         "analyze_candidate_impact_keys": ["payload.rate", "payload.gap_visits"]},
    )

    entries = _audit_by_check_id(pack["audit"]["truncated_candidate_groups"])
    assert entries
    assert all(e["candidates_sent"] == 5 for e in entries.values())
    # payload.rate одинаков во всех строках C06 -> уезжает в common и критерием
    # быть не может; берётся следующий ключ списка
    if "C06" in entries:
        assert entries["C06"]["criterion"] == "payload.gap_visits"
    assert draft_findings.resolve_candidate_top_n({}) == draft_findings.CANDIDATE_TOP_N
    assert draft_findings.resolve_candidate_impact_keys(None) == \
        list(draft_findings.CANDIDATE_IMPACT_KEYS)


def test_impossible_cap_is_a_configuration_error(tmp_path):
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)

    try:
        draft_findings.build_input_pack(
            paths, CONFIG, METHODOLOGY,
            {**DEFAULTS, "analyze_input_pack_byte_cap": 1_000},
        )
    except ValueError as error:
        assert "конфигурационная ошибка" in str(error)
    else:  # pragma: no cover - защита от молчаливого прохода
        raise AssertionError("ожидался ValueError о конфигурационной ошибке")


# ── 2c. PACK-2-FIX-1: прогрессивная обрезка (B1) + согласованный coverage (B2)

def _write_many_small_groups(
    paths: _Paths, *, num_groups: int, per_group: int, detail_len: int
) -> int:
    """Много мелких групп кандидатов, ни одна не превышает top_n=25 сама по
    себе (кейс из review: 400 групп по 10 — прежний однопроходный алгоритм
    проходил мимо такого клиента целиком и падал тем же исключением, которое
    PACK-2 должен был устранять). Возвращает исходное общее число кандидатов.
    """
    columns = [*CANDIDATE_COLUMNS, "candidate_reason", "segment", "row_ref", "context_refs"]
    rows = []
    for group_index in range(num_groups):
        reason = f"reason_{group_index:04d}"
        for item_index in range(per_group):
            decoded = {
                "check_id": "C06", "row_role": "candidate", "candidate": True,
                "status": "ok", "confidence": "HIGH", "significant": True,
                "payload": {
                    "gap_visits": item_index + group_index,
                    "detail": ("x" * detail_len) + str(item_index),
                },
                "candidate_reason": reason,
                "segment": f"landing_page=/cars/{group_index}/{item_index}",
                "row_ref": f"c06-{group_index}-{item_index}", "context_refs": [],
            }
            rows.append([decoded.get(column) for column in columns])
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": columns, "rows": rows, "coverage": {"checks_calculated": 1},
    })
    return len(rows)


def test_many_small_groups_below_top_n_collapse_progressively(tmp_path):
    """400 групп по 10 кандидатов: снижение N + межгрупповое объединение.

    Первый эшелон (снижение top-N) эту нагрузку не закрывает: фиксированный
    оверхед схлопнутой группы растёт линейно с ЧИСЛОМ групп. Второй эшелон
    (PACK-2-FIX-1B) сводит полностью схлопнутые группы в один агрегат.
    """
    paths = _Paths(tmp_path)
    total = _write_many_small_groups(paths, num_groups=400, per_group=10, detail_len=100)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert pack["audit"]["final_serialized_bytes"] < pack["audit"]["byte_cap"]
    assert pack["audit"]["byte_cap_exceeded"] is False
    assert pack["audit"]["truncated_candidate_groups"]

    merged_entries = [
        entry for entry in pack["audit"]["truncated_candidate_groups"]
        if "merged_groups" in entry
    ]
    assert len(merged_entries) == 1
    merged_entry = merged_entries[0]
    assert merged_entry["merged_check_ids"] == ["C06"]
    assert merged_entry["candidates_sent"] == 0
    assert merged_entry["merged_groups"] > 1
    # объединение сократило число элементов пакета ровно на слитые группы
    assert len(pack["analysis_candidates"]["rows"]) == 400 - merged_entry["merged_groups"] + 1

    top_n = draft_findings.resolve_candidate_top_n(DEFAULTS)
    merged_rows = 0
    for row in pack["analysis_candidates"]["rows"]:
        assert row[4]["rows"] == []
        tail = row[4]["tail_aggregate"]
        assert tail["truncated_candidates"] == row[2]
        if "merged_check_ids" in tail:
            merged_rows += 1
            assert tail["merged_groups"] == merged_entry["merged_groups"]
            assert row[0] is None
        else:
            # ни одна исходная группа не превышала top_n=25 сама по себе (в ней
            # 10 кандидатов) — обрезка сработала только за счёт снижения N (B1)
            assert row[2] < top_n
    assert merged_rows == 1

    assert pack["coverage"]["candidates_included"] == 0
    assert pack["coverage"]["candidates_omitted"] == total
    assert pack["coverage"]["candidates_aggregated"] == total
    # проверка не исчезла из coverage вместе с объединёнными группами
    assert pack["coverage"]["included_check_ids"] == ["C06"]

    # детерминизм: повторная сборка даёт байт-в-байт тот же пакет
    assert draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS) == pack


def test_coverage_included_plus_omitted_equals_total_for_any_truncation_case(tmp_path):
    # (а) обрезки не было
    paths_a = _Paths(tmp_path / "a")
    _write_candidates(paths_a, [
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}],
        ["D01", "candidate", True, "ok", "HIGH", True, {"repeats": 3}],
    ])
    pack_a = draft_findings.build_input_pack(paths_a, CONFIG, METHODOLOGY, DEFAULTS)
    assert pack_a["audit"]["truncated_candidate_groups"] == []
    assert (
        pack_a["coverage"]["candidates_included"] + pack_a["coverage"]["candidates_omitted"] == 2
    )

    # (б) частичная обрезка групп (клиент, заведомо не влезающий в cap)
    paths_b = _Paths(tmp_path / "b")
    _write_oversized_client(paths_b)
    pack_b = draft_findings.build_input_pack(paths_b, CONFIG, METHODOLOGY, DEFAULTS)
    assert pack_b["audit"]["truncated_candidate_groups"]
    assert any(row[4]["rows"] for row in pack_b["analysis_candidates"]["rows"])
    assert (
        pack_b["coverage"]["candidates_included"] + pack_b["coverage"]["candidates_omitted"]
        == 1276
    )

    # (в) полное схлопывание групп + межгрупповое объединение (второй эшелон)
    paths_c = _Paths(tmp_path / "c")
    total_c = _write_many_small_groups(paths_c, num_groups=400, per_group=10, detail_len=100)
    pack_c = draft_findings.build_input_pack(paths_c, CONFIG, METHODOLOGY, DEFAULTS)
    assert all(row[4]["rows"] == [] for row in pack_c["analysis_candidates"]["rows"])
    assert any(
        "merged_groups" in entry
        for entry in pack_c["audit"]["truncated_candidate_groups"]
    )
    assert (
        pack_c["coverage"]["candidates_included"] + pack_c["coverage"]["candidates_omitted"]
        == total_c
    )


def test_real_ceiling_still_raises_after_both_echelons(tmp_path):
    """5000 групп по 1 кандидату: оба эшелона исчерпаны, cap недостижим.

    Группа из одного кандидата не имеет отличающихся колонок -> segments пуст,
    хвоста нет. Снижение top-N её не режет, а объединять её нельзя (это не
    схлопнутая группа, в ней осталось бы потерять не-null значения). Это и есть
    реальный потолок, и сообщение об ошибке должно описывать именно его.
    """
    paths = _Paths(tmp_path)
    _write_many_small_groups(paths, num_groups=5000, per_group=1, detail_len=100)

    try:
        draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    except ValueError as error:
        message = str(error)
        assert "конфигурационная ошибка" in message
        assert "оба эшелона обрезки исчерпаны" in message
        # формулировка фактически верна: объединять было нечего, а все 5000
        # кандидатов остались поимённо — они обрезке не подлежат
        assert "объединено схлопнутых групп: 0" in message
        assert "5000 групп и 5000 поимённых" in message
        assert "analyze_input_pack_byte_cap" in message
    else:  # pragma: no cover - защита от молчаливого прохода
        raise AssertionError("ожидался ValueError о конфигурационной ошибке")


# ── 2d. PACK-2-FIX-1C: матрица «группы × кандидаты × check_id» ─────────────
# Три раунда подряд фикстура была проще реальности по неучтённой оси, и дефект
# «размер меряется не над тем объектом, который отправляется» каждый раз
# воспроизводился в новой секции. Поэтому проверка варьирует ОБЕ оси
# одновременно, а структурный инвариант меряется на ФИНАЛЬНОМ объекте — после
# всех дозаписей coverage и audit.

MATRIX_CHECK_IDS = [f"D{index:02d}" for index in range(1, 13)] + [
    f"A{index:02d}" for index in range(1, 13)
]
METHODOLOGY_WIDE = {
    "checks": [{"id": check_id, "name": f"Проверка {check_id}"} for check_id in MATRIX_CHECK_IDS]
}


def _write_matrix_client(
    paths: _Paths, *, num_groups: int, per_group: int, num_check_ids: int
) -> int:
    """Клиент на `num_groups` групп по `per_group` кандидатов, `num_check_ids`
    разных check_id, раскиданных по группам по кругу. Возвращает исходное общее
    число кандидатов.
    """
    columns = [*CANDIDATE_COLUMNS, "candidate_reason", "segment", "row_ref", "context_refs"]
    rows = []
    for group_index in range(num_groups):
        check_id = MATRIX_CHECK_IDS[group_index % num_check_ids]
        reason = f"reason_{group_index:04d}"
        for item_index in range(per_group):
            decoded = {
                "check_id": check_id, "row_role": "candidate", "candidate": True,
                "status": "ok", "confidence": "HIGH", "significant": True,
                "payload": {
                    "gap_visits": item_index + group_index,
                    "detail": ("x" * 100) + str(item_index),
                },
                "candidate_reason": reason,
                "segment": f"landing_page=/cars/{group_index}/{item_index}",
                "row_ref": f"{check_id.lower()}-{group_index}-{item_index}", "context_refs": [],
            }
            rows.append([decoded.get(column) for column in columns])
    # фикстура обязана соответствовать докстрингу теста по обеим осям
    assert len({row[0] for row in rows}) == num_check_ids
    assert len(rows) == num_groups * per_group
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": columns, "rows": rows,
        "coverage": {"checks_calculated": num_check_ids},
    })
    return len(rows)


def _assert_matrix_case(
    paths: _Paths, *, num_groups: int, per_group: int, num_check_ids: int
) -> dict:
    """Единая проверка кейса матрицы: структурный инвариант + B2 + детерминизм.

    Структурный инвариант меряется на ФИНАЛЬНОМ объекте (`len(json.dumps(pack))
    <= cap`), а не на промежуточном состоянии внутри алгоритма — это ровно тот
    тест, которого не хватало, чтобы поймать BLOCKER 1 раундом раньше.
    """
    total = _write_matrix_client(
        paths, num_groups=num_groups, per_group=per_group, num_check_ids=num_check_ids
    )
    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY_WIDE, DEFAULTS)

    system_prompt = draft_findings.build_system_prompt(DEFAULTS)
    actual = len(json.dumps(
        {**pack, "system_prompt": system_prompt}, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))
    cap = pack["audit"]["byte_cap"]
    assert actual <= cap, f"пакет {actual} б вышел за cap {cap} б"
    assert actual == pack["audit"]["final_serialized_bytes"]
    assert pack["audit"]["byte_cap_exceeded"] is False

    # B2: ни один кандидат не потерялся между included и omitted
    coverage = pack["coverage"]
    assert coverage["candidates_included"] + coverage["candidates_omitted"] == total

    # audit гетерогенен (check_id / merged_check_ids) — читается через аксессор
    audit_ids: set[str] = set()
    for entry in pack["audit"]["truncated_candidate_groups"]:
        entry_ids = draft_findings.audit_entry_check_ids(entry)
        assert entry_ids
        audit_ids.update(entry_ids)
    assert audit_ids <= set(MATRIX_CHECK_IDS[:num_check_ids])
    assert set(coverage["included_check_ids"]) == set(MATRIX_CHECK_IDS[:num_check_ids])

    # детерминизм: повторный прогон даёт побайтово равный пакет
    repeat = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY_WIDE, DEFAULTS)
    dump = lambda value: json.dumps(  # noqa: E731
        value, ensure_ascii=False, separators=(",", ":")
    )
    assert dump(repeat) == dump(pack)
    return pack


def test_matrix_400_groups_10_candidates_1_check_id(tmp_path):
    """400 групп × 10 кандидатов × 1 check_id — кейс прошлого раунда."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=400, per_group=10, num_check_ids=1)


def test_matrix_400_groups_10_candidates_4_check_ids(tmp_path):
    """400 групп × 10 кандидатов × 4 check_id — главный кейс FIX-1C.

    С одним check_id тот же кейс проходил с запасом ~123 байта, с четырьмя —
    падал: `_refresh_coverage` дописывал в уже «уложившийся» пакет более
    длинный `included_check_ids`.
    """
    _assert_matrix_case(_Paths(tmp_path), num_groups=400, per_group=10, num_check_ids=4)


def test_matrix_400_groups_10_candidates_24_check_ids(tmp_path):
    """400 групп × 10 кандидатов × 24 check_id — реалистично для pognali.rent."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=400, per_group=10, num_check_ids=24)


def test_matrix_1500_groups_3_candidates_4_check_ids(tmp_path):
    """1500 групп × 3 кандидата × 4 check_id — падал в ревью."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=1500, per_group=3, num_check_ids=4)


def test_matrix_3000_groups_2_candidates_4_check_ids(tmp_path):
    """3000 групп × 2 кандидата × 4 check_id — падал в ревью."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=3000, per_group=2, num_check_ids=4)


def test_matrix_1000_groups_5_candidates_24_check_ids(tmp_path):
    """1000 групп × 5 кандидатов × 24 check_id."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=1000, per_group=5, num_check_ids=24)


def test_matrix_600_groups_10_candidates_4_check_ids(tmp_path):
    """600 групп × 10 кандидатов × 4 check_id."""
    _assert_matrix_case(_Paths(tmp_path), num_groups=600, per_group=10, num_check_ids=4)


def test_matrix_ceiling_5000_groups_1_candidate_24_check_ids_raises(tmp_path):
    """5000 групп × 1 кандидат × 24 check_id — реальный потолок, ValueError.

    Группа из одного кандидата не имеет отличающихся колонок -> segments пуст,
    схлопывать и объединять нечего. Числа в сообщении читаются из фактического
    состояния пакета на момент броска.
    """
    paths = _Paths(tmp_path)
    _write_matrix_client(paths, num_groups=5000, per_group=1, num_check_ids=24)

    try:
        draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY_WIDE, DEFAULTS)
    except ValueError as error:
        message = str(error)
        assert "конфигурационная ошибка" in message
        # объединять было нечего, и сообщение это и говорит — оно фактически
        # верно, а не «исчерпано» при неполном объединении
        assert "объединено схлопнутых групп: 0" in message
        assert "5000 групп и 5000 поимённых" in message
    else:  # pragma: no cover - защита от молчаливого прохода
        raise AssertionError("ожидался ValueError о конфигурационной ошибке")


def test_maximal_merge_is_forced_before_raising(tmp_path):
    """Схлопнутые группы объединяются максимально до броска ValueError.

    1500 групп × 3 кандидата × 4 check_id при заведомо недостижимом cap: все
    1500 групп схлопываемы, значит на момент отказа объединены обязаны быть
    все 1500, а не часть — иначе «оба эшелона исчерпаны» противоречит
    собственным числам сообщения.
    """
    paths = _Paths(tmp_path)
    _write_matrix_client(paths, num_groups=1500, per_group=3, num_check_ids=4)

    try:
        draft_findings.build_input_pack(
            paths, CONFIG, METHODOLOGY_WIDE,
            {**DEFAULTS, "analyze_input_pack_byte_cap": 2_000},
        )
    except ValueError as error:
        message = str(error)
        assert "объединено схлопнутых групп: 1500" in message
        assert "в пакете осталось 1 групп" in message
    else:  # pragma: no cover - защита от молчаливого прохода
        raise AssertionError("ожидался ValueError о конфигурационной ошибке")


def test_explicit_warn_bytes_zero_is_not_replaced_by_default(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}],
    ])
    defaults = {**DEFAULTS, "analyze_input_pack_warn_bytes": 0}

    assert draft_findings.resolve_warn_bytes(defaults) == 0
    assert draft_findings.resolve_byte_cap({**DEFAULTS, "analyze_input_pack_byte_cap": 0}) == 0

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, defaults)
    assert pack["audit"]["warn_bytes"] == 0


def test_explicit_candidate_top_n_zero_is_not_replaced_by_default(tmp_path):
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)
    defaults = {**DEFAULTS, "analyze_candidate_top_n": 0}

    assert draft_findings.resolve_candidate_top_n(defaults) == 0

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, defaults)

    truncated = _audit_by_check_id(pack["audit"]["truncated_candidate_groups"])
    assert truncated
    for row in pack["analysis_candidates"]["rows"]:
        if row[0] in truncated:
            assert row[4]["rows"] == []
            assert truncated[row[0]]["candidates_sent"] == 0


def test_explicit_empty_impact_keys_fall_back_to_row_order(tmp_path):
    """Явный пустой список полей влияния — отказ от ранжирования, не «ключа нет».

    `value or default` не отличил бы `[]` от отсутствия настройки; при явном
    пустом списке критерием обязан стать детерминированный row_order.
    """
    paths = _Paths(tmp_path)
    _write_oversized_client(paths)

    assert draft_findings.resolve_candidate_impact_keys(
        {**DEFAULTS, "analyze_candidate_impact_keys": []}
    ) == []
    assert draft_findings.resolve_candidate_impact_keys({}) == \
        list(draft_findings.CANDIDATE_IMPACT_KEYS)

    pack = draft_findings.build_input_pack(
        paths, CONFIG, METHODOLOGY, {**DEFAULTS, "analyze_candidate_impact_keys": []},
    )
    entries = pack["audit"]["truncated_candidate_groups"]
    assert entries
    assert all(entry["criterion"] == "row_order" for entry in entries)


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
    assert "tail_aggregate" in prompt
    for category in schemas.MONEY_CATEGORIES:
        assert category in prompt


def test_system_prompt_covers_fully_merged_check_ids(tmp_path):
    """Правило №9 покрывает check_id, оставшиеся только в merged_check_ids.

    После второго эшелона такая проверка не имеет в пакете ни одной строки, но
    остаётся в known_check_ids и check_names (они считаются до byte-cap).
    Модель обязана получить и правило, и данные, чтобы его применить: правило
    в system_prompt, merged_check_ids — в самом пакете.
    """
    paths = _Paths(tmp_path)
    _write_many_small_groups(paths, num_groups=400, per_group=10, detail_len=100)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    prompt = draft_findings.build_system_prompt(DEFAULTS)

    merged_ids = [
        check_id
        for row in pack["analysis_candidates"]["rows"]
        for check_id in ((row[4].get("tail_aggregate") or {}).get("merged_check_ids") or [])
    ]
    assert merged_ids, "ожидалось сработавшее объединение групп второго эшелона"
    assert "merged_check_ids" in prompt
    assert "поимённым evidence" in prompt or "поимённое evidence" in prompt
    # проверки объединённого элемента по-прежнему видны модели в реестрах
    assert set(merged_ids) <= set(pack["known_check_ids"])


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


# ── 7. PACK-0: byte-cap в конфиге + корпус валидации из full_pack ──────────
# Сжатие/урезание отправляемого пакета не имеет права ужесточать проверку
# assumptions: числа сверяются с полным собранным пакетом (full_pack), а в
# модель и в аудиторский артефакт уходит send_pack.

def test_byte_cap_and_warn_bytes_come_from_defaults_with_constant_fallback(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}]])

    from_defaults = draft_findings.build_input_pack(
        paths, CONFIG, METHODOLOGY,
        {**DEFAULTS, "analyze_input_pack_byte_cap": 77_000,
         "analyze_input_pack_warn_bytes": 55_000},
    )
    assert from_defaults["audit"]["byte_cap"] == 77_000
    assert from_defaults["audit"]["warn_bytes"] == 55_000

    # DEFAULTS этих ключей не несёт -> константы модуля
    fallback = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)
    assert fallback["audit"]["byte_cap"] == draft_findings.INPUT_PACK_BYTE_CAP
    assert fallback["audit"]["warn_bytes"] == draft_findings.INPUT_PACK_WARN_BYTES
    assert draft_findings.resolve_byte_cap({}) == draft_findings.INPUT_PACK_BYTE_CAP
    assert draft_findings.resolve_warn_bytes(None) == draft_findings.INPUT_PACK_WARN_BYTES


def test_project_defaults_yaml_carries_pack_size_keys():
    from src.pipeline import orchestrator

    defaults = orchestrator.load_defaults()
    assert defaults["analyze_input_pack_byte_cap"] == 150_000
    assert defaults["analyze_input_pack_warn_bytes"] == 120_000


def test_project_defaults_yaml_carries_candidate_truncation_keys():
    """Параметры обоих эшелонов обрезки настраиваются конфигом, а не кодом (B3).

    load_defaults() отдаёт их наравне с остальными ключами — отдельного
    проброса в draft()/build_input_pack не требуется.
    """
    from src.pipeline import orchestrator

    defaults = orchestrator.load_defaults()
    assert defaults["analyze_candidate_top_n"] == 25
    assert defaults["analyze_candidate_impact_keys"] == list(
        draft_findings.CANDIDATE_IMPACT_KEYS
    )
    assert draft_findings.resolve_candidate_top_n(defaults) == 25
    assert draft_findings.resolve_candidate_impact_keys(defaults) == list(
        draft_findings.CANDIDATE_IMPACT_KEYS
    )


def _write_oversized_optional_input(paths: _Paths, marker_number: int) -> None:
    """inputs/optional_context.yaml, который заведомо не влезает в byte-cap."""
    paths.inputs.mkdir(parents=True, exist_ok=True)
    (paths.inputs / "optional_context.yaml").write_text(
        f"contacts_total: {marker_number}\ncomment: '" + "я" * 120_000 + "'\n",
        encoding="utf-8",
    )


def test_full_pack_keeps_sections_cut_from_send_pack(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}]])
    _write_oversized_optional_input(paths, 123456)

    send_pack, full_pack = draft_findings.build_input_pack(
        paths, CONFIG, METHODOLOGY, DEFAULTS, return_full=True
    )

    assert "inputs.optional_context" in send_pack["audit"]["omitted_context"]
    assert "optional_context" not in send_pack["inputs"]
    assert full_pack["inputs"]["optional_context"]["contacts_total"] == 123456
    corpus = draft_findings.build_validation_corpus(full_pack)
    assert corpus["optional_context"]["contacts_total"] == 123456


class _CapturingResponses:
    def __init__(self, findings_payload, capture):
        self._payload = findings_payload
        self.capture = capture

    def create(self, **kwargs):
        self.capture.append(kwargs)
        return types.SimpleNamespace(
            output_text=json.dumps({"findings": self._payload}, ensure_ascii=False)
        )


class _CapturingClient:
    def __init__(self, findings_payload, capture):
        self.responses = _CapturingResponses(findings_payload, capture)


def _finding_payload(**overrides) -> dict:
    base = dict(
        check_id="A04",
        name="Кампания расходует деньги и не даёт ни одной чистой конверсии",
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


def _prepare_a04_client(paths: _Paths) -> None:
    _write_degradation(paths, [{"check_id": "A04", "runnable": True, "confidence_cap": "MED"}])
    _write_json(paths.metrics, "a04", [{
        "check_id": "A04",
        "cost_normalized_rub": 10000.0,
        "net_conversions": 0,
        "period_months": 6,
        "confidence": "MED",
    }])


def test_assumption_confirmed_only_by_dropped_section_is_not_rejected(tmp_path):
    paths = _Paths(tmp_path)
    _prepare_a04_client(paths)
    _write_oversized_optional_input(paths, 123456)
    payload = [_finding_payload(assumptions=["Всего обращений за период — 123456"])]
    capture: list[dict] = []

    names = draft_findings.draft(
        paths, CONFIG, METHODOLOGY, client=_CapturingClient(payload, capture)
    )

    sent_pack = json.loads(capture[0]["input"][0]["content"].partition("\n\n")[2])
    # число подтверждается только секцией, вырезанной byte-cap'ом из send_pack
    assert "optional_context" not in sent_pack["inputs"]
    assert "123456" not in json.dumps(sent_pack, ensure_ascii=False)
    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME, "F-A-01.yaml"]
    assert not (paths.findings_draft / draft_findings.REJECTED_DIRNAME).exists()


def test_hallucinated_assumption_still_rejected_after_split(tmp_path):
    paths = _Paths(tmp_path)
    _prepare_a04_client(paths)
    payload = [_finding_payload(assumptions=["Средний чек взят как 987654 ₽"])]

    names = draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_MockClient(payload))

    assert names == [draft_findings.INPUT_PACK_ARTIFACT_NAME]
    rejected = sorted((paths.findings_draft / draft_findings.REJECTED_DIRNAME).glob("*.yaml"))
    assert len(rejected) == 1


def test_audit_artifact_equals_sent_body(tmp_path):
    paths = _Paths(tmp_path)
    _prepare_a04_client(paths)
    _write_oversized_optional_input(paths, 123456)
    capture: list[dict] = []

    draft_findings.draft(paths, CONFIG, METHODOLOGY, client=_CapturingClient([], capture))

    artifact = json.loads(
        (paths.findings_draft / draft_findings.INPUT_PACK_ARTIFACT_NAME).read_text(
            encoding="utf-8"
        )
    )
    sent_pack = json.loads(capture[0]["input"][0]["content"].partition("\n\n")[2])
    assert artifact["system_prompt"] == capture[0]["instructions"]
    assert {k: v for k, v in artifact.items() if k != "system_prompt"} == sent_pack


def test_pack_over_warn_bytes_logs_warning_without_raising(tmp_path):
    paths = _Paths(tmp_path)
    _prepare_a04_client(paths)
    messages: list[str] = []

    draft_findings.draft(
        paths, CONFIG, METHODOLOGY, client=_MockClient([]),
        log=lambda message="": messages.append(message),
    )
    assert not any("WARNING" in message for message in messages)

    # тот же прогон, но с порогом ниже фактического размера пакета
    import src.pipeline.orchestrator as orchestrator

    real_defaults = orchestrator.load_defaults()
    original = draft_findings.orchestrator_mod.load_defaults
    draft_findings.orchestrator_mod.load_defaults = lambda: {
        **real_defaults, "analyze_input_pack_warn_bytes": 10,
    }
    try:
        draft_findings.draft(
            paths, CONFIG, METHODOLOGY, client=_MockClient([]),
            log=lambda message="": messages.append(message),
        )
    finally:
        draft_findings.orchestrator_mod.load_defaults = original

    assert any("WARNING" in message for message in messages)


def test_explicit_warn_bytes_zero_warns_on_any_pack_size(tmp_path):
    """Интеграционный тест четвёртого falsy-экземпляра (PACK-2-FIX-2).

    Тест на выход резолвера этот баг по построению не ловит: явный 0 честно
    доезжал до pack["audit"]["warn_bytes"], а подменялся на константу уже
    ВТОРОЙ точкой потребления — `... or resolve_warn_bytes(defaults)` в
    draft(). Поэтому проверяется факт записи WARNING в лог стадии на заведомо
    МАЛЕНЬКОМ пакете: при пороге 0 предупреждение обязано быть при любом
    размере.
    """
    paths = _Paths(tmp_path)
    _prepare_a04_client(paths)
    messages: list[str] = []

    import src.pipeline.orchestrator as orchestrator

    real_defaults = orchestrator.load_defaults()
    original = draft_findings.orchestrator_mod.load_defaults
    draft_findings.orchestrator_mod.load_defaults = lambda: {
        **real_defaults, "analyze_input_pack_warn_bytes": 0,
    }
    try:
        draft_findings.draft(
            paths, CONFIG, METHODOLOGY, client=_MockClient([]),
            log=lambda message="": messages.append(message),
        )
    finally:
        draft_findings.orchestrator_mod.load_defaults = original

    artifact = (paths.findings_draft / draft_findings.INPUT_PACK_ARTIFACT_NAME)
    pack_size = len(artifact.read_text(encoding="utf-8").encode("utf-8"))
    assert pack_size < draft_findings.INPUT_PACK_WARN_BYTES, "пакет должен быть мал"
    warnings = [message for message in messages if "WARNING" in message]
    assert warnings, "при warn_bytes=0 WARNING обязан быть при любом размере пакета"
    assert "пороге предупреждения 0 байт" in warnings[0]


# ── 8. PACK-1: сжатие пакета без потери значимых значений ──────────────────
# Пакет уменьшается только за счёт того, что не несёт данных: null-полей
# union-схемы, задвоенного coverage, телеметрии сканирования, реестров по
# незадействованным проверкам и стоп-слов стадии extract.

_WIDE_COLUMNS = [
    *CANDIDATE_COLUMNS, "candidate_reason", "segment", "row_ref", "context_refs",
    *(f"metric_{index:03d}" for index in range(190)),
]


def _wide_row(**values) -> list:
    """Строка union-схемы: заданные поля заполнены, остальные — null."""
    return [values.get(column) for column in _WIDE_COLUMNS]


def _write_wide_candidates(paths: _Paths, coverage: dict | None = None) -> None:
    """Кандидат + строка контекста по нему в широкой union-схеме."""
    rows = [
        _wide_row(
            check_id="C06", row_role="candidate", candidate=True, status="ok",
            confidence="HIGH", significant=True, candidate_reason="funnel_gap",
            segment="landing_page=/cars/1", row_ref="c06-1",
            context_refs=["ctx-1"], metric_000=0.45, metric_001="", metric_002=0,
            metric_003=False,
        ),
        _wide_row(
            check_id="C06", row_role="context", candidate=False, status="ok",
            row_ref="ctx-1", metric_010=1234, metric_011="форма брони",
        ),
    ]
    _write_json(paths.metrics, "analysis_candidates", {
        "columns": _WIDE_COLUMNS,
        "rows": rows,
        "coverage": coverage or {
            "rows_total": 2,
            "contract_coverage": 1.0,
            "artifacts": [{"artifact": f"a{i}", "state": "complete"} for i in range(200)],
        },
    })


def _count_key(value, name: str) -> int:
    if isinstance(value, dict):
        return (1 if name in value else 0) + sum(
            _count_key(item, name) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_count_key(item, name) for item in value)
    return 0


def test_pack_carries_no_null_values_in_candidates_and_context(tmp_path):
    paths = _Paths(tmp_path)
    _write_wide_candidates(paths)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    candidates = pack["analysis_candidates"]
    common = candidates["rows"][0][3]
    assert not any(value is None for value in common.values())
    # пустая строка, 0 и false — значимые значения, они остаются
    assert common["metric_001"] == "" and common["metric_002"] == 0
    assert common["metric_003"] is False
    assert common["metric_000"] == 0.45
    assert "metric_050" not in common

    context_rows = candidates["context"]["rows"]
    assert context_rows and all(isinstance(row, dict) for row in context_rows)
    assert "columns" not in candidates["context"]
    for row in context_rows:
        assert not any(value is None for value in row.values())
    assert context_rows[0]["metric_010"] == 1234
    assert context_rows[0]["metric_011"] == "форма брони"
    assert "metric_050" not in context_rows[0]


def test_coverage_appears_once_and_carries_no_scan_telemetry(tmp_path):
    paths = _Paths(tmp_path)
    _write_wide_candidates(paths)

    pack = draft_findings.build_input_pack(paths, CONFIG, METHODOLOGY, DEFAULTS)

    assert _count_key(pack, "coverage") == 1
    assert "coverage" not in pack["analysis_candidates"]
    assert "artifacts" not in pack["coverage"]
    assert "artifacts" not in json.dumps(pack, ensure_ascii=False)
    # содержательные поля источника при этом сохранены
    assert pack["coverage"]["contract_coverage"] == 1.0
    assert pack["coverage"]["included_check_ids"] == ["C06"]


def _registry_methodology() -> dict:
    """Реестр из 100 проверок с реальными размерами блоков (D12/A26/T10/C25/S27)."""
    blocks = {"D": 12, "A": 26, "T": 10, "C": 25, "S": 27}
    checks = [
        {"id": f"{letter}{number:02d}", "name": f"Проверка {letter}{number:02d}"}
        for letter, count in blocks.items()
        for number in range(1, count + 1)
    ]
    assert len(checks) == 100
    return {"checks": checks}


def test_registries_are_narrowed_to_used_check_ids_only(tmp_path):
    paths = _Paths(tmp_path)
    methodology = _registry_methodology()
    _write_degradation(paths, [
        {"check_id": c["id"], "runnable": c["id"] not in {"S27", "T10"},
         "confidence_cap": "MED", "type_effective": "A"}
        for c in methodology["checks"]
    ])
    _write_candidates(paths, [
        ["C06", "candidate", True, "ok", "HIGH", True, {"rate": 0.45}],
        ["D01", "candidate", True, "ok", "HIGH", True, {"repeats": 3}],
    ])

    pack = draft_findings.build_input_pack(paths, CONFIG, methodology, DEFAULTS)

    used = {"C06", "D01"}
    assert set(pack["check_names"]) == used
    assert set(pack["known_check_ids"]) <= used
    assert set(pack["known_check_ids"]) == used
    assert pack["known_check_ids"] == sorted(pack["known_check_ids"])
    assert set(pack["constraints"]["source_cap_by_check"]) == used
    degradation_ids = {row[0] for row in pack["degradation"]["rows"]}
    assert degradation_ids == used

    # компенсация сужения: охват реестра виден скалярами, а не умолчанием
    assert pack["client_context"]["checks_total"] == 100
    assert pack["client_context"]["checks_not_runnable"] == 2

    # сужение детерминировано
    assert draft_findings.build_input_pack(paths, CONFIG, methodology, DEFAULTS) == pack


def test_extract_stage_stopwords_are_not_sent_but_stay_in_validation_corpus(tmp_path):
    paths = _Paths(tmp_path)
    _write_candidates(paths, [["C06", "candidate", True, "ok", "HIGH", True, {"r": 1}]])
    paths.inputs.mkdir(parents=True)
    (paths.inputs / "wordstat_stopwords.yaml").write_text(
        "stopwords:\n" + "".join(f"  - слово{i}\n" for i in range(300)), encoding="utf-8"
    )
    (paths.inputs / "client_answers.yaml").write_text(
        "business:\n  avg_check_rub: 5000\n", encoding="utf-8"
    )

    send_pack, full_pack = draft_findings.build_input_pack(
        paths, CONFIG, METHODOLOGY, DEFAULTS, return_full=True
    )

    assert "wordstat_stopwords" not in send_pack["inputs"]
    assert "слово299" not in json.dumps(send_pack, ensure_ascii=False)
    assert send_pack["inputs"]["client_answers"]["business"]["avg_check_rub"] == 5000
    # корпус сверки assumptions (PACK-0) остаётся полным
    corpus = draft_findings.build_validation_corpus(full_pack)
    assert "wordstat_stopwords" in corpus
    assert "слово299" in json.dumps(corpus, ensure_ascii=False)
