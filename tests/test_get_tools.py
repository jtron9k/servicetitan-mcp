"""Tests for the Phase 2 single-record `get_*` tools.

These lock in the exact ServiceTitan resource paths each new getter hits. Every
path here was verified live (200) during planning; the test guards against a
Phase-1-style regression where a wrong category/resource slug silently 404s.

Pattern mirrors `tests/test_reporting_tools.py`: a MockTransport-backed client
patched onto `server._get_client`, asserting the outgoing request path.
"""

from __future__ import annotations

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


# (tool function, category, resource) — the path is
# /{category}/v2/tenant/123/{resource}/{id}
GET_TOOLS = [
    (server.get_lead, "crm", "leads"),
    (server.get_booking, "crm", "bookings"),
    (server.get_appointment, "jpm", "appointments"),
    (server.get_job_type, "jpm", "job-types"),
    (server.get_technician, "settings", "technicians"),
    (server.get_technician_shift, "dispatch", "technician-shifts"),
    (server.get_non_job_appointment, "dispatch", "non-job-appointments"),
    (server.get_purchase_order, "inventory", "purchase-orders"),
    (server.get_membership, "memberships", "memberships"),
    (server.get_recurring_service, "memberships", "recurring-services"),
    (server.get_call, "telecom", "calls"),
]


@pytest.mark.parametrize(
    "tool, category, resource",
    GET_TOOLS,
    ids=[t.__name__ for t, _c, _r in GET_TOOLS],
)
async def test_get_tool_hits_verified_single_record_path(
    monkeypatch, tool, category, resource
):
    """Each getter issues GET /{category}/v2/tenant/123/{resource}/{id} and
    returns the record JSON."""
    record_id = 999
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        captured["path"] = request.url.path
        return httpx.Response(200, json={"id": record_id})

    _wire_client(monkeypatch, handler)
    out = await tool("t", record_id)

    assert "Error" not in out, out
    assert captured["path"] == f"/{category}/v2/tenant/123/{resource}/{record_id}"
    # Single record — must NOT be the paginated collection path.
    assert not captured["path"].endswith(f"/{resource}")
    assert f'"id": {record_id}' in out
