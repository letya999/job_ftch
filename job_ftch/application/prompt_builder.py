"""Dynamic relevance prompt generation from profile shots (BR-1).

Replaces offline scripts/gen_relevance_prompt.py with in-pipeline dynamic generation.
Resume shots = PRIMARY, vacancy shots = SECONDARY per BR-2/BR-3.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider, Store
    from job_ftch.domain.profile import ProfileCatalog, SearchProfile

logger = structlog.get_logger(__name__)

_PROMPT_GEN_SYSTEM = """You are an expert recruitment system prompt engineer.
Your task is to analyze a candidate's profile and job examples to write a decision prompt.
This prompt will be given to another LLM to filter job postings for the candidate.

REQUIREMENTS:
1. Plain instructional text only — no meta-commentary, no explanations.
2. Derive the accept/reject boundary EXCLUSIVELY from the provided examples.
3. NEVER reject a job by title alone — judge by the STATED RESPONSIBILITIES.
4. NEVER introduce categories or signals not present in the examples.
5. NO hardcoded company names, title blocklists, or nationality restrictions.
6. Be concise — 5-8 bullet rules maximum.
7. When resume and vacancy examples conflict, prefer the vacancy examples (they reflect actual job choices).
8. Focus on WHAT the candidate will BUILD/DO, not what technologies they know.
9. State the domain boundary explicitly. The judge that consumes this brief knows nothing
   about this field, so name the core work that makes a role target, and name the closest
   neighbouring work that must stay adjacent. Both must come from the examples.

OUTPUT FORMAT:
Start with a 1-sentence profile description of the target role.
Then "CORE WORK:" one or two sentences naming the concrete work that qualifies a role as
target in this domain, and the nearest adjacent work that does not.
Then "ACCEPT:" followed by bullet rules for when to accept.
Then "REJECT:" followed by bullet rules for when to reject.
Then "LOOK FOR:" bullet signals that indicate the role is relevant.
Then "AVOID:" bullet signals that indicate the role should be rejected."""

_MAX_SHOT_CHARS = 3000


class DecisionProfileBrief(BaseModel):
    """Immutable, versioned profile context used by relevance judges."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1)
    input_hash: str = Field(min_length=16)
    rubric_version: str = Field(min_length=1)
    compiler_model: str = Field(min_length=1)
    ontology_snapshot_hash: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=12000)


def _shots_hash(profile: SearchProfile) -> str:
    """Hash every input used to build the cached profile brief."""
    items = {
        "description": profile.profile_description,
        "target_roles": profile.target_roles,
        "target_domains": profile.target_domains,
        "target_industries": profile.target_industries,
        "hard_requirements": profile.hard_requirements,
        "soft_preferences": profile.soft_preferences,
        "anti_preferences": profile.anti_preferences,
        "required_skills": tuple(skill.canonical_name for skill in profile.required_skills),
        "preferred_skills": tuple(skill.canonical_name for skill in profile.preferred_skills),
        "positive_resume": profile.positive_example_texts,
        "negative_resume": profile.negative_example_texts,
        "positive_jobs": profile.positive_job_example_texts,
        "negative_jobs": profile.negative_job_example_texts,
    }
    digest = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]
    return digest


