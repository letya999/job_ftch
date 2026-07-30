"""Source-policy hints for career-site style sources.

These hints do not block ingest on their own. They classify source families so
runtime, assessment, and downstream review can distinguish classic job boards
from freelance marketplaces or service classifieds.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SourcePolicyHint:
    family: str
    policy_name: str
    allows_generic_job_pipeline: bool
    rationale: str


_POLICY_HINTS: tuple[tuple[tuple[str, ...], SourcePolicyHint], ...] = (
    (
        ("weblancer.net", "fl.ru", "kadrof.ru"),
        SourcePolicyHint(
            family="freelance_board",
            policy_name="freelance_board",
            allows_generic_job_pipeline=False,
            rationale="Freelance and project boards should be reviewed separately from classic vacancy boards.",
        ),
    ),
    (
        ("naimi.kz", "lalafo.kg"),
        SourcePolicyHint(
            family="service_marketplace",
            policy_name="service_marketplace",
            allows_generic_job_pipeline=False,
            rationale="Service marketplaces and classifieds are not stable vacancy inventories.",
        ),
    ),
)


def resolve_source_policy(url: str) -> SourcePolicyHint:
    host = (urlsplit(url).hostname or "").casefold()
    for host_tokens, hint in _POLICY_HINTS:
        if any(host == token or host.endswith(f".{token}") for token in host_tokens):
            return hint
    return SourcePolicyHint(
        family="career_site",
        policy_name="career_site",
        allows_generic_job_pipeline=True,
        rationale="Classic career-site or job-board source.",
    )


def source_policy_metadata(url: str) -> dict[str, str | bool]:
    hint = resolve_source_policy(url)
    return {
        "source_policy": hint.policy_name,
        "source_family": hint.family,
        "allows_generic_job_pipeline": hint.allows_generic_job_pipeline,
    }


__all__ = ["SourcePolicyHint", "resolve_source_policy", "source_policy_metadata"]
