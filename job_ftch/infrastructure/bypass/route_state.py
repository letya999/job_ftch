"""Typed state for adaptive ingest routing.

The engine and network route are deliberately independent.  A proxy is not a
"heavier browser tier" and switching an engine must not silently reset the
network decision made for the source.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class NetworkRoute(StrEnum):
    DIRECT = "direct"
    PROXY = "proxy"
    RESIDENTIAL_PROXY = "residential_proxy"


class SessionMode(StrEnum):
    FRESH = "fresh"
    STICKY = "sticky"


class ChallengeState(StrEnum):
    NONE = "none"
    OBSERVED = "observed"
    SOLVING = "solving"


@dataclass(frozen=True, slots=True)
class RouteState:
    """One point in the route graph used for a source attempt."""

    engine: str = "noop"
    network: NetworkRoute = NetworkRoute.DIRECT
    session: SessionMode = SessionMode.FRESH
    challenge: ChallengeState = ChallengeState.NONE
    generation: int = 0

    def transition(
        self,
        *,
        engine: str | None = None,
        network: NetworkRoute | None = None,
        session: SessionMode | None = None,
        challenge: ChallengeState | None = None,
    ) -> RouteState:
        return replace(
            self,
            engine=engine if engine is not None else self.engine,
            network=network if network is not None else self.network,
            session=session if session is not None else self.session,
            challenge=challenge if challenge is not None else self.challenge,
            generation=self.generation + 1,
        )
