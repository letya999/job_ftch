"""Domain layer - pure models, zero I/O. Entities, value objects, domain rules."""

from domain.dedup import (
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
from domain.filter_profile import FilterProfile
from domain.job_group import (
    JobGroup,
    SourceAttribution,
    compute_group_id,
    compute_identity_fingerprint,
    merge_jobs,
)
from domain.job_quality import (
    ExtractionRejectionReason,
    JobReviewReason,
    JobValidationRejectionReason,
)
from domain.models import CompensationRange, Job, JobExtractionStatus, RawItem, SourceKind, WorkMode
from domain.quarantine import QuarantinedRawItem, RawItemRejectionReason
from domain.rejected import RejectedItem, RejectedOutcome
from domain.triage import TriageRejectionReason

__all__ = [
    "CompensationRange",
    "DedupKeyKind",
    "DuplicateRecord",
    "DuplicateRejectionReason",
    "ExtractionRejectionReason",
    "FilterProfile",
    "Job",
    "JobExtractionStatus",
    "JobGroup",
    "JobReviewReason",
    "JobValidationRejectionReason",
    "QuarantinedRawItem",
    "RawItem",
    "RawItemRejectionReason",
    "RejectedItem",
    "RejectedOutcome",
    "RememberedDedupKey",
    "SourceAttribution",
    "SourceKind",
    "TriageRejectionReason",
    "WorkMode",
    "compute_group_id",
    "compute_identity_fingerprint",
    "dedup_company_for_raw_item",
    "dedup_content_key_for_raw_item",
    "dedup_similarity_text_for_raw_item",
    "dedup_text_for_raw_item",
    "dedup_title_for_raw_item",
    "dedup_url_for_raw_item",
    "merge_jobs",
    "processed_key_for_raw_item",
]
