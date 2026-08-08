from __future__ import annotations

from collections.abc import Mapping

from paritylab.models import Finding, SessionState, SignalClass
from paritylab.scoring.common import _deep_get, _finding, _realm_map


def _capability_findings(session: SessionState) -> list[Finding]:
    deep = _realm_map(session).get("deep", {})
    runtime = _deep_get(deep, "extras.runtime", {})
    if not isinstance(runtime, Mapping):
        return []
    findings: list[Finding] = []

    storage = runtime.get("storage")
    if not isinstance(storage, Mapping) or not storage:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_STORAGE_CAPABILITY_MISSING",
                "Storage capability shape missing",
                "Quota, persistence, cache, worker and partition-related storage APIs were not reported.",
                realms=["deep"],
            )
        )
    else:
        quota = _deep_get(storage, "estimate.quota")
        usage = _deep_get(storage, "estimate.usage")
        if isinstance(quota, (int, float)) and isinstance(usage, (int, float)) and usage > quota:
            findings.append(
                _finding(
                    SignalClass.MEDIUM,
                    "JS_STORAGE_QUOTA_CONFLICT",
                    "Storage usage exceeds reported quota",
                    "StorageManager returned a usage value greater than its quota.",
                    evidence={"usage": usage, "quota": quota},
                    realms=["deep"],
                )
            )
        embedded = runtime.get("embeddedStorage")
        if not isinstance(embedded, Mapping) or embedded.get("supported") is not True:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "JS_STORAGE_EMBEDDED_PROBE_MISSING",
                    "Embedded storage transition unavailable",
                    "The second-origin iframe did not return cookie, localStorage and Storage Access API evidence.",
                    realms=["deep"],
                )
            )
        else:
            findings.append(
                _finding(
                    SignalClass.INFO,
                    "JS_STORAGE_PARTITION_CAPTURED",
                    "Embedded storage partition state captured",
                    "First-party storage capabilities were correlated with a second-origin iframe receipt.",
                    evidence={
                        "embedded_cookie": embedded.get("cookie"),
                        "embedded_local_storage": embedded.get("localStorage"),
                        "storage_access_api": embedded.get("storageAccessAPI"),
                        "has_storage_access": embedded.get("hasStorageAccess"),
                    },
                    realms=["deep"],
                )
            )
            if embedded.get("sameOrigin") is True:
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "JS_STORAGE_PARTITION_ORIGIN_CONFLICT",
                        "Embedded storage probe is not cross-origin",
                        "The storage fixture resolved to the same origin, invalidating partition evidence.",
                        realms=["deep"],
                    )
                )
        if storage.get("storageManager") is True and storage.get("estimate") is None:
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "JS_STORAGE_ESTIMATE_MISSING",
                    "StorageManager estimate unavailable",
                    "StorageManager is exposed but did not return quota evidence.",
                    realms=["deep"],
                )
            )

    media = runtime.get("mediaDevices")
    if not isinstance(media, Mapping) or not media:
        findings.append(
            _finding(
                SignalClass.LOW,
                "JS_MEDIA_DEVICE_SHAPE_MISSING",
                "Media-device API shape missing",
                "Supported constraints, device kinds and capability methods were not reported.",
                realms=["deep"],
            )
        )
    else:
        device_count = media.get("deviceCount")
        kind_counts = media.get("kindCounts")
        if isinstance(device_count, int) and isinstance(kind_counts, Mapping):
            counted = sum(value for value in kind_counts.values() if isinstance(value, int))
            if counted != device_count:
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "JS_MEDIA_DEVICE_COUNT_CONFLICT",
                        "Media-device counts are inconsistent",
                        "The total device count differs from the sum of device-kind counts.",
                        evidence={"device_count": device_count, "kind_count_total": counted},
                        realms=["deep"],
                    )
                )
        permissions = media.get("permissionStates")
        if isinstance(permissions, Mapping):
            labels = media.get("labelsExposed")
            if labels is True and all(
                permissions.get(name) == "denied" for name in ("camera", "microphone")
            ):
                findings.append(
                    _finding(
                        SignalClass.MEDIUM,
                        "JS_MEDIA_LABEL_PERMISSION_CONFLICT",
                        "Media labels conflict with denied permissions",
                        "Device labels are exposed while both camera and microphone permissions report denied.",
                        realms=["deep"],
                    )
                )
        initial_count = media.get("initialDeviceCount")
        change_events = media.get("deviceChangeEvents")
        if (
            isinstance(initial_count, int)
            and isinstance(device_count, int)
            and initial_count != device_count
            and change_events == 0
        ):
            findings.append(
                _finding(
                    SignalClass.LOW,
                    "JS_MEDIA_DEVICE_TRANSITION_UNSIGNALED",
                    "Media device inventory changed without an event",
                    "Two bounded enumerations differ but no devicechange event was observed.",
                    evidence={"initial_count": initial_count, "final_count": device_count},
                    realms=["deep"],
                )
            )
    return findings
