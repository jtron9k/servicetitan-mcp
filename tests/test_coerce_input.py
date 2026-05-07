"""Tests for native dict/list input on `run_report` and `servicetitan_api_call`.

Pre-fix contract: callers had to stringify JSON args (`parameters='{"k":"v"}'`).
LLM callers naturally emit dicts, so the natural form failed the Pydantic
schema. These tests lock in the new contract: pass real dicts/lists, get
the request through to ServiceTitan unchanged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    """Avoid real network auth. Every test gets a dummy bearer token."""

    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    yield


def _wire_client(monkeypatch, handler) -> ServiceTitanClient:
    """Build a MockTransport-backed client and patch `_get_client` to return it."""
    transport = httpx.MockTransport(handler)
    c = ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=transport,
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )
    monkeypatch.setattr(server, "_get_client", lambda _tenant: c)
    return c


# ── run_report ────────────────────────────────────────────────────────


async def test_run_report_accepts_dict_parameters(monkeypatch):
    """A real dict is forwarded as ServiceTitan's [{name, value}] shape."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/reporting/v2/tenant/123/report-category/biz/reports/42/data" in request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"data": [], "page": 1, "pageSize": 50, "totalCount": 0, "hasMore": False})

    _wire_client(monkeypatch, handler)
    out = await server.run_report(
        tenant="t",
        report_id=42,
        category="biz",
        parameters={"From": "2024-01-01", "To": "2024-12-31"},
    )

    assert "Error" not in out, out
    assert captured["body"] == {
        "parameters": [
            {"name": "From", "value": "2024-01-01"},
            {"name": "To", "value": "2024-12-31"},
        ]
    }


async def test_run_report_rejects_list_parameters(monkeypatch):
    """A list is not a valid report-param shape; handler returns a friendly error."""
    _wire_client(monkeypatch, lambda _: pytest.fail("handler should not be called"))

    out = await server.run_report(
        tenant="t",
        report_id=42,
        category="biz",
        parameters=["From", "2024-01-01"],  # type: ignore[arg-type]
    )

    assert out.startswith("Error: 'parameters' must be a JSON object"), out


async def test_run_report_with_no_parameters(monkeypatch):
    """Omitting `parameters` yields the empty-array body the API expects."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"data": [], "page": 1, "pageSize": 50, "totalCount": 0, "hasMore": False})

    _wire_client(monkeypatch, handler)
    out = await server.run_report(tenant="t", report_id=42, category="biz")

    assert "Error" not in out, out
    assert captured["body"] == {"parameters": []}


# ── servicetitan_api_call ────────────────────────────────────────────


async def test_api_call_accepts_dict_body(monkeypatch):
    """POST with a dict body forwards it as JSON object."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"id": 99})

    _wire_client(monkeypatch, handler)
    out = await server.servicetitan_api_call(
        tenant="t",
        method="POST",
        path="/crm/v2/tenant/{tenant_id}/customers",
        body={"name": "Acme", "active": True},
    )

    assert "API Error" not in out, out
    assert captured["body"] == {"name": "Acme", "active": True}


async def test_api_call_accepts_list_body(monkeypatch):
    """POST with a list body forwards it as a JSON array (batch endpoints)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True})

    _wire_client(monkeypatch, handler)
    out = await server.servicetitan_api_call(
        tenant="t",
        method="POST",
        path="/some/v2/tenant/{tenant_id}/batch",
        body=[{"id": 1}, {"id": 2}],
    )

    assert "API Error" not in out, out
    assert captured["body"] == [{"id": 1}, {"id": 2}]


async def test_api_call_accepts_dict_query_params(monkeypatch):
    """GET with a dict query_params forwards them as URL params."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params.get("pageSize") == "100"
        assert request.url.params.get("name") == "Acme"
        return httpx.Response(200, json={"data": [], "page": 1, "pageSize": 100, "totalCount": 0, "hasMore": False})

    _wire_client(monkeypatch, handler)
    out = await server.servicetitan_api_call(
        tenant="t",
        method="GET",
        path="/crm/v2/tenant/{tenant_id}/customers",
        query_params={"pageSize": 100, "name": "Acme"},
    )

    assert "API Error" not in out, out


async def test_api_call_rejects_non_dict_query_params(monkeypatch):
    """A list passed for query_params is rejected with a friendly error."""
    _wire_client(monkeypatch, lambda _: pytest.fail("handler should not be called"))

    out = await server.servicetitan_api_call(
        tenant="t",
        method="GET",
        path="/x",
        query_params=["pageSize", 100],  # type: ignore[arg-type]
    )

    assert out.startswith("Error: 'query_params' must be a JSON object"), out


# ── Schema introspection ─────────────────────────────────────────────


async def test_tool_schemas_use_object_not_string():
    """Lock the contract: a future refactor must not silently revert these
    args to `string`. The schema should advertise object/array types."""
    tools = await server.mcp.list_tools()
    by_name = {t.name: t for t in tools}

    rr = by_name["run_report"].inputSchema
    rr_param = rr["properties"]["parameters"]
    assert "string" not in _flatten_types(rr_param), (
        f"run_report.parameters schema must not be string-typed: {rr_param}"
    )

    api = by_name["servicetitan_api_call"].inputSchema
    qp = api["properties"]["query_params"]
    body = api["properties"]["body"]
    assert "string" not in _flatten_types(qp), f"query_params schema must not be string-typed: {qp}"
    assert "string" not in _flatten_types(body), f"body schema must not be string-typed: {body}"


def _flatten_types(schema: dict) -> set[str]:
    """Collect every `type` string present in a JSON Schema fragment.

    Handles top-level `type`, `anyOf`/`oneOf` unions, and `null`-typed
    branches that pydantic emits for `dict | None`-style annotations.
    """
    found: set[str] = set()
    t = schema.get("type")
    if isinstance(t, str):
        found.add(t)
    elif isinstance(t, list):
        found.update(t)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            found |= _flatten_types(sub)
    return found
