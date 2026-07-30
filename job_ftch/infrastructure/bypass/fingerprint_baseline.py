"""Fingerprint baseline store (Wave 0.1).

Persists per-persona fingerprint/TLS baselines so that live probes can diff
against a committed reference and regression-test stealth consistency.

Default backing: the ``StoreConnector`` KV interface (which the PostgreSQL
adapter maps onto ``jf_kv``). When no connector is provided the baselines
are written to ``tests/fixtures/bypass/baselines/*.json`` so the repo stays
self-describing without external infra (the file fallback is the canonical
place for committed baselines; PG is for tenant/runtime telemetry that
operators want to refresh on a schedule).

Keys live under ``fp_baseline:{scope}:{persona_name}`` where ``scope`` is
``fingerprint`` (CreepJS/sannysoft verdict) or ``tls`` (JA3/JA4/JA4H/h2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from job_ftch.application.contracts import StoreConnector

logger = structlog.get_logger("job_ftch.bypass.fingerprint_baseline")

_KEY_PREFIX = "fp_baseline"
_FILE_FALLBACK_DIR = Path("tests/fixtures/bypass/baselines")


@dataclass(slots=True)
class BaselineRecord:
    """Single fingerprint/TLS baseline observation for one persona."""

    persona_name: str
    scope: str  # "fingerprint" | "tls"
    generated_at: str  # ISO 8601 UTC
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "persona_name": self.persona_name,
                "scope": self.scope,
                "generated_at": self.generated_at,
                "payload": self.payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> BaselineRecord:
        data = json.loads(raw)
        return cls(
            persona_name=str(data["persona_name"]),
            scope=str(data["scope"]),
            generated_at=str(data["generated_at"]),
            payload=dict(data.get("payload", {})),
        )

    @property
    def key(self) -> str:
        return f"{_KEY_PREFIX}:{self.scope}:{self.persona_name}"


class FingerprintBaselineStore:
    """Facade over StoreConnector (PG/SQLite) with a file fallback.

    The fallback lives at ``tests/fixtures/bypass/baselines/`` so the test
    suite can assert against committed baselines even when ``store_dsn`` is
    unset (the common CI case). PG is the default for production tenants
    that want to refresh baselines nightly via the fingerprint-harness.
    """

    def __init__(
        self,
        *,
        connector: StoreConnector | None = None,
        fallback_dir: Path | str | None = None,
    ) -> None:
        self._connector = connector
        self._fallback_dir = Path(fallback_dir) if fallback_dir else _FILE_FALLBACK_DIR

    @classmethod
    def from_connector(
        cls,
        connector: StoreConnector | None = None,
        *,
        fallback_dir: Path | str | None = None,
    ) -> FingerprintBaselineStore:
        """Build a baseline store from an already-constructed connector.

        Callers that hold an async-initialised Store/StoreConnector pass it
        here. Scripts and tests that run without infrastructure simply call
        ``FingerprintBaselineStore()`` with no arguments to get the file
        fallback.
        """
        return cls(connector=connector, fallback_dir=fallback_dir)

    async def save(self, record: BaselineRecord) -> None:
        if self._connector is not None:
            await self._connector.set(record.key, record.to_json())
            return
        self._fallback_dir.mkdir(parents=True, exist_ok=True)
        path = self._fallback_dir / f"{record.scope}_{record.persona_name}.json"
        path.write_text(record.to_json(), encoding="utf-8")

    async def load(self, persona_name: str, scope: str) -> BaselineRecord | None:
        key = f"{_KEY_PREFIX}:{scope}:{persona_name}"
        if self._connector is not None:
            raw = await self._connector.get(key)
            if raw is None:
                return None
            try:
                return BaselineRecord.from_json(raw)
            except (json.JSONDecodeError, KeyError):
                return None
        path = self._fallback_dir / f"{scope}_{persona_name}.json"
        if not path.exists():
            return None
        try:
            return BaselineRecord.from_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None


@dataclass(slots=True)
class BaselineDiff:
    """Result of comparing a live probe against the committed baseline."""

    persona_name: str
    scope: str
    matched: bool
    diffs: dict[str, str]
    warnings: list[str]


def compare_tls(
    baseline: BaselineRecord,
    live: dict[str, Any],
    *,
    strict: bool = True,
) -> BaselineDiff:
    """Diff live TLS probe against baseline. Strict on JA3/JA4/JA4H hashes."""
    fields = ("ja3_hash", "ja4", "ja4_h", "akamai_h2_fingerprint")
    diffs: dict[str, str] = {}
    warnings: list[str] = []
    base_payload = baseline.payload
    for field in fields:
        base_value = base_payload.get(field)
        live_value = live.get(field)
        if base_value is None:
            warnings.append(f"baseline_missing:{field}")
            continue
        if base_value != live_value:
            diffs[field] = f"{base_value!r} != {live_value!r}"
    return BaselineDiff(
        persona_name=baseline.persona_name,
        scope="tls",
        matched=not diffs,
        diffs=diffs,
        warnings=warnings,
    )


_CREEP_TRUST_KEYS = (
    "trust_score",
    "fingerprint",
    "lies",
    "webgl_vendors",
    "webgl_renderer",
    "hardware_concurrency",
    "device_memory",
    "screen_width",
    "font_enumeration_count",
    "font_list_match",
    "sec_ch_ua",
)


def compare_fingerprint(
    baseline: BaselineRecord,
    live: dict[str, Any],
) -> BaselineDiff:
    """Diff live CreepJS-style fingerprint against baseline.

    Regressions of interest (Wave 0.1 acceptance):
    - ``tamper_detected`` flipping true (toString guard broke)
    - ``hardware_concurrency``/``device_memory`` silently mismatching the
      persona (registry leakage)
    - ``webgl_renderer`` flipping (GPU/SwiftShader drift across container
      rebuilds)
    - >2 personas sharing the same hardware fingerprint tuple (added by the
      test harness, not here)
    """
    base_payload = baseline.payload
    diffs: dict[str, str] = {}
    warnings: list[str] = []
    for key in _CREEP_TRUST_KEYS:
        base_value = base_payload.get(key)
        live_value = live.get(key)
        if base_value is None:
            continue
        if base_value != live_value:
            diffs[key] = f"{base_value!r} != {live_value!r}"
    if live.get("tamper_detected") is True:
        diffs["tamper_detected"] = "true (toString guard regression)"
    return BaselineDiff(
        persona_name=baseline.persona_name,
        scope="fingerprint",
        matched=not diffs,
        diffs=diffs,
        warnings=warnings,
    )


def pairwise_hardware_duplicates(records: list[dict[str, Any]]) -> list[list[str]]:
    """Find groups of personas sharing the same (cores, memory, renderer).

    Wave 0.1 acceptance: no tuple may be shared by more than 2 personas.
    Returns a list of persona-name groups whose tuple was over-shared.
    """
    from collections import defaultdict

    by_tuple: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for record in records:
        tuple_key = (
            record.get("browser_family"),
            record.get("hardware_concurrency"),
            record.get("device_memory"),
            record.get("webgl_renderer"),
        )
        name = record.get("persona_name") or record.get("name") or ""
        if name:
            by_tuple[tuple_key].append(name)
    return [names for names in by_tuple.values() if len(names) > 2]
