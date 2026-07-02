"""Tests for the slot-based tenant config path (Claude Desktop .mcpb bundle).

Locks in:
  1. `ST_TENANT_SLOT{n}_*` vars load tenants when `ST_TENANTS` is unset, and
     `ST_TENANTS` takes precedence when both are set.
  2. Empty strings and unsubstituted `${user_config.*}` placeholders (what
     Claude Desktop injects for blank optional settings fields) are treated
     as absent — a blank slot is skipped, gaps between slots are fine.
  3. A named slot missing any credential fails fast with an error naming the
     exact missing env var, and slug validation matches the ST_TENANTS rules.
  4. The pre-existing ST_TENANTS loading path (previously untested).
"""

from __future__ import annotations

import os

import pytest

from servicetitan_mcp import config

_FIELDS = ("NAME", "ID", "CLIENT_ID", "CLIENT_SECRET", "APP_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Scrub ambient ST_* vars (real .env/shell must not leak in) + reset cache."""
    for key in list(os.environ):
        if key.startswith("ST_"):
            monkeypatch.delenv(key, raising=False)
    config._reset_cache_for_tests()
    yield
    config._reset_cache_for_tests()


def _set_slot(monkeypatch, n: int, name: str, **overrides):
    """Fill all five vars for slot `n` with dummies; override/blank via kwargs."""
    for field in _FIELDS:
        default = name if field == "NAME" else f"{name}-{field.lower()}"
        monkeypatch.setenv(f"ST_TENANT_SLOT{n}_{field}", overrides.get(field, default))


def _set_named_tenant(monkeypatch, name: str):
    upper = name.upper()
    for field in _FIELDS[1:]:
        monkeypatch.setenv(f"ST_TENANT_{upper}_{field}", f"{name}-{field.lower()}")


def test_slots_only_loads_tenants(monkeypatch):
    _set_slot(monkeypatch, 1, "acme")
    _set_slot(monkeypatch, 2, "other")
    tenants = config.load_tenants()
    assert set(tenants) == {"acme", "other"}
    acme = config.get_tenant("acme")
    assert acme.tenant_id == "acme-id"
    assert acme.client_id == "acme-client_id"
    assert acme.client_secret == "acme-client_secret"
    assert acme.app_key == "acme-app_key"


def test_st_tenants_takes_precedence_over_slots(monkeypatch):
    monkeypatch.setenv("ST_TENANTS", "roster")
    _set_named_tenant(monkeypatch, "roster")
    _set_slot(monkeypatch, 1, "slotted")
    assert set(config.load_tenants()) == {"roster"}


def test_empty_string_fields_treated_as_absent(monkeypatch):
    _set_slot(monkeypatch, 1, "acme")
    for field in _FIELDS:
        monkeypatch.setenv(f"ST_TENANT_SLOT2_{field}", "")
    assert set(config.load_tenants()) == {"acme"}


def test_unsubstituted_placeholder_treated_as_absent(monkeypatch):
    """Some hosts leave the literal '${user_config.x}' for unset fields."""
    _set_slot(monkeypatch, 1, "acme")
    for field in _FIELDS:
        monkeypatch.setenv(
            f"ST_TENANT_SLOT2_{field}", "${user_config.tenant2_" + field.lower() + "}"
        )
    assert set(config.load_tenants()) == {"acme"}


def test_gap_in_slots_allowed(monkeypatch):
    _set_slot(monkeypatch, 1, "acme")
    _set_slot(monkeypatch, 3, "third")
    assert set(config.load_tenants()) == {"acme", "third"}


def test_name_without_creds_raises(monkeypatch):
    _set_slot(monkeypatch, 1, "acme")
    monkeypatch.setenv("ST_TENANT_SLOT2_NAME", "other")
    with pytest.raises(RuntimeError, match="Tenant 2 .* ST_TENANT_SLOT2_ID"):
        config.load_tenants()


def test_partial_creds_names_exact_missing_var(monkeypatch):
    _set_slot(monkeypatch, 2, "other", APP_KEY="")
    with pytest.raises(RuntimeError, match="ST_TENANT_SLOT2_APP_KEY"):
        config.load_tenants()


def test_invalid_slug_raises(monkeypatch):
    _set_slot(monkeypatch, 1, "9bad!")
    with pytest.raises(RuntimeError, match=r"\[a-z0-9_-\]"):
        config.load_tenants()


def test_uppercase_name_normalizes(monkeypatch):
    _set_slot(monkeypatch, 1, "Acme")
    assert set(config.load_tenants()) == {"acme"}
    assert config.get_tenant("acme").name == "acme"


def test_spaces_in_name_normalize_to_underscores(monkeypatch):
    _set_slot(monkeypatch, 1, "St Louis")
    assert set(config.load_tenants()) == {"st_louis"}
    assert config.get_tenant("st_louis").name == "st_louis"


def test_duplicate_slot_names_raise(monkeypatch):
    _set_slot(monkeypatch, 1, "acme")
    _set_slot(monkeypatch, 2, "acme")
    with pytest.raises(RuntimeError, match="Duplicate tenant name 'acme'"):
        config.load_tenants()


def test_no_config_error_mentions_both_paths():
    with pytest.raises(RuntimeError, match="ST_TENANTS") as exc:
        config.load_tenants()
    assert "ST_TENANT_SLOT1" in str(exc.value)


def test_st_tenants_path_still_loads(monkeypatch):
    """Regression guard for the original roster path."""
    monkeypatch.setenv("ST_TENANTS", "acme,other")
    _set_named_tenant(monkeypatch, "acme")
    _set_named_tenant(monkeypatch, "other")
    tenants = config.load_tenants()
    assert list(tenants) == ["acme", "other"]
    assert tenants["other"].app_key == "other-app_key"


def test_legacy_vars_still_error_without_slots(monkeypatch):
    monkeypatch.setenv("ST_APP_KEY", "legacy")
    with pytest.raises(RuntimeError, match="legacy single-tenant vars"):
        config.load_tenants()
