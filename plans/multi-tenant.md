# Multi-Tenant ServiceTitan MCP Server

## Context

The user operates four ServiceTitan tenants (one per business) and wants a single MCP server instance to query any of them. Workloads are mixed: some questions target one business ("most recent job in Nashville"), others aggregate across all four ("total jobs booked across all businesses last month"). Today `servicetitan_mcp` is hardcoded to a single tenant via `ST_APP_KEY` / `ST_CLIENT_ID` / `ST_CLIENT_SECRET` / `ST_TENANT_ID` env vars read once at tool-call time in [servicetitan_mcp/server.py:65](servicetitan_mcp/server.py:65), so using four tenants requires four separate Claude Desktop server entries and manual switching between chats — unworkable for the cross-tenant questions the user asks often.

The goal is to let the LLM name a tenant on each call, fan out across tenants when it needs aggregation, and keep single-tenant calls simple. Credentials stay in Claude Desktop's `env` block (user's preference — no new config file). The existing `TokenManager` in [servicetitan_mcp/auth.py:15](servicetitan_mcp/auth.py:15) already keys tokens by `tenant_id`, so the foundation for multi-tenant token caching is already in place.

## Design decisions (confirmed with user)

- **Tenant selection**: explicit required `tenant: str` argument on every MCP tool. No default tenant.
- **Cross-tenant aggregation**: LLM orchestrates — calls tools N times with different tenants and aggregates itself. Server stays per-call single-tenant.
- **Missing tenant arg**: hard error listing configured tenants (via FastMCP's required-arg schema enforcement + a friendly unknown-tenant message).
- **Credential store**: namespaced env vars, roster-driven.
- **Backward compatibility**: clean break. Four tenants need re-keying either way — a legacy shim is only cost.

## Env var schema

```
ST_TENANTS=nashville,memphis,atlanta,dallas

ST_TENANT_NASHVILLE_ID=...
ST_TENANT_NASHVILLE_CLIENT_ID=...
ST_TENANT_NASHVILLE_CLIENT_SECRET=...
ST_TENANT_NASHVILLE_APP_KEY=...
# (repeat for MEMPHIS, ATLANTA, DALLAS)
```

Tenant names normalize to lowercase for lookups, uppercase for env-key matching. Validate with `^[a-z][a-z0-9_-]*$`. If `ST_TENANTS` is unset but legacy `ST_TENANT_ID` is present, fail fast with a migration-hint error.

## File-by-file changes

### NEW: [servicetitan_mcp/config.py](servicetitan_mcp/config.py)

```python
@dataclass(frozen=True)
class TenantCredentials:
    name: str          # canonical lowercase
    tenant_id: str
    client_id: str
    client_secret: str
    app_key: str

class UnknownTenantError(ValueError): ...   # carries valid-names list

def load_tenants() -> dict[str, TenantCredentials]: ...   # reads env, caches in module global
def tenant_names() -> list[str]: ...                      # for error messages + list_tenants tool
def get_tenant(name: str) -> TenantCredentials: ...       # raises UnknownTenantError with valid list
```

Runs once at import. Raises with per-var specificity (e.g. `"Missing ST_TENANT_MEMPHIS_CLIENT_SECRET"`). Empty/missing `ST_TENANTS` is a hard error.

### MODIFY: [servicetitan_mcp/client.py:136-140](servicetitan_mcp/client.py:136)

Replace the module-level `_main_limiter` / `_reporting_limiter` singletons with per-tenant factories:

```python
_main_limiters: dict[str, TokenBucket] = {}
_reporting_limiters: dict[str, TokenBucket] = {}

def main_limiter_for(tenant_name: str) -> TokenBucket: ...
def reporting_limiter_for(tenant_name: str) -> TokenBucket: ...
```

Keep `_concurrency_sem` process-wide (it guards local httpx fan-out, not ST quota). Rationale: ST rate limits are per-app-per-tenant, so sharing buckets across tenants under-utilizes capacity by 4x. `ServiceTitanClient.__init__` already accepts `main_limiter` / `reporting_limiter` kwargs ([client.py:155-156](servicetitan_mcp/client.py:155)) — injection path is already built, just pass the per-tenant bucket in.

### MODIFY: [servicetitan_mcp/server.py:65-84](servicetitan_mcp/server.py:65)

Replace `_get_client()` with `_get_client(tenant: str)`:

```python
_CLIENT_CACHE: dict[str, ServiceTitanClient] = {}

def _get_client(tenant: str) -> ServiceTitanClient:
    name = tenant.strip().lower()
    if name not in _CLIENT_CACHE:
        creds = get_tenant(name)   # raises UnknownTenantError with valid list
        _CLIENT_CACHE[name] = ServiceTitanClient(
            creds.app_key, creds.client_id, creds.client_secret, creds.tenant_id,
            main_limiter=main_limiter_for(name),
            reporting_limiter=reporting_limiter_for(name),
        )
    return _CLIENT_CACHE[name]
```

Caching clients per tenant keeps connection pooling, per-tenant limiters, and `TokenManager` caching all aligned across calls. Add a small helper `_resolve(tenant)` that wraps `_get_client` and, on `UnknownTenantError`, returns a clear message: `"Unknown tenant 'foo'. Configured: nashville, memphis, atlanta, dallas. Call list_tenants for the authoritative list."`

### NEW tool: `list_tenants` in [servicetitan_mcp/server.py](servicetitan_mcp/server.py)

Added just after `_get_client`. No `tenant` arg. Returns names only — never secrets or tenant ids. Docstring prompts the LLM to call it first when the user names a business.

```python
@mcp.tool()
def list_tenants() -> str:
    """Configured ServiceTitan tenants. Call this first when the user
    mentions a business by name or asks for a cross-tenant aggregation."""
    return json.dumps({"tenants": tenant_names()}, indent=2)
```

### MODIFY: all 60 `@mcp.tool()` functions in [servicetitan_mcp/server.py](servicetitan_mcp/server.py)

Verified count: 60 `@mcp.tool()` decorators; `list_business_units` appears once at [server.py:929](servicetitan_mcp/server.py:929). Mechanical pass: add `tenant: str` as the first required parameter to each tool, replace `client = _get_client()` with `client = _get_client(tenant)`. Include a short note in each docstring: `"tenant: name of a configured ServiceTitan tenant (call list_tenants)"`. A FastMCP decorator won't help abstract this because the schema is derived from the signature.

`servicetitan_api_call` at [server.py:1298](servicetitan_mcp/server.py:1298): same treatment. The existing `{tenant_id}` placeholder substitution at [server.py:1328](servicetitan_mcp/server.py:1328) already uses `client.tenant_id`, which is now the resolved tenant's id — no logic change needed there.

### VERIFY-ONLY: [servicetitan_mcp/auth.py](servicetitan_mcp/auth.py)

No changes. `TokenManager._tokens` is already keyed by `tenant_id` ([auth.py:15](servicetitan_mcp/auth.py:15), [auth.py:24](servicetitan_mcp/auth.py:24), [auth.py:41](servicetitan_mcp/auth.py:41)), and each tenant has a distinct id, so tokens cache independently with zero collisions. Module-level `token_manager` singleton ([auth.py:49](servicetitan_mcp/auth.py:49)) keeps working.

### MODIFY: [README.md](README.md)

- Replace the single-tenant env var block with the namespaced schema.
- Add a full `claude_desktop_config.json` example showing four tenants in the `env` block.
- Document the required `tenant` parameter and the new `list_tenants` discovery tool.
- Add a "Cross-tenant queries" section explaining the LLM fans out via parallel calls (no server-side aggregation).
- Note the migration: old `ST_TENANT_ID` / `ST_APP_KEY` / `ST_CLIENT_ID` / `ST_CLIENT_SECRET` vars are no longer read; include the exact error message users will see on an unmigrated config.
- Also update the header docstring at [server.py:3-8](servicetitan_mcp/server.py:3).

## Implementation order

**Execute one step at a time. After each step, stop and report results to the user. Wait for user confirmation before starting the next step.** Each step has a concrete test gate; do not proceed until it passes and the user gives the go-ahead.

### Step 1 — `config.py` (new file)
- Create [servicetitan_mcp/config.py](servicetitan_mcp/config.py) with `TenantCredentials`, `UnknownTenantError`, `load_tenants()`, `tenant_names()`, `get_tenant(name)`.
- **Test gate**: `python -c "from servicetitan_mcp.config import load_tenants; print(list(load_tenants()))"` with real env set — should print four tenant names. Also test error paths: no `ST_TENANTS` set (migration hint), a tenant missing one var (per-var error), unknown tenant lookup (lists valid names).
- **Stop and confirm with user before step 2.**

### Step 2 — `client.py` per-tenant limiter factories
- Replace module-level `_main_limiter` / `_reporting_limiter` at [client.py:138-139](servicetitan_mcp/client.py:138) with `main_limiter_for(name)` / `reporting_limiter_for(name)` factories backed by `dict[str, TokenBucket]`.
- Keep thin shims named `_main_limiter` / `_reporting_limiter` pointing at a `"__legacy__"` bucket so current server.py still imports. (Removed in step 5.)
- **Test gate**: `python -c "import servicetitan_mcp.server"` imports cleanly. `python -c "from servicetitan_mcp.client import main_limiter_for; b1=main_limiter_for('a'); b2=main_limiter_for('a'); b3=main_limiter_for('b'); assert b1 is b2 and b1 is not b3"` passes.
- **Stop and confirm with user before step 3.**

### Step 3 — `server.py` infrastructure: `_get_client(tenant)`, `_resolve`, `list_tenants`, header docstring
- Refactor `_get_client()` at [server.py:65-84](servicetitan_mcp/server.py:65) to take `tenant: str`, look up credentials via `config.get_tenant`, cache clients in `_CLIENT_CACHE`.
- Add `_resolve(tenant)` helper that wraps `_get_client` and translates `UnknownTenantError` into a friendly tool-facing message.
- Add `list_tenants` MCP tool.
- Update header docstring at [server.py:3-8](servicetitan_mcp/server.py:3).
- Do NOT yet modify the 60 tools — they still call `_get_client()` with no args, which will now fail at call time. That's fine; we're gating on import + `list_tenants`.
- **Test gate**: `python -c "import servicetitan_mcp.server"` imports. Launch via Claude Desktop (or MCP inspector), call `list_tenants`, confirm four names returned. Calling any other tool should error (expected — step 4 fixes).
- **Stop and confirm with user before step 4.**

### Step 4 — Add `tenant: str` to all 60 tools + `servicetitan_api_call`
- Mechanical pass in sub-groups by section (CRM, JPM, accounting, dispatch, pricebook, payroll, memberships, reporting, escape hatch). **Within this step, stop after each sub-group**, run `python -c "import servicetitan_mcp.server"`, and report progress to the user before continuing.
- Add `tenant: str` as first required param; replace `client = _get_client()` with `client = _resolve(tenant)`; add `"tenant: name of a configured ServiceTitan tenant (call list_tenants)"` line to each docstring.
- **Test gate**: Import clean. In Claude Desktop: `list_customers(tenant="nashville", ...)` works; calling without `tenant` produces a FastMCP schema error; `tenant="typo"` produces the unknown-tenant message.
- **Stop and confirm with user before step 5.**

### Step 5 — Remove step 2's temporary limiter shims
- Delete the `_main_limiter` / `_reporting_limiter` compatibility names from `client.py`. Nothing should reference them now.
- **Test gate**: `python -c "import servicetitan_mcp.server"` imports. Grep for `_main_limiter\b|_reporting_limiter\b` in the codebase returns zero hits outside the factory implementation.
- **Stop and confirm with user before step 6.**

### Step 6 — Update README
- Replace single-tenant env var block with namespaced schema and four-tenant `claude_desktop_config.json` example.
- Document the `tenant` parameter and `list_tenants` tool.
- Add "Cross-tenant queries" section.
- Document the legacy-env-var migration error message users will see.
- **Test gate**: Manual read-through; user confirms docs match their setup.
- **Stop and confirm with user before step 7.**

### Step 7 — End-to-end verification against live tenants
- Walk through every item in the "Verification" section below in Claude Desktop against real ServiceTitan tenants.
- **Stop and confirm with user** — implementation complete when all nine verification steps pass.

## Risks & migration notes

- **60-tool edit is the main risk surface.** Group-by-group compile-check mitigates. Resist "while I'm here" cleanup in the same pass — keep changes mechanical.
- **Silent default-tenant trap**: never add a fallback that picks a tenant when `tenant` is absent. Hard-error only. A silent wrong-tenant answer ("most recent job" for the wrong business) is worse than any UX inconvenience.
- **Tool schema churn**: every existing Claude Desktop conversation's tool list changes (new required arg on every tool). Expected; ship README update in the same commit.
- **Shared httpx pool stays shared** — connections are per-host, ST is one host, no isolation benefit from splitting.
- **Legacy env vars**: if a user upgrades without migrating their config, startup should detect `ST_TENANT_ID` set + `ST_TENANTS` unset and fail with a clear migration hint, not a vague "missing var" error.

## Verification (end-to-end)

1. `python -c "from servicetitan_mcp.config import load_tenants; print(list(load_tenants()))"` → prints four tenant names.
2. `python -m servicetitan_mcp.server` starts without error under stdio with the real env.
3. In Claude Desktop, new chat: **"List my configured ServiceTitan tenants."** → calls `list_tenants`, returns four names.
4. **"What's the most recent job booked in Nashville?"** → single `list_jobs(tenant="nashville", ...)` call with the expected filter.
5. **"Total jobs booked across all businesses last month."** → LLM makes four parallel `list_jobs` calls (one per tenant) and aggregates in its reply.
6. Manually call a tool without `tenant` (via the MCP inspector or a test harness) → FastMCP schema error surfaced.
7. Call with `tenant="typo"` → our "Unknown tenant 'typo'. Configured: …" message.
8. Over ~15 minutes of mixed-tenant use, no cross-tenant 401s in stderr — confirms per-tenant token cache independence.
9. Watch stderr under a burst of concurrent same-tenant calls — confirms the per-tenant main limiter still rate-limits correctly (no 429 flood).

## Critical files

- [servicetitan_mcp/server.py](servicetitan_mcp/server.py) — `_get_client`, `list_tenants` (new), 60 tool signatures, `servicetitan_api_call`, header docstring
- [servicetitan_mcp/client.py](servicetitan_mcp/client.py) — per-tenant limiter factories at lines 136-140
- [servicetitan_mcp/config.py](servicetitan_mcp/config.py) — **new file**, tenant registry
- [servicetitan_mcp/auth.py](servicetitan_mcp/auth.py) — verify-only, no edits
- [README.md](README.md) — env var schema, `tenant` param docs, cross-tenant usage section
