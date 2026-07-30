"""Domain Intelligence cache for bypass routing decisions.

Persists per-domain knowledge across runs:
- Last successful bypass tier
- Known anti-bot vendor (Cloudflare, DataDome, PerimeterX, etc.)
- Known API endpoints (JSON feeds discovered by api_sniffer)
- Rate limit thresholds
- CF clearance cookies with TTL

Integrates with RiskRouter reputation scoring to skip escalation
warmup for known-difficult domains.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog

logger = structlog.get_logger("job_ftch.bypass.domain_intel")

_DEFAULT_CACHE_PATH = ".runtime/domain_intel.json"
_CF_COOKIE_TTL = 900.0  # 15 minutes default
_ROUTE_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_API_ENDPOINTS = 50
_MAX_SUCCESSFUL_PAIRS = 20
_COOKIE_ALLOWLIST = frozenset({"cf_clearance", "cf_bm", "dd_cookie", "_px3", "datadome"})
_LOCAL_FILE_LOCK = threading.RLock()


@dataclass(slots=True)
class CachedCookie:
    name: str
    value: str
    domain: str
    expires_at: float
    exit_ip: str = ""
    persona_id: str = ""

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @property
    def route_key(self) -> tuple[str, str, str]:
        return (self.domain, self.exit_ip, self.persona_id)


@dataclass(slots=True)
class TierStats:
    ok: int = 0
    blocked: int = 0
    captcha: int = 0
    timeout: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.ok + self.blocked + self.captcha + self.timeout

    @property
    def ok_rate(self) -> float:
        t = self.total
        return self.ok / t if t > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "captcha": self.captcha,
            "timeout": self.timeout,
            "by_kind": dict(self.by_kind),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TierStats:
        return cls(
            ok=data.get("ok", 0),
            blocked=data.get("blocked", 0),
            captcha=data.get("captcha", 0),
            timeout=data.get("timeout", 0),
            by_kind=dict(data.get("by_kind", {})),
        )


@dataclass(slots=True)
class DomainEntry:
    domain: str
    last_successful_tier: str = ""
    last_successful_network: str = "direct"
    known_vendor: str = ""
    api_endpoints: list[str] = field(default_factory=list)
    successful_monitor_scrapers: list[str] = field(default_factory=list)
    rate_limit_rpm: int = 0
    robots_txt_disallowed: bool = False
    has_sitemap: bool = False
    cookies: list[CachedCookie] = field(default_factory=list)
    tier_stats: dict[str, TierStats] = field(default_factory=dict)
    route_failure_streaks: dict[str, int] = field(default_factory=dict)
    last_updated: float = 0.0

    def add_cookie(
        self,
        name: str,
        value: str,
        *,
        ttl: float = _CF_COOKIE_TTL,
        exit_ip: str = "",
        persona_id: str = "",
    ) -> None:
        if name.lower() not in _COOKIE_ALLOWLIST:
            return
        self.cookies = [
            c
            for c in self.cookies
            if not (c.name == name and c.exit_ip == exit_ip and c.persona_id == persona_id)
        ]
        self.cookies.append(
            CachedCookie(
                name=name,
                value=value,
                domain=self.domain,
                expires_at=time.monotonic() + ttl,
                exit_ip=exit_ip,
                persona_id=persona_id,
            )
        )

    def get_valid_cookies(
        self,
        *,
        exit_ip: str = "",
        persona_id: str = "",
    ) -> dict[str, str]:
        self.cookies = [c for c in self.cookies if not c.expired]
        if not exit_ip and not persona_id:
            return {c.name: c.value for c in self.cookies}
        return {
            c.name: c.value
            for c in self.cookies
            if (not exit_ip or c.exit_ip == exit_ip)
            and (not persona_id or c.persona_id == persona_id)
        }

    def get_tier_stats(self, tier: str) -> TierStats:
        if tier not in self.tier_stats:
            self.tier_stats[tier] = TierStats()
        return self.tier_stats[tier]

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "last_successful_tier": self.last_successful_tier,
            "last_successful_network": self.last_successful_network,
            "known_vendor": self.known_vendor,
            "api_endpoints": self.api_endpoints,
            "successful_monitor_scrapers": self.successful_monitor_scrapers,
            "rate_limit_rpm": self.rate_limit_rpm,
            "robots_txt_disallowed": self.robots_txt_disallowed,
            "has_sitemap": self.has_sitemap,
            "tier_stats": {t: s.to_dict() for t, s in self.tier_stats.items()},
            "route_failure_streaks": dict(self.route_failure_streaks),
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainEntry:
        raw_stats = data.get("tier_stats", {})
        tier_stats = {t: TierStats.from_dict(s) for t, s in raw_stats.items()} if raw_stats else {}
        return cls(
            domain=data.get("domain", ""),
            last_successful_tier=data.get("last_successful_tier", ""),
            last_successful_network=data.get("last_successful_network", "direct"),
            known_vendor=data.get("known_vendor", ""),
            api_endpoints=list(data.get("api_endpoints", []))[-_MAX_API_ENDPOINTS:],
            successful_monitor_scrapers=list(data.get("successful_monitor_scrapers", []))[
                -_MAX_SUCCESSFUL_PAIRS:
            ],
            rate_limit_rpm=data.get("rate_limit_rpm", 0),
            robots_txt_disallowed=data.get("robots_txt_disallowed", False),
            has_sitemap=data.get("has_sitemap", False),
            tier_stats=tier_stats,
            route_failure_streaks=dict(data.get("route_failure_streaks", {})),
            last_updated=data.get("last_updated", 0.0),
        )


def _sanitize_endpoint(endpoint: str) -> str:
    """Keep endpoint shape while dropping credentials, values and fragments."""
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return ""
    try:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return ""
    query_keys = sorted({key for key, _value in parse_qsl(parsed.query, keep_blank_values=True)})
    safe_query = urlencode([(key, "") for key in query_keys])
    return urlunsplit((parsed.scheme, host, parsed.path, safe_query, ""))


@contextmanager
def _exclusive_file_lock(path: Path) -> Any:
    """Cross-process advisory lock next to the cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with _LOCAL_FILE_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import importlib

            fcntl: Any = importlib.import_module("fcntl")

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_cache_data(path: Path, *, tolerate_corruption: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {"domains": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        if tolerate_corruption:
            return {"domains": []}
        raise
    return data if isinstance(data, dict) else {"domains": []}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _apply_operation(
    entries: dict[str, DomainEntry],
    operation: tuple[Any, ...],
) -> None:
    kind, domain, *values = operation
    entry = entries.setdefault(domain, DomainEntry(domain=domain))
    now = time.time()
    if kind == "success":
        tier, network = str(values[0]), str(values[1])
        route = _route_key(tier, network)
        entry.last_successful_tier = tier
        entry.last_successful_network = network
        entry.get_tier_stats(route).ok += 1
        entry.route_failure_streaks[route] = 0
    elif kind == "failure":
        tier, network, failure_kind = str(values[0]), str(values[1]), str(values[2])
        stats = entry.get_tier_stats(_route_key(tier, network))
        if failure_kind == "captcha":
            stats.captcha += 1
        elif failure_kind == "timeout":
            stats.timeout += 1
        else:
            stats.blocked += 1
        stats.by_kind[failure_kind] = stats.by_kind.get(failure_kind, 0) + 1
        route = _route_key(tier, network)
        entry.route_failure_streaks[route] = entry.route_failure_streaks.get(route, 0) + 1
        if (
            entry.route_failure_streaks[route] >= 3
            and entry.last_successful_tier == tier
            and entry.last_successful_network == network
        ):
            entry.last_successful_tier = ""
            entry.last_successful_network = "direct"
    elif kind == "vendor":
        entry.known_vendor = str(values[0])
    elif kind == "endpoint":
        endpoint = str(values[0])
        if endpoint not in entry.api_endpoints:
            entry.api_endpoints.append(endpoint)
            entry.api_endpoints = entry.api_endpoints[-_MAX_API_ENDPOINTS:]
    elif kind == "pair":
        pair = str(values[0])
        if pair not in entry.successful_monitor_scrapers:
            entry.successful_monitor_scrapers.append(pair)
            entry.successful_monitor_scrapers = entry.successful_monitor_scrapers[
                -_MAX_SUCCESSFUL_PAIRS:
            ]
    entry.last_updated = now


def _route_key(tier: str, network: str) -> str:
    return tier if network == "direct" else f"{tier}@{network}"


def _split_route_key(route: str) -> tuple[str, str]:
    tier, separator, network = route.partition("@")
    return tier, network if separator else "direct"


class DomainIntelligence:
    """Persistent domain knowledge cache."""

    def __init__(self, cache_path: str | Path | None = None) -> None:
        self._path = Path(cache_path) if cache_path else Path(_DEFAULT_CACHE_PATH)
        self._entries: dict[str, DomainEntry] = {}
        self._dirty = False
        self._pending_operations: list[tuple[Any, ...]] = []

        from job_ftch.infrastructure.bypass.fingerprint_evolution import FingerprintEvolution
        from job_ftch.infrastructure.bypass.temporal_graph import TemporalConsistencyGraph

        self.temporal_graph = TemporalConsistencyGraph()
        self.fingerprint_evolution = FingerprintEvolution()
        self.fingerprint_evolution.seed_population()
        self._evolution_outcomes_since_evolve = 0
        self._EVOLVE_THRESHOLD = 20

    def get(self, domain: str) -> DomainEntry:
        domain = domain.lower().strip()
        if domain not in self._entries:
            self._entries[domain] = DomainEntry(domain=domain)
        return self._entries[domain]

    def record_success(self, domain: str, tier: str, *, network: str = "direct") -> None:
        entry = self.get(domain)
        entry.last_successful_tier = tier
        entry.last_successful_network = network
        entry.get_tier_stats(_route_key(tier, network)).ok += 1
        entry.route_failure_streaks[_route_key(tier, network)] = 0
        entry.last_updated = time.time()
        self._dirty = True
        self._pending_operations.append(("success", domain.lower().strip(), tier, network))
        self._record_fingerprint_outcome(success=True)

    def record_failure_kind(
        self,
        domain: str,
        tier: str,
        kind: str,
        *,
        network: str = "direct",
    ) -> None:
        entry = self.get(domain)
        stats = entry.get_tier_stats(_route_key(tier, network))
        if kind == "captcha":
            stats.captcha += 1
        elif kind == "timeout":
            stats.timeout += 1
        else:
            stats.blocked += 1
        stats.by_kind[kind] = stats.by_kind.get(kind, 0) + 1
        route = _route_key(tier, network)
        entry.route_failure_streaks[route] = entry.route_failure_streaks.get(route, 0) + 1
        if (
            entry.route_failure_streaks[route] >= 3
            and entry.last_successful_tier == tier
            and entry.last_successful_network == network
        ):
            entry.last_successful_tier = ""
            entry.last_successful_network = "direct"
        entry.last_updated = time.time()
        self._dirty = True
        self._pending_operations.append(("failure", domain.lower().strip(), tier, network, kind))
        self._record_fingerprint_outcome(success=False)

    def _record_fingerprint_outcome(self, *, success: bool) -> None:
        """Feed outcome to the fingerprint evolution genetic algorithm."""
        best = self.fingerprint_evolution.best_gene()
        if best is not None:
            self.fingerprint_evolution.record_outcome(best.gene_id, success=success)
        self._evolution_outcomes_since_evolve += 1
        if self._evolution_outcomes_since_evolve >= self._EVOLVE_THRESHOLD:
            self.fingerprint_evolution.evolve()
            self._evolution_outcomes_since_evolve = 0

    def get_best_fingerprint(self) -> Any:
        """Return the fittest fingerprint gene for use in persona alignment."""
        return self.fingerprint_evolution.best_gene()

    def get_baseline_store(self) -> Any:
        """Return a FingerprintBaselineStore for coherence auditing."""
        from job_ftch.infrastructure.bypass.fingerprint_baseline import FingerprintBaselineStore

        return FingerprintBaselineStore()

    def record_vendor(self, domain: str, vendor: str) -> None:
        entry = self.get(domain)
        entry.known_vendor = vendor
        entry.last_updated = time.time()
        self._dirty = True
        self._pending_operations.append(("vendor", domain.lower().strip(), vendor))

    def add_api_endpoint(self, domain: str, endpoint: str) -> None:
        endpoint = _sanitize_endpoint(endpoint)
        if not endpoint:
            return
        entry = self.get(domain)
        if endpoint not in entry.api_endpoints:
            entry.api_endpoints.append(endpoint)
            entry.api_endpoints = entry.api_endpoints[-_MAX_API_ENDPOINTS:]
            entry.last_updated = time.time()
            self._dirty = True
            self._pending_operations.append(("endpoint", domain.lower().strip(), endpoint))

    def record_monitor_scraper(self, domain: str, monitor: str, scraper: str | None) -> None:
        pair = f"{monitor}:{scraper or 'rich_payload'}"
        entry = self.get(domain)
        if pair not in entry.successful_monitor_scrapers:
            entry.successful_monitor_scrapers.append(pair)
            entry.successful_monitor_scrapers = entry.successful_monitor_scrapers[
                -_MAX_SUCCESSFUL_PAIRS:
            ]
            entry.last_updated = time.time()
            self._dirty = True
            self._pending_operations.append(("pair", domain.lower().strip(), pair))

    def cache_cookie(
        self,
        domain: str,
        name: str,
        value: str,
        *,
        ttl: float = _CF_COOKIE_TTL,
        exit_ip: str = "",
        persona_id: str = "",
    ) -> None:
        if name.lower() not in _COOKIE_ALLOWLIST:
            return
        entry = self.get(domain)
        entry.add_cookie(name, value, ttl=ttl, exit_ip=exit_ip, persona_id=persona_id)
        self._dirty = True

    def get_cookies(
        self,
        domain: str,
        *,
        exit_ip: str = "",
        persona_id: str = "",
    ) -> dict[str, str]:
        return self.get(domain).get_valid_cookies(
            exit_ip=exit_ip,
            persona_id=persona_id,
        )

    _MIN_OBSERVATIONS = 3

    def get_recommended_tier(self, domain: str) -> str | None:
        """Return the engine portion of the recommended successful route."""
        route = self.get_recommended_route(domain)
        return route[0] if route is not None else None

    def get_recommended_route(self, domain: str) -> tuple[str, str] | None:
        """Return a fresh successful (engine, network) recommendation."""
        entry = self.get(domain)
        if entry.last_updated and (time.time() - entry.last_updated) > _ROUTE_TTL_SECONDS:
            return None
        best_tier: str | None = None
        best_rate: float = -1.0
        for tier, stats in entry.tier_stats.items():
            if (
                stats.total >= self._MIN_OBSERVATIONS
                and entry.route_failure_streaks.get(tier, 0) < 3
                and stats.ok_rate > best_rate
            ):
                best_rate = stats.ok_rate
                best_tier = tier
        if best_tier:
            return _split_route_key(best_tier)
        if entry.last_successful_tier:
            return entry.last_successful_tier, entry.last_successful_network
        return None

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            with _exclusive_file_lock(self._path):
                data = _read_cache_data(self._path)
            for entry_data in data.get("domains", []):
                entry = DomainEntry.from_dict(entry_data)
                if entry.domain:
                    self._entries[entry.domain] = entry
            logger.info("domain_intel_loaded", count=len(self._entries))
            self._pending_operations.clear()
        except Exception as exc:
            logger.warning("domain_intel_load_failed", error=type(exc).__name__)

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with _exclusive_file_lock(self._path):
                disk_data = _read_cache_data(self._path, tolerate_corruption=True)
                merged = {
                    item.domain: item
                    for raw in disk_data.get("domains", [])
                    if (item := DomainEntry.from_dict(raw)).domain
                }
                if not merged and not self._pending_operations:
                    merged = dict(self._entries)
                for operation in self._pending_operations:
                    _apply_operation(merged, operation)
                for domain, current in self._entries.items():
                    if current.cookies and domain in merged:
                        merged[domain].cookies = list(current.cookies)
                data = {
                    "domains": [merged[key].to_dict() for key in sorted(merged)],
                }
                _atomic_write_json(self._path, data)
                self._entries = merged
            self._dirty = False
            self._pending_operations.clear()
            logger.debug("domain_intel_saved", count=len(self._entries))
        except Exception as exc:
            logger.warning("domain_intel_save_failed", error=type(exc).__name__)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_domains": len(self._entries),
            "with_vendor": sum(1 for e in self._entries.values() if e.known_vendor),
            "with_api": sum(1 for e in self._entries.values() if e.api_endpoints),
            "with_tier": sum(1 for e in self._entries.values() if e.last_successful_tier),
        }


_instance: DomainIntelligence | None = None


def get_domain_intel(cache_path: str | Path | None = None) -> DomainIntelligence:
    global _instance
    if _instance is None:
        _instance = DomainIntelligence(cache_path)
        _instance.load()
    return _instance
