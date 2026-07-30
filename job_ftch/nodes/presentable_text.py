"""Presentable text node (LLM point ③ per ADR-019).

A same-type ``Stage[JobRecord, JobRecord]`` that calls the LLM to format the
job as a clean Markdown summary suitable for a Telegram channel post.

Cost-controlled via ``max_per_run``. When the limit is exceeded the node
returns a template-formatted fallback so the pipeline keeps running. Caches
results in ``Store.set_run_state`` keyed by ``source_record_id``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.graph.params import int_param
from job_ftch.domain import JobRecord, MatchDecision, PresentableJob

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider, Store
    from job_ftch.application.run_budget import AsyncCallBudget


logger = structlog.get_logger(__name__)


def _format_compensation(item: JobRecord) -> str | None:
    comp = item.compensation
    if comp is None:
        return None
    parts: list[str] = []
    if comp.min_amount is not None:
        parts.append(f"{comp.min_amount:,}")
    if comp.max_amount is not None:
        parts.append(f"{comp.max_amount:,}")
    if not parts:
        return str(comp.currency)
    rng = " – ".join(parts)
    return f"{rng} {comp.currency}" + (
        f" / {comp.period.value}" if comp.period and comp.period.value != "unknown" else ""
    )


def _format_location(item: JobRecord) -> str | None:
    bits: list[str] = []
    if item.city:
        bits.append(item.city)
    if item.country:
        bits.append(item.country)
    if item.work_mode and item.work_mode.value != "unknown":
        bits.append(item.work_mode.value)
    return " · ".join(bits) if bits else None


def _template_present(item: JobRecord) -> PresentableJob:
    """Fallback formatting when LLM is unavailable or limit reached."""
    title = item.title or "Job posting"
    body_lines: list[str] = [f"# {title}"]
    if item.company:
        body_lines.append(f"**Company:** {item.company}")
    if item.description:
        body_lines.append("")
        body_lines.append(item.description[:3000])
    lang = "ru" if item.language and item.language.value == "ru" else "en"
    return PresentableJob(
        title=title,
        body="\n".join(body_lines),
        salary_formatted=_format_compensation(item),
        location_formatted=_format_location(item),
        contact_section=None,
        tags=tuple(s.canonical_name for s in item.skills_explicit[:10]),
        ats_score=0.5,
        language=lang,
    )


def _build_presentable_prompt(item: JobRecord) -> str:
    """Build the compact, user-visible context needed for a vacancy card.

    ``JobRecord.metadata`` carries pipeline provenance, ontology snapshots and
    BGE vectors.  None of that helps format a Telegram post, and serialising
    the complete record here makes every presentation request unnecessarily
    large.  Keep this payload deliberately explicit.
    """
    payload = {
        "title": item.title,
        "company": item.company,
        "description": (item.description or "")[:3000],
        "location": _format_location(item),
        "compensation": _format_compensation(item),
        "employment_type": (
            item.employment_type.value if item.employment_type.value != "unknown" else None
        ),
        "seniority": item.seniority.value if item.seniority.value != "unknown" else None,
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    allowed_fields = (
        "title, body, salary_formatted, location_formatted, contact_section, "
        "tags, ats_score, language"
    )
    return (
        "Format the job for a Telegram vacancy card.\n"
        f"Return only these PresentableJob fields: {allowed_fields}.\n"
        "The JSON intentionally contains only user-visible vacancy fields; "
        "do not infer details that are absent.\n"
        "JOB:\n" + json.dumps(payload, ensure_ascii=False)
    )


class PresentableTextNode:
    """Stage[JobRecord, JobRecord] — same type, enriches with ``presentable``."""

    def __init__(
        self,
        llm: LLMProvider,
        store: Store,
        *,
        max_per_run: int = 50,
        budget: AsyncCallBudget | None = None,
        enable: bool = True,
    ) -> None:
        self._llm = llm
        self._store = store
        self._max = max_per_run
        self._budget = budget
        self._enable = enable
        self._count = 0

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "max_per_run" in params:
            self._max = int_param(params, "max_per_run", self._max)
        if "enabled" in params:
            self._enable = bool(params["enabled"])

    async def process(self, item: JobRecord) -> JobRecord | None:
        if (
            not self._enable
            or item.presentable is not None
            or item.routing_decision is MatchDecision.REJECT
        ):
            return item

        if item.metadata.get("extraction_backend") == "heuristic":
            return item.model_copy(update={"presentable": _template_present(item)})

        source_id = item.source_record_id or item.job_id or "unknown"
        cache_key = f"presentable:{source_id}"
        cached = await self._store.get_run_state(cache_key)
        if cached:
            try:
                return item.model_copy(
                    update={"presentable": PresentableJob.model_validate_json(cached)}
                )
            except Exception:
                logger.debug("presentable_cache_decode_failed", source_id=source_id, exc_info=True)

        if self._budget is not None:
            acquired = await self._budget.try_acquire()
            if not acquired:
                logger.info(
                    "presentable_fallback_template",
                    reason="budget_exhausted",
                    source_id=source_id,
                )
                return item.model_copy(update={"presentable": _template_present(item)})
        elif self._count >= self._max:
            logger.info(
                "presentable_fallback_template",
                reason="max_per_run_reached",
                source_id=source_id,
            )
            return item.model_copy(update={"presentable": _template_present(item)})

        present_fn = getattr(self._llm, "present", None)
        if not callable(present_fn):
            return item.model_copy(update={"presentable": _template_present(item)})

        try:
            result: Any = await present_fn(_build_presentable_prompt(item), PresentableJob)
        except Exception as exc:
            logger.warning("presentable_llm_failed", error=str(exc), source_id=source_id)
            return item.model_copy(update={"presentable": _template_present(item)})

        if result is None:
            return item.model_copy(update={"presentable": _template_present(item)})

        if self._budget is None:
            self._count += 1
        await self._store.set_run_state(cache_key, result.model_dump_json())
        return item.model_copy(update={"presentable": result})
