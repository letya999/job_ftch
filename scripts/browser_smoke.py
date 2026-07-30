#!/usr/bin/env python3
"""Verify that the managed Patchright Chromium runtime can launch and close."""

from __future__ import annotations

import asyncio

from patchright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("patchright_launch_close_ok")
