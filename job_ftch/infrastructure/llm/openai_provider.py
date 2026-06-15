"""OpenAI + Instructor backed structured extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    import instructor

    _INSTRUCTOR_AVAILABLE = True
except ImportError:
    instructor = None  # type: ignore[assignment]
    _INSTRUCTOR_AVAILABLE = False

from job_ftch.application.registry import register_llm

if TYPE_CHECKING:
    from job_ftch.config import Settings

_SYSTEM_PROMPT = """Extract one job posting into the provided schema.
Use null or empty collections for unknown fields.
Prefer factual values copied or normalized from the text.
Do not invent company, location, compensation, seniority, or skills.

Classify post_type:
- job_posting: employer or recruiter is actively hiring for a specific named role
- candidate_seeking: a person is looking for work (#resume, "open to work", "ищу работу")
- announcement: meetup, webinar, conference, stream, digest, newsletter, podcast, course,
  event invite, roundup, news article, discussion thread, panel, product launch, tool release
- spam: scam, gambling, MLM, unrelated promotion

When in doubt between job_posting and announcement:
- No specific role being filled -> announcement
- Text is about an event or broadcast happening -> announcement
- Promotes a product, tool, or company news without hiring -> announcement
- Contains "вакансия", "hiring", "#job", "open position", specific salary range -> job_posting

Rate ai_relevance (relevance to AI/ML/data domain, 0.0 to 1.0):
- 0.0: completely non-technical (legal, HR, sales, operations, marketing)
- 0.3: tangentially technical (QA, project management, business analysis)
- 0.5: general software engineering without domain specification
- 0.7: data, analytics, platform, infrastructure, or ML-adjacent engineering
- 1.0: core AI/ML/NLP/CV/LLM/data science role

Rate search_relevance (0.0 to 1.0): how well this posting matches the candidate's target
roles, which are listed in square brackets at the very top of the user message.
- 1.0: the role IS one of the listed target roles (allow cross-language / synonym matches,
  e.g. "ML-инженер" matches "ML Engineer")
- 0.5: adjacent or partially overlapping role, or no target roles were provided
- 0.0: clearly a different profession than any listed role (e.g. QA/manual testing when the
  candidate seeks AI/ML/engineering roles)
Judge by the actual role, not by shared generic words like "engineer" or "automation".

Extract normalized technical skills in English lowercase when possible.
For Russian job postings, translate role names and skill names to English.
Separate responsibilities, must-have requirements, and nice-to-have requirements.
Infer language as ru or en when reasonably clear from the text."""


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
        if not _INSTRUCTOR_AVAILABLE:
            raise ImportError(
                "openai and instructor are required. Install with: pip install 'job_ftch[openai]'"
            )
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
