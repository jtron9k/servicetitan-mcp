"""Tests for ServiceTitanClient retry + rate-limit + concurrency behavior."""

import asyncio
import time

import httpx
import pytest

from servicetitan_mcp import auth, client as client_mod
from servicetitan_mcp.client import (
    RetryConfig,
    ServiceTitanClient,
    TokenBucket,
)


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    """Avoid real network auth. Every test gets a dummy bearer token."""

    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    yield


def _build_client(handler, **kwargs) -> ServiceTitanClient:
    """Build a ServiceTitanClient with a MockTransport wired to `handler`."""
    transport = httpx.MockTransport(handler)
    return ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=transport,
        **kwargs,
    )


async def test_successful_get_returns_json():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": 1}], "page": 1})

    c = _build_client(handler)
    result = await c.get("/crm/v2/tenant/123/customers")
    assert result == {"data": [{"id": 1}], "page": 1}
    assert len(calls) == 1
    # auth headers should be attached
    assert calls[0].headers["Authorization"] == "Bearer fake-token"
    assert calls[0].headers["ST-App-Key"] == "k"


async def test_429_with_retry_after_waits_and_retries():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    c = _build_client(
        handler,
        retry=RetryConfig(max_retries=3, base_backoff=0.01),
    )
    start = time.monotonic()
    result = await c.get("/crm/v2/tenant/123/customers")
    elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert len(attempts) == 2
    # Retry-After=1 must be honored regardless of base_backoff being small
    assert elapsed >= 0.9, f"expected >=1s wait from Retry-After, got {elapsed:.3f}s"


async def test_429_without_retry_after_uses_exponential_backoff():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(time.monotonic())
        if len(attempts) < 3:
            return httpx.Response(429, text="throttled")
        return httpx.Response(200, json={"ok": True})

    c = _build_client(
        handler,
        retry=RetryConfig(max_retries=3, base_backoff=0.1),
    )
    start = time.monotonic()
    result = await c.get("/jpm/v2/tenant/123/jobs")
    elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert len(attempts) == 3
    # First retry waits base_backoff=0.1, second waits 0.2 → total ~0.3s
    assert 0.25 <= elapsed <= 1.5, f"expected ~0.3s total, got {elapsed:.3f}s"


async def test_429_exhausted_raises_with_body():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(429, text="still throttled buddy")

    c = _build_client(
        handler,
        retry=RetryConfig(max_retries=2, base_backoff=0.01),
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await c.get("/crm/v2/tenant/123/customers")

    # max_retries=2 means 1 initial + 2 retries = 3 total attempts
    assert len(attempts) == 3
    # body must be surfaced in the error message (existing _raise_with_body behavior)
    assert "still throttled buddy" in str(exc_info.value)


async def test_503_is_retried():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json={"ok": True})

    c = _build_client(handler, retry=RetryConfig(max_retries=3, base_backoff=0.01))
    result = await c.get("/crm/v2/tenant/123/customers")
    assert result == {"ok": True}
    assert len(attempts) == 2


async def test_401_is_not_retried():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, text="bad token")

    c = _build_client(handler, retry=RetryConfig(max_retries=3, base_backoff=0.01))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await c.get("/crm/v2/tenant/123/customers")
    # non-retryable — only one attempt
    assert len(attempts) == 1
    assert "bad token" in str(exc_info.value)


async def test_400_is_not_retried():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad request")

    c = _build_client(handler, retry=RetryConfig(max_retries=3, base_backoff=0.01))
    with pytest.raises(httpx.HTTPStatusError):
        await c.get("/crm/v2/tenant/123/customers")
    assert len(attempts) == 1


async def test_reporting_path_uses_reporting_limiter():
    """A call to /reporting/... should consume from the reporting bucket only."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    # Reporting bucket: 1 token capacity, very slow refill.
    # Main bucket: generous.
    reporting = TokenBucket(rate=0.01, capacity=1)
    main = TokenBucket(rate=100, capacity=100)

    c = _build_client(
        handler,
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=main,
        reporting_limiter=reporting,
    )

    # First reporting call uses the one token — fast
    start = time.monotonic()
    await c.get("/reporting/v2/tenant/123/report-categories")
    first_elapsed = time.monotonic() - start

    # Many non-reporting calls should NOT be blocked by the empty reporting bucket
    start = time.monotonic()
    for _ in range(5):
        await c.get("/crm/v2/tenant/123/customers")
    non_reporting_elapsed = time.monotonic() - start

    assert first_elapsed < 0.1, f"first reporting call slow: {first_elapsed:.3f}s"
    assert non_reporting_elapsed < 0.1, (
        f"5 main-API calls took {non_reporting_elapsed:.3f}s — "
        "reporting bucket incorrectly throttled main API"
    )


async def test_concurrency_semaphore_caps_in_flight():
    """No more than `concurrency` calls should be in flight at once."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()
    release = asyncio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            # First wave holds, until test releases them all at once
            await asyncio.wait_for(release.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
        async with lock:
            in_flight -= 1
        return httpx.Response(200, json={"ok": True})

    def handler(request):
        return asyncio.get_event_loop().run_until_complete(slow_handler(request))

    # MockTransport supports async handlers directly
    async def async_handler(request: httpx.Request) -> httpx.Response:
        return await slow_handler(request)

    transport = httpx.MockTransport(async_handler)
    c = ServiceTitanClient(
        app_key="k", client_id="ci", client_secret="cs", tenant_id="123",
        transport=transport,
        concurrency=3,
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )

    # Fire 10 parallel requests — semaphore should cap at 3
    async def call():
        await c.get("/crm/v2/tenant/123/customers")

    tasks = [asyncio.create_task(call()) for _ in range(10)]
    # let some pile up at the semaphore
    await asyncio.sleep(0.1)
    release.set()
    await asyncio.gather(*tasks)

    assert peak <= 3, f"peak in-flight was {peak}, expected <= 3 (semaphore cap)"
