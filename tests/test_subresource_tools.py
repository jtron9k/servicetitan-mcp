"""Tests for the Phase 2 slice-3 CRM/JPM sub-resource read tools.

Locks in:
  1. The exact nested/reference paths each tool issues (e.g.
     `/crm/v2/tenant/123/customers/{id}/notes`, `/jpm/v2/tenant/123/job-cancel-reasons`)
     — guards against Phase-1-style wrong-slug 404s.
  2. `get_job_history` hits `.../jobs/{id}/history`, sends NO pagination params
     (the endpoint isn't paginated), and returns the raw {"history": [...]} shape
     with no `_fmt` footer.
  3. The `active_only` → `active=True` server-side filter mapping on the
     cancel/hold reason reference lists.

Pattern mirrors `tests/test_export_tools.py` / `tests/test_get_tools.py`: a
MockTransport-backed client patched onto `server._get_client`.
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


# (id, async-thunk, expected path) for the eight list-style sub-resource tools.
# Each returns a {data, hasMore} envelope → goes through _fmt.
LIST_TOOLS = [
    ("customer_notes", lambda: server.list_customer_notes("t", 777),
     "/crm/v2/tenant/123/customers/777/notes"),
    ("location_notes", lambda: server.list_location_notes("t", 888),
     "/crm/v2/tenant/123/locations/888/notes"),
    ("location_contacts", lambda: server.list_location_contacts("t", 888),
     "/crm/v2/tenant/123/locations/888/contacts"),
    ("customer_custom_field_types", lambda: server.list_customer_custom_field_types("t"),
     "/crm/v2/tenant/123/customers/custom-fields"),
    ("location_custom_field_types", lambda: server.list_location_custom_field_types("t"),
     "/crm/v2/tenant/123/locations/custom-fields"),
    ("job_notes", lambda: server.list_job_notes("t", 555),
     "/jpm/v2/tenant/123/jobs/555/notes"),
    ("job_cancel_reasons", lambda: server.list_job_cancel_reasons("t"),
     "/jpm/v2/tenant/123/job-cancel-reasons"),
    ("job_hold_reasons", lambda: server.list_job_hold_reasons("t"),
     "/jpm/v2/tenant/123/job-hold-reasons"),
]


@pytest.mark.parametrize(
    "thunk, expected_path",
    [(t, p) for _id, t, p in LIST_TOOLS],
    ids=[i for i, _t, _p in LIST_TOOLS],
)
async def test_list_subresource_tool_hits_verified_path(monkeypatch, thunk, expected_path):
    """Each list-style sub-resource tool issues GET on its verified path and
    formats the {data, hasMore} envelope (pagination footer present)."""
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


async def test_get_job_history_path_no_paging_raw_shape(monkeypatch):
    """get_job_history hits .../jobs/{id}/history, sends NO page/pageSize, and
    returns the raw {"history": [...]} object (no pagination footer)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        captured["path"] = request.url.path
        captured["page"] = request.url.params.get("page")
        captured["pageSize"] = request.url.params.get("pageSize")
        return httpx.Response(
            200, json={"history": [{"id": 1, "eventType": "Job Booked"}]}
        )

    _wire_client(monkeypatch, handler)
    out = await server.get_job_history("t", 555)

    assert "Error" not in out, out
    assert captured["path"] == "/jpm/v2/tenant/123/jobs/555/history"
    # Not paginated — these params must not be sent.
    assert captured["page"] is None
    assert captured["pageSize"] is None
    assert '"history"' in out
    assert "Job Booked" in out
    assert "hasMore=" not in out  # no _fmt footer for the non-enveloped shape


@pytest.mark.parametrize(
    "thunk_factory",
    [
        lambda active_only: server.list_job_cancel_reasons("t", active_only=active_only),
        lambda active_only: server.list_job_hold_reasons("t", active_only=active_only),
    ],
    ids=["cancel_reasons", "hold_reasons"],
)
async def test_reason_lists_active_only_mapping(monkeypatch, thunk_factory):
    """active_only=True → active=True query param; active_only=False → omitted."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["active"] = request.url.params.get("active")
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)

    await thunk_factory(True)
    assert captured["active"] == "True"

    await thunk_factory(False)
    assert captured["active"] is None
