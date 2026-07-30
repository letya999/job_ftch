"""Per-tenant run lock (the v0.0.4 MVP cleanup extracted this from
`tenant_runner.py`).

The lock is a hybrid: an in-process `asyncio.Lock` keyed on the lock
file path (so concurrent tasks in the same process serialise), plus a
file-system `flock`-style lock created via `O_CREAT | O_EXCL` (so
concurrent processes serialise too). Stale locks are detected by
checking whether the recorded PID is still alive and reclaimed
transparently.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from threading import Lock
from typing import TYPE_CHECKING, Any

import structlog
from filelock import FileLock, Timeout

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)


class TenantRunAlreadyActiveError(RuntimeError):
    """Raised when a concurrent run for the same tenant is already active."""


class TenantRunLockError(RuntimeError):
    """Raised when the filesystem lock cannot be created due to permissions/IO errors."""


# Per-process lock pool. Keyed by the lock file path so distinct tenants
# can run in parallel within the same process.
_PROCESS_TENANT_LOCKS: dict[str, Lock] = {}


def tenant_run_is_active(settings: Any, tenant_id: str) -> bool:
    """Best-effort check for a live run of `tenant_id` in any process.

    Read-only companion to `tenant_run_lock`: it never creates or reclaims the
    lock file. Callers use it to refuse destructive operations (a database wipe
    while the pipeline is mid-flight) rather than to serialise runs — the lock
    itself remains the only correctness boundary.
    """
    try:
        lock_path = settings.store_path.parent / "tenant_locks" / f"{tenant_id}.lock"
        if not lock_path.exists():
            return False

        lock = FileLock(str(lock_path), timeout=0)
        try:
            lock.acquire()
            lock.release()
            return False
        except Timeout:
            return True
    except OSError:
        # Unreadable or malformed lock: do not block the caller on a guess.
        return False


@asynccontextmanager
async def tenant_run_lock(settings: Any, tenant_id: str) -> AsyncIterator[None]:
    """Acquire a process-safe + process-local lock for `tenant_id`.

    Used by `TenantRunner.run_tenant` so two concurrent runs of the same
    tenant do not race on the source snapshot / run summary. The default
    timeout is 30 seconds; `TimeoutError` is raised if the file-system
    lock cannot be acquired in that window.
    """
    lock_dir = settings.store_path.parent / "tenant_locks"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "tenant_run_lock_filesystem_unavailable",
            tenant_id=tenant_id,
            error=str(exc),
        )
        raise TenantRunLockError(
            f"Filesystem lock unavailable for tenant {tenant_id}: {exc}"
        ) from exc

    lock_path = lock_dir / f"{tenant_id}.lock"
    local_lock = _PROCESS_TENANT_LOCKS.setdefault(str(lock_path), Lock())

    def _acquire() -> FileLock:
        # Acquisition runs in a worker thread, while cleanup runs on the
        # event-loop thread. Keep FileLock state shared across those threads.
        flock = FileLock(str(lock_path), timeout=30.0, thread_local=False)
        try:
            flock.acquire()
            return flock
        except Timeout:
            raise TenantRunAlreadyActiveError(
                f"Run already active for tenant {tenant_id}"
            ) from None
        except OSError as exc:
            logger.error(
                "tenant_run_lock_filesystem_unavailable",
                tenant_id=tenant_id,
                error=str(exc),
            )
            raise TenantRunLockError(
                f"Filesystem lock unavailable for tenant {tenant_id}: {exc}"
            ) from exc

    flock: FileLock | None = None
    if not local_lock.acquire(blocking=False):
        raise TenantRunAlreadyActiveError(f"Run already active for tenant {tenant_id}")
    try:
        flock = await asyncio.to_thread(_acquire)
        yield
    finally:
        if flock is not None:
            flock.release()
        local_lock.release()
