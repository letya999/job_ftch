from __future__ import annotations

import json

from paritylab.baselines import audit_baselines, load_baseline_runs, load_profiles


def _write_run(root, name: str, profile: str, findings: list[str]) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "raw.json").write_text(
        json.dumps(
            {
                "metadata": {"baseline_profile": profile},
                "findings": [{"code": code} for code in findings],
                "probes": [{"realm": "window"}, {"realm": "deep"}],
                "requests": [
                    {
                        "path": "/",
                        "http_version": "2",
                        "headers": [["user-agent", "Mozilla/5.0 Chrome/146.0.0.0"]],
                    }
                ],
                "summary": {"score": 0, "disposition": "pass"},
            }
        ),
        encoding="utf-8",
    )


def test_baseline_audit_requires_repeated_expected_runs(tmp_path) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "version": "test",
                "profiles": [
                    {
                        "id": "manual",
                        "browser": "chromium",
                        "engine": "blink",
                        "os": "windows",
                        "mode": "headed",
                        "controller": "manual",
                        "expected_disposition": "pass",
                        "minimum_runs": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    version, profiles = load_profiles(profiles_path)
    _write_run(tmp_path, "one", "manual", ["INFO_ONE"])
    incomplete = audit_baselines(version, profiles, load_baseline_runs(tmp_path))
    assert incomplete.complete is False
    _write_run(tmp_path, "two", "manual", ["INFO_ONE", "INFO_VARIABLE"])
    complete = audit_baselines(version, profiles, load_baseline_runs(tmp_path))
    assert complete.complete is True
    assert complete.profiles[0].stable_finding_ratio == 0.75
    assert complete.profiles[0].realm_coverage == ("deep", "window")
    assert complete.profiles[0].observed_browsers == ("chromium",)
    assert complete.profiles[0].browser_mismatch_count == 0


def test_repository_baseline_matrix_is_broad() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    _version, profiles = load_profiles(root / "data" / "baseline_profiles.json")
    assert len(profiles) >= 10
    assert {profile.engine for profile in profiles} >= {"blink", "gecko", "webkit", "httpx", "curl"}
    assert {profile.controller for profile in profiles} >= {
        "playwright",
        "patchright",
        "nodriver",
        "camoufox",
    }
    by_id = {profile.id: profile for profile in profiles}
    assert "manual-chromium-windows-headed" not in by_id
    assert "manual-firefox-windows-headed" not in by_id
    assert by_id["playwright-chrome-channel-windows-headed"].controller == "playwright"
    assert by_id["playwright-firefox-windows-headed"].controller == "playwright"
    assert "manual-webkit-windows-headed" not in by_id
    assert by_id["playwright-webkit-windows-headed"].controller == "playwright"
