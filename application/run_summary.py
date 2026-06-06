from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from application.outcomes import PipelineStage, RejectReason


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


@dataclass(slots=True)
class RunSummary:
    run_id: str
    fetched: int = 0
    source_records: int = 0
    sanitized: int = 0
    dropped: int = 0
    emitted: int = 0
    quarantined: int = 0
    failed: int = 0
    extracted: int = 0
    duplicates: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    reason_counts: dict[str, int] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_now_utc)
    finished_at: datetime | None = None

    def record_stage(self, stage: PipelineStage | str) -> None:
        _increment(self.stage_counts, str(stage))

    def record_reason(self, reason: RejectReason | str) -> None:
        _increment(self.reason_counts, str(reason))

    def record_source(self, source_key: str) -> None:
        _increment(self.source_counts, source_key)

    def finish(self) -> RunSummary:
        self.finished_at = _now_utc()
        return self

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "fetched": self.fetched,
            "source_records": self.source_records,
            "sanitized": self.sanitized,
            "dropped": self.dropped,
            "emitted": self.emitted,
            "quarantined": self.quarantined,
            "failed": self.failed,
            "extracted": self.extracted,
            "duplicates": self.duplicates,
            "stage_counts": dict(self.stage_counts),
            "reason_counts": dict(self.reason_counts),
            "source_counts": dict(self.source_counts),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at is not None else None,
        }
