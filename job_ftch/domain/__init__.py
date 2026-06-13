"""Domain layer - pure models, zero I/O. Entities, value objects, domain rules."""

from job_ftch.domain.candidate import CandidateIdentity, CandidateProfile, CandidateResumeSnapshot
from job_ftch.domain.contracts import draft_to_record, job_to_draft, job_to_record
from job_ftch.domain.dedup import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RememberedDedupKey,
    dedup_company_for_raw_item,
    dedup_content_key_for_raw_item,
    dedup_similarity_text_for_raw_item,
    dedup_text_for_raw_item,
    dedup_title_for_raw_item,
    dedup_url_for_raw_item,
    processed_key_for_raw_item,
)
from job_ftch.domain.filter_profile import FilterProfile
from job_ftch.domain.job_quality import (
    ExtractionRejectionReason,
    JobReviewReason,
    JobValidationRejectionReason,
)
from job_ftch.domain.lineage import JobLineage, build_job_lineage
from job_ftch.domain.models import (
    CompensationRange,
    EmploymentType,
    Job,
    JobDraft,
    JobExtractionStatus,
    JobRecord,
    JobStatus,
    LanguageCode,
    MatchDecision,
    PostType,
    ProfileMatchScore,
    ProvenanceTrail,
    RawItem,
    RiskLevel,
    Seniority,
    SkillTag,
    SourceKind,
    WorkMode,
)
from job_ftch.domain.profile import (
    CompensationExpectation,
    ProfileCatalog,
    ProfileWeights,
    SearchProfile,
)
from job_ftch.domain.quarantine import QuarantinedRawItem, RawItemRejectionReason
from job_ftch.domain.rejected import RejectedItem, RejectedOutcome
from job_ftch.domain.runtime_profile import ManagedCandidateProfile
from job_ftch.domain.runtime_source import (
    RuntimeSourceRecord,
    source_spec_identifier,
    source_spec_locator,
    source_spec_name,
)
from job_ftch.domain.source_health import SourceHealth
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.domain.tenant import OutputSpec, ScheduleSpec, TenantConfig, TenantInfo
from job_ftch.domain.triage import TriageRejectionReason

from .job_group import (
    JobGroup,
    SourceAttribution,
    compute_group_id,
    compute_identity_fingerprint,
    create_job_group,
    merge_job_into_group,
    merge_jobs,
    remove_job_from_group,
)

__all__ = [
    "CompensationRange",
    "CareerSiteSpec",
    "CandidateIdentity",
    "CandidateProfile",
    "CandidateResumeSnapshot",
    "DedupKeyKind",
    "DuplicateRecord",
    "DuplicateRejectionReason",
    "CompensationExpectation",
    "ExtractionRejectionReason",
    "EmploymentType",
    "FilterProfile",
    "Job",
    "JobDraft",
    "JobExtractionStatus",
    "JobGroup",
    "JobLineage",
    "JobRecord",
    "JobReviewReason",
    "JobValidationRejectionReason",
    "JobStatus",
    "LanguageCode",
    "MatchDecision",
    "ManagedCandidateProfile",
    "PostType",
    "OutputSpec",
    "ProfileCatalog",
    "ProfileMatchScore",
    "ProvenanceTrail",
    "ProfileWeights",
    "QuarantinedRawItem",
    "RawItem",
    "RawItemRejectionReason",
    "RejectedItem",
    "RejectedOutcome",
    "RememberedDedupKey",
    "RiskLevel",
    "RuntimeSourceRecord",
    "ScheduleSpec",
    "SearchProfile",
    "Seniority",
    "SkillTag",
    "SourceAttribution",
    "SourceHealth",
    "SourceKind",
    "TenantConfig",
    "TenantInfo",
    "TriageRejectionReason",
    "WorkMode",
    "compute_group_id",
    "compute_identity_fingerprint",
    "draft_to_record",
    "create_job_group",
    "merge_job_into_group",
    "remove_job_from_group",
    "dedup_company_for_raw_item",
    "dedup_content_key_for_raw_item",
    "dedup_similarity_text_for_raw_item",
    "dedup_text_for_raw_item",
    "dedup_title_for_raw_item",
    "dedup_url_for_raw_item",
    "merge_jobs",
    "processed_key_for_raw_item",
    "job_to_draft",
    "job_to_record",
    "build_job_lineage",
    "source_spec_identifier",
    "source_spec_locator",
    "source_spec_name",
]
