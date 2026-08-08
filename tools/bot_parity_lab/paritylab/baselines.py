from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BaselineProfile:
    id: str
    browser: str
    engine: str
    os: str
    mode: str
    controller: str
    expected_disposition: str
    minimum_runs: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BaselineProfile:
        return cls(**value)


@dataclass(frozen=True, slots=True)
class BaselineRun:
    path: Path
    profile_id: str
    finding_codes: frozenset[str]
    probe_realms: frozenset[str]
    http_versions: tuple[str, ...]
    score: int | None
    disposition: str | None
    observed_browser: str


@dataclass(frozen=True, slots=True)
class ProfileAudit:
    profile_id: str
    run_count: int
    minimum_runs: int
    complete: bool
    stable_finding_ratio: float | None
    realm_coverage: tuple[str, ...]
    http_versions: tuple[str, ...]
    disposition_counts: dict[str, int]
    observed_browsers: tuple[str, ...]
    browser_mismatch_count: int


@dataclass(frozen=True, slots=True)
class BaselineAudit:
    version: str
    profiles: tuple[ProfileAudit, ...]

    @property
    def complete(self) -> bool:
        return bool(self.profiles) and all(profile.complete for profile in self.profiles)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "complete": self.complete,
            "profiles": [
                {
                    "profile_id": item.profile_id,
                    "run_count": item.run_count,
                    "minimum_runs": item.minimum_runs,
                    "complete": item.complete,
                    "stable_finding_ratio": item.stable_finding_ratio,
                    "realm_coverage": list(item.realm_coverage),
                    "http_versions": list(item.http_versions),
                    "disposition_counts": item.disposition_counts,
                    "observed_browsers": list(item.observed_browsers),
                    "browser_mismatch_count": item.browser_mismatch_count,
                }
                for item in self.profiles
            ],
        }


def load_profiles(path: Path) -> tuple[str, tuple[BaselineProfile, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = tuple(BaselineProfile.from_dict(item) for item in payload["profiles"])
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate baseline profile id")
    return str(payload["version"]), profiles


def load_baseline_runs(root: Path) -> tuple[BaselineRun, ...]:
    runs: list[BaselineRun] = []
    for path in sorted(root.glob("**/raw.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = payload.get("metadata", {})
        profile_id = metadata.get("baseline_profile") if isinstance(metadata, dict) else None
        if not isinstance(profile_id, str) or not profile_id:
            continue
        findings = payload.get("findings", [])
        probes = payload.get("probes", [])
        requests = payload.get("requests", [])
        summary = payload.get("summary")
        runs.append(
            BaselineRun(
                path=path,
                profile_id=profile_id,
                finding_codes=frozenset(
                    str(item.get("code"))
                    for item in findings
                    if isinstance(item, dict) and item.get("code")
                ),
                probe_realms=frozenset(
                    str(item.get("realm"))
                    for item in probes
                    if isinstance(item, dict) and item.get("realm")
                ),
                http_versions=tuple(
                    sorted(
                        {
                            str(item.get("http_version"))
                            for item in requests
                            if isinstance(item, dict) and item.get("http_version")
                        }
                    )
                ),
                score=int(summary["score"])
                if isinstance(summary, dict) and isinstance(summary.get("score"), int)
                else None,
                disposition=str(summary["disposition"])
                if isinstance(summary, dict) and summary.get("disposition")
                else None,
                observed_browser=_observed_browser(requests),
            )
        )
    return tuple(runs)


def audit_baselines(
    version: str, profiles: tuple[BaselineProfile, ...], runs: tuple[BaselineRun, ...]
) -> BaselineAudit:
    grouped: dict[str, list[BaselineRun]] = defaultdict(list)
    for run in runs:
        grouped[run.profile_id].append(run)
    audits: list[ProfileAudit] = []
    for profile in profiles:
        profile_runs = grouped.get(profile.id, [])
        stable_ratio = _stable_finding_ratio(profile_runs)
        dispositions = Counter(run.disposition or "missing" for run in profile_runs)
        expected_ok = bool(profile_runs) and all(
            run.disposition == profile.expected_disposition for run in profile_runs
        )
        browser_mismatches = sum(
            run.observed_browser != profile.browser
            for run in profile_runs
            if profile.browser != "none"
        )
        audits.append(
            ProfileAudit(
                profile_id=profile.id,
                run_count=len(profile_runs),
                minimum_runs=profile.minimum_runs,
                complete=(
                    len(profile_runs) >= profile.minimum_runs
                    and expected_ok
                    and browser_mismatches == 0
                ),
                stable_finding_ratio=stable_ratio,
                realm_coverage=tuple(
                    sorted(set().union(*(run.probe_realms for run in profile_runs)))
                ),
                http_versions=tuple(
                    sorted(set().union(*(set(run.http_versions) for run in profile_runs)))
                ),
                disposition_counts=dict(dispositions),
                observed_browsers=tuple(
                    sorted({run.observed_browser for run in profile_runs if run.observed_browser})
                ),
                browser_mismatch_count=browser_mismatches,
            )
        )
    return BaselineAudit(version, tuple(audits))


def _stable_finding_ratio(runs: list[BaselineRun]) -> float | None:
    if len(runs) < 2:
        return None
    all_codes = set().union(*(run.finding_codes for run in runs))
    if not all_codes:
        return 1.0
    frequencies = [sum(code in run.finding_codes for run in runs) / len(runs) for code in all_codes]
    return round(statistics.fmean(max(value, 1 - value) for value in frequencies), 6)


def _observed_browser(requests: object) -> str:
    if not isinstance(requests, list):
        return "unknown"
    for request in requests:
        if not isinstance(request, dict) or request.get("path") != "/":
            continue
        headers = request.get("headers", [])
        if not isinstance(headers, list):
            continue
        values = {
            str(item[0]).lower(): str(item[1])
            for item in headers
            if isinstance(item, list) and len(item) == 2
        }
        ua = values.get("user-agent", "").lower()
        if "firefox/" in ua:
            return "firefox"
        if any(token in ua for token in ("chrome/", "chromium/", "edg/")):
            return "chromium"
        if "applewebkit/" in ua and "safari/" in ua:
            return "webkit"
    return "unknown"
