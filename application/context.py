from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ProcessingContext:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    dry_run: bool = False
    max_text_length: int = 20_000
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    dedup_threshold: int = 90
    source_key: str | None = None