def _brief_input_hash(
    profile: SearchProfile,
    *,
    ontology_snapshot_hash: str,
    rubric_version: str,
    compiler_model: str,
) -> str:
    payload = {
        "profile": _shots_hash(profile),
        "ontology_snapshot_hash": ontology_snapshot_hash,
        "rubric_version": rubric_version,
        "compiler_model": compiler_model,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class DecisionProfileBriefCompiler:
    """Cache one profile brief instead of replaying raw shots per vacancy."""

    def __init__(
        self,
        llm: LLMProvider,
        store: Store,
        *,
        # v2 adds the mandatory CORE WORK section. Bumping the version invalidates every
        # cached brief so a stale v1 brief cannot leave the judge without a domain boundary
        # now that the static prompt no longer carries one.
        rubric_version: str = "decision-brief-v2",
        compiler_model: str | None = None,
    ) -> None:
        self._llm = llm
        self._store = store
        self._rubric_version = rubric_version
        self._compiler_model = compiler_model or type(llm).__name__

    async def compile(
        self,
        profile: SearchProfile,
        *,
        ontology_snapshot_hash: str = "none",
    ) -> DecisionProfileBrief | None:
        input_hash = _brief_input_hash(
            profile,
            ontology_snapshot_hash=ontology_snapshot_hash,
            rubric_version=self._rubric_version,
            compiler_model=self._compiler_model,
        )
        cache_key = f"relevance:brief:{profile.profile_id}:{input_hash}"
        cached = await self._store.get_run_state(cache_key)
        if cached:
            try:
                return DecisionProfileBrief.model_validate_json(cached)
            except ValueError:
                pass
        text = await build_relevance_prompt_from_profile(
            profile,
            self._llm,
            self._store,
            profile_id=profile.profile_id,
            cache_namespace=input_hash,
        )
        if not text:
            return None
        brief = DecisionProfileBrief(
            profile_id=profile.profile_id,
            input_hash=input_hash,
            rubric_version=self._rubric_version,
            compiler_model=self._compiler_model,
            ontology_snapshot_hash=ontology_snapshot_hash,
            text=text[:12000],
        )
        await self._store.set_run_state(cache_key, brief.model_dump_json())
        return brief


async def build_relevance_prompt_from_profile(
    profile: SearchProfile,
    llm: LLMProvider,
    store: Store,
    *,
    profile_id: str = "default",
    cache_namespace: str = "",
) -> str | None:
    """Generate or retrieve cached relevance prompt for a profile.

    BR-1: Dynamic generation from shots in DB on each pipeline run.
    BR-2: Resume shots = PRIMARY (first in prompt).
    BR-3: Vacancy shots = SECONDARY (supplement resume context).

    Cache key = "relevance:prompt:{profile_id}", cached value includes
    a hash of the shots. If shots changed, prompt is regenerated.
    """
    cache_key = f"relevance:prompt:{profile_id}:{cache_namespace or 'profile'}"
    cached = await store.get_run_state(cache_key)

    current_hash = _shots_hash(profile)

    if cached:
        try:
            cached_parts = cached.split("|", 1)
            if len(cached_parts) == 2:
                cached_hash, cached_prompt = cached_parts
                if cached_hash == current_hash:
                    logger.debug(
                        "relevance_prompt_cached", profile_id=profile_id, hash=current_hash
                    )
                    return cached_prompt
        except Exception:
            pass

    pos_resume = list(profile.positive_example_texts)
    neg_resume = list(profile.negative_example_texts)
    pos_vacancy = list(profile.positive_job_example_texts)
    neg_vacancy = list(profile.negative_job_example_texts)

    if not (pos_resume or pos_vacancy):
        logger.debug("relevance_prompt_no_shots", profile_id=profile_id)
        return None

    user_prompt_parts: list[str] = []

    structured = (
        ("TARGET ROLES", profile.target_roles),
        ("TARGET DOMAINS", profile.target_domains),
        ("TARGET INDUSTRIES", profile.target_industries),
        ("HARD REQUIREMENTS", profile.hard_requirements),
        ("SOFT PREFERENCES", profile.soft_preferences),
        ("ANTI-PREFERENCES", profile.anti_preferences),
        ("REQUIRED SKILLS", tuple(skill.canonical_name for skill in profile.required_skills)),
        ("PREFERRED SKILLS", tuple(skill.canonical_name for skill in profile.preferred_skills)),
    )
    if profile.profile_description:
        user_prompt_parts.append(
            f"## EXISTING PROFILE DESCRIPTION\n{profile.profile_description}\n"
        )
    for heading, values in structured:
        if values:
            user_prompt_parts.append(
                f"## {heading}\n" + "\n".join(f"- {value}" for value in values)
            )

    if pos_resume:
        user_prompt_parts.append("## WHAT THE CANDIDATE IS (resume — PRIMARY)\n")
        for r in pos_resume:
            user_prompt_parts.append(f"---\n{r[:_MAX_SHOT_CHARS]}\n")

    if pos_vacancy:
        user_prompt_parts.append(
            "\n## JOBS THE CANDIDATE ACCEPTED (vacancy examples — SECONDARY)\n"
        )
        for v in pos_vacancy:
            user_prompt_parts.append(f"---\n{v[:_MAX_SHOT_CHARS]}\n")

    if neg_vacancy:
        user_prompt_parts.append("\n## JOBS THE CANDIDATE REJECTED (vacancy examples)\n")
        for v in neg_vacancy:
            user_prompt_parts.append(f"---\n{v[:_MAX_SHOT_CHARS]}\n")

    if neg_resume:
        user_prompt_parts.append("\n## NOT THE CANDIDATE (negative resume examples)\n")
        for r in neg_resume:
            user_prompt_parts.append(f"---\n{r[:_MAX_SHOT_CHARS]}\n")

    user_prompt = "\n".join(user_prompt_parts)

    try:
        if not hasattr(llm, "generate_text") or not callable(getattr(llm, "generate_text", None)):
            logger.warning("relevance_prompt_llm_no_generate_text", profile_id=profile_id)
            return None

        generated = await llm.generate_text(
            system_prompt=_PROMPT_GEN_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.2,
        )

        cached_value = f"{current_hash}|{generated}"
        await store.set_run_state(cache_key, cached_value)
        logger.info("relevance_prompt_generated", profile_id=profile_id, hash=current_hash)
        return generated

    except Exception as exc:
        logger.warning("relevance_prompt_generation_failed", profile_id=profile_id, error=str(exc))
        return None


async def build_relevance_prompts_for_catalog(
    catalog: ProfileCatalog,
    llm: LLMProvider,
    store: Store,
) -> dict[str, str | None]:
    """Generate relevance prompts for all profiles in a catalog.

    Returns dict mapping profile_id -> generated_prompt (or None).
    """
    compiler = DecisionProfileBriefCompiler(llm, store)
    results: dict[str, str | None] = {}
    for profile in catalog.profiles:
        brief = await compiler.compile(profile)
        results[profile.profile_id] = brief.text if brief is not None else None
    return results
