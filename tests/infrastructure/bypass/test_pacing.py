import asyncio
import time

import pytest

from job_ftch.infrastructure.bypass.pacing import DomainPacer


@pytest.fixture
def pacer():
    return DomainPacer(default_rate=10.0)


@pytest.mark.asyncio
async def test_acquire_under_limit(pacer):
    pacer.default_rate = 10.0
    domain = "example.com"

    start_time = time.monotonic()
    for _ in range(5):
        await pacer.acquire(domain)
    end_time = time.monotonic()

    # 5 requests at 10/s with 0-0.3s jitter each. Wait should be minimal beyond jitter.
    assert end_time - start_time < 2.5


@pytest.mark.asyncio
async def test_acquire_over_limit(pacer):
    pacer.default_rate = 1.0
    domain = "slow.com"

    start_time = time.monotonic()
    # First acquire is immediate
    await pacer.acquire(domain)
    # Second should take ~1s + jitter
    await pacer.acquire(domain)
    # Third should take ~1s + jitter
    await pacer.acquire(domain)
    end_time = time.monotonic()

    # 3 acquires at 1/s. 1st is 0s. 2nd is 1s. 3rd is 1s. Total wait ~ 2s + jitter.
    assert end_time - start_time >= 2.0


@pytest.mark.asyncio
async def test_rate_limit_tightens_bucket(pacer):
    domain = "banned.com"
    pacer.record_rate_limit(domain, retry_after=10.0)

    state = pacer._get_bucket_state_sync(domain)
    assert state["rate"] == 5.0
    assert state["penalty_until"] > time.monotonic()


@pytest.mark.asyncio
async def test_success_gradually_restores(pacer):
    domain = "recover.com"
    pacer.record_rate_limit(domain, retry_after=0.1)

    await asyncio.sleep(0.15)  # Wait for penalty to expire

    state = pacer._get_bucket_state_sync(domain)
    assert state["rate"] == 5.0

    pacer.record_success(domain)
    state = pacer._get_bucket_state_sync(domain)
    # Rate should move 10% towards 10.0 -> 5.0 + 0.5 = 5.5
    assert state["rate"] > 5.0


@pytest.mark.asyncio
async def test_jitter_is_added(pacer):
    pacer.default_rate = 100.0  # High rate so basic wait is 0
    domain = "jitter.com"

    waits = []
    for _ in range(10):
        wait = await pacer.acquire(domain)
        waits.append(wait)

    assert len(set(waits)) > 1  # Non-zero variance
