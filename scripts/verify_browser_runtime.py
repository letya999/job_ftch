"""Smoke-check browser dependencies expected in the runtime image."""

from __future__ import annotations

import importlib
import os
from pathlib import Path


def _fail(message: str) -> None:
    raise SystemExit(message)


def _require_module(name: str) -> None:
    importlib.import_module(name)


def _require_camoufox_can_launch() -> None:
    """Launch Camoufox for real, not just import it.

    Importing proves nothing about the Firefox runtime: the image shipped a downloaded
    binary with no GTK behind it for weeks, so the tier imported cleanly and then failed
    at launch with "libgtk-3.so.0: cannot open shared object file". Only a launch catches
    a missing system library.
    """
    import asyncio

    from camoufox.async_api import AsyncCamoufox

    async def _launch() -> None:
        async with AsyncCamoufox(headless=True) as browser:  # type: ignore[no-untyped-call]
            page = await browser.new_page()
            await page.close()

    try:
        asyncio.run(_launch())
    except Exception as exc:  # noqa: BLE001 - the message carries the missing library
        _fail(f"camoufox failed to launch: {exc}")


def _require_patchright_can_launch() -> None:
    """Launch the Chromium runtime used by ordinary browser-backed sources."""
    import asyncio

    from patchright.async_api import async_playwright

    async def _launch() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await browser.close()

    try:
        asyncio.run(_launch())
    except Exception as exc:  # noqa: BLE001 - expose the missing binary/library
        _fail(f"patchright Chromium failed to launch: {exc}")


def _require_nodriver_can_launch(chromium_binary: Path) -> None:
    """Exercise sequential and concurrent nodriver/CDP startup as appuser."""
    import asyncio
    import shutil
    import tempfile

    from job_ftch.infrastructure.bypass.nodriver_bypass import NodriverBypass

    async def _launch(profile: str) -> None:
        bypass = NodriverBypass(browser_executable_path=str(chromium_binary))
        async with bypass.open_page(
            {
                "headless": True,
                "persistent_context": True,
                "_profile_dir": profile,
                "sandbox": False,
            }
        ):
            pass

    async def _exercise() -> None:
        root = tempfile.mkdtemp(prefix="nodriver_runtime_smoke_")
        try:
            sequential = str(Path(root) / "sequential")
            await _launch(sequential)
            await _launch(sequential)
            await asyncio.gather(
                _launch(str(Path(root) / "concurrent_a")),
                _launch(str(Path(root) / "concurrent_b")),
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    try:
        asyncio.run(_exercise())
    except Exception as exc:  # noqa: BLE001 - expose the browser/CDP failure
        _fail(f"nodriver Chromium failed to launch: {exc}")


def main() -> None:
    required_modules = ("patchright", "camoufox", "nodriver", "cloakbrowser")
    for module_name in required_modules:
        _require_module(module_name)

    browsers_root_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_root_env:
        browsers_root = Path(browsers_root_env)
    else:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            browsers_root = Path(local_appdata) / "ms-playwright"
        else:
            browsers_root = Path("/opt/playwright")
    if not browsers_root.exists():
        _fail(f"Browser binaries root does not exist: {browsers_root}")

    chromium_binaries = [
        *browsers_root.rglob("chrome"),
        *browsers_root.rglob("chrome.exe"),
        *browsers_root.rglob("chromium"),
    ]
    chromium_binaries = [path for path in chromium_binaries if path.is_file()]
    if not chromium_binaries:
        _fail(f"No Chromium binary found under {browsers_root}")

    import cloakbrowser

    cloak_binary = Path(cloakbrowser.ensure_binary())
    if not cloak_binary.exists():
        _fail(f"cloakbrowser ensure_binary() returned a missing path: {cloak_binary}")

    _require_patchright_can_launch()
    _require_nodriver_can_launch(chromium_binaries[0])
    _require_camoufox_can_launch()

    print("browser runtime ok")
    print(f"playwright browsers root: {browsers_root}")
    print(f"chromium binary: {chromium_binaries[0]}")
    print(f"cloakbrowser binary: {cloak_binary}")


if __name__ == "__main__":
    main()
