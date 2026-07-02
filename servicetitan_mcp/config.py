"""Multi-tenant ServiceTitan credential registry.

Two ways to configure tenants, checked in this order:

1. `ST_TENANTS` (comma-separated names) plus per-tenant namespaced env vars
   `ST_TENANT_<UPPERCASE_NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY`.
   Names normalize to lowercase for lookups and uppercase for env-key matching.
2. Numbered slots `ST_TENANT_SLOT1_NAME / _ID / _CLIENT_ID / _CLIENT_SECRET /
   _APP_KEY` (up to `_SLOT_COUNT`). Used by the Claude Desktop `.mcpb` bundle,
   whose settings form can only map fixed field names to env vars. A slot with
   an empty name is skipped; gaps between slots are fine.
"""

import os
import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

_PER_TENANT_VARS = (
    ("tenant_id", "ID"),
    ("client_id", "CLIENT_ID"),
    ("client_secret", "CLIENT_SECRET"),
    ("app_key", "APP_KEY"),
)

_LEGACY_VARS = ("ST_TENANT_ID", "ST_APP_KEY", "ST_CLIENT_ID", "ST_CLIENT_SECRET")

# Number of numbered tenant slots exposed by the .mcpb bundle settings form.
_SLOT_COUNT = 5


@dataclass(frozen=True)
class TenantCredentials:
    name: str
    tenant_id: str
    client_id: str
    client_secret: str
    app_key: str


class UnknownTenantError(ValueError):
    """Raised when a requested tenant name is not in the registry."""

    def __init__(self, name: str, valid: list[str]):
        self.requested = name
        self.valid = valid
        super().__init__(
            f"Unknown tenant {name!r}. Configured: {', '.join(valid) or '(none)'}. "
            "Call list_tenants for the authoritative list."
        )


_cache: dict[str, TenantCredentials] | None = None


def _parse_roster(raw: str) -> list[str]:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        raise RuntimeError("ST_TENANTS is set but empty after parsing.")
    for n in names:
        if not _NAME_RE.match(n):
            raise RuntimeError(
                f"Invalid tenant name {n!r} in ST_TENANTS. "
                "Names must be lowercase, start with a letter, "
                "and use only [a-z0-9_-]."
            )
    seen: set[str] = set()
    deduped: list[str] = []
    for n in names:
        if n in seen:
            raise RuntimeError(f"Duplicate tenant name {n!r} in ST_TENANTS.")
        seen.add(n)
        deduped.append(n)
    return deduped


def _env(key: str) -> str | None:
    """Env value, treating empty/whitespace and unsubstituted MCPB placeholders
    as absent (Claude Desktop substitutes '' for blank optional fields, and some
    hosts leave the literal '${user_config.x}' when a field has no value)."""
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("${user_config."):
        return None
    return val


def _load_slots() -> dict[str, TenantCredentials] | None:
    """Load tenants from ST_TENANT_SLOT1..N_* vars (Claude Desktop .mcpb path).

    Returns None if no slot has a name. A slot with an empty name is skipped,
    so gaps between filled slots are fine. A named slot missing any credential
    fails fast.
    """
    tenants: dict[str, TenantCredentials] = {}
    for n in range(1, _SLOT_COUNT + 1):
        prefix = f"ST_TENANT_SLOT{n}_"
        name = _env(prefix + "NAME")
        if name is None:
            continue
        # Forgive the two most likely typing habits in the settings form:
        # uppercase and spaces ("St Louis" -> "st_louis").
        name = re.sub(r"\s+", "_", name.lower())
        if not _NAME_RE.match(name):
            raise RuntimeError(
                f"Invalid tenant name {name!r} in Tenant {n}. "
                "Names must be lowercase, start with a letter, "
                "and use only [a-z0-9_-]."
            )
        if name in tenants:
            raise RuntimeError(f"Duplicate tenant name {name!r} (Tenant {n}).")
        values: dict[str, str] = {}
        for field, suffix in _PER_TENANT_VARS:
            val = _env(prefix + suffix)
            if val is None:
                raise RuntimeError(
                    f"Tenant {n} ({name!r}) is missing {prefix + suffix}. "
                    "Fill in all five fields for this tenant in the extension "
                    "settings, or clear its name to disable the slot."
                )
            values[field] = val
        tenants[name] = TenantCredentials(name=name, **values)
    return tenants or None


def _load_one(name: str) -> TenantCredentials:
    upper = name.upper()
    values: dict[str, str] = {}
    for field, suffix in _PER_TENANT_VARS:
        key = f"ST_TENANT_{upper}_{suffix}"
        val = os.environ.get(key)
        if not val:
            raise RuntimeError(f"Missing {key} for tenant {name!r}.")
        values[field] = val
    return TenantCredentials(name=name, **values)


def load_tenants() -> dict[str, TenantCredentials]:
    """Load all configured tenants. Cached after first call.

    `ST_TENANTS` (namespaced vars) takes precedence; otherwise falls back to
    the numbered `ST_TENANT_SLOT*` vars set by the .mcpb bundle. Raises
    RuntimeError on missing/invalid config. Hard-errors if the legacy
    single-tenant env vars are set but `ST_TENANTS` is unset, to steer users
    through the migration.
    """
    global _cache
    if _cache is not None:
        return _cache

    roster = os.environ.get("ST_TENANTS", "").strip()
    if not roster:
        slot_tenants = _load_slots()
        if slot_tenants is not None:
            _cache = slot_tenants
            return slot_tenants
        legacy_present = [v for v in _LEGACY_VARS if os.environ.get(v)]
        if legacy_present:
            raise RuntimeError(
                "ST_TENANTS is not set, but legacy single-tenant vars are present: "
                f"{', '.join(legacy_present)}. This server is now multi-tenant. "
                "Set ST_TENANTS=<comma-separated names> and namespaced vars "
                "ST_TENANT_<NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY per tenant. "
                "See README for the full migration."
            )
        raise RuntimeError(
            "No tenants configured. Either set ST_TENANTS=<comma-separated names> "
            "plus ST_TENANT_<NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY per "
            "tenant, or (Claude Desktop extension) fill in Tenant 1 in the "
            "extension settings (ST_TENANT_SLOT1_*)."
        )

    names = _parse_roster(roster)
    tenants = {name: _load_one(name) for name in names}
    _cache = tenants
    return tenants


def tenant_names() -> list[str]:
    """List of configured tenant names, in roster order."""
    return list(load_tenants().keys())


def get_tenant(name: str) -> TenantCredentials:
    """Look up a tenant by name. Raises UnknownTenantError if not configured."""
    key = name.strip().lower()
    tenants = load_tenants()
    if key not in tenants:
        raise UnknownTenantError(name, list(tenants.keys()))
    return tenants[key]


def _reset_cache_for_tests() -> None:
    """Clear the module cache — tests only."""
    global _cache
    _cache = None
