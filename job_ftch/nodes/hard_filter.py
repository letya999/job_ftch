"""Hard gate after post-type classification."""

from __future__ import annotations

import re
from hashlib import sha256

from opentelemetry import trace

from job_ftch.domain import (
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    PostType,
    RawItem,
    source_identity_for_raw_item,
)
from job_ftch.domain.profile import ProfileCatalog  # noqa: TC001

_tracer = trace.get_tracer("job_ftch.nodes")
_AI_ROLE_SIGNAL_RE = re.compile(
    r"\b(ai|ml|llm|rag|genai|gpt)\b.{0,40}\b(engineer|developer|architect|lead)\b|"
    r"\b(engineer|developer|architect|lead)\b.{0,40}\b(ai|ml|llm|rag|genai|gpt)\b|"
    r"(ai[/ -]?ml[-\s]?инженер|ai[-\s]?архитектор|ии[-\s]?архитектор|"
    r"llm[-\s]?инженер|rag[-\s]?инженер|ai[-\s]?инженер)",
    re.IGNORECASE,
)
_JOB_STRUCTURE_RE = re.compile(
    r"(обязанности|требования|чем предстоит заниматься|мы ожидаем|"
    r"с нами ты будешь|для нас ценно|стек|responsibilities|requirements|salary|"
    r"заработная плата|удаленка|удаленно|full[-\s]?time)",
    re.IGNORECASE,
)


def _looks_like_ai_job_despite_announcement(text: str) -> bool:
    return bool(_AI_ROLE_SIGNAL_RE.search(text) and _JOB_STRUCTURE_RE.search(text))


class HardFilterNode:
    """Attach hard-constraint evidence without early relevance destruction.

    The language gate is intentionally permissive: if the user has not
    populated ``allowed_languages`` on any profile (the default — the
    bot only fills this when an LLM-driven PDF resume extraction
    succeeds), the pipeline accepts *all* languages. Otherwise the
    filter honours the explicit allow-list.
    """

    def __init__(self, catalog: ProfileCatalog) -> None:
        self._catalog = catalog

    async def process(self, item: RawItem) -> RawItem | None:
        with _tracer.start_as_current_span("hard_filter.check") as span:
            span.set_attribute("job_ftch.node", "HardFilterNode")
            metadata = dict(item.metadata)
            evidence: list[str] = []
            post_type = metadata.get("preclassified_post_type", PostType.UNKNOWN.value)
            if post_type in {
                PostType.CANDIDATE_SEEKING.value,
                PostType.ANNOUNCEMENT.value,
                PostType.SPAM.value,
            }:
                if (
                    post_type == PostType.ANNOUNCEMENT.value
                    and _looks_like_ai_job_despite_announcement(item.text)
                ):
                    span.set_attribute("job_ftch.hard_filter.override", "ai_job_signal")
                    metadata["hard_filter_override"] = "ai_job_signal"
                    span.set_attribute("job_ftch.node.result", "pass_override")
                    evidence.append("announcement_overridden_by_job_signal")
                else:
                    evidence.append(f"post_type:{post_type}")

            language = str(metadata.get("detected_language", "unknown"))
            if not self._language_allowed(language):
                evidence.append(f"language_not_allowed:{language}")

            lowered = item.text.casefold()
            for profile in self._catalog.profiles:
                blocked_company = next(
                    (
                        company
                        for company in profile.blocked_companies
                        if company.casefold() in lowered
                    ),
                    None,
                )
                if blocked_company is not None:
                    evidence.append(f"blocked_company:{blocked_company}")

            if evidence:
                metadata["hard_filter_evidence"] = tuple(evidence)
                metadata["early_triage_state"] = "uncertain"
                identity = source_identity_for_raw_item(item)
                metadata["evidence_atoms"] = [
                    *metadata.get("evidence_atoms", []),
                    *[
                        EvidenceAtom(
                            evidence_id=(
                                f"{item.stable_id}:hard:{sha256(reason.encode()).hexdigest()[:16]}"
                            ),
                            claim=ClaimKind.HARD_CONSTRAINT,
                            subject="profile_constraints",
                            polarity=EvidencePolarity.CONTRADICTS,
                            strength=1.0,
                            reliability=0.9,
                            provenance=EvidenceProvenance.INFERRED,
                            producer="hard_filter",
                            producer_version="hard-filter-v2",
                            source_family=identity.family,
                            observation_kind=identity.observation_kind,
                            transport=identity.transport,
                            independence_key=f"{item.stable_id}:hard:{reason}",
                            observation_id=item.stable_id,
                            candidate_id=str(metadata.get("candidate_span_id") or item.stable_id),
                            evidence_ref=f"raw:hard_constraint:{reason}",
                        ).model_dump(mode="json")
                        for reason in evidence
                    ],
                ]
                span.set_attribute("job_ftch.node.result", "evidence")
                return item.model_copy(update={"metadata": metadata})
            span.set_attribute("job_ftch.node.result", "pass")
            return item

    def _language_allowed(self, language: str) -> bool:
        """Return True if the detected language is acceptable.

        The behaviour is "permissive by default": if no profile declares
        ``allowed_languages`` we accept everything (including the
        common case where the bot's text-only ``/positive`` path does
        not yet populate that field). The pipeline still honours an
        explicit allow-list when one is set.
        """
        allowed: set[str] = set()
        for profile in self._catalog.profiles:
            for lang in profile.allowed_languages:
                allowed.add(lang.value)
        if not allowed:
            return True
        if language in allowed:
            return True
        # Be lenient: unknown language is always allowed (the detector
        # may be unsure for short or mixed-language posts).
        return language == "unknown"
