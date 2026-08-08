"""Single owner for dedup claim settlement lifecycle."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger("job_ftch.dedup_settlement")


class SettlementOutcome(enum.Enum):
    COMMIT = "commit"
    RELEASE = "release"


@runtime_checkable
class DedupSettlement(Protocol):
    async def commit_claim(self, item_id: str) -> None: ...
    async def release_claim(self, item_id: str) -> None: ...


@runtime_checkable
class DedupSettlementParticipants(Protocol):
    def settlement_participants(self) -> tuple[DedupSettlement, ...]: ...


class DedupSettlementCoordinator:
    """Single owner of dedup claim lifecycle.

    Collects participants once at creation, deduplicates by identity.
    settle() is idempotent per item_id: repeated calls are no-ops
    (needed for fan-out where parent and children share a coordinator).
    """

    def __init__(self, participants: tuple[DedupSettlement, ...]) -> None:
        seen: set[int] = set()
        unique: list[DedupSettlement] = []
        for p in participants:
            if id(p) not in seen:
                seen.add(id(p))
                unique.append(p)
        self._participants = tuple(unique)
        self._settled: set[str] = set()
        if not self._participants:
            logger.warning("dedup_settlement_no_participants")

    async def settle(self, item_id: str, outcome: SettlementOutcome) -> None:
        if item_id in self._settled:
            return
        self._settled.add(item_id)
        for participant in self._participants:
            if outcome is SettlementOutcome.COMMIT:
                await participant.commit_claim(item_id)
            else:
                await participant.release_claim(item_id)


def collect_settlement_participants(
    nodes: Sequence[object],
) -> tuple[DedupSettlement, ...]:
    """Gather DedupSettlement instances from a node list.

    Nodes implementing DedupSettlementParticipants are expanded;
    nodes implementing DedupSettlement directly are included as-is.
    """
    result: list[DedupSettlement] = []
    seen: set[int] = set()
    for node in nodes:
        if isinstance(node, DedupSettlementParticipants):
            for p in node.settlement_participants():
                if id(p) not in seen:
                    seen.add(id(p))
                    result.append(p)
        elif isinstance(node, DedupSettlement) and id(node) not in seen:
            seen.add(id(node))
            result.append(node)
    return tuple(result)
