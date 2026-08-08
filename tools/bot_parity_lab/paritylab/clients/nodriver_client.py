from __future__ import annotations

import importlib.util
import json
from contextlib import suppress
from typing import Any

import httpx

from paritylab.clients.base import (
    ClientRunConfig,
    ClientRunResult,
    build_target_url,
    new_session_id,
    result_from_finish,
)


class NodriverAdapter:
    name = "nodriver"
    family = "nodriver-chromium"
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        if importlib.util.find_spec("nodriver") is None:
            return ClientRunResult.skipped_result(self.name, self.family, "optional nodriver package is not installed")
        import nodriver as uc

        sid = new_session_id(self.name)
        target = build_target_url(config, session_id=sid, client_name=self.name, client_family=self.family)
        browser: Any = None
        payload: dict[str, Any] | None = None
        try:
            browser = await uc.start(
                headless=config.headless,
                browser_args=["--ignore-certificate-errors", "--allow-insecure-localhost"],
            )
            tab = await browser.get(target)
            with suppress(Exception):
                await tab.bypass_insecure_connection_warning()
            finish = await tab.select("#finish-button:not([disabled])", timeout=35)
            target_element = await tab.select("#interaction-target")
            input_element = await tab.select("#interaction-input")
            await target_element.mouse_move()
            await target_element.mouse_click()
            await input_element.mouse_move()
            await input_element.mouse_click()
            await input_element.send_keys("parity")
            await tab.evaluate("window.scrollTo({top: 460, behavior: 'smooth'})")
            await tab.sleep(0.35)
            await tab.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            await tab.sleep(0.45)
            await finish.mouse_move()
            await finish.mouse_click()
            await tab.sleep(3)
            raw_payload = await tab.evaluate(
                "JSON.stringify(window.__parityLabResult || null)", return_by_value=True
            )
            if isinstance(raw_payload, str):
                parsed = json.loads(raw_payload)
                if isinstance(parsed, dict):
                    payload = parsed
        except Exception as exc:
            return ClientRunResult.skipped_result(
                self.name,
                self.family,
                f"nodriver launch/run failed: {type(exc).__name__}: {exc}",
            )
        finally:
            if browser is not None:
                with suppress(Exception):
                    browser.stop()

        if payload is None:
            async with httpx.AsyncClient(
                verify=False, timeout=config.timeout_seconds
            ) as client:
                report = await client.get(
                    f"{config.base_url}/api/report/{sid}", params={"sid": sid}
                )
                report.raise_for_status()
                current = report.json().get("session", {})
                if not current.get("summary"):
                    response = await client.post(
                        f"{config.base_url}/api/finish/{sid}",
                        params={"sid": sid},
                        json={
                            "client": self.name,
                            "family": self.family,
                            "expectedFailure": config.expected_failure,
                            "gate": config.gate,
                            "metadata": {"adapter": "nodriver"},
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                else:
                    payload = {
                        "summary": current["summary"],
                        "artifact_dir": str(config.artifacts_dir / sid),
                        "finding_codes": [
                            item.get("code", "") for item in current.get("findings", [])
                        ],
                    }
        return result_from_finish(name=self.name, family=self.family, session_id=sid, payload=payload)
