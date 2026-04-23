"""Tests for the async token-bucket rate limiter."""

import asyncio
import time

import pytest

from servicetitan_mcp.client import TokenBucket


async def test_token_bucket_starts_full_and_allows_burst():
    """A fresh bucket with capacity 5 should allow 5 immediate acquires."""
    bucket = TokenBucket(rate=5, capacity=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"5 acquires on full bucket took {elapsed:.3f}s, expected near-instant"


async def test_token_bucket_throttles_when_empty():
    """Once drained, the next acquire must wait roughly 1/rate seconds."""
    bucket = TokenBucket(rate=10, capacity=2)
    await bucket.acquire()
    await bucket.acquire()
    start = time.monotonic()
    await bucket.acquire()
    elapsed = time.monotonic() - start
    # Third acquire had to wait for refill — expect ~0.1s at rate=10
    assert 0.07 <= elapsed <= 0.25, f"expected ~0.1s wait, got {elapsed:.3f}s"


async def test_token_bucket_refills_over_time():
    """Waiting replenishes tokens up to capacity."""
    bucket = TokenBucket(rate=20, capacity=3)
    # drain
    for _ in range(3):
        await bucket.acquire()
    # wait long enough to fully refill
    await asyncio.sleep(0.3)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"refilled bucket took {elapsed:.3f}s, should be instant"
