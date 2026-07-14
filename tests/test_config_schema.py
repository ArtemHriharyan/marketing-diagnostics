"""Тесты схемы конфигов: проверяют наличие и тип обязательных полей.

Не проверяют бизнес-логику. Только инварианты структуры конфигов,
которые должны сохраняться при любых изменениях defaults.yaml,
_template/config.yaml и _template/inputs/client_answers.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = REPO_ROOT / "config" / "defaults.yaml"
TEMPLATE_CONFIG_PATH = REPO_ROOT / "clients" / "_template" / "config.yaml"
CLIENT_ANSWERS_PATH = REPO_ROOT / "clients" / "_template" / "inputs" / "client_answers.yaml"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── defaults.yaml ─────────────────────────────────────────────────────────────

class TestDefaults:
    def setup_method(self):
        self.cfg = _load(DEFAULTS_PATH)

    def test_manual_source_confidence_cap_is_med(self):
        assert self.cfg.get("manual_source_confidence_cap") == "MED"

    def test_crux_min_field_data_is_true(self):
        assert self.cfg.get("crux_min_field_data") is True

    def test_core_defaults_unchanged(self):
        """Базовые числовые пороги не исчезли (регрессия)."""
        assert "data_window_months" in self.cfg
        assert "utm_undefined_threshold" in self.cfg
        assert "significance_alpha" in self.cfg
        assert "min_sample_visits" in self.cfg


# ── _template/config.yaml ─────────────────────────────────────────────────────

class TestTemplateConfig:
    def setup_method(self):
        self.cfg = _load(TEMPLATE_CONFIG_PATH)

    def test_webmaster_mode_manual(self):
        wb = self.cfg["sources"]["webmaster"]
        assert wb.get("mode") == "manual"

    def test_webmaster_manual_export_dir_present(self):
        wb = self.cfg["sources"]["webmaster"]
        assert "manual_export_dir" in wb
        assert wb["manual_export_dir"]  # не пустая строка

    def test_gsc_mode_manual(self):
        gsc = self.cfg["sources"]["gsc"]
        assert gsc.get("mode") == "manual"

    def test_gsc_manual_export_dir_present(self):
        gsc = self.cfg["sources"]["gsc"]
        assert "manual_export_dir" in gsc
        assert gsc["manual_export_dir"]

    def test_gsc_credentials_path_key_present(self):
        gsc = self.cfg["sources"]["gsc"]
        assert "credentials_path" in gsc  # может быть null — ключ обязан существовать

    def test_crux_enabled(self):
        crux = self.cfg["sources"]["crux"]
        assert crux.get("enabled") is True

    def test_crux_api_key_env(self):
        crux = self.cfg["sources"]["crux"]
        assert crux.get("api_key_env") == "CRUX_API_KEY"


# ── _template/inputs/client_answers.yaml ─────────────────────────────────────

class TestClientAnswers:
    def setup_method(self):
        self.cfg = _load(CLIENT_ANSWERS_PATH)

    def test_finance_vat_basis_by_source_is_list(self):
        assert isinstance(self.cfg["finance"]["vat_basis_by_source"], list)

    def test_product_groups_is_list(self):
        assert isinstance(self.cfg.get("product_groups"), list)

    def test_capacity_limits_is_list(self):
        assert isinstance(self.cfg.get("capacity_limits"), list)

    def test_cancellations_returns_range_key_present(self):
        assert "cancellations_returns_range" in self.cfg

    def test_changes_log_is_list(self):
        assert isinstance(self.cfg.get("changes_log"), list)

    def test_crm_monthly_export_available_key_present(self):
        assert "crm_monthly_export_available" in self.cfg["crm"]
