"""Explainable profile phrase evidence; deliberately non-blocking."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord
    from job_ftch.domain.profile import SearchProfile


def _phrases(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(value.casefold().strip() for value in values if len(value.strip()) >= 3)
    )


class LexicalEvidenceNode:
    """Record exact profile phrase matches without turning keyword overlap into a gate."""

    def __init__(self, profile: SearchProfile | Any) -> None:
        profiles = tuple(getattr(profile, "profiles", (profile,)))
        self._profiles = tuple(
            (
                candidate.profile_id,
                _phrases(
                    (
                        *candidate.target_roles,
                        *candidate.target_domains,
                        *candidate.project_types,
                        *(skill.canonical_name for skill in candidate.required_skills),
                        *(skill.canonical_name for skill in candidate.preferred_skills),
                    )
                ),
                _phrases((*candidate.anti_preferences, *candidate.blocked_domains)),
            )
            for candidate in profiles
        )

    async def process(self, item: JobRecord) -> JobRecord:
        text = "\n".join(
            part
            for part in (item.title, item.description, item.role_family, item.role_track)
            if part
        ).casefold()
        by_profile = {
            profile_id: {
                "positive": tuple(phrase for phrase in positive if phrase in text),
                "negative": tuple(phrase for phrase in negative if phrase in text),
            }
            for profile_id, positive, negative in self._profiles
        }
        positive = tuple(
            dict.fromkeys(
                phrase for matches in by_profile.values() for phrase in matches["positive"]
            )
        )
        negative = tuple(
            dict.fromkeys(
                phrase for matches in by_profile.values() for phrase in matches["negative"]
            )
        )
        return item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "lexical_positive_matches": positive,
                    "lexical_negative_matches": negative,
                    "lexical_profile_matches": by_profile,
                    "lexical_score": len(positive) - len(negative),
                }
            }
        )
