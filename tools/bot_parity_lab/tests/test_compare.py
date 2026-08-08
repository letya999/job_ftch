from __future__ import annotations

import json

from paritylab.compare import compare_artifacts


def test_identical_artifacts_have_no_diff(tmp_path) -> None:
    artifact = tmp_path / "raw.json"
    artifact.write_text(
        json.dumps(
            {
                "session_id": "same",
                "client_name": "manual",
                "client_family": "browser",
                "requests": [],
                "tls_fingerprints": [],
                "probes": [],
                "behavior": [],
                "opaque_payloads": [],
            }
        ),
        encoding="utf-8",
    )
    comparison = compare_artifacts(artifact, artifact)
    assert comparison["difference_count"] == 0
    assert comparison["differences"] == []
