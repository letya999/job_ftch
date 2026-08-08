from __future__ import annotations

import json

from paritylab.reputation import OfflineIPReputation


def test_offline_reputation_exposes_dataset_provenance(tmp_path) -> None:
    policy = tmp_path / "reputation.json"
    policy.write_text(
        json.dumps(
            {
                "networks": [
                    {
                        "cidr": "203.0.113.0/24",
                        "label": "documentation",
                        "risk": 0,
                        "source": "fixture",
                        "asn": 64500,
                        "network_type": "documentation",
                        "country": "ZZ",
                        "tags": ["test"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reputation = OfflineIPReputation(policy)
    match = reputation.lookup("203.0.113.4")
    provenance = reputation.provenance()
    assert match.asn == 64500
    assert match.tags == ("test",)
    assert provenance["network_count"] == 1
    assert len(str(provenance["policy_sha256"])) == 64
    assert provenance["policy_mtime_ns"] is not None
