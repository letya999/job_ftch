from pathlib import Path

from job_ftch.application.evidence_policy import load_evidence_parameters
from job_ftch.domain import ClaimKind, SourceFamily


def test_policy_is_parameterized_by_claim_and_source_family() -> None:
    params = load_evidence_parameters(Path("config/evidence_policy.yaml"))
    assert (ClaimKind.IS_JOB, SourceFamily.ATS_API) in params
    assert (
        params[(ClaimKind.IS_JOB, SourceFamily.ATS_API)].prior
        > params[(ClaimKind.IS_JOB, SourceFamily.TELEGRAM)].prior
    )
