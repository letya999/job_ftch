from __future__ import annotations

import importlib
import os
from typing import Any

import httpx

from paritylab.clients.base import (
    ClientHookContext,
    ClientRunConfig,
    ClientRunResult,
    build_target_url,
    maybe_await,
    new_session_id,
    result_from_finish,
)


class HookAdapter:
    name = "project-browser-hook"
    family = "owned-browser-client"
    default_expected_failure = False

    async def run(self, config: ClientRunConfig) -> ClientRunResult:
        dotted = os.environ.get("PARITYLAB_CLIENT_HOOK", "")
        if not dotted or ":" not in dotted:
            return ClientRunResult.skipped_result(
                self.name,
                self.family,
                "set PARITYLAB_CLIENT_HOOK=module:function to run the owned browser client",
            )
        module_name, function_name = dotted.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            hook = getattr(module, function_name)
        except Exception as exc:
            return ClientRunResult.skipped_result(self.name, self.family, f"cannot import hook: {exc}")

        sid = new_session_id(self.name)
        target = build_target_url(config, session_id=sid, client_name=self.name, client_family=self.family)
        context = ClientHookContext(
            url=target,
            finish_url=f"{config.base_url}/api/finish/{sid}?sid={sid}",
            session_id=sid,
            client_name=self.name,
            client_family=self.family,
            artifacts_dir=config.artifacts_dir / sid,
            gate=config.gate,
            expected_failure=config.expected_failure,
            timeout_seconds=config.timeout_seconds,
        )
        try:
            metadata = await maybe_await(hook(context))
        except Exception as exc:
            return ClientRunResult.skipped_result(self.name, self.family, f"hook raised: {type(exc).__name__}: {exc}")
        async with httpx.AsyncClient(verify=False, timeout=config.timeout_seconds) as client:
            response = await client.post(
                context.finish_url,
                json={
                    "client": self.name,
                    "family": self.family,
                    "expectedFailure": config.expected_failure,
                    "gate": config.gate,
                    "metadata": metadata if isinstance(metadata, dict) else {"hookResult": str(metadata)},
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        return result_from_finish(name=self.name, family=self.family, session_id=sid, payload=payload)
