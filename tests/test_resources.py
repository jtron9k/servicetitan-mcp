"""Tests for the @mcp.resource() handlers (tenants + lookup tables).

Resources mirror the data the tools already expose, but resource reads
don't cost a tool call on clients that auto-prefetch them. The lookup
resource caches per (tenant, kind) for one hour so re-reads are free.
"""

from __future__ import annotations

import json

import pytest

from servicetitan_mcp import server


@pytest.fixture(autouse=True)
def _clear_resource_cache():
    """Each test starts with an empty cache so order doesn't matter."""
    server._resource_cache.clear()
    yield
    server._resource_cache.clear()


class FakeClient:
    """Records every list_resource call and returns canned data per (cat, res)."""

    def __init__(self, fixtures: dict[tuple[str, str], dict] | None = None):
        self.calls: list[tuple[str, str, int, int]] = []
        self._fixtures = fixtures or {}

    async def list_resource(self, category, resource, page=1, page_size=50, params=None):
        self.calls.append((category, resource, page, page_size))
        return self._fixtures.get(
            (category, resource),
            {"data": [], "totalCount": 0, "page": page, "pageSize": page_size, "hasMore": False},
        )


def test_resource_tenants_returns_configured_names(monkeypatch):
    """The tenants resource must return whatever `tenant_names()` returns,
    keyed under "tenants" — same shape as the tool."""

    monkeypatch.setattr(server, "tenant_names", lambda: ["acme", "other"])

    parsed = json.loads(server.resource_tenants())
    assert parsed == {"tenants": ["acme", "other"]}


async def test_resource_lookup_cache_miss_fetches_and_caches(monkeypatch):
    """First read of a (tenant, kind) hits the API and caches the result."""

    fake = FakeClient(
        fixtures={
            ("settings", "business-units"): {
                "data": [{"id": 1, "name": "Plumbing"}, {"id": 2, "name": "HVAC"}],
                "totalCount": 2,
            }
        }
    )
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    out = await server.resource_lookup(tenant="acme", kind="business_units")
    parsed = json.loads(out)

    assert parsed["tenant"] == "acme"
    assert parsed["kind"] == "business_units"
    assert [item["name"] for item in parsed["items"]] == ["Plumbing", "HVAC"]
    assert fake.calls == [("settings", "business-units", 1, 200)]
    assert ("acme", "business_units") in server._resource_cache


async def test_resource_lookup_cache_hit_skips_api(monkeypatch):
    """A second read of the same (tenant, kind) within TTL must not hit the API."""

    fake = FakeClient(
        fixtures={
            ("dispatch", "zones"): {
                "data": [{"id": 7, "name": "North"}],
                "totalCount": 1,
            }
        }
    )
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    first = await server.resource_lookup(tenant="acme", kind="zones")
    second = await server.resource_lookup(tenant="acme", kind="zones")

    assert first == second
    assert len(fake.calls) == 1, (
        f"cache hit should skip the API; got {len(fake.calls)} calls"
    )


async def test_resource_lookup_cache_expires_after_ttl(monkeypatch):
    """Once the TTL elapses, the next read refetches."""

    fake = FakeClient(
        fixtures={
            ("inventory", "warehouses"): {
                "data": [{"id": 1, "name": "Main"}],
                "totalCount": 1,
            }
        }
    )
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    await server.resource_lookup(tenant="acme", kind="warehouses")
    assert len(fake.calls) == 1

    # Force expiry by rewriting the cache entry's expires_at into the past.
    _, payload = server._resource_cache[("acme", "warehouses")]
    server._resource_cache[("acme", "warehouses")] = (0.0, payload)

    await server.resource_lookup(tenant="acme", kind="warehouses")
    assert len(fake.calls) == 2, "expired cache entry must trigger a refetch"


async def test_resource_lookup_separates_cache_per_tenant_and_kind(monkeypatch):
    """Two tenants reading the same kind get independent cache entries — no
    cross-tenant data leak — and one tenant reading two kinds keeps both."""

    fake = FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    await server.resource_lookup(tenant="acme", kind="zones")
    await server.resource_lookup(tenant="other", kind="zones")
    await server.resource_lookup(tenant="acme", kind="warehouses")

    assert {("acme", "zones"), ("other", "zones"), ("acme", "warehouses")} <= set(
        server._resource_cache
    )
    assert len(fake.calls) == 3, "each distinct (tenant, kind) should fetch once"


async def test_resource_lookup_unknown_kind_returns_error_without_api_call(monkeypatch):
    """Unknown kinds short-circuit before fan-out and don't poison the cache."""

    fake = FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    out = await server.resource_lookup(tenant="acme", kind="not_a_real_kind")
    parsed = json.loads(out)

    assert "error" in parsed
    assert "not_a_real_kind" in parsed["error"]
    assert "valid_kinds" in parsed
    assert fake.calls == []
    assert ("acme", "not_a_real_kind") not in server._resource_cache


async def test_resource_lookup_normalizes_tenant_to_lowercase(monkeypatch):
    """`servicetitan://lookups/ACME/zones` should hit the same cache entry as
    `.../acme/zones` — config slugs are always lowercase."""

    fake = FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda _t: fake)

    await server.resource_lookup(tenant="ACME", kind="zones")
    await server.resource_lookup(tenant="acme", kind="zones")

    assert len(fake.calls) == 1, (
        "tenant name should be normalized so capitalization variants share a cache slot"
    )
    assert ("acme", "zones") in server._resource_cache
    assert ("ACME", "zones") not in server._resource_cache


async def test_resource_lookup_registered_with_fastmcp():
    """Sanity check: the resource templates are registered so MCP clients can
    actually discover them. Catches accidental decorator removal."""

    templates = await server.mcp.list_resource_templates()
    uris = [str(t.uriTemplate) for t in templates]
    assert "servicetitan://lookups/{tenant}/{kind}" in uris

    resources = await server.mcp.list_resources()
    static_uris = [str(r.uri) for r in resources]
    assert "servicetitan://tenants" in static_uris
