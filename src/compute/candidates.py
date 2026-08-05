"""Детерминированная сборка компактного слоя кандидатов для analyze.

Блоки compute размечают строки полями ``candidate``, ``row_role``,
``candidate_reason`` и ``context_refs``. Сборщик включает только явно
положительные кандидаты, их адресный контекст и все ограничения. Неразмеченные
legacy-артефакты не считаются кандидатами и отражаются в coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import assign_evidence_ids, evidence_label, write_json_atomic


ROW_ROLES: frozenset[str] = frozenset(
    {"candidate", "summary", "baseline", "context", "limitation", "detail"}
)
_CONTEXT_ROLES: frozenset[str] = ROW_ROLES - {"candidate"}
_RESERVED_ARTIFACTS: frozenset[str] = frozenset(
    {"analysis_candidates", "degradation_report", "metrics_summary"}
)
_CONTRACT_FIELDS: frozenset[str] = frozenset(
    {"candidate", "row_role", "candidate_reason", "context_refs", "row_ref"}
)
_FIXED_COLUMNS: tuple[str, ...] = (
    "artifact",
    "evidence_id",
    "evidence_label",
    "row_ref",
    "candidate",
    "row_role",
    "candidate_reason",
    "context_refs",
)


def _extract_rows(payload: Any) -> tuple[list[dict[str, Any]], int, int, str]:
    """Привести поддерживаемые JSON-формы к строкам без эвристик по бизнес-полям."""
    raw_rows: list[Any]
    shape: str

    if isinstance(payload, list):
        raw_rows = payload
        shape = "records"
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        columns = payload.get("columns")
        if isinstance(columns, list) and all(isinstance(c, str) for c in columns):
            raw_rows = []
            invalid = 0
            for values in payload["rows"]:
                if not isinstance(values, list) or len(values) != len(columns):
                    invalid += 1
                    continue
                raw_rows.append(dict(zip(columns, values)))
            return raw_rows, len(payload["rows"]), invalid, "columnar"
        raw_rows = payload["rows"]
        shape = "record_container"
    elif isinstance(payload, dict) and _CONTRACT_FIELDS.intersection(payload):
        raw_rows = [payload]
        shape = "single_record"
    else:
        return [], 0, 0, "unsupported"

    rows = [row for row in raw_rows if isinstance(row, dict)]
    return rows, len(raw_rows), len(raw_rows) - len(rows), shape


def _valid_context_refs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(ref, str) and ref for ref in value)


def _contract_state(row: dict[str, Any]) -> str:
    has_contract = bool(_CONTRACT_FIELDS.intersection(row))
    if not has_contract:
        return "unmarked"

    role = row.get("row_role")
    reason = row.get("candidate_reason")
    refs = row.get("context_refs")
    complete = (
        isinstance(row.get("candidate"), bool)
        and role in ROW_ROLES
        and isinstance(reason, str)
        and bool(reason)
        and _valid_context_refs(refs)
    )
    return "complete" if complete else "partial"


def _normalise_row(
    artifact: str,
    fallback_ref: str,
    row: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Нормализовать служебные поля; вернуть строку и признак их противоречия."""
    candidate = row.get("candidate") is True
    role = row.get("row_role")
    invalid = False

    if "candidate" in row and not isinstance(row["candidate"], bool):
        invalid = True
    if role not in ROW_ROLES:
        if role is not None:
            invalid = True
        role = "candidate" if candidate else None
    elif candidate and role != "candidate":
        invalid = True
        role = "candidate"
    elif not candidate and role == "candidate":
        invalid = True

    raw_ref = row.get("evidence_id") or row.get("row_ref")
    if isinstance(raw_ref, str) and raw_ref:
        row_ref = raw_ref
    else:
        # Легаси-артефакт без разметки: ID всё равно считается от содержания
        # строки (см. common.assign_evidence_ids), а не от её позиции.
        row_ref = fallback_ref
        if raw_ref is not None:
            invalid = True

    raw_context_refs = row.get("context_refs", [])
    if _valid_context_refs(raw_context_refs):
        context_refs = list(raw_context_refs)
    else:
        context_refs = []
        invalid = True

    normalised = dict(row)
    normalised.update(
        {
            "artifact": artifact,
            "evidence_id": row_ref,
            "evidence_label": row.get("evidence_label") or evidence_label(row),
            "row_ref": row_ref,
            "candidate": candidate,
            "row_role": role,
            "candidate_reason": row.get("candidate_reason"),
            "context_refs": context_refs,
        }
    )
    return normalised, invalid


