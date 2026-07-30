"""Regression tests for cross-platform browser/driver descendant reaping.

The bypass stack (patchright, nodriver, camoufox, cloakbrowser) can orphan
browser/driver subprocesses. On Windows a lingering child keeps the interpreter
alive, so a finished run never exits. These tests verify the shared teardown in
``browser_utils`` terminates every browser descendant this process spawned,
never touches non-browser or foreign processes, and runs on all platforms.
"""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from job_ftch.infrastructure.sources import browser_utils
from job_ftch.infrastructure.sources.browser_utils import (
    _select_stale_driver_pids,
    reap_stale_browser_drivers,
    terminate_browser_descendants,
)

_SENTINEL = "job_ftch_reaper_test_sentinel"


def _spawn_child(*, browser_marked: bool) -> subprocess.Popen[bytes]:
    """Spawn a long-lived child; tag it as browser-like when requested.

    The sentinel token rides along as an extra argv entry so the reaper's
    marker match (patched to the sentinel below) recognises it without needing a
    real browser binary on the test host.
    """
    marker = _SENTINEL if browser_marked else "job_ftch_reaper_test_non_browser"
    python = getattr(sys, "_base_executable", sys.executable)
    args = [python, "-c", f"import time; _marker={marker!r}; time.sleep(300)"]
    return subprocess.Popen(args)


def _kill_quietly(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


def _marked_descendant_pids() -> frozenset[int]:
    pids: set[int] = set()
    for proc in psutil.Process().children(recursive=True):
        try:
            cmdline = " ".join(proc.cmdline() or [])
        except Exception:
            continue
        if _SENTINEL in cmdline:
            pids.add(proc.pid)
    return frozenset(pids)


@pytest.fixture
def sentinel_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict the reaper to the test sentinel so real drivers are untouched."""
    monkeypatch.setattr(browser_utils, "_BROWSER_PROC_MARKERS", (_SENTINEL,))
    monkeypatch.setattr(browser_utils, "_NODE_DRIVER_MARKERS", ())


def test_select_stale_driver_pids_gates_on_age_children_and_kind() -> None:
    # (pid, ppid, age_seconds, is_browser)
    snapshot = [
        (100, 42, 250.0, True),  # stale + browser + childless -> selected
        (101, 42, 250.0, True),  # browser but parent of 102 -> kept (live)
        (102, 101, 249.0, True),  # child of 101 -> not orphaned -> kept
        (103, 42, 20.0, True),  # browser but too young -> kept
        (104, 42, 250.0, False),  # stale + childless but not a browser -> kept
    ]

    assert _select_stale_driver_pids(snapshot, min_age_seconds=180) == [100]


def test_select_stale_driver_pids_empty_snapshot() -> None:
    assert _select_stale_driver_pids([], min_age_seconds=180) == []


def test_terminate_kills_spawned_browser_child(sentinel_markers: None) -> None:
    child = _spawn_child(browser_marked=True)
    try:
        assert child.pid in {p.pid for p in psutil.Process().children(recursive=True)}

        terminate_browser_descendants()

        assert child.wait(timeout=10) is not None
        assert child.poll() is not None
    finally:
        _kill_quietly(child)


def test_terminate_ignores_non_browser_child(sentinel_markers: None) -> None:
    child = _spawn_child(browser_marked=False)
    try:
        terminate_browser_descendants()

        # A non-browser descendant must survive: the marker filter is what keeps
        # unrelated subprocesses (and, by process-tree isolation, the user's own
        # Chrome) safe from teardown.
        time.sleep(0.5)
        assert child.poll() is None
    finally:
        _kill_quietly(child)


def test_terminate_spares_requested_pid(sentinel_markers: None) -> None:
    child = _spawn_child(browser_marked=True)
    try:
        spare_pids = _marked_descendant_pids()
        assert spare_pids

        terminate_browser_descendants(spare_pids=spare_pids)

        time.sleep(0.5)
        assert child.poll() is None
    finally:
        _kill_quietly(child)


def test_terminate_is_idempotent_with_no_targets(sentinel_markers: None) -> None:
    # No sentinel-tagged descendants exist; the call must be a safe no-op.
    terminate_browser_descendants()
    terminate_browser_descendants()


def test_reap_stale_terminates_childless_orphan(sentinel_markers: None) -> None:
    # Real snapshot -> select -> terminate path (the per-open_page reaper). With
    # min_age_seconds=0 the childless sentinel child qualifies as a stale orphan.
    child = _spawn_child(browser_marked=True)
    try:
        assert child.pid in _marked_descendant_pids()

        reap_stale_browser_drivers(min_age_seconds=0)

        assert child.wait(timeout=10) is not None
    finally:
        _kill_quietly(child)


def test_reap_stale_keeps_fresh_driver_alive(sentinel_markers: None) -> None:
    # The just-spawned child is younger than the age gate, so the concurrency-safe
    # reaper must leave it running (a live sibling scrape must not be killed).
    child = _spawn_child(browser_marked=True)
    try:
        reap_stale_browser_drivers(min_age_seconds=3600)

        time.sleep(0.5)
        assert child.poll() is None
    finally:
        _kill_quietly(child)
