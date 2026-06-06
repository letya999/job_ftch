"""OpenAI + Instructor backed structured extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import instructor

from application.registry import register_llm

if TYPE_CHECKING:
    from config import Settings

_SYSTEM_PROMPT = """Extract one job posting into the provided schema.
Use null for unknown fields.
Prefer factual values copied or normalized from the text.
Do not invent company, location, or compensation."""


class OpenAIInstructorLLMProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._client = instructor.from_provider(
            f"openai/{model}",
            async_client=True,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def extract(self, text: str, schema: type[Any]) -> Any:
        return await self._client.create(
            model=self._model,
            response_model=schema,
            max_retries=self._max_retries,
            strict=True,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )


@register_llm("openai")
def _build_openai_llm(settings: Settings) -> OpenAIInstructorLLMProvider:
    if settings.openai_api_key is None:
        msg = "openai_api_key is required when llm_backend=openai."
        raise ValueError(msg)
    return OpenAIInstructorLLMProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
