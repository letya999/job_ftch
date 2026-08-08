"""SessionIdentity: the one authoritative identity per session (TRACK A / A1).

Wraps a :class:`BrowserPersona` (the already-coherent declared identity) with
session-level context - exit geography, real runtime version, and a generation
counter - and projects it through the same native kwargs the rest of the stack
already understands. It deliberately does NOT re-declare persona fields; the
persona stays the single source of fingerprint truth, so there is nothing to
drift out of sync.

Regeneration (a new exit country or a runtime-reported version) returns a NEW
generation of a still-coherent identity rather than mutating in place, matching
the escalation ladder's "fresh session" semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from job_ftch.infrastructure.bypass.persona import (
    BrowserPersona,
    align_persona_version,
    select_persona,
)

DerivedFrom = Literal["runtime", "declared"]


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """One coherent identity for the lifetime of a (domain, generation) session."""

    persona: BrowserPersona
    generation: int = 1
    exit_country: str = ""
    runtime_version: str = ""
    derived_from: DerivedFrom = "declared"
    domain: str = ""
    _engine_family: str = field(default="", repr=False)

    # ── read-only projection of the persona's story ────────────────────
    @property
    def browser_family(self) -> str:
        return self.persona.browser_family

    @property
    def browser_version(self) -> str:
        return self.persona.browser_version

    @property
    def ua(self) -> str:
        return self.persona.ua

    @property
    def sec_ch_ua(self) -> str:
        return self.persona.sec_ch_ua

    @property
    def navigator_platform(self) -> str:
        return self.persona.navigator_platform

    @property
    def timezone(self) -> str:
        return self.persona.timezone

    @property
    def locale(self) -> str:
        return self.persona.locale

    @property
    def webgl_renderer(self) -> str:
        return self.persona.webgl_renderer

    @property
    def hardware_concurrency(self) -> int:
        return self.persona.hardware_concurrency

    @property
    def device_memory(self) -> int:
        return self.persona.device_memory

    # ── native projection (delegates to the persona) ───────────────────
    def context_kwargs(self) -> dict[str, Any]:
        return self.persona.context_kwargs()

    def http_headers(self) -> dict[str, str]:
        return self.persona.http_headers()

    def http_headers_ordered(self) -> list[tuple[str, str]]:
        return self.persona.http_headers_ordered()

    # ── factories ──────────────────────────────────────────────────────
    @classmethod
    def for_persona(
        cls,
        persona: BrowserPersona,
        *,
        generation: int = 1,
        exit_country: str = "",
        runtime_version: str = "",
        derived_from: DerivedFrom = "declared",
        domain: str = "",
        engine_family: str = "",
    ) -> SessionIdentity:
        return cls(
            persona=persona,
            generation=generation,
            exit_country=exit_country,
            runtime_version=runtime_version,
            derived_from=derived_from,
            domain=domain,
            _engine_family=engine_family or persona.browser_family,
        )

    @classmethod
    def for_session(
        cls,
        domain: str,
        *,
        engine_family: str,
        exit_country: str = "",
        runtime_version: str = "",
    ) -> SessionIdentity:
        """Build the coherent identity for one (domain, engine) session.

        ``exit_country`` aligns locale/timezone to the exit IP (no geo mismatch);
        ``runtime_version`` aligns UA/client hints to the browser's real reported
        version, so the declared story matches what the engine actually is.
        """
        persona = select_persona(domain, engine_family, proxy_country=exit_country)
        derived_from: DerivedFrom = "declared"
        if runtime_version:
            persona = align_persona_version(persona, engine_family, runtime_version)
            derived_from = "runtime"
        return cls.for_persona(
            persona,
            exit_country=exit_country,
            runtime_version=runtime_version,
            derived_from=derived_from,
            domain=domain,
            engine_family=engine_family,
        )

    # ── regeneration (new generation, still coherent) ──────────────────
    def with_exit_country(self, exit_country: str) -> SessionIdentity:
        """Return the next generation aligned to a new exit country."""
        return SessionIdentity.for_session(
            self.domain or "unknown",
            engine_family=self._engine_family or self.persona.browser_family,
            exit_country=exit_country,
            runtime_version=self.runtime_version,
        )._with_generation(self.generation + 1)

    def with_runtime_version(self, browser_family: str, runtime_version: str) -> SessionIdentity:
        """Return the next generation aligned to a runtime-reported version."""
        persona = align_persona_version(self.persona, browser_family, runtime_version)
        return SessionIdentity(
            persona=persona,
            generation=self.generation + 1,
            exit_country=self.exit_country,
            runtime_version=runtime_version,
            derived_from="runtime",
            domain=self.domain,
            _engine_family=browser_family or self._engine_family,
        )

    def _with_generation(self, generation: int) -> SessionIdentity:
        return SessionIdentity(
            persona=self.persona,
            generation=generation,
            exit_country=self.exit_country,
            runtime_version=self.runtime_version,
            derived_from=self.derived_from,
            domain=self.domain,
            _engine_family=self._engine_family,
        )
