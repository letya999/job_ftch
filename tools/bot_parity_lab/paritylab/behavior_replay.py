from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path

from paritylab.models import BehaviorEvent, JsonValue, utc_now_iso


@dataclass(frozen=True, slots=True)
class BehaviorSignature:
    event_count: int
    feature_count: int
    exact_sha256: str
    simhash64: int


@dataclass(frozen=True, slots=True)
class ReplayAssessment:
    eligible: bool
    exact_match: bool
    nearest_similarity: float | None
    matched_session_hash: str | None
    compared_count: int
    signature_prefix: str | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "eligible": self.eligible,
            "exact_match": self.exact_match,
            "nearest_similarity": self.nearest_similarity,
            "matched_session_hash": self.matched_session_hash,
            "compared_count": self.compared_count,
            "signature_prefix": self.signature_prefix,
        }


def behavior_signature(events: list[BehaviorEvent]) -> BehaviorSignature | None:
    ordered = sorted(events, key=lambda item: (item.since_navigation_ms, item.sequence))
    if len(ordered) < 8:
        return None
    features: list[str] = []
    types = [event.event_type for event in ordered]
    features.extend(f"type:{item}" for item in types)
    features.extend(f"bigram:{left}>{right}" for left, right in pairwise(types))
    for previous, current in pairwise(ordered):
        interval = max(0.0, current.since_navigation_ms - previous.since_navigation_ms)
        features.append(f"dt:{min(100, round(interval / 10))}")
    pointer = [event for event in ordered if event.event_type in {"pointermove", "mousemove"}]
    for previous, current in pairwise(pointer):
        p, c = _point(previous), _point(current)
        if p is None or c is None:
            continue
        dx, dy = c[0] - p[0], c[1] - p[1]
        distance = math.hypot(dx, dy)
        direction = round(((math.atan2(dy, dx) + math.pi) / (2 * math.pi)) * 16) % 16
        features.append(f"move:{direction}:{min(20, round(distance / 25))}")
    for event in ordered:
        if event.event_type in {"wheel", "scroll"}:
            delta = event.data.get("deltaY", event.data.get("scrollY"))
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                features.append(f"scroll:{max(-20, min(20, round(float(delta) / 50)))}")
        elif event.event_type in {"keydown", "keyup"}:
            features.append(
                f"key:{event.event_type}:{event.data.get('category', 'unknown')}:{bool(event.data.get('repeat'))}"
            )
    canonical = "|".join(features)
    exact = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    weights = [0] * 64
    for feature in features:
        value = int.from_bytes(
            hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    simhash = sum((1 << bit) for bit, weight in enumerate(weights) if weight >= 0)
    return BehaviorSignature(len(ordered), len(features), exact, simhash)


def _point(event: BehaviorEvent) -> tuple[float, float] | None:
    x, y = event.data.get("x"), event.data.get("y")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y)):
        return None
    assert isinstance(x, (int, float)) and isinstance(y, (int, float))
    return float(x), float(y)


def simhash_similarity(left: int, right: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / 64.0)


class BehaviorReplayIndex:
    def __init__(self, path: Path, *, near_threshold: float = 0.94) -> None:
        self.path = path
        self.near_threshold = near_threshold
        self._lock = asyncio.Lock()
        self._records = self._load()

    def _load(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        records: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    async def assess_and_record(
        self, session_id: str, events: list[BehaviorEvent]
    ) -> ReplayAssessment:
        signature = behavior_signature(events)
        if signature is None:
            return ReplayAssessment(False, False, None, None, 0, None)
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        async with self._lock:
            nearest, matched, exact, compared = 0.0, None, False, 0
            for record in self._records:
                if record.get("session_hash") == session_hash:
                    continue
                prior_count = int(record.get("event_count", 0))
                if (
                    prior_count
                    and abs(prior_count - signature.event_count)
                    / max(prior_count, signature.event_count)
                    > 0.25
                ):
                    continue
                compared += 1
                is_exact = record.get("exact_sha256") == signature.exact_sha256
                similarity = simhash_similarity(
                    signature.simhash64, int(str(record.get("simhash64", "0")), 16)
                )
                if is_exact or similarity > nearest:
                    exact, nearest = is_exact, 1.0 if is_exact else similarity
                    matched = str(record.get("session_hash", "")) or None
            record = {
                **asdict(signature),
                "simhash64": f"{signature.simhash64:016x}",
                "session_hash": session_hash,
                "observed_at": utc_now_iso(),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            self._records.append(record)
        return ReplayAssessment(
            True,
            exact,
            round(nearest, 6) if matched else None,
            matched if exact or nearest >= self.near_threshold else None,
            compared,
            signature.exact_sha256[:16],
        )
