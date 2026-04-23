"""Regression tests for pagination display.

Pre-fix bug: `_fmt` silently sliced the displayed list to 25 items regardless
of requested page_size, and showed `hasMore=False` from whatever ST returned —
causing callers with page_size=200 to see only 25 items and assume there were
no more. That produced ~40%-of-truth counts for attribution work.
"""

from __future__ import annotations

import json

import httpx
import pytest

from servicetitan_mcp import auth, client as client_mod, server
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
    monkeypatch.setattr(server, "_get_client", lambda: c)
    return c


def _parse_footer(output: str) -> dict:
    """Pull the trailing `(Showing N of T ... hasMore=X)` into a dict."""
    assert "(Showing " in output, f"no footer in output: {output[-200:]}"
    tail = output[output.rindex("(Showing ") :].strip("()")
    # "Showing 71 of 71 results — page 1, pageSize 100, hasMore=False"
    parts = {}
    parts["shown"] = int(tail.split(" ", 2)[1])
    parts["total"] = tail.split(" of ", 1)[1].split(" ", 1)[0]
    parts["hasMore"] = "hasMore=True" in tail
    return parts


async def test_list_tool_with_page_size_100_returns_all_71_records(monkeypatch):
    """Repro of the reported bug: a page_size large enough to fit every record
    must return every record to the caller, not a silent 25-item slice."""

    # 71 records — more than the old hidden 25-item cap.
    jobs = [{"id": i, "summary": f"Job {i}"} for i in range(1, 72)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/jpm/v2/tenant/123/jobs" in request.url.path
        assert request.url.params.get("pageSize") == "100"
        # Mirror ST: full page fits, so hasMore is False on page 1.
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 1,
                "pageSize": 100,
                "totalCount": 71,
                "hasMore": False,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(page=1, page_size=100)

    # Strip the footer to parse the JSON payload
    body, _, _ = output.partition("\n\n(Showing")
    returned = json.loads(body)
    assert len(returned) == 71, (
        f"expected all 71 jobs visible to caller, got {len(returned)}. "
        "The _fmt 25-item silent cap has regressed."
    )
    footer = _parse_footer(output)
    assert footer["shown"] == 71
    assert footer["total"] == "71"
    assert footer["hasMore"] is False


async def test_list_tool_hasmore_true_when_full_page_and_no_totalcount(monkeypatch):
    """If ST doesn't return totalCount and a full page comes back, the caller
    must see hasMore=True — the pre-fix inference silently set it False."""

    jobs = [{"id": i} for i in range(1, 51)]

    def handler(request: httpx.Request) -> httpx.Response:
        # ST sometimes omits totalCount AND hasMore; caller must still be told
        # to keep paging when a full page was returned.
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 1,
                "pageSize": 50,
                "totalCount": None,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(page=1, page_size=50)

    footer = _parse_footer(output)
    assert footer["shown"] == 50
    assert footer["total"] == "unknown", (
        f"expected 'unknown' when ST gives totalCount=None, got {footer['total']!r}"
    )
    assert footer["hasMore"] is True, (
        "full page with no totalCount should infer hasMore=True"
    )


async def test_list_tool_hasmore_false_when_partial_page(monkeypatch):
    """A partial page (fewer items than page_size) with no totalCount should
    infer hasMore=False — that's the signal there's nothing left."""

    jobs = [{"id": i} for i in range(1, 22)]  # 21 items, page_size=50

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 3,
                "pageSize": 50,
                "totalCount": None,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(page=3, page_size=50)

    footer = _parse_footer(output)
    assert footer["shown"] == 21
    assert footer["hasMore"] is False, (
        "partial page with no totalCount should infer hasMore=False"
    )
