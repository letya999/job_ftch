"""Session memory persistence for returning user simulation.

Persists session state between runs to simulate "returning user":
- Cookies (cf_clearance, dd_cookie, etc.)
- localStorage entries
- Visit count and last visit timestamp
- Behavioral profile (average reading time, scroll patterns)

Anti-bot systems check for "returning user" signals. Persistent session
memory creates the illusion of a real user who visits the site regularly.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("job_ftch.bypass.session_memory")

_DEFAULT_STORAGE_DIR = ".runtime/session_memory"
_COOKIE_ALLOWLIST = frozenset({"cf_clearance", "cf_bm", "dd_cookie", "_px3", "datadome"})


@dataclass(slots=True)
class SessionState:
    """Persistent session state for a persona."""

    persona_id: str
    domain: str = ""
    cookies: list[dict[str, Any]] = field(default_factory=list)
    localStorage: dict[str, str] = field(default_factory=dict)
    visit_count: int = 0
    last_visit_timestamp: float = 0.0
    avg_reading_time: float = 30.0  # seconds
    avg_scroll_count: int = 5
    total_time_spent: float = 0.0  # seconds

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "persona_id": self.persona_id,
            "domain": self.domain,
            "cookies": self.cookies,
            "localStorage": self.localStorage,
            "visit_count": self.visit_count,
            "last_visit_timestamp": self.last_visit_timestamp,
            "avg_reading_time": self.avg_reading_time,
            "avg_scroll_count": self.avg_scroll_count,
            "total_time_spent": self.total_time_spent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        """Create from dict."""
        return cls(
            persona_id=data.get("persona_id", ""),
            domain=data.get("domain", ""),
            cookies=data.get("cookies", []),
            localStorage=data.get("localStorage", {}),
            visit_count=data.get("visit_count", 0),
            last_visit_timestamp=data.get("last_visit_timestamp", 0.0),
            avg_reading_time=data.get("avg_reading_time", 30.0),
            avg_scroll_count=data.get("avg_scroll_count", 5),
            total_time_spent=data.get("total_time_spent", 0.0),
        )


class SessionMemory:
    """Persist and restore session state for a persona.

    Usage:
        memory = SessionMemory("persona_01")
        await memory.apply_to_browser(context)  # Restore state
        # ... use browser ...
        await memory.capture_from_browser(context)  # Capture state
        memory.save()  # Persist to disk
    """

    def __init__(
        self,
        persona_id: str,
        storage_dir: str | Path | None = None,
        *,
        domain: str | None = None,
    ) -> None:
        self._persona_id = persona_id
        self._domain = (domain or "").strip().lower()
        self._storage_dir = Path(storage_dir or _DEFAULT_STORAGE_DIR)
        # TRACK B2: key by (persona, domain) so one site's clearance state never
        # bleeds into another. ``domain=None`` keeps the legacy per-persona file
        # for backward compatibility with existing callers and tests.
        if self._domain:
            safe_domain = re.sub(r"[^a-z0-9_.-]+", "_", self._domain).strip("._") or "domain"
            filename = f"{persona_id}__{safe_domain}.json"
        else:
            filename = f"{persona_id}.json"
        self._storage_path = self._storage_dir / filename
        self._state = self._load()

    def _new_state(self) -> SessionState:
        return SessionState(persona_id=self._persona_id, domain=self._domain)

    def _load(self) -> SessionState:
        """Load session state from disk."""
        if not self._storage_path.exists():
            return self._new_state()

        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            return SessionState.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("session_memory_load_failed", error=str(exc))
            return self._new_state()

    def save(self) -> None:
        """Persist session state to disk."""
        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._storage_path.write_text(
                json.dumps(self._state.to_dict(), indent=2),
                encoding="utf-8",
            )
            logger.debug("session_memory_saved", persona_id=self._persona_id)
        except OSError as exc:
            logger.warning("session_memory_save_failed", error=str(exc))

    async def apply_to_browser(self, context: Any) -> None:
        """Restore session state to browser context.

        Restores cookies and localStorage to simulate returning user.
        """
        # Restore cookies
        if self._state.cookies:
            try:
                await context.add_cookies(self._state.cookies)
                logger.debug(
                    "session_cookies_restored",
                    persona_id=self._persona_id,
                    count=len(self._state.cookies),
                )
            except Exception as exc:
                logger.warning("session_cookies_restore_failed", error=str(exc))

        # Restore localStorage via JavaScript
        if self._state.localStorage:
            try:
                # Get a page from context
                pages = context.pages
                if pages:
                    page = pages[0]
                    for key, value in self._state.localStorage.items():
                        await page.evaluate(f"localStorage.setItem('{key}', '{value}')")
                    logger.debug(
                        "session_localstorage_restored",
                        persona_id=self._persona_id,
                        count=len(self._state.localStorage),
                    )
            except Exception as exc:
                logger.warning("session_localstorage_restore_failed", error=str(exc))

    async def capture_from_browser(self, context: Any) -> None:
        """Capture session state from browser context.

        Captures cookies and localStorage for future sessions.
        """
        # Capture cookies
        try:
            all_cookies = await context.cookies()
            # Filter to allowlisted cookies
            self._state.cookies = [
                cookie for cookie in all_cookies if cookie.get("name") in _COOKIE_ALLOWLIST
            ]
            logger.debug(
                "session_cookies_captured",
                persona_id=self._persona_id,
                count=len(self._state.cookies),
            )
        except Exception as exc:
            logger.warning("session_cookies_capture_failed", error=str(exc))

        # Capture localStorage via JavaScript
        try:
            pages = context.pages
            if pages:
                page = pages[0]
                localStorage_data = await page.evaluate(
                    "Object.fromEntries(Object.entries(localStorage))"
                )
                self._state.localStorage = localStorage_data
                logger.debug(
                    "session_localstorage_captured",
                    persona_id=self._persona_id,
                    count=len(self._state.localStorage),
                )
        except Exception as exc:
            logger.warning("session_localstorage_capture_failed", error=str(exc))

        # Update visit metadata
        self._state.visit_count += 1
        self._state.last_visit_timestamp = time.time()

    def update_behavioral_profile(self, reading_time: float, scroll_count: int) -> None:
        """Update behavioral profile with new observations.

        Uses exponential moving average to track user behavior over time.
        """
        alpha = 0.3  # Smoothing factor
        self._state.avg_reading_time = (
            alpha * reading_time + (1 - alpha) * self._state.avg_reading_time
        )
        self._state.avg_scroll_count = int(
            alpha * scroll_count + (1 - alpha) * self._state.avg_scroll_count
        )
        self._state.total_time_spent += reading_time

    @property
    def state(self) -> SessionState:
        """Get current session state."""
        return self._state
