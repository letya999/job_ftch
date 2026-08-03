"""Adaptive bypass escalation manager (ADR-023, ADR-037)."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from job_ftch.application.contracts import BrowserSessionBypass
from job_ftch.application.registry import (
    BypassCapability,
    get_bypass_capability,
    list_bypass_capabilities,
    register_bypass,
    resolve_bypass,
)
from job_ftch.infrastructure.bypass.attempt_budget import AttemptBudget
from job_ftch.infrastructure.bypass.failure_signal import (
    FailureKind,
    HeuristicFailureSignal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
from job_ftch.infrastructure.bypass.route_state import (
    ChallengeState,
    NetworkRoute,
    RouteState,
    SessionMode,
)
from job_ftch.infrastructure.bypass.transition_policy import (
    TransitionAction,
    TransitionPolicy,
)
from job_ftch.infrastructure.sources.source_deadline import (
    remaining_source_seconds,
)

logger = structlog.get_logger("job_ftch.bypass.adaptive")

_SESSION_COOKIE_ALLOWLIST = frozenset({"cf_clearance", "cf_bm", "dd_cookie", "_px3", "datadome"})
_MAX_SESSION_COOKIES = 32


# Default tier order; actual tiers are filtered to those that are
# currently registered (per ADR-037: registry-driven, no hardcoded
# bypass dependency). If `cloakbrowser` is not installed the
# `cloak` tier is silently absent.
DEFAULT_TIER_ORDER: tuple[str, ...] = (
    "noop",
    "curl_stealth",
    "tls_client",
    "stealth_browser",
    "patchright_browser",
    "nodriver",
    "camoufox",
    "cloak",
)

OPTIONAL_TIERS: frozenset[str] = frozenset({"camoufox", "patchright_browser", "tls_client"})


class AdaptiveBypassManager:
    """Manages tiered escalation of bypass strategies.

    Per ADR-037, the tier list is built dynamically from the registry: any
    `bypass` factory that exists in the registry is a candidate, in the
    order given by `DEFAULT_TIER_ORDER`. Operators that want to opt in or
    out of a specific tier do so by installing or omitting the
    corresponding extra (e.g. `[stealth]` for curl_stealth/stealth_browser,
    `[camoufox]` for the `camoufox` tier, `[nodriver]` for the `nodriver`
    tier, `cloakbrowser` for the `cloak` tier).

    The installed engines form one axis of a route graph. Network routing is
    a separate axis: an IP/rate-limit signal can activate or rotate a proxy
    while retaining the current transport/browser. Failure classification,
    not a universal ladder, selects the next graph edge.

    BehaviorSim (Bezier mouse trajectory) is NOT a standalone tier in the
    escalation chain. It is a composable decorator that is always applied
    on top of whatever browser tier is active. When `apply_page()` is
    called, it first delegates to the current strategy, then invokes
    `BehaviorSimBypass.apply_page()` to add human-like mouse movement and
    timing. For HTTP-only tiers (noop, curl_stealth) `apply_page()` is
    never called by browser_utils, so the decorator stays dormant.
    """

    FAILURE_WINDOW_SECONDS = 1800  # 30 min
    FAILURE_WINDOW_COUNT = 3
    # Failure kinds that count towards the escalation threshold.
    NETWORK_KINDS: frozenset[FailureKind] = frozenset(
        {
            FailureKind.RATE_LIMIT,
            FailureKind.BLOCKED_IP,
            FailureKind.DNS_ERROR,
            FailureKind.CONNECT_ERROR,
        }
    )
    DEBOUNCED_NETWORK_KINDS: frozenset[FailureKind] = frozenset(
        {FailureKind.TIMEOUT, FailureKind.DNS_ERROR, FailureKind.CONNECT_ERROR}
    )

    def __init__(
        self,
        bypass_config: dict[str, Any] | None = None,
        *,
        failure_signal: HeuristicFailureSignal | None = None,
        adaptive_enabled: bool = True,
    ) -> None:
        self.config = bypass_config or {}
        self.adaptive_enabled = adaptive_enabled
        self._signal = failure_signal or HeuristicFailureSignal()
        self._transition_policy = TransitionPolicy()
        from job_ftch.config import get_settings

        self._timeout_threshold = get_settings().bypass_timeout_escalate_threshold
        settings = get_settings()
        self._budget = AttemptBudget(
            max_route_attempts=int(
                self.config.get(
                    "max_route_attempts_per_operation",
                    settings.bypass_max_route_attempts_per_operation,
                )
            ),
            max_source_browser_launches=int(
                self.config.get(
                    "max_source_browser_launches",
                    settings.bypass_max_source_browser_launches,
                )
            ),
            max_source_proxy_rotations=int(
                self.config.get(
                    "max_source_proxy_rotations",
                    settings.bypass_max_source_proxy_rotations,
                )
            ),
            max_weighted_work=int(
                self.config.get(
                    "max_weighted_work_per_source",
                    settings.bypass_max_weighted_work_per_source,
                )
            ),
            max_proxy_rotations_per_operation=int(
                self.config.get(
                    "max_proxy_rotations_per_operation",
                    settings.bypass_max_proxy_rotations_per_operation,
                )
            ),
            max_same_route_retries_per_operation=int(
                self.config.get(
                    "max_same_route_retries_per_operation",
                    settings.bypass_max_same_route_retries_per_operation,
                )
            ),
        )
        # BehaviorSim is a composable decorator, not a standalone tier:
        # it adds human-like mouse/scroll behavior ON TOP of whatever
        # browser tier is currently active. It is always applied after
        # the primary strategy's apply_page, regardless of tier.
        from job_ftch.infrastructure.bypass.behavior_sim import BehaviorSimBypass
        from job_ftch.infrastructure.bypass.multi_layer_obfuscation import (
            build_default_orchestrator,
        )

        self._behavior_sim = BehaviorSimBypass()
        self._obfuscation_orchestrator = build_default_orchestrator()
        self._obfuscation_metadata: dict[str, Any] = {}
        self._session_memory_enabled = getattr(settings, "session_memory_enabled", False)
        self._session_memory: Any = None
        self._browser_session_state_enabled = bool(
            getattr(settings, "browser_session_state_enabled", False)
        )
        self._browser_session_state_dir: Path = getattr(
            settings,
            "browser_session_state_dir",
            Path(".runtime/session_states"),
        )
        self._configured_profile_dir: Path | None = getattr(settings, "browser_profile_dir", None)
        self._configured_profile_persistent = bool(
            getattr(settings, "browser_profile_persistent", False)
            and self._configured_profile_dir is not None
        )
        try:
            solver_config = {
                key.removeprefix("captcha_"): value
                for key, value in self.config.items()
                if key.startswith("captcha_")
            }
            self._captcha_solver = resolve_bypass(
                "captcha_solver",
                bypass_config=solver_config,
            )
        except (ValueError, KeyError):
            self._captcha_solver = None
        self._tiers: list[str] = []
        for name in DEFAULT_TIER_ORDER:
            try:
                resolve_bypass(name)
            except (ValueError, KeyError):
                if name not in OPTIONAL_TIERS:
                    logger.warning(
                        "bypass_tier_unavailable",
                        tier=name,
                        hint="install the corresponding extra to enable this tier",
                    )
                continue
            self._tiers.append(name)
        if not self._tiers:
            self._tiers = ["noop"]
        self._capabilities: dict[str, BypassCapability] = {}
        for name in self._tiers:
            try:
                self._capabilities[name] = get_bypass_capability(name)
            except ValueError:
                self._capabilities[name] = BypassCapability(
                    cost=self._tiers.index(name),
                )
        self._session_handoff: Any = None
        if bool(self.config.get("session_handoff", False)):
            handoff_candidates = sorted(
                (
                    (name, capability)
                    for name, capability in list_bypass_capabilities().items()
                    if "session_handoff" in capability.challenge_actions
                    and self._legal_gate_allows(capability)
                ),
                key=lambda item: item[1].cost,
            )
            for name, _capability in handoff_candidates:
                try:
                    self._session_handoff = resolve_bypass(name, bypass_config=self.config)
                except (ValueError, KeyError):
                    continue
                break
        self.current_tier_index = 0
        self._current_strategy: Any = None
        self._context: Any = None
        self._route_state = RouteState(engine=self._tiers[0])
        self._failure_window: dict[str, deque[tuple[float, str]]] = {}
        # Engines that already failed against this source's protection. The
        # ladder never returns to them until a network/session reset clears the
        # set, which (with the no-downgrade rule in ``_select_engine``) stops
        # the stealth_browser<->patchright oscillation that left camoufox/cloak
        # unreachable (defect B4).
        self._failed_tiers: set[str] = set()
        self._session_cookies: list[dict[str, Any]] = []
        self._profile_dir: str | None = None
        self._owns_profile_dir = True
        self._observed_challenge_type: str | None = None
        import asyncio

        self._profile_lock = asyncio.Lock()
        self._escalations_total: int = 0
        self._ensure_strategy()
        logger.info(
            "bypass_policy_ready",
            fallback_order=tuple(self._tiers),
            axes=("engine", "network", "session", "challenge"),
            capabilities=tuple(
                {
                    "name": name,
                    "transport": capability.transport,
                    "browser_family": capability.browser_family,
                    "supports_proxy": capability.supports_proxy,
                    "legal_gate": capability.legal_gate,
                    "min_remaining_seconds": capability.min_remaining_seconds,
                }
                for name, capability in self._capabilities.items()
            ),
        )

    def bind_context(self, context: Any) -> None:
        """Attach the source-scoped identity/metrics context to this controller."""
        self._context = context
        preflight = getattr(context, "preflight", None)
        recommended = getattr(preflight, "tier", None)
        if recommended in self._tiers and recommended != self.current_name:
            capability = self._capabilities[recommended]
            if self._capability_allowed(capability):
                self.current_tier_index = self._tiers.index(recommended)
                self._ensure_strategy()
                logger.info("bypass_preflight_engine_selected", engine=recommended)
        if getattr(preflight, "network", "direct") == "proxy":
            if bool(getattr(context, "residential_proxy_available", False)):
                self._route_state = self._route_state.transition(
                    network=NetworkRoute.RESIDENTIAL_PROXY,
                    session=SessionMode.STICKY,
                )
                logger.info("bypass_preflight_network_selected", network="residential_proxy")
            elif bool(getattr(context, "proxy_available", False)):
                self._route_state = self._route_state.transition(
                    network=NetworkRoute.PROXY,
                    session=SessionMode.STICKY,
                )
                logger.info("bypass_preflight_network_selected", network="proxy")
        self._sync_context_route()

    def _sync_context_route(self) -> None:
        family_setter = getattr(self._context, "set_browser_family", None)
        if callable(family_setter) and hasattr(self, "_capabilities"):
            family_setter(self._capabilities[self.current_name].browser_family)
        setter = getattr(self._context, "set_effective_route", None)
        if callable(setter):
            setter(tier=self.current_name, network=self._route_state.network.value)

    def _resolve_proxy_country(self) -> str:
        """Resolve the country of the active proxy for geolocation coherence."""
        if not self.uses_proxy or self._context is None:
            return "US"
        proxy_url = getattr(self._context, "current_proxy_url", None)
        if not proxy_url:
            return "US"
        active_proxy = getattr(self._context, "_active_proxy", None)
        country = getattr(active_proxy, "country", None)
        if country:
            return str(country).upper()
        return "US"

    _TEMPORAL_HINT_CAP: float = 2.0

    def _apply_temporal_hint(self, ctx: Any) -> None:
        """Feed temporal_pacing_seconds from orchestrator into the DomainPacer."""
        pacing = ctx.metadata.get("temporal_pacing_seconds")
        if pacing and pacing > 0:
            capped = min(pacing, self._TEMPORAL_HINT_CAP)
            from job_ftch.infrastructure.bypass.pacing import get_domain_pacer

            get_domain_pacer().apply_temporal_hint(ctx.domain, capped)

    def _ensure_strategy(self) -> None:
        name = self._tiers[self.current_tier_index]
        logger.debug(
            "adaptive_bypass_init_tier",
            tier=self.current_tier_index,
            name=name,
            tiers=self._tiers,
        )
        self._current_strategy = resolve_bypass(name, bypass_config=self.config)
        if self._route_state.engine != name:
            self._route_state = self._route_state.transition(engine=name)
        self._sync_context_route()

    def escalate(self) -> bool:
        """Use the conservative unknown-signal fallback edge."""
        if not self.adaptive_enabled:
            return False
        result = self._select_engine(tuple(self._tiers[self.current_tier_index + 1 :]))
        if not result and self.exhausted:
            self._try_managed_fallback()
        return result

    def _try_managed_fallback(self) -> bool:
        """Attempt managed_scraper as last-resort when all local tiers exhausted."""
        if getattr(self, "_managed_fallback_used", False):
            return False
        try:
            managed = resolve_bypass("managed_scraper", bypass_config=self.config)
            if managed is not None:
                self._current_strategy = managed
                self._managed_fallback_used = True
                logger.info("bypass_managed_fallback_activated")
                return True
        except (ValueError, KeyError):
            pass
        return False

    def escalate_to(self, target_name: str) -> bool:
        """Escalate directly to a named tier when it is available."""
        if not self.adaptive_enabled:
            return False
        try:
            target_index = self._tiers.index(target_name)
        except ValueError:
            return False
        if target_index <= self.current_tier_index:
            return False
        return self._select_engine((target_name,))

    @property
    def current_name(self) -> str:
        return self._tiers[self.current_tier_index]

    @property
    def route_state(self) -> RouteState:
        return self._route_state

    @property
    def uses_proxy(self) -> bool:
        return self._route_state.network in (
            NetworkRoute.PROXY,
            NetworkRoute.RESIDENTIAL_PROXY,
        )

    @property
    def current_proxy_url(self) -> str | None:
        if not self.uses_proxy:
            return None
        value = getattr(self._context, "current_proxy_url", None)
        return str(value) if value else None

    @property
    def requires_browser(self) -> bool:
        return self._capabilities[self.current_name].requires_browser

    @property
    def owns_page_hardening(self) -> bool:
        """This controller injects persona/JS hardening itself in ``apply_page``.

        ``browser_utils`` checks this flag to avoid also calling
        ``BypassContext.on_page`` on the same page, which would inject a second
        conflicting copy of the hardening blob (defect A2).
        """
        return True

    @property
    def capability_inventory(self) -> dict[str, BypassCapability]:
        return dict(self._capabilities)

    @property
    def escalations_total(self) -> int:
        return self._escalations_total

    @property
    def available_tiers(self) -> tuple[str, ...]:
        return tuple(self._tiers)

    @property
    def exhausted(self) -> bool:
        """Whether adaptive escalation has reached its last installed tier."""
        return self.current_tier_index >= len(self._tiers) - 1

    @property
    def obfuscation_metadata(self) -> dict[str, Any]:
        """Metadata produced by the last orchestrator run (for downstream consumers)."""
        return self._obfuscation_metadata

    def get_browser_session_bypass(self) -> BrowserSessionBypass | None:
        """Return the active session-owning engine, if the tier provides one."""
        if isinstance(self._current_strategy, BrowserSessionBypass):
            return self._current_strategy
        return None

    def open_page(
        self,
        config: dict[str, Any],
        *,
        use_proxy: bool = False,
    ) -> Any:
        """Delegate browser lifecycle to the active session-owning strategy."""
        strategy = self.get_browser_session_bypass()
        if strategy is None:
            raise TypeError(
                f"active bypass tier {self.current_name!r} does not own a browser session"
            )

        # Check temporal consistency of (persona, domain, timestamp) sessions
        # against the persistent domain-intelligence temporal graph. This detects
        # impossible-travel / burst patterns that would flag an automated identity.
        if self._context is not None:
            import time

            from job_ftch.infrastructure.bypass.domain_intel import get_domain_intel
            from job_ftch.infrastructure.bypass.temporal_graph import SessionRecord

            domain = getattr(self._context, "domain", "unknown")
            persona_id = getattr(getattr(self._context, "persona", None), "name", "default")
            temporal_graph = getattr(get_domain_intel(), "temporal_graph", None)
            if temporal_graph is not None:
                candidate = SessionRecord(
                    session_id=config.get("session_id", ""),
                    persona_id=persona_id,
                    domain=domain,
                    timestamp=time.monotonic(),
                )
                verdict = temporal_graph.check_consistency(candidate)
                if not verdict.consistent:
                    logger.warning(
                        "temporal_consistency_violation",
                        domain=domain,
                        persona=persona_id,
                        violations=verdict.violations,
                        score=verdict.score,
                    )
                temporal_graph.add_session(candidate)

        effective_proxy = use_proxy or self.uses_proxy
        session_config = dict(config)
        if effective_proxy and self.current_proxy_url:
            session_config["_proxy_url"] = self.current_proxy_url
        return self._open_page_owned(
            strategy,
            session_config,
            use_proxy=effective_proxy,
        )

    @asynccontextmanager
    async def _open_page_owned(
        self,
        strategy: BrowserSessionBypass,
        config: dict[str, Any],
        *,
        use_proxy: bool,
    ) -> Any:
        """Serialize one persistent profile and count only actual launches."""

        async def _enter() -> Any:
            if not self._budget.allow_browser_launch():
                raise RuntimeError("browser launch budget exhausted")
            self._budget.note_browser_launch()
            return strategy.open_page(config, use_proxy=use_proxy)

        if config.get("persistent_context"):
            async with self._profile_lock:
                context_manager = await _enter()
                async with context_manager as page:
                    yield page
            return
        context_manager = await _enter()
        async with context_manager as page:
            yield page

    def activate_proxy(self) -> bool:
        """Escalate one step along the network axis (defect B5).

        The ladder is ``direct -> datacenter proxy -> residential proxy``: a
        datacenter pool is cheaper and is tried first, with residential kept as
        the heavier fallback. Previously only the residential step existed, so
        an IP block with no residential pool configured left the network axis
        dead and answered the block with a heavier browser from the same IP.

        Changing the exit IP invalidates any IP-bound clearance cookies
        (``cf_clearance`` etc.), so every activation drops the session cookies
        and starts a fresh session generation. Keeping one exit IP afterwards
        (stickiness) is a property of the proxy provider, not of this edge.
        """
        if not self.adaptive_enabled or not self._budget.allow_proxy_rotation():
            return False
        current = self._route_state.network
        if current is NetworkRoute.RESIDENTIAL_PROXY:
            return False  # top of the network ladder
        residential = bool(getattr(self._context, "residential_proxy_available", False))
        datacenter = bool(getattr(self._context, "proxy_available", False))
        if current is NetworkRoute.DIRECT:
            if datacenter:
                return self._transition_network(NetworkRoute.PROXY)
            if residential:
                return self._transition_network(NetworkRoute.RESIDENTIAL_PROXY)
            return False
        # current is datacenter PROXY: escalate to residential when available.
        if residential:
            return self._transition_network(NetworkRoute.RESIDENTIAL_PROXY)
        return False

    def _transition_network(self, network: NetworkRoute) -> bool:
        """Atomically move the network axis, dropping the IP-bound session."""
        self._session_cookies.clear()
        # A new exit IP may clear protection that blocked earlier engines, so
        # let the ladder try them again from the current tier upward.
        self._failed_tiers.clear()
        self._budget.note_proxy_rotation()
        self._route_state = self._route_state.transition(
            network=network,
            session=SessionMode.FRESH,
        )
        self._sync_context_route()
        self._escalations_total += 1
        logger.info(
            "bypass_network_transition",
            engine=self.current_name,
            network=network.value,
        )
        return True

    def _rotate_proxy(
        self,
        source_id: str,
        *,
        status_code: int | None,
        body: bytes | None,
        error: BaseException | None,
    ) -> bool:
        if not self.uses_proxy or not self._budget.allow_proxy_rotation():
            return False
        rotate = getattr(self._context, "rotate_proxy", None)
        if not callable(rotate):
            return False
        rotate(
            source_id,
            status_code=status_code,
            body=body,
            error=error,
        )
        self._budget.note_proxy_rotation()
        self._session_cookies.clear()
        self._route_state = self._route_state.transition(session=SessionMode.FRESH)
        self._sync_context_route()
        logger.info("bypass_proxy_rotated", engine=self.current_name)
        return True

    def _persona_session_memory(self) -> Any:
        """Lazy per-persona SessionMemory for returning-user simulation (Wave 5.2).

        Opt-in via ``JOB_FTCH_SESSION_MEMORY_ENABLED``. Persists only
        clearance-allowlist cookies keyed by persona, so a later run of the
        same persona presents "returning user" signals.
        """
        if not self._session_memory_enabled:
            return None
        persona_id = getattr(getattr(self._context, "persona", None), "name", "default")
        if (
            self._session_memory is None
            or getattr(self._session_memory, "_persona_id", None) != persona_id
        ):
            from job_ftch.infrastructure.bypass.session_memory import SessionMemory

            self._session_memory = SessionMemory(persona_id)
        return self._session_memory

    def prepare_browser_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Overlay allowlisted source-session cookies onto a browser attempt."""
        prepared = dict(config)
        if prepared.get("persistent_context"):
            if self._profile_dir is None:
                if self._configured_profile_persistent and self._configured_profile_dir:
                    profile_dir = self._configured_profile_dir
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    self._profile_dir = str(profile_dir)
                    self._owns_profile_dir = False
                else:
                    self._profile_dir = tempfile.mkdtemp(prefix="job_ftch_source_profile_")
                    self._owns_profile_dir = True
                if os.name != "nt":
                    os.chmod(self._profile_dir, 0o700)
            prepared["_profile_dir"] = self._profile_dir
        configured = list(prepared.get("cookies") or [])
        memory = self._persona_session_memory()
        restored = list(memory.state.cookies) if memory is not None else []
        persistent_state = self._load_persistent_session_state()
        persistent_cookies = list(persistent_state.get("cookies") or [])
        persistent_user_agent = str(persistent_state.get("user_agent", "") or "")
        if persistent_user_agent and not prepared.get("user_agent"):
            prepared["user_agent"] = persistent_user_agent
        by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for cookie in [*persistent_cookies, *restored, *configured, *self._session_cookies]:
            key = (
                str(cookie.get("name", "")),
                str(cookie.get("domain", "")),
                str(cookie.get("path", "/")),
            )
            by_key[key] = dict(cookie)
        if by_key:
            prepared["cookies"] = list(by_key.values())[:_MAX_SESSION_COOKIES]
        return prepared

    async def close(self) -> None:
        """Release source-scoped runtime artifacts after the terminal snapshot."""
        if self._profile_dir is not None:
            profile = Path(self._profile_dir)
            owns_profile_dir = self._owns_profile_dir
            self._profile_dir = None
            self._owns_profile_dir = True
            if owns_profile_dir:
                shutil.rmtree(profile, ignore_errors=True)

    async def capture_session_state(self, page: Any) -> None:
        """Capture only clearance cookies; never persist or log their values."""
        raw: Any = None
        exporter = getattr(page, "export_cookies", None)
        if callable(exporter):
            raw = await exporter()
        else:
            context = getattr(page, "context", None)
            cookies = getattr(context, "cookies", None)
            if callable(cookies):
                raw = await cookies()
        if not isinstance(raw, list):
            return
        allowed: list[dict[str, Any]] = []
        for cookie in raw:
            if not isinstance(cookie, dict):
                continue
            name = str(cookie.get("name", "")).lower()
            if name not in _SESSION_COOKIE_ALLOWLIST:
                continue
            allowed.append(dict(cookie))
            if len(allowed) >= _MAX_SESSION_COOKIES:
                break
        self._session_cookies = allowed
        if allowed:
            self._route_state = self._route_state.transition(session=SessionMode.STICKY)
            self._sync_context_route()
            memory = self._persona_session_memory()
            if memory is not None:
                import time

                memory.state.cookies = [dict(cookie) for cookie in allowed]
                memory.state.visit_count += 1
                memory.state.last_visit_timestamp = time.time()
                memory.save()
            await self._save_persistent_session_state(page, allowed)

    def _session_state_domain(self) -> str:
        domain = str(getattr(self._context, "domain", "") or "").lower()
        return domain.strip()

    def _session_state_path(self, domain: str) -> Path | None:
        if not self._browser_session_state_enabled or not domain:
            return None
        safe_domain = re.sub(r"[^a-z0-9_.-]+", "_", domain.lower()).strip("._")
        if not safe_domain:
            return None
        return self._browser_session_state_dir / f"{safe_domain}.json"

    def _load_persistent_session_state(self) -> dict[str, Any]:
        path = self._session_state_path(self._session_state_domain())
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        cookies = data.get("cookies")
        if not isinstance(cookies, list):
            data["cookies"] = []
        else:
            data["cookies"] = [
                dict(cookie)
                for cookie in cookies
                if isinstance(cookie, dict)
                and str(cookie.get("name", "")).lower() in _SESSION_COOKIE_ALLOWLIST
            ][:_MAX_SESSION_COOKIES]
        return data

    async def _save_persistent_session_state(
        self,
        page: Any,
        cookies: list[dict[str, Any]],
    ) -> None:
        if not cookies:
            return
        domain = self._session_state_domain()
        if not domain:
            try:
                from urllib.parse import urlparse

                domain = urlparse(str(getattr(page, "url", "") or "")).netloc.lower()
            except Exception:
                domain = ""
        path = self._session_state_path(domain)
        if path is None:
            return
        user_agent = ""
        try:
            evaluate = getattr(page, "evaluate", None)
            if callable(evaluate):
                user_agent = str(await evaluate("navigator.userAgent"))
        except Exception:
            user_agent = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(
                    {
                        "domain": domain,
                        "user_agent": user_agent,
                        "cookies": [dict(cookie) for cookie in cookies][:_MAX_SESSION_COOKIES],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp_path.replace(path)
        except OSError:
            logger.debug("browser_session_state_save_failed", domain=domain)

    def _select_engine(self, candidates: tuple[str, ...]) -> bool:
        """Choose the first installed candidate that escalates the engine.

        The ladder is monotonic (defect B4): a candidate is rejected if it has
        already failed for this source, or if it is cheaper than the current
        engine. Escalation therefore only ever moves to a stronger, not-yet
        failed tier, so a recurring challenge climbs stealth -> patchright ->
        camoufox -> cloak instead of oscillating between the two cheapest.
        """
        current_cost = self._capabilities[self.current_name].cost
        for candidate in candidates:
            if candidate == self.current_name:
                continue
            if candidate in self._failed_tiers:
                continue
            try:
                target_index = self._tiers.index(candidate)
            except ValueError:
                continue
            capability = self._capabilities[candidate]
            if capability.cost < current_cost:
                continue
            if not self._capability_allowed(capability):
                logger.info(
                    "bypass_capability_skipped",
                    engine=candidate,
                    legal_gate=capability.legal_gate,
                    min_remaining_seconds=capability.min_remaining_seconds,
                )
                continue
            if not self._budget.allow_route_transition(weight=max(1, capability.cost // 10)):
                logger.info("bypass_route_budget_exhausted", engine=candidate)
                continue
            new_name = self.current_name
            # The engine we are leaving has failed against this protection; do
            # not return to it until a network/session reset clears the set.
            self._failed_tiers.add(new_name)
            self.current_tier_index = target_index
            self._ensure_strategy()
            self._budget.note_route_transition(weight=max(1, capability.cost // 10))
            self._escalations_total += 1
            logger.info(
                "bypass_route_transition",
                from_engine=new_name,
                to_engine=candidate,
                network=self._route_state.network.value,
            )
            return True
        return False

    def _legal_gate_allows(self, capability: BypassCapability) -> bool:
        gate = capability.legal_gate
        if gate is None:
            return True
        raw = self.config.get(f"allow_{gate}", True)
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "no", "off"}
        return bool(raw)

    def _capability_allowed(self, capability: BypassCapability) -> bool:
        if not self._legal_gate_allows(capability):
            return False
        remaining = remaining_source_seconds()
        return remaining is None or remaining >= capability.min_remaining_seconds

    def _select_capability(self, actions: tuple[str, ...]) -> bool:
        """Select the cheapest installed/legal engine advertising an action."""
        for action in actions:
            candidates = sorted(
                (
                    (name, capability)
                    for name, capability in self._capabilities.items()
                    if action in capability.challenge_actions
                    and capability.requires_browser
                    and self._capability_allowed(capability)
                ),
                key=lambda item: item[1].cost,
            )
            if self._select_engine(tuple(name for name, _capability in candidates)):
                return True
        return False

    def request_capability(self, action: str, *, reason: str) -> bool:
        """Request a policy transition without naming a concrete backend."""
        if not self.adaptive_enabled:
            return False
        changed = self._select_capability((action,))
        logger.info(
            "bypass_capability_requested",
            action=action,
            reason=reason,
            changed=changed,
            engine=self.current_name,
        )
        return changed

    def set_observed_challenge_type(self, challenge_type: str | None) -> None:
        """Remember monitor-detected challenge type for the next browser solve.

        Some WAF pages return HTTP 200 and expose CAPTCHA evidence in a
        preflight/monitor detector, but the live browser page later no longer
        contains an easy-to-detect site marker. Keeping the detector's typed
        observation lets the solver route to the intended provider instead of
        falling back to an ``unknown`` DOM guess.
        """
        if isinstance(challenge_type, str) and challenge_type.strip():
            self._observed_challenge_type = challenge_type.strip()

    @staticmethod
    def _captcha_actions(
        body: bytes | None,
        headers: Mapping[str, str] | None,
    ) -> tuple[str, ...]:
        evidence = (body or b"").decode("utf-8", errors="ignore").lower()
        header_text = " ".join(f"{key}:{value}" for key, value in (headers or {}).items()).lower()
        evidence = f"{evidence} {header_text}"
        if any(marker in evidence for marker in ("turnstile", "cf-chl", "cloudflare")):
            return ("cloudflare_challenge", "generic_challenge", "terminal")
        if any(
            marker in evidence
            for marker in ("browser fingerprint", "webdriver", "automation detected")
        ):
            return ("fingerprint_resistant", "generic_challenge", "terminal")
        return ("generic_challenge", "terminal")

    async def handle_failure(
        self,
        source_id: str,
        *,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        error: BaseException | None = None,
        retry_after: float | None = None,
        override_kind: FailureKind | None = None,
    ) -> str:
        """Record a failure and escalate if the policy triggers.

        Returns the FailureKind that was classified. If escalation
        happened, the manager's strategy is already updated.

        ``override_kind`` lets a caller that has evidence the HTTP-level
        classifier cannot see (defect B1: a 200 shell with the listing stripped
        is a *silent block* only the scraper knows is wrong) inject the already
        determined kind instead of re-deriving it from status/body.
        """
        kind = override_kind or self._signal.classify(
            status_code=status_code,
            headers=headers,
            body=body,
            error=error,
        )
        del retry_after  # Retry-After is consumed by the HTTP RouteBudget layer.
        decision = self._transition_policy.decide(kind)
        capability = self._capabilities[self.current_name]
        self._budget.log_attempt(
            source_id=source_id,
            transport=capability.transport,
            browser=capability.browser_family,
            network=self._route_state.network.value,
            failure_kind=kind,
            status_code=status_code,
            session_generation=self._route_state.generation,
            challenge_action=self._route_state.challenge.value,
        )
        if kind == FailureKind.OK:
            return kind
        if kind == FailureKind.UNKNOWN:
            # An unrecognised protection response must not be a silent no-op
            # (defect B2): record it and, once the per-source failure window
            # crosses the threshold, conservatively escalate to the next engine.
            self._record_failure(source_id, kind)
            if self.adaptive_enabled and self._should_escalate(source_id):
                self.escalate()
            return kind
        if decision.action is TransitionAction.SOLVE_CURRENT_SESSION and self.adaptive_enabled:
            self._route_state = self._route_state.transition(
                challenge=ChallengeState.OBSERVED,
                session=SessionMode.STICKY,
            )
            self._select_capability(self._captcha_actions(body, headers))
            return kind
        if decision.action is TransitionAction.ACTIVATE_PROXY:
            # HTTP retry policy owns any bounded Retry-After sleep.  The route
            # controller may be called while a detail/browser limiter is held,
            # so it only changes route state and never sleeps here.
            if self.adaptive_enabled and not self.activate_proxy():
                self._rotate_proxy(
                    source_id,
                    status_code=status_code,
                    body=body,
                    error=error,
                )
            return kind
        if decision.action is TransitionAction.TLS_IMPERSONATION:
            if self.adaptive_enabled and self._capabilities[self.current_name].transport == "httpx":
                tls_candidates = tuple(
                    name
                    for name, capability in sorted(
                        self._capabilities.items(), key=lambda item: item[1].cost
                    )
                    if "tls_impersonation" in capability.challenge_actions
                )
                self._select_engine(tls_candidates)
            return kind
        if decision.action is TransitionAction.FINGERPRINT_RESISTANT_ENGINE:
            if self.adaptive_enabled:
                self._select_capability(("fingerprint_resistant", "terminal"))
            return kind
        if decision.action is TransitionAction.ACTIVATE_PROXY_THEN_FALLBACK:
            if (
                self.adaptive_enabled
                and not self.activate_proxy()
                and not self._rotate_proxy(
                    source_id,
                    status_code=status_code,
                    body=body,
                    error=error,
                )
            ):
                self.escalate()
            return kind
        if decision.action is TransitionAction.DEBOUNCED_PROXY:
            self._record_failure(source_id, kind)
            if (
                self.adaptive_enabled
                and self._should_escalate(source_id, threshold=self._timeout_threshold)
                and not self.activate_proxy()
            ):
                self._rotate_proxy(
                    source_id,
                    status_code=status_code,
                    body=body,
                    error=error,
                )
            return kind
        if decision.action is TransitionAction.RETRY_SAME_ROUTE:
            # Same-route retries belong to the caller's retry loop. Only when
            # those are exhausted for this operation do we force a route change
            # (defect B3): the exhaustion guard must escalate off the stuck
            # route, not forbid every transition as it did before.
            if self.adaptive_enabled and self._budget.same_route_retry_exhausted():
                logger.info(
                    "bypass_same_route_retry_exhausted",
                    tier=self.current_name,
                    failure_kind=kind,
                    status_code=status_code,
                )
                self.escalate()
            return kind
        # Server/parser/auth/board-gone failures are not anti-bot evidence.
        return kind

    def start_operation(
        self,
        operation_id: str,
        *,
        kind: str,
        max_browser_launches: int,
    ) -> Any:
        return self._budget.start_operation(
            operation_id,
            kind=kind,
            max_browser_launches=max_browser_launches,
        )

    def end_operation(self, token: Any) -> None:
        self._budget.end_operation(token)

    def _record_failure(self, source_id: str, kind: FailureKind) -> None:
        import time

        now = time.monotonic()
        window = self._failure_window.setdefault(source_id, deque())
        window.append((now, kind))
        cutoff = now - self.FAILURE_WINDOW_SECONDS
        while window and window[0][0] < cutoff:
            window.popleft()

    def _should_escalate(self, source_id: str, *, threshold: int | None = None) -> bool:
        window = self._failure_window.get(source_id)
        if not window:
            return False
        return len(window) >= (threshold or self.FAILURE_WINDOW_COUNT)

    async def apply_http(self, client: Any) -> Any:
        if self._context is not None:
            client = await self._context.apply_http(client, use_proxy=False)
        if (
            self._session_handoff is not None
            and not self.uses_proxy
            and self._capabilities[self.current_name].transport == "curl_impersonation"
        ):
            return await self._session_handoff.apply_http(client)
        proxy_setter = getattr(self._current_strategy, "set_proxy_url", None)
        if callable(proxy_setter):
            proxy_setter(self.current_proxy_url)
            return await self._current_strategy.apply_http(client)
        client = await self._current_strategy.apply_http(client)
        if self.uses_proxy and self._context is not None:
            client = await self._context.apply_http(client, use_proxy=True)

        return client

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._context is not None:
            kwargs = self._context.apply_browser_args(
                kwargs,
                use_proxy=self.uses_proxy,
            )
        return cast("dict[str, Any]", self._current_strategy.apply_browser_args(kwargs))

    async def apply_page(self, page: Any) -> None:
        """Apply the current tier's page hooks, then compose behavior_sim and orchestrator.

        BehaviorSim is a decorator, not a standalone escalation tier: it
        adds human-like mouse movement and timing on top of whatever
        browser tier is active (stealth_browser, camoufox, nodriver, …).
        For HTTP-only tiers (noop, curl_stealth) apply_page is never
        called by browser_utils, so the decorator stays dormant.
        """
        # The tier's own page hooks run first (nodriver/stealth_browser CDP
        # setup, camoufox no-op). Persona/JS hardening is applied exactly once
        # by ``_apply_per_request_hardening`` below — the previous extra
        # ``self._context.apply_page(page)`` call has been removed because it
        # injected a second, conflicting copy of the persona hardening blob.
        await self._current_strategy.apply_page(page)
        family = self._capabilities[self.current_name].browser_family or "chromium"
        is_chromium = family.startswith("chromium")
        # The reCAPTCHA action probe and the Chromium stealth blob are
        # Blink-specific. Injecting them into a Firefox engine (camoufox) both
        # fails and destroys camoufox's own C++-level fingerprint coherence, so
        # they are gated to Chromium-family engines only.
        if is_chromium:
            await self._install_recaptcha_action_probe(page)
        await self._behavior_sim.apply_page(page)
        if is_chromium:
            await self._apply_per_request_hardening(page)

        from job_ftch.infrastructure.bypass.multi_layer_obfuscation import ObfuscationContext

        ctx = ObfuscationContext(
            domain=getattr(self._context, "domain", "unknown"),
            persona_name=getattr(getattr(self._context, "persona", None), "name", "default"),
            browser_family=self._capabilities[self.current_name].browser_family or "chromium",
            transport="browser",
            page=page,
        )
        ctx.metadata["proxy_country"] = self._resolve_proxy_country()
        await self._obfuscation_orchestrator.apply_all(ctx)
        self._obfuscation_metadata = ctx.metadata
        self._behavior_sim.update_noise_params(ctx.metadata)

    async def _install_recaptcha_action_probe(self, page: Any) -> None:
        """Capture dynamic grecaptcha.execute actions before page scripts run.

        reCAPTCHA v3 provider payloads need the page action. Many SPAs do not
        expose it in the initial HTML; they call grecaptcha.execute from a
        bundled module after navigation. This hook records those calls without
        logging tokens, credentials, or page content.
        """
        add_init_script = getattr(page, "add_init_script", None)
        if not callable(add_init_script):
            return
        script = r"""
        (() => {
          if (window.__job_ftch_recaptcha_probe_installed) return;
          window.__job_ftch_recaptcha_probe_installed = true;
          window.__job_ftch_recaptcha_executes = window.__job_ftch_recaptcha_executes || [];
          const remember = (sitekey, options) => {
            try {
              const action = options && typeof options === 'object' ? String(options.action || '') : '';
              window.__job_ftch_recaptcha_executes.push({
                sitekey: sitekey ? String(sitekey) : '',
                action,
                ts: Date.now()
              });
              if (window.__job_ftch_recaptcha_executes.length > 20) {
                window.__job_ftch_recaptcha_executes.shift();
              }
            } catch (_) {}
          };
          const wrap = (grecaptcha) => {
            if (!grecaptcha || grecaptcha.__job_ftch_wrapped) return grecaptcha;
            const original = grecaptcha.execute;
            if (typeof original === 'function') {
              grecaptcha.execute = function(sitekey, options) {
                remember(sitekey, options);
                return original.apply(this, arguments);
              };
            }
            try { Object.defineProperty(grecaptcha, '__job_ftch_wrapped', {value: true}); } catch (_) {}
            return grecaptcha;
          };
          let current = window.grecaptcha;
          Object.defineProperty(window, 'grecaptcha', {
            configurable: true,
            get() { return current; },
            set(value) { current = wrap(value); }
          });
          if (current) current = wrap(current);
        })();
        """
        try:
            await add_init_script(script)
        except Exception:
            return

    async def _apply_per_request_hardening(self, page: Any) -> None:
        """Apply stealth hardening with session-stable seeds.

        Fingerprint seeds (canvas/audio/performance) MUST stay constant for
        the lifetime of a session generation: a real user's canvas and audio
        hashes do not change between page views, so rotating them per
        navigation is itself a bot signal (invariant I7). Seeds are therefore
        derived from the sticky persona, or from the current route-state
        generation when no persona is bound, and only change on a deliberate
        session reset (new generation / proxy rotation).
        """
        try:
            from job_ftch.infrastructure.bypass.stealth_hardening import (
                apply_stealth_hardening,
            )

            persona = None
            if self._context is not None:
                persona = getattr(self._context, "persona", None)
            # Stable per-session performance offset, deterministic in the
            # session generation so it does not flip between navigations.
            performance_offset = float(
                ((self._route_state.generation * 2654435761) % 4000) / 1000.0 - 2.0
            )
            canvas_seed = (
                int(getattr(persona, "canvas_seed", 0) or 0)
                or 1000 + (self._route_state.generation * 2654435761) % 99000
            )
            if persona is not None:
                from job_ftch.infrastructure.bypass.stealth_hardening import (
                    apply_persona_hardening,
                )

                await apply_persona_hardening(
                    page,
                    persona,
                    proxy_active=self.uses_proxy,
                    performance_offset=performance_offset,
                )
            else:
                await apply_stealth_hardening(
                    page,
                    canvas_seed=canvas_seed,
                    proxy_active=self.uses_proxy,
                    performance_offset=performance_offset,
                )
        except Exception:
            pass

    async def solve_page_challenge(self, page: Any, *, url: str) -> bool:
        """Run one bounded solver action in the current browser session."""
        solve_detected = getattr(self._captcha_solver, "solve_detected", None)
        if not callable(solve_detected):
            return False
        proxy_setter = getattr(self._captcha_solver, "set_proxy_url", None)
        if callable(proxy_setter) and self.current_proxy_url:
            proxy_setter(self.current_proxy_url)
        self._route_state = self._route_state.transition(
            challenge=ChallengeState.SOLVING,
            session=SessionMode.STICKY,
        )
        self._sync_context_route()
        solve = getattr(self._captcha_solver, "solve", None)
        observed_type = self._observed_challenge_type
        if observed_type and callable(solve):
            result = await solve(page, challenge_type=observed_type, url=url)
        else:
            result = await solve_detected(page, url=url)
        solved = bool(getattr(result, "solved", False))
        logger.info(
            "bypass_captcha_solver_result",
            solved=solved,
            challenge_type=getattr(result, "challenge_type", None),
            result_kind=(
                getattr(getattr(result, "result_kind", None), "value", None)
                or getattr(result, "result_kind", None)
            ),
            failure_reason=(
                getattr(getattr(result, "failure_reason", None), "value", None)
                or getattr(result, "failure_reason", None)
            ),
            provider_task_id_present=bool(getattr(result, "provider_task_id", None)),
            raw_provider_status=getattr(result, "raw_provider_status", None),
            error=getattr(result, "error", None),
        )
        if solved:
            self._route_state = self._route_state.transition(challenge=ChallengeState.NONE)
            self._observed_challenge_type = None
            self._sync_context_route()
        return solved


@register_bypass("auto")
def _create_adaptive(
    bypass_config: dict[str, Any] | None = None,
) -> AdaptiveBypassManager | Any:
    from job_ftch.config import get_settings

    if not get_settings().adaptive_scraping_enabled:
        return resolve_bypass("noop")
    return AdaptiveBypassManager(bypass_config, adaptive_enabled=True)
