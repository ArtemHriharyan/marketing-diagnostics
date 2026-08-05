"""ROBUST-1: отсутствующая зависимость деградирует, ошибка кода — падает.

Сценарии:
1. Блок, импортирующий несуществующий модуль, уходит в деградацию
   ("missing_dependency"), соседние блоки считаются, dispatch_blocks не
   бросает исключение.
2. Блок, бросающий на импорте НЕ ImportError, роняет прогон — регресс на
   «проглатывание» любых исключений веткой деградации запрещён.
3. Отсутствующая зависимость попадает в degradation_report и в раздел
   «Что не удалось проверить» (register_missing_dependencies).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from src.compute import common  # noqa: E402
from src.pipeline import degradation as degradation_mod  # noqa: E402


class _Paths:
    """Минимальная замена ClientPaths (см. tests/test_compute_common.py)."""

    def __init__(self, root: Path):
        self.root = root
        self.canonical = root / "data" / "canonical"
        self.inputs = root / "inputs"
        self.metrics = root / "data" / "metrics"


_EMPTY_REPORT = {"runnable_check_ids": [], "skipped": [], "counts": {}}


def _make_block_package(tmp_path: Path, package: str, modules: dict[str, str]) -> None:
    """Создать на диске пакет с блоками и положить его на sys.path."""
    pkg_dir = tmp_path / package
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    for name, source in modules.items():
        (pkg_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    if str(tmp_path) not in sys.path:
        sys.path.insert(0, str(tmp_path))


_OK_BLOCK = """
    def run(paths, defaults, runnable_ids):
        return ["a01"]
    """


def test_missing_dependency_block_degrades_and_others_run(tmp_path):
    """Блок с несуществующим импортом -> деградация, остальные считаются."""
    package = "robust1_pkg_missing"
    _make_block_package(
        tmp_path,
        package,
        {
            "block_needs_dep": """
                import totally_absent_third_party_dep  # noqa: F401

                def run(paths, defaults, runnable_ids):
                    return ["never"]
                """,
            "block_ok": _OK_BLOCK,
        },
    )

    result = common.dispatch_blocks(
        _Paths(tmp_path),
        {},
        _EMPTY_REPORT,
        block_names=["block_needs_dep", "block_ok"],
        block_package=package,
    )

    assert result["block_status"]["block_needs_dep"] == "missing_dependency"
    assert result["block_status"]["block_ok"] == "ok"
    assert result["artifacts"] == ["a01"]
    assert result["missing_dependencies"] == {
        "block_needs_dep": "totally_absent_third_party_dep"
    }
    assert (
        result["block_errors"]["block_needs_dep"]
        == "отсутствует зависимость: totally_absent_third_party_dep"
    )


def test_missing_block_module_itself_degrades(tmp_path):
    """Отсутствующий модуль блока — тот же ImportError, тот же путь деградации."""
    package = "robust1_pkg_absent_module"
    _make_block_package(tmp_path, package, {"block_ok": _OK_BLOCK})

    result = common.dispatch_blocks(
        _Paths(tmp_path),
        {},
        _EMPTY_REPORT,
        block_names=["block_ok", "block_that_does_not_exist"],
        block_package=package,
    )

    assert result["block_status"]["block_ok"] == "ok"
    assert result["block_status"]["block_that_does_not_exist"] == "missing_dependency"


def test_non_import_error_on_module_import_crashes_the_run(tmp_path):
    """Любое НЕ-ImportError на импорте модуля обязано ронять прогон."""
    package = "robust1_pkg_broken"
    _make_block_package(
        tmp_path,
        package,
        {
            "block_broken": """
                raise RuntimeError("ошибка на уровне модуля")

                def run(paths, defaults, runnable_ids):
                    return []
                """,
            "block_ok": _OK_BLOCK,
        },
    )

    with pytest.raises(RuntimeError, match="ошибка на уровне модуля"):
        common.dispatch_blocks(
            _Paths(tmp_path),
            {},
            _EMPTY_REPORT,
            block_names=["block_broken", "block_ok"],
            block_package=package,
        )


def test_runtime_error_inside_run_still_degrades(tmp_path):
    """Регресс: ошибка в run (а не на импорте) по-прежнему не роняет прогон."""
    package = "robust1_pkg_run_error"
    _make_block_package(
        tmp_path,
        package,
        {
            "block_raises": """
                def run(paths, defaults, runnable_ids):
                    raise RuntimeError("boom")
                """,
            "block_ok": _OK_BLOCK,
        },
    )

    result = common.dispatch_blocks(
        _Paths(tmp_path),
        {},
        _EMPTY_REPORT,
        block_names=["block_raises", "block_ok"],
        block_package=package,
    )

    assert result["block_status"]["block_raises"] == "error"
    assert result["block_status"]["block_ok"] == "ok"
    assert "missing_dependencies" not in result


# ── Попадание в degradation_report и в отчёт ────────────────────────────────

def test_register_missing_dependencies_writes_report_and_limitation():
    report = {"skipped": [], "counts": {"total": 3, "runnable": 3, "skipped": 0}}

    degradation_mod.register_missing_dependencies(report, {"block1": "scipy"})

    assert report["missing_dependencies"] == [
        {"block": "block1", "module": "scipy",
         "reason": "отсутствует зависимость: scipy"}
    ]
    # Раздел «Что не удалось проверить» рендерится из skipped (build_report).
    limitation = report["skipped"][0]
    assert limitation["reason"] == "отсутствует зависимость: scipy"
    assert limitation["missing"] == ["scipy"]
    # counts — счётчики реестра проверок, блоки их не меняют.
    assert report["counts"] == {"total": 3, "runnable": 3, "skipped": 0}


def test_register_missing_dependencies_is_idempotent():
    report = {"skipped": []}
    for _ in range(2):
        degradation_mod.register_missing_dependencies(report, {"block1": "scipy"})
    assert len(report["missing_dependencies"]) == 1
    assert len(report["skipped"]) == 1


def test_check_dependencies_reports_present_and_absent():
    rows = degradation_mod.check_dependencies(
        [
            {"module": "json", "package": "stdlib", "affects": ("compute",)},
            {"module": "no_such_pkg_robust1", "package": "no-such",
             "affects": ("analyze",)},
        ]
    )
    assert [row["present"] for row in rows] == [True, False]
    assert degradation_mod.missing_dependencies(rows) == [rows[1]]


def test_build_degradation_report_carries_dependency_state():
    report = degradation_mod.build_degradation_report({"checks": []})
    assert "missing" in report["dependencies"]
    assert isinstance(report["dependencies"]["missing"], list)
