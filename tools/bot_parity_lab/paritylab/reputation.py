from __future__ import annotations

import importlib.util
import hashlib
import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class ReputationMatch:
    ip: str
    cidr: str | None
    label: str
    risk: int
    source: str
    asn: int | None = None
    organization: str | None = None
    network_type: str | None = None
    country: str | None = None
    tags: tuple[str, ...] = ()


class OfflineIPReputation:
    """Offline CIDR policy plus optional local MaxMind ASN MMDB.

    No DNS, WHOIS, reputation API, or other network lookup is performed. The
    JSON policy remains authoritative for risk; MMDB contributes ASN metadata.
    """

    def __init__(self, path: Path, asn_database: Path | None = None) -> None:
        self._policy_path = path
        self._entries: list[
            tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, dict[str, Any]]
        ] = []
        self._asn_database = asn_database if asn_database and asn_database.is_file() else None
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for raw in payload.get("networks", []):
                network = ipaddress.ip_network(str(raw["cidr"]), strict=False)
                self._entries.append((network, dict(raw)))
        self._entries.sort(key=lambda item: item[0].prefixlen, reverse=True)

    def provenance(self) -> dict[str, str | int | bool | None]:
        return {
            "policy_path": self._policy_path.name,
            "policy_sha256": _file_sha256(self._policy_path),
            "policy_mtime_ns": (
                self._policy_path.stat().st_mtime_ns if self._policy_path.is_file() else None
            ),
            "network_count": len(self._entries),
            "asn_database": self._asn_database.name if self._asn_database else None,
            "asn_sha256": _file_sha256(self._asn_database) if self._asn_database else None,
            "maxmind_available": importlib.util.find_spec("maxminddb") is not None,
        }

    def _asn_lookup(self, ip: str) -> tuple[int | None, str | None]:
        if self._asn_database is None or importlib.util.find_spec("maxminddb") is None:
            return None, None
        import maxminddb

        try:
            with maxminddb.open_database(str(self._asn_database)) as reader:
                record = reader.get(ip) or {}
        except (OSError, ValueError, TypeError):
            return None, None
        asn = record.get("autonomous_system_number")
        organization = record.get("autonomous_system_organization")
        return (
            int(asn) if isinstance(asn, int) else None,
            str(organization) if organization else None,
        )

    def lookup(self, ip: str) -> ReputationMatch:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return ReputationMatch(ip=ip, cidr=None, label="invalid", risk=100, source="parser")
        mmdb_asn, mmdb_organization = self._asn_lookup(ip)
        for network, raw in self._entries:
            if address.version == network.version and address in network:
                tags = raw.get("tags", [])
                return ReputationMatch(
                    ip=ip,
                    cidr=str(network),
                    label=str(raw.get("label", "matched")),
                    risk=int(raw.get("risk", 0)),
                    source=str(raw.get("source", "offline-policy")),
                    asn=int(raw["asn"]) if isinstance(raw.get("asn"), int) else mmdb_asn,
                    organization=str(raw.get("organization") or mmdb_organization or "") or None,
                    network_type=str(raw.get("network_type", "")) or None,
                    country=str(raw.get("country", "")) or None,
                    tags=tuple(str(value) for value in tags) if isinstance(tags, list) else (),
                )
        label = "private-unlisted" if address.is_private else "unlisted"
        return ReputationMatch(
            ip=ip,
            cidr=None,
            label=label,
            risk=10,
            source="maxmind+offline-policy" if mmdb_asn is not None else "offline-policy",
            asn=mmdb_asn,
            organization=mmdb_organization,
        )


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
