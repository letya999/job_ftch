"""Compatibility export for the shared SSRF guard.

Wraps any httpx.AsyncBaseTransport and blocks outbound requests to private,
loopback, link-local, and multicast addresses. This prevents redirect-based
SSRF attacks where a remote career site could redirect the scraper to an
internal service.

Limitation: the guard resolves the host independently of the resolution the
real transport performs when it connects, so it does not fully close the
DNS-rebinding TOCTOU window (an attacker returning a public address here and a
private one at connect time). It is defense-in-depth for the common
redirect-to-internal case, not a hard sandbox.
"""

from job_ftch.infrastructure.network.ssrf_guard import (
    SSRFGuardedTransport,
    _host_is_private,
    check_ssrf,
)

__all__ = ["SSRFGuardedTransport", "_host_is_private", "check_ssrf"]
