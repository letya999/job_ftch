from __future__ import annotations

from job_ftch.infrastructure.bypass.challenge_classifier import classify_challenge
from job_ftch.infrastructure.bypass.failure_signal import FailureKind
from starlette.testclient import TestClient

from paritylab.app import create_app
from paritylab.config import LabConfig
from paritylab.protection_fixtures import FIXTURES
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry


def test_health_and_static_page(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        page = client.get("/?sid=test-session&client=test&family=test")
        assert page.status_code == 200
        assert "Bot / Browser Parity Lab" in page.text
        assert "test-session" in page.text


def test_non_loopback_bind_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="loopback"):
        LabConfig(host="0.0.0.0")


def test_owned_protection_fixtures_are_local_and_inert(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())
    with TestClient(app) as client:
        for fixture_id, fixture in FIXTURES.items():
            response = client.get(f"/fixtures/protection/{fixture_id}")
            assert response.status_code == fixture.status_code
            assert response.headers["x-parity-owned-fixture"] == fixture_id
            assert response.headers["cache-control"] == "no-store"
            assert "<script" not in response.text.lower()
            assert "token" not in response.text.lower()


def test_owned_protection_fixtures_are_classified_before_parsing(tmp_path) -> None:
    expected = {
        "waf_block": (FailureKind.BLOCKED_FINGERPRINT, None),
        "captcha_recaptcha": (FailureKind.CAPTCHA, "recaptcha"),
        "passive_challenge": (FailureKind.CHALLENGE, None),
        "qrator_jsid": (FailureKind.QRATOR_CHALLENGE, "qrator_jsid"),
    }
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())
    with TestClient(app) as client:
        for fixture_id, (kind, challenge_type) in expected.items():
            response = client.get(f"/fixtures/protection/{fixture_id}")
            detection = classify_challenge(
                surface="parity_fixture",
                status_code=response.status_code,
                headers=response.headers,
                body=response.content,
            )
            assert detection.detected is True
            assert detection.kind is kind
            assert detection.challenge_type == challenge_type
            assert len(detection.evidence_hash) == 16


def test_challenge_contract_is_manual_only_and_exposes_no_secret_values(tmp_path) -> None:
    config = LabConfig(
        artifacts_dir=tmp_path / "artifacts",
        certs_dir=tmp_path / "certs",
        ip_reputation_file=tmp_path / "reputation.json",
        enable_http3=False,
    )
    app = create_app(config, store=ArtifactStore(config.artifacts_dir), registry=TLSConnectionRegistry())
    with TestClient(app) as client:
        response = client.get("/api/protection/captcha_recaptcha/contract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["solve_supported"] is False
    assert payload["contract"] == {
        "challenge_type": "recaptcha",
        "synthetic_sitekey": "PARITY_TEST_SITEKEY_NOT_VALID",
        "action": "jobs_search",
        "min_score": 0.7,
        "deadline_reserve_seconds": 20.0,
        "response_action": "manual_required",
        "provider_task_created": False,
    }
    assert "token" not in str(payload).lower()
