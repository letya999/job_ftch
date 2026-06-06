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
from domain.models import CompensationRange, Job, RawItem, SourceKind, WorkMode
from domain.quarantine import QuarantinedRawItem, RawItemRejectionReason
from domain.triage import TriageRejectionReason

__all__ = [
    "CompensationRange",
    "DedupKeyKind",
    "DuplicateRecord",
    "DuplicateRejectionReason",
    "Job",
    "QuarantinedRawItem",
    "RawItem",
    "RawItemRejectionReason",
    "RememberedDedupKey",
    "SourceKind",
    "TriageRejectionReason",
    "WorkMode",
    "dedup_company_for_raw_item",
    "dedup_content_key_for_raw_item",
    "dedup_similarity_text_for_raw_item",
    "dedup_text_for_raw_item",
    "dedup_title_for_raw_item",
    "dedup_url_for_raw_item",
    "processed_key_for_raw_item",
]