def _fingerprint(row: dict[str, Any]) -> str:
    semantic_row = {
        key: value for key, value in row.items()
        if key not in ("row_ref", "evidence_id", "evidence_label")
    }
    return json.dumps(semantic_row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _to_columnar(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    columns = list(_FIXED_COLUMNS)
    seen = set(columns)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns, [[row.get(column) for column in columns] for row in rows]


def build_analysis_candidates(metrics_dir: Path) -> dict[str, Any]:
    """Собрать ``analysis_candidates`` из JSON-артефактов каталога metrics."""
    metrics_dir = Path(metrics_dir)
    records: list[dict[str, Any]] = []
    artifact_coverage: list[dict[str, Any]] = []
    invalid_artifacts: list[str] = []
    invalid_annotation_rows = 0

    for path in sorted(metrics_dir.glob("*.json")):
        if path.stem in _RESERVED_ARTIFACTS:
            continue
        audit: dict[str, Any] = {
            "artifact": path.stem,
            "shape": None,
            "status": None,
            "rows_total": 0,
            "rows_valid": 0,
            "rows_contract_complete": 0,
            "rows_partial": 0,
            "rows_unmarked": 0,
            "candidate_rows": 0,
            "included_rows": 0,
        }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            audit.update({"shape": "invalid_json", "status": "invalid"})
            invalid_artifacts.append(path.stem)
            artifact_coverage.append(audit)
            continue

        rows, rows_total, invalid_rows, shape = _extract_rows(payload)
        audit["shape"] = shape
        audit["rows_total"] = rows_total
        audit["rows_valid"] = len(rows)
        if invalid_rows:
            audit["rows_invalid"] = invalid_rows

        states = [_contract_state(row) for row in rows]
        audit["rows_contract_complete"] = states.count("complete")
        audit["rows_partial"] = states.count("partial")
        audit["rows_unmarked"] = states.count("unmarked")
        audit["candidate_rows"] = sum(row.get("candidate") is True for row in rows)

        if shape == "unsupported":
            audit["status"] = "legacy"
        elif invalid_rows:
            audit["status"] = "partial"
        elif not rows or audit["rows_contract_complete"] == len(rows):
            audit["status"] = "complete"
        elif audit["rows_unmarked"] == len(rows):
            audit["status"] = "legacy"
        else:
            audit["status"] = "partial"

        fallback_refs = assign_evidence_ids(path.stem, rows)
        for row, fallback_ref in zip(rows, fallback_refs):
            normalised, invalid = _normalise_row(path.stem, fallback_ref, row)
            invalid_annotation_rows += int(invalid)
            records.append(normalised)
        artifact_coverage.append(audit)

    unique_refs: dict[str, dict[str, Any]] = {}
    unique_records: list[dict[str, Any]] = []
    duplicate_row_refs = 0
    for row in records:
        row_ref = row["row_ref"]
        if row_ref in unique_refs:
            duplicate_row_refs += 1
            continue
        unique_refs[row_ref] = row
        unique_records.append(row)

    aliases: dict[str, str] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    deduplicated: list[dict[str, Any]] = []
    duplicate_rows = 0
    for row in unique_records:
        fingerprint = _fingerprint(row)
        if fingerprint in fingerprints:
            aliases[row["row_ref"]] = fingerprints[fingerprint]["row_ref"]
            duplicate_rows += 1
            continue
        fingerprints[fingerprint] = row
        deduplicated.append(row)

    ref_index = {row["row_ref"]: row for row in deduplicated}

    def resolve_ref(ref: str) -> dict[str, Any] | None:
        while ref in aliases:
            ref = aliases[ref]
        return ref_index.get(ref)

    selected_refs = {
        row["row_ref"]
        for row in deduplicated
        if row["candidate"] is True or row["row_role"] == "limitation"
    }
    missing_context_refs: set[str] = set()
    unusable_context_refs: set[str] = set()
    resolved_ref_count = 0
    total_ref_count = 0

    for row in deduplicated:
        if row["candidate"] is not True:
            continue
        resolved_refs: list[str] = []
        for context_ref in row["context_refs"]:
            total_ref_count += 1
            target = resolve_ref(context_ref)
            if target is None:
                missing_context_refs.add(context_ref)
                continue
            if target["row_role"] not in _CONTEXT_ROLES:
                unusable_context_refs.add(context_ref)
                continue
            resolved_ref_count += 1
            resolved_refs.append(target["row_ref"])
            selected_refs.add(target["row_ref"])
        row["context_refs"] = list(dict.fromkeys(resolved_refs))

    selected = [row for row in deduplicated if row["row_ref"] in selected_refs]
    columns, columnar_rows = _to_columnar(selected)

    included_by_artifact: dict[str, int] = {}
    for row in selected:
        included_by_artifact[row["artifact"]] = included_by_artifact.get(row["artifact"], 0) + 1
    for audit in artifact_coverage:
        audit["included_rows"] = included_by_artifact.get(audit["artifact"], 0)

    valid_rows = sum(audit["rows_valid"] for audit in artifact_coverage)
    complete_rows = sum(audit["rows_contract_complete"] for audit in artifact_coverage)
    coverage = {
        "artifacts_scanned": len(artifact_coverage),
        "artifacts_complete": sum(audit["status"] == "complete" for audit in artifact_coverage),
        "artifacts_partial": sum(audit["status"] == "partial" for audit in artifact_coverage),
        "artifacts_legacy": sum(audit["status"] == "legacy" for audit in artifact_coverage),
        "artifacts_invalid": len(invalid_artifacts),
        "rows_total": sum(audit["rows_total"] for audit in artifact_coverage),
        "rows_valid": valid_rows,
        "rows_contract_complete": complete_rows,
        "contract_coverage": round(complete_rows / valid_rows, 4) if valid_rows else 1.0,
        "candidate_rows_declared": sum(row["candidate"] is True for row in records),
        "candidate_rows_included": sum(row["candidate"] is True for row in selected),
        "context_rows_included": sum(
            row["row_role"] in {"summary", "baseline", "context", "detail"}
            for row in selected
        ),
        "limitation_rows_included": sum(row["row_role"] == "limitation" for row in selected),
        "rows_included": len(selected),
        "duplicate_rows_excluded": duplicate_rows,
        "duplicate_row_refs_excluded": duplicate_row_refs,
        "invalid_annotation_rows": invalid_annotation_rows,
        "context_refs_total": total_ref_count,
        "context_refs_resolved": resolved_ref_count,
        "missing_context_refs": sorted(missing_context_refs),
        "unusable_context_refs": sorted(unusable_context_refs),
        "invalid_artifacts": sorted(invalid_artifacts),
        "artifacts": artifact_coverage,
    }
    return {
        "schema_version": 1,
        "columns": columns,
        "rows": columnar_rows,
        "coverage": coverage,
    }


def run(paths: Any, defaults: dict[str, Any], runnable_ids: set[str]) -> list[str]:
    """Записать analysis_candidates.json последним compute-блоком."""
    del defaults, runnable_ids
    metrics_dir = Path(paths.metrics)
    result = build_analysis_candidates(metrics_dir)
    write_json_atomic(metrics_dir / "analysis_candidates.json", result)
    return ["analysis_candidates"]
