import json

from scripts.eval import run_protected_proxy_captcha_matrix as matrix
from scripts.eval.run_protected_proxy_captcha_matrix import (
    _diagnose_results,
    dedupe_targets_by_domain,
)


def test_dedupe_targets_by_domain_keeps_first_representative() -> None:
    kept, skipped = dedupe_targets_by_domain(
        [
            "https://himalayas.app/jobs/countries/poland",
            "https://www.himalayas.app/jobs/countries/serbia",
            "https://example.test/jobs",
        ]
    )

    assert kept == [
        "https://himalayas.app/jobs/countries/poland",
        "https://example.test/jobs",
    ]
    assert skipped == ["https://www.himalayas.app/jobs/countries/serbia"]


def test_diagnostics_classifies_solver_and_missing_handoff(tmp_path) -> None:
    path = tmp_path / "ingest.json"
    path.write_text(
        json.dumps(
            [
                {
                    "url": "https://a.test/jobs",
                    "parse_status": "parsed_failed",
                    "stats": {"detected_captcha_types": ["turnstile"]},
                    "bypass_attempts": [],
                },
                {
                    "url": "https://b.test/jobs",
                    "parse_status": "parsed_failed",
                    "bypass_attempts": [{}],
                    "bypass_route_transitions": [
                        {"axis": "challenge", "failure_reason": "unsupported_challenge"}
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    report = _diagnose_results(path)

    assert report["counts"] == {
        "challenge_detected_without_bypass_attempt": 1,
        "solver_unsupported_challenge": 1,
    }


def test_capsolver_cloudflare_proxy_status_prefers_dedicated_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST",
        "http://capsolver-static:9000",
    )
    monkeypatch.delenv("JOB_FTCH_RESIDENTIAL_PROXY_LIST", raising=False)
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.proxy_bypass._has_gateway_config",
        lambda: False,
    )

    status = matrix._capsolver_cloudflare_proxy_status()

    assert status["available"] is True
    assert status["source"] == "JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST"
    assert status["dedicated_count"] == 1


def test_capsolver_cloudflare_proxy_status_blocks_gateway_only(monkeypatch) -> None:
    class _GatewayOnlyResidential:
        available = True
        gateway_provider = object()

    monkeypatch.delenv("JOB_FTCH_CAPSOLVER_CHALLENGE_PROXY_LIST", raising=False)
    monkeypatch.delenv("JOB_FTCH_RESIDENTIAL_PROXY_LIST", raising=False)
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.proxy_bypass._load_residential_proxies",
        lambda: [],
    )
    monkeypatch.setattr(
        "job_ftch.infrastructure.bypass.proxy_bypass.ResidentialProxyBypass",
        _GatewayOnlyResidential,
    )

    status = matrix._capsolver_cloudflare_proxy_status()

    assert status["available"] is False
    assert status["source"] == "gateway_only"


def test_matrix_blocks_paid_capsolver_without_cloudflare_proxy(monkeypatch, tmp_path) -> None:
    targets = tmp_path / "targets.yaml"
    targets.write_text("urls:\n  - https://himalayas.app/jobs\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        matrix.sys,
        "argv",
        [
            "run_protected_proxy_captcha_matrix.py",
            "--targets",
            str(targets),
            "--providers",
            "capsolver",
            "--allow-paid",
            "--no-require-residential",
            "--out-dir",
            str(out_dir),
        ],
    )
    monkeypatch.setattr(matrix, "_residential_available", lambda: True)
    monkeypatch.setattr(
        matrix,
        "_capsolver_cloudflare_proxy_status",
        lambda: {
            "available": False,
            "source": "gateway_only",
            "dedicated_count": 0,
            "raw_count": 0,
            "gateway_mode": True,
        },
    )

    assert matrix.main() == 2

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"
    assert manifest["blocker"] == "capsolver_cloudflare_proxy_unavailable"
    assert manifest["runs"] == []
