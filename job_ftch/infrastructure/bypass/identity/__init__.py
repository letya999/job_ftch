"""Universal, self-consistent session identity (TRACK A).

One authoritative identity per session that regenerates (new geo, new runtime
version, new generation) but never leaks a "this is a scraper" signal: every
surface - navigator, UA client hints, WebGL, timezone, TLS/JA3 impersonation,
and the window vs worker realms - must tell one and the same story. The
coherence contract in :mod:`coherence` is the single place that enforces it, for
both offline tests and the live self-check.
"""

from __future__ import annotations

from job_ftch.infrastructure.bypass.identity.coherence import (
    CoherenceIssue,
    CoherenceReport,
    IdentityIncoherenceError,
    assert_coherent,
    check_identity,
    cross_check_observed,
)
from job_ftch.infrastructure.bypass.identity.model import SessionIdentity

__all__ = [
    "CoherenceIssue",
    "CoherenceReport",
    "IdentityIncoherenceError",
    "SessionIdentity",
    "assert_coherent",
    "check_identity",
    "cross_check_observed",
]
