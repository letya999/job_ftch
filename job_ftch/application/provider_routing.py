"""Node-scoped LLM provider resolution and graceful preflight."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn, cast

from job_ftch.application.llm_quota import LLMPreflightResult, run_llm_preflight, safe_llm_error

if TYPE_CHECKING:
    from job_ftch.config import LLMProviderProfile, Settings


class UnavailableLLMProvider:
    """A live-safe provider placeholder; construction never kills the service."""

    def __init__(self, *, provider: str, model: str, error: str) -> None:
        self.provider_id = provider
        self.model_id = model
        self._error = error

    async def preflight(self) -> LLMPreflightResult:
        return LLMPreflightResult(available=False, error=self._error, model=self.model_id)

    def _raise(self) -> NoReturn:
        raise RuntimeError(f"LLM binding unavailable: {self._error}")

    async def extract(self, text: str, schema: type[Any]) -> Any:
        del text, schema
        self._raise()

    async def classify(self, prompt: str, schema: type[Any]) -> Any:
        del prompt, schema
        self._raise()

    async def present(self, job_payload: str, schema: type[Any]) -> Any:
        del job_payload, schema
        self._raise()

    async def generate_text(
        self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2
    ) -> str:
        del system_prompt, user_prompt, temperature
        self._raise()


@dataclass(frozen=True, slots=True)
class ResolvedLLMBinding:
    name: str
    requested_provider: str
    requested_model: str
    resolved_provider: str
    resolved_model: str
    capability: str
    switched: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "binding": self.name,
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "resolved_provider": self.resolved_provider,
            "resolved_model": self.resolved_model,
            "capability": self.capability,
            "switched": self.switched,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BindingPreflight:
    binding: ResolvedLLMBinding
    provider: object
    result: LLMPreflightResult
    checked_at: datetime


def _profile_settings(
    settings: Settings,
    profile: LLMProviderProfile,
    *,
    model: str,
) -> Settings:
    credential = settings.openai_api_key
    if profile.credential_ref != "JOB_FTCH_OPENAI_API_KEY":
        raw = os.environ.get(profile.credential_ref, "").strip()
        if raw:
            from pydantic import SecretStr

            credential = SecretStr(raw)
        else:
            credential = None
    base_url = profile.base_url
    if profile is settings.llm_provider_profiles.get("cliproxy_captcha") and not base_url:
        base_url = settings.captcha_vision_base_url or None
    return settings.model_copy(
        update={
            "llm_backend": profile.backend,
            "openai_base_url": base_url,
            "openai_api_key": credential,
            "openai_model": model,
            "openai_timeout_seconds": profile.timeout_seconds,
            "openai_max_retries": profile.max_retries,
        }
    )


def _profile_for(settings: Settings, name: str) -> LLMProviderProfile:
    profile = settings.llm_provider_profiles.get(name)
    if profile is None:
        raise ValueError(f"Unknown LLM provider profile: {name}")
    # Legacy llm_backend remains readable for one migration window. New named
    # bindings still default to OpenAI; only an explicitly legacy non-OpenAI
    # settings object maps its main profile to that backend.
    if (
        name == "openai_main"
        and settings.llm_backend != "openai"
        and "llm_provider_profiles" not in settings.model_fields_set
        and "llm_bindings" not in settings.model_fields_set
    ):
        return profile.model_copy(update={"backend": settings.llm_backend})
    return profile


def build_llm_bindings(
    settings: Settings,
    *,
    names: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Build only the providers named by the graph/runtime composition root."""
    from job_ftch.application.registry import create_llm

    cache: dict[tuple[str, str], object] = {}
    result: dict[str, object] = {}
    binding_names = tuple(settings.llm_bindings) if names is None else names
    for name in binding_names:
        binding = settings.llm_bindings.get(name)
        if binding is None:
            raise ValueError(f"Missing LLM binding for graph node: {name}")
        profile = _profile_for(settings, binding.provider)
        key = (binding.provider, binding.model)
        if key not in cache:
            try:
                provider = create_llm(_profile_settings(settings, profile, model=binding.model))
                provider_obj = cast("Any", provider)
                provider_obj.provider_id = binding.provider
                provider_obj.requested_provider = binding.provider
                provider_obj.requested_model = binding.model
            except Exception as exc:  # noqa: BLE001 - provider outage is runtime degradation
                provider = UnavailableLLMProvider(
                    provider=binding.provider,
                    model=binding.model,
                    error=safe_llm_error(exc) or "provider_initialization_failed",
                )
            cache[key] = provider
        result[name] = cache[key]
    return result


async def preflight_bindings(
    settings: Settings,
    providers: dict[str, object],
    *,
    names: tuple[str, ...],
) -> tuple[dict[str, BindingPreflight], list[dict[str, object]]]:
    """Probe required graph bindings and apply only their declared fallbacks."""
    checked: dict[str, BindingPreflight] = {}
    switches: list[dict[str, object]] = []
    for name in names:
        requested = settings.llm_bindings.get(name)
        if requested is None:
            raise ValueError(f"Missing LLM binding for graph node: {name}")
        provider = providers[name]
        result = await run_llm_preflight(provider)
        resolved = ResolvedLLMBinding(
            name=name,
            requested_provider=requested.provider,
            requested_model=requested.model,
            resolved_provider=requested.provider,
            resolved_model=requested.model,
            capability=requested.capability,
            reason=result.error or None,
        )
        if not result.available:
            for fallback_provider, fallback_model in requested.fallbacks:
                profile = _profile_for(settings, fallback_provider)
                if requested.capability not in profile.capabilities:
                    continue
                from job_ftch.application.registry import create_llm

                try:
                    fallback = create_llm(
                        _profile_settings(settings, profile, model=fallback_model)
                    )
                    fallback_result = await run_llm_preflight(fallback)
                except Exception as exc:  # noqa: BLE001 - fallback is best effort
                    fallback_result = LLMPreflightResult(
                        available=False, error=safe_llm_error(exc), model=fallback_model
                    )
                    fallback = UnavailableLLMProvider(
                        provider=fallback_provider,
                        model=fallback_model,
                        error=fallback_result.error or "fallback_initialization_failed",
                    )
                fallback_obj = cast("Any", fallback)
                fallback_obj.provider_id = fallback_provider
                fallback_obj.requested_provider = requested.provider
                fallback_obj.requested_model = requested.model
                if fallback_result.available:
                    resolved = ResolvedLLMBinding(
                        name=name,
                        requested_provider=requested.provider,
                        requested_model=requested.model,
                        resolved_provider=fallback_provider,
                        resolved_model=fallback_model,
                        capability=requested.capability,
                        switched=True,
                        reason=result.error or "requested_binding_unavailable",
                    )
                    checked[name] = BindingPreflight(
                        resolved, fallback, fallback_result, datetime.now(UTC)
                    )
                    switches.append(resolved.as_dict())
                    break
            else:
                checked[name] = BindingPreflight(resolved, provider, result, datetime.now(UTC))
            continue
        checked[name] = BindingPreflight(resolved, provider, result, datetime.now(UTC))
    return checked, switches


def required_binding_names(settings: Settings) -> tuple[str, ...]:
    """Return bindings needed by the current ingest graph, excluding CAPTCHA."""
    names = ["extraction"]
    if settings.llm_relevance_max_per_run > 0:
        names.append("relevance")
    return tuple(names)
