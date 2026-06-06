from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OutcomeKind(StrEnum):
    PASS = "pass"
    DROP = "drop"
    QUARANTINE = "quarantine"
    FAIL = "fail"


class PipelineStage(StrEnum):
    SOURCE = "source"
    SANITIZE = "sanitize"
    RAW_VALIDATE = "raw_validate"
    ORIGIN_POLICY = "origin_policy"
    TRIAGE = "triage"
    RAW_DEDUP = "raw_dedup"
    EXTRACT = "extract"
    JOB_VALIDATE = "job_validate"
    JOB_NORMALIZE = "job_normalize"
    JOB_DEDUP = "job_dedup"
    SCORE = "score"
    EMIT = "emit"


class RejectReason(StrEnum):
    EMPTY_TEXT = "empty_text"
    EMPTY_SOURCE_NAME = "empty_source_name"
    MISSING_LOCATOR = "missing_locator"
    TEXT_TOO_LONG = "text_too_long"
    INVALID_URL = "invalid_url"
    INVALID_ORIGIN_URL = "invalid_origin_url"
    DISALLOWED_URL_HOST = "disallowed_url_host"
    DISALLOWED_ORIGIN_HOST = "disallowed_origin_host"
    PRIVATE_URL_HOST = "private_url_host"
    PRIVATE_ORIGIN_HOST = "private_origin_host"
    INVALID_RAW_ITEM = "invalid_raw_item"
    SOURCE_FETCH_ERROR = "source_fetch_error"
    ALREADY_PROCESSED = "already_processed"
    NODE_DROPPED = "node_dropped"
    NODE_FAILED = "node_failed"
    SINK_EMIT_ERROR = "sink_emit_error"
    SINK_FINALIZE_ERROR = "sink_finalize_error"
    TOO_SHORT = "too_short"
    NON_JOB = "non_job"
    DUPLICATE_RAW = "duplicate_raw"
    DUPLICATE_JOB = "duplicate_job"
    EXTRACTION_FAILED = "extraction_failed"
    LOW_QUALITY = "low_quality"
    NOT_AI_RELEVANT = "not_ai_relevant"

    @classmethod
    def from_value(cls, value: str) -> RejectReason:
        try:
            return cls(value)
        except ValueError:
            return cls.INVALID_RAW_ITEM


@dataclass(frozen=True, slots=True)
class NodeOutcome[T]:
    kind: OutcomeKind
    item: T | None = None
    reason: RejectReason | None = None
    message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def pass_(cls, item: T) -> NodeOutcome[T]:
        return cls(kind=OutcomeKind.PASS, item=item)

    @classmethod
    def drop(
        cls,
        *,
        reason: RejectReason,
        message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> NodeOutcome[T]:
        return cls(
            kind=OutcomeKind.DROP,
            reason=reason,
            message=message,
            metadata=metadata or {},
        )

    @classmethod
    def quarantine(
        cls,
        *,
        item: T | None = None,
        reason: RejectReason,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> NodeOutcome[T]:
        return cls(
            kind=OutcomeKind.QUARANTINE,
            item=item,
            reason=reason,
            message=message,
            metadata=metadata or {},
        )

    @classmethod
    def fail(
        cls,
        *,
        item: T | None = None,
        reason: RejectReason,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> NodeOutcome[T]:
        return cls(
            kind=OutcomeKind.FAIL,
            item=item,
            reason=reason,
            message=message,
            metadata=metadata or {},
        )
