from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from paritylab.clients.base import (
    ClientRunConfig,
    ClientRunResult,
    new_session_id,
    result_from_finish,
)


class RawHttpxAdapter:
    name = "raw-httpx"
    family = "httpx"
    default_expected_failure = True

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        sid = new_session_id(self.name)
        expected = config.expected_failure
        params = {
            "sid": sid,
            "client": self.name,
            "family": self.family,
            "expected_failure": "1" if expected else "0",
            "gate": "1" if config.gate else "0",
            "baseline_profile": config.baseline_profile,
        }
        async with httpx.AsyncClient(
            verify=False,
            http2=True,
            follow_redirects=True,
            timeout=config.timeout_seconds,
        ) as client:
            await client.get(f"{config.base_url}/", params=params)
            await client.get(f"{config.base_url}/api/cookie/set", params={"sid": sid})
            await client.get(f"{config.base_url}/api/cookie/echo", params={"sid": sid})
            await client.get(f"{config.base_url}/api/fetch", params={"sid": sid})
            await client.get(f"{config.base_url}/api/redirect/start", params={"sid": sid})
            response = await client.post(
                f"{config.base_url}/api/finish/{sid}",
                params={"sid": sid},
                json={
                    "client": self.name,
                    "family": self.family,
                    "expectedFailure": expected,
                    "gate": config.gate,
                    "metadata": {
                        "adapter": "httpx",
                        "negativeControl": expected,
                        "baseline_profile": config.baseline_profile,
                    },
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        return result_from_finish(name=self.name, family=self.family, session_id=sid, payload=payload)


class BrowserishHttpxAdapter:
    """Headers-only mimic negative control: browser headers without a JS runtime."""

    name = "browserish-httpx"
    family = "httpx-header-mimic"
    default_expected_failure = True

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        sid = new_session_id(self.name)
        expected = config.expected_failure
        headers = [
            ("sec-ch-ua", '"Chromium";v="151", "Not.A/Brand";v="24"'),
            ("sec-ch-ua-mobile", "?0"),
            ("sec-ch-ua-platform", '"Windows"'),
            ("upgrade-insecure-requests", "1"),
            ("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
            ("accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"),
            ("sec-fetch-site", "none"),
            ("sec-fetch-mode", "navigate"),
            ("sec-fetch-user", "?1"),
            ("sec-fetch-dest", "document"),
            ("accept-encoding", "gzip, deflate, br, zstd"),
            ("accept-language", "en-US,en;q=0.9"),
        ]
        params = {
            "sid": sid,
            "client": self.name,
            "family": self.family,
            "expected_failure": "1" if expected else "0",
            "gate": "1" if config.gate else "0",
            "baseline_profile": config.baseline_profile,
        }
        async with httpx.AsyncClient(
            verify=False,
            http2=True,
            follow_redirects=True,
            timeout=config.timeout_seconds,
            headers=headers,
        ) as client:
            await client.get(f"{config.base_url}/", params=params)
            for path in (
                "/static/style.css", "/static/probe.js", "/static/pixel.svg", "/favicon.ico",
                "/api/cookie/set", "/api/cookie/echo", "/api/fetch", "/api/cacheable",
                "/api/cacheable", "/api/redirect/start", "/api/beacon",
            ):
                if path == "/api/beacon":
                    await client.post(f"{config.base_url}{path}", params={"sid": sid}, content=b"headers-only")
                else:
                    await client.get(f"{config.base_url}{path}", params={"sid": sid})
            response = await client.post(
                f"{config.base_url}/api/finish/{sid}",
                params={"sid": sid},
                json={
                    "client": self.name,
                    "family": self.family,
                    "expectedFailure": expected,
                    "gate": config.gate,
                    "metadata": {
                        "adapter": "httpx",
                        "browserHeadersOnly": True,
                        "baseline_profile": config.baseline_profile,
                    },
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        return result_from_finish(name=self.name, family=self.family, session_id=sid, payload=payload)


class CurlAdapter:
    """Real curl/libcurl negative control, including its own TLS stack."""

    name = "curl"
    family = "curl-libcurl"
    default_expected_failure = True

    async def _run_command(self, *arguments: str, timeout: float) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return int(process.returncode or 0), stdout, stderr

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        executable = shutil.which("curl")
        if executable is None:
            return ClientRunResult.skipped_result(self.name, self.family, "curl executable is not installed")
        version_code, version_out, _ = await self._run_command(executable, "--version", timeout=5.0)
        version_text = version_out.decode("utf-8", errors="replace").splitlines()[0] if version_code == 0 else "unknown"
        supports_h2 = "HTTP2" in version_out.decode("utf-8", errors="replace").upper()
        sid = new_session_id(self.name)
        expected = config.expected_failure
        query = (
            f"sid={sid}&client={self.name}&family={self.family}"
            f"&expected_failure={1 if expected else 0}&gate={1 if config.gate else 0}"
            f"&baseline_profile={config.baseline_profile}"
        )
        protocol_args = ["--http2"] if supports_h2 else []
        with tempfile.TemporaryDirectory(prefix="paritylab-curl-") as temporary:
            cookie_jar = str(Path(temporary) / "cookies.txt")
            urls = [
                f"{config.base_url}/?{query}",
                f"{config.base_url}/api/cookie/set?sid={sid}",
                f"{config.base_url}/api/cookie/echo?sid={sid}",
                f"{config.base_url}/api/fetch?sid={sid}",
                f"{config.base_url}/api/redirect/start?sid={sid}",
            ]
            command = [
                executable,
                "--silent",
                "--show-error",
                "--insecure",
                "--location",
                "--cookie-jar",
                cookie_jar,
                "--cookie",
                cookie_jar,
                "--output",
                str(Path(temporary) / "response.bin"),
                *protocol_args,
                *urls,
            ]
            code, _, stderr = await self._run_command(*command, timeout=config.timeout_seconds)
            if code != 0:
                return ClientRunResult.skipped_result(
                    self.name,
                    self.family,
                    f"curl request failed ({code}): {stderr.decode('utf-8', errors='replace')[:500]}",
                )
            finish_payload = json.dumps(
                {
                    "client": self.name,
                    "family": self.family,
                    "expectedFailure": expected,
                    "gate": config.gate,
                    "metadata": {"adapter": "curl", "version": version_text, "http2": supports_h2},
                },
                separators=(",", ":"),
            )
            finish_command = [
                executable,
                "--silent",
                "--show-error",
                "--insecure",
                *protocol_args,
                "--header",
                "content-type: application/json",
                "--data-binary",
                finish_payload,
                f"{config.base_url}/api/finish/{sid}?sid={sid}",
            ]
            code, stdout, stderr = await self._run_command(*finish_command, timeout=config.timeout_seconds)
            if code != 0:
                return ClientRunResult.skipped_result(
                    self.name,
                    self.family,
                    f"curl finalize failed ({code}): {stderr.decode('utf-8', errors='replace')[:500]}",
                )
        try:
            payload: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return ClientRunResult.skipped_result(self.name, self.family, f"curl finalize returned invalid JSON: {exc}")
        return result_from_finish(name=self.name, family=self.family, session_id=sid, payload=payload)
