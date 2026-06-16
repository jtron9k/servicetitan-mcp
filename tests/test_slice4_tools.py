"""Tests for the Phase 2 slice-4 read tools.

Locks in the exact paths and query-param mappings for the seven new read tools
(all live-verified 200 against hoffmann_stl during planning):
  - Inventory transaction feeds: adjustments, transfers, receipts, returns.
  - Dispatch reference: arrival-windows, teams.
  - Customer-interactions: technician-ratings.

Guards against Phase-1-style wrong-slug 404s and silent no-op filters.

Pattern mirrors `tests/test_subresource_tools.py`: a MockTransport-backed client
patched onto `server._get_client`.
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


# (id, async-thunk, expected path) for the seven slice-4 list tools.
# Each returns a {data, hasMore} envelope → goes through _fmt.
LIST_TOOLS = [
    ("inventory_adjustments", lambda: server.list_inventory_adjustments("t"),
     "/inventory/v2/tenant/123/adjustments"),
    ("inventory_transfers", lambda: server.list_inventory_transfers("t"),
     "/inventory/v2/tenant/123/transfers"),
    ("inventory_receipts", lambda: server.list_inventory_receipts("t"),
     "/inventory/v2/tenant/123/receipts"),
    ("inventory_returns", lambda: server.list_inventory_returns("t"),
     "/inventory/v2/tenant/123/returns"),
    ("arrival_windows", lambda: server.list_arrival_windows("t"),
     "/dispatch/v2/tenant/123/arrival-windows"),
    ("teams", lambda: server.list_teams("t"),
     "/dispatch/v2/tenant/123/teams"),
    ("technician_ratings", lambda: server.list_technician_ratings("t"),
     "/customer-interactions/v2/tenant/123/technician-ratings"),
]


@pytest.mark.parametrize(
    "thunk, expected_path",
    [(t, p) for _id, t, p in LIST_TOOLS],
    ids=[i for i, _t, _p in LIST_TOOLS],
)
async def test_list_slice4_tool_hits_verified_path(monkeypatch, thunk, expected_path):
    """Each slice-4 list tool issues GET on its verified path and formats the
    {data, hasMore} envelope (pagination footer present)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": [{"id": 1}], "hasMore": False})

    _wire_client(monkeypatch, handler)
    out = await thunk()

    assert "Error" not in out, out
    assert captured["path"] == expected_path
    assert '"id": 1' in out
    assert "hasMore=" in out  # _fmt footer


async def test_inventory_filters_map_to_query_params(monkeypatch):
    """Inventory date/business-unit filters map to ST's camelCase query params,
    and omitted filters are not sent."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["createdOnOrAfter"] = request.url.params.get("createdOnOrAfter")
        captured["createdOnOrBefore"] = request.url.params.get("createdOnOrBefore")
        captured["businessUnitIds"] = request.url.params.get("businessUnitIds")
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)

    await server.list_inventory_adjustments(
        "t", created_on_or_after="2026-01-01", business_unit_id=42
    )
    assert captured["createdOnOrAfter"] == "2026-01-01"
    assert captured["businessUnitIds"] == "42"
    assert captured["createdOnOrBefore"] is None  # omitted → not sent


async def test_technician_ratings_filters_map_to_query_params(monkeypatch):
    """technician_id → technicianId; date filters → camelCase; omitted → absent."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["technicianId"] = request.url.params.get("technicianId")
        captured["createdOnOrAfter"] = request.url.params.get("createdOnOrAfter")
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)

    await server.list_technician_ratings("t", technician_id=99)
    assert captured["technicianId"] == "99"
    assert captured["createdOnOrAfter"] is None
