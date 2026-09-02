from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_ftch.config import Settings

ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_COMPOSE_PROD = ROOT / "job_ftch/adapters/telegram_bot/docker-compose.prod.yml"
TELEGRAM_COMPOSE_DEV = ROOT / "job_ftch/adapters/telegram_bot/docker-compose.dev.yml"
TELEGRAM_DOCKERFILE_PROD = ROOT / "job_ftch/adapters/telegram_bot/Dockerfile.prod"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_prod_compose_selects_release_runtime_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    compose = _read_yaml(TELEGRAM_COMPOSE_PROD)
    env = compose["services"]["bot"]["environment"]

    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("JOB_FTCH_ENV", str(env["JOB_FTCH_ENV"]))
    monkeypatch.setenv(
        "JOB_FTCH_RUNTIME_CONFIG_PATH",
        str(env["JOB_FTCH_RUNTIME_CONFIG_PATH"]),
    )
    monkeypatch.delenv("JOB_FTCH_SINK_BACKEND", raising=False)
    monkeypatch.delenv("JOB_FTCH_POSTING_BACKEND", raising=False)

    settings = Settings(_env_file=None, openai_api_key="placeholder")  # type: ignore[call-arg]

    assert settings.bgem3_enabled is False
    assert settings.relevance_backend == "keywords"
    assert settings.sink_backend == "none"
    assert settings.posting_backend == "none"


@pytest.mark.parametrize("compose_path", [TELEGRAM_COMPOSE_DEV, TELEGRAM_COMPOSE_PROD])
def test_compose_relative_host_paths_resolve_inside_repo(compose_path: Path) -> None:
    compose = _read_yaml(compose_path)
    compose_dir = compose_path.parent
    runtime_mount = (ROOT / ".runtime").resolve()
    for service in compose["services"].values():
        for volume in service.get("volumes", []):
            if not isinstance(volume, str) or ":" not in volume:
                continue
            host = volume.split(":", 1)[0]
            if not host.startswith("."):
                continue
            resolved = (compose_dir / host).resolve()
            assert str(resolved).startswith(str(ROOT))
            if resolved == runtime_mount:
                continue
            assert resolved.exists(), f"{compose_path}: missing bind mount host path {host}"


@pytest.mark.parametrize("compose_path", [TELEGRAM_COMPOSE_DEV, TELEGRAM_COMPOSE_PROD])
def test_compose_env_files_have_tracked_examples(compose_path: Path) -> None:
    compose = _read_yaml(compose_path)
    compose_dir = compose_path.parent
    for service in compose["services"].values():
        env_files = service.get("env_file", [])
        if isinstance(env_files, str):
            env_files = [env_files]
        for raw_env_file in env_files:
            env_file = (compose_dir / raw_env_file).resolve()
            example = env_file.with_name(f"{env_file.name}.example")
            rel_example = example.relative_to(ROOT).as_posix()
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", rel_example],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )


def test_prod_deploy_docs_use_env_file_for_compose_interpolation() -> None:
    docs = (ROOT / "docs/deploy.md").read_text(encoding="utf-8")
    prod_compose = "job_ftch/adapters/telegram_bot/docker-compose.prod.yml"
    prod_env = "job_ftch/adapters/telegram_bot/.env.prod"

    for line in docs.splitlines():
        if "docker compose" not in line or prod_compose not in line:
            continue
        assert f"--env-file {prod_env}" in line


def test_prod_bot_image_copies_current_source_over_runtime_base() -> None:
    text = TELEGRAM_DOCKERFILE_PROD.read_text(encoding="utf-8")

    assert "COPY --chown=appuser:appuser . /app" in text
    assert "uv sync" not in text
    assert "patchright install" not in text
    assert "camoufox fetch" not in text
    assert "cloakbrowser install" not in text
    assert "chown -R" not in text


def test_workflow_paths_exist() -> None:
    workflow_dir = ROOT / ".github/workflows"
    for workflow in workflow_dir.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        workflow_events = data.get("on") or data.get(True) or {}
        for event in workflow_events.values():
            if not isinstance(event, dict):
                continue
            for path_pattern in event.get("paths", []):
                concrete = (
                    path_pattern[: -len("/**")] if path_pattern.endswith("/**") else path_pattern
                )
                if concrete.endswith(".toml"):
                    assert (ROOT / concrete).exists()
                else:
                    assert (ROOT / concrete).exists(), f"{workflow}: {path_pattern}"
        for line in text.splitlines():
            if "ruff check " in line or "docker build -f " in line or "test -f " in line:
                for token in line.split():
                    if token.startswith("job_ftch/") or token.startswith("pyproject.toml"):
                        assert (ROOT / token).exists(), f"{workflow}: {token}"
