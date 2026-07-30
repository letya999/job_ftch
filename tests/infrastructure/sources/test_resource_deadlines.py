from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from job_ftch.infrastructure.bypass.captcha_solver import CaptchaSolverBypass
from job_ftch.infrastructure.sources.browser_utils import open_page
from job_ftch.infrastructure.sources.source_deadline import source_deadline_scope


class _SlowBrowserBypass:
    cleaned = False

    async def apply_http(self, client):
        return client

    def apply_browser_args(self, kwargs):
        return kwargs

    async def apply_page(self, page) -> None:
        del page

    @asynccontextmanager
    async def open_page(self, config, *, use_proxy: bool = False):
        del config, use_proxy
        try:
            await asyncio.sleep(60)
            yield object()
        finally:
            self.cleaned = True


@pytest.mark.asyncio
async def test_source_deadline_cancels_browser_launch_and_runs_cleanup() -> None:
    bypass = _SlowBrowserBypass()
    # Leave enough scheduling headroom for the session context to enter, then
    # verify that deadline cancellation unwinds its cleanup path.
    async with source_deadline_scope(asyncio.get_running_loop().time() + 1.0):
        with pytest.raises(TimeoutError):
            async with open_page({}, bypass_strategy=bypass):
                pytest.fail("slow browser must not open after the source deadline")
    assert bypass.cleaned


class _PollingPage:
    url = "https://example.test/jobs"

    async def evaluate(self, script: str):
        if script == "document.readyState":
            return "loading"
        return ""


@pytest.mark.asyncio
async def test_source_deadline_cancels_solver_polling_without_child_tasks() -> None:
    solver = CaptchaSolverBypass(wait_seconds=30)
    before = set(asyncio.all_tasks())
    async with source_deadline_scope(asyncio.get_running_loop().time() + 0.01):
        with pytest.raises(TimeoutError):
            await solver.solve(_PollingPage(), challenge_type="cloudflare")
    await asyncio.sleep(0)
    leaked = [task for task in asyncio.all_tasks() - before if not task.done()]
    assert leaked == []
