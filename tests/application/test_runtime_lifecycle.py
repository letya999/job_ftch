import argparse
from pathlib import Path

import pytest

from job_ftch.application.tenant_locks import TenantRunAlreadyActiveError, tenant_run_lock
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.cli import _cmd_run_modern
from job_ftch.config import get_settings
from job_ftch.domain import (
    CandidateIdentity,
    CandidateProfile,
    ManagedCandidateProfile,
    SearchProfile,
    TenantConfig,
)


@pytest.fixture
def dummy_tenant_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "tenant.yaml"
    config_file.write_text(
        """
tenant_id: test_lifecycle_tenant
display_name: Test Tenant
auth_provider: "env"
sources:
  - type: telegram_channel
    entity: "test_channel"
        """
    )
    return config_file


def test_cli_run_config_uses_tenant_runner(
    dummy_tenant_config: Path, monkeypatch: pytest.MonkeyPatch
):
    """A test proves job_ftch run --config tenant.yaml reaches TenantRunner.run_tenant()."""
    called_tenant_id = None
    called_max_items = -1

    async def mock_run_tenant(self, tenant_id: str, *, max_items: int | None = None, **kwargs):
        nonlocal called_tenant_id, called_max_items
        called_tenant_id = tenant_id
        called_max_items = max_items
        from job_ftch.application.pipeline import RunSummary

        summary = RunSummary()
        summary.tenant_id = tenant_id
        return summary

    monkeypatch.setattr(TenantRunner, "run_tenant", mock_run_tenant)

    settings = get_settings()
    args = argparse.Namespace(command="run", config=dummy_tenant_config, max_items=42, json=True)

    result = _cmd_run_modern(settings, args)

    assert result == 0
    assert called_tenant_id == "test_lifecycle_tenant"
    assert called_max_items == 42


def test_cli_run_config_honors_tenant_auth_provider(
    dummy_tenant_config: Path, monkeypatch: pytest.MonkeyPatch
):
    """A test proves TenantConfig.auth_provider is honored by the CLI run path."""
    resolved_auth = None

    # Register a mock auth provider for testing
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.application.registry import _AUTH_PROVIDERS

    class MockAuthProvider(AuthProvider):
        pass

    _AUTH_PROVIDERS["mock_auth"] = lambda settings: MockAuthProvider()

    dummy_tenant_config.write_text(
        dummy_tenant_config.read_text().replace(
            'auth_provider: "env"', 'auth_provider: "mock_auth"'
        )
    )

    original_from_tenants = TenantRunner.from_tenants

    @classmethod
    def mock_from_tenants(cls, tenants, *, base_settings=None, **kwargs):
        nonlocal resolved_auth
        # Intercept the auth resolution by checking the tenant auth inside the runtime
        runner = original_from_tenants(tenants, base_settings=base_settings, **kwargs)
        resolved_auth = runner.get_runtime(tenants[0].tenant_id).auth_provider

        # stub run_tenant to avoid actual work
        async def mock_run_tenant(*args, **kwargs):
            from job_ftch.application.pipeline import RunSummary

            return RunSummary()

        runner.run_tenant = mock_run_tenant.__get__(runner, TenantRunner)
        return runner

    monkeypatch.setattr(TenantRunner, "from_tenants", mock_from_tenants)

    settings = get_settings()
    args = argparse.Namespace(command="run", config=dummy_tenant_config, max_items=1, json=True)

    _cmd_run_modern(settings, args)

    assert isinstance(resolved_auth, MockAuthProvider)


@pytest.mark.asyncio
async def test_reset_tenant_refuses_while_run_active(tmp_path: Path):
    """A test proves reset and clear cannot mutate a tenant during an active run."""
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    config = TenantConfig.model_validate(
        {"tenant_id": "test_reset", "display_name": "Reset", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    # simulate an active run
    runtime_settings = runner.get_runtime("test_reset").settings
    async with tenant_run_lock(runtime_settings, "test_reset"):
        with pytest.raises(TenantRunAlreadyActiveError):
            await runner.reset_tenant("test_reset")


@pytest.mark.asyncio
async def test_clear_all_refuses_while_run_active(tmp_path: Path):
    """A test proves reset and clear cannot mutate a tenant during an active run."""
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    config = TenantConfig.model_validate(
        {"tenant_id": "test_clear", "display_name": "Clear", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    # simulate an active run
    runtime_settings = runner.get_runtime("test_clear").settings
    async with tenant_run_lock(runtime_settings, "test_clear"):
        with pytest.raises(TenantRunAlreadyActiveError):
            await runner.clear_all("test_clear")


@pytest.mark.asyncio
async def test_clear_dedup_refuses_while_run_active(tmp_path: Path):
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    config = TenantConfig.model_validate(
        {"tenant_id": "test_dedup", "display_name": "Clear Dedup", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    runtime_settings = runner.get_runtime("test_dedup").settings
    async with tenant_run_lock(runtime_settings, "test_dedup"):
        with pytest.raises(TenantRunAlreadyActiveError):
            await runner.clear_dedup("test_dedup")


@pytest.mark.asyncio
async def test_clear_run_data_refuses_while_run_active(tmp_path: Path):
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    config = TenantConfig.model_validate(
        {"tenant_id": "test_run_data", "display_name": "Clear Run Data", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    runtime_settings = runner.get_runtime("test_run_data").settings
    async with tenant_run_lock(runtime_settings, "test_run_data"):
        with pytest.raises(TenantRunAlreadyActiveError):
            await runner.clear_run_data("test_run_data")


@pytest.mark.asyncio
async def test_clear_run_data_refuses_multi_tenant_runner_until_catalog_is_tenant_scoped(
    tmp_path: Path,
):
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})
    configs = [
        TenantConfig.model_validate(
            {"tenant_id": "tenant_one", "display_name": "One", "sources": []}
        ),
        TenantConfig.model_validate(
            {"tenant_id": "tenant_two", "display_name": "Two", "sources": []}
        ),
    ]
    runner = TenantRunner.from_tenants(configs, base_settings=settings)

    with pytest.raises(RuntimeError, match="job catalog and vector cleanup"):
        await runner.clear_run_data("tenant_one")


@pytest.mark.asyncio
async def test_clear_run_data_preserves_candidate_profile_shots(tmp_path: Path):
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})
    config = TenantConfig.model_validate(
        {"tenant_id": "tenant_one", "display_name": "One", "sources": []}
    )
    runner = TenantRunner.from_tenants([config], base_settings=settings)
    profile = ManagedCandidateProfile(
        user_id="u1",
        profile_id="user_u1",
        profile=CandidateProfile(
            identity=CandidateIdentity(candidate_id="u1"),
            search_profiles=(
                SearchProfile(
                    profile_id="user_u1",
                    positive_example_texts=("resume pos",),
                    negative_example_texts=("resume neg",),
                    positive_job_example_texts=("job pos",),
                    negative_job_example_texts=("job neg",),
                ),
            ),
        ),
    )
    await runner.save_and_activate_candidate_profile("tenant_one", profile)

    await runner.clear_run_data("tenant_one")

    loaded = await runner.get_candidate_profile("tenant_one", "u1", "user_u1")
    assert loaded is not None
    sp = loaded.profile.search_profiles[0]
    assert sp.positive_example_texts == ("resume pos",)
    assert sp.negative_example_texts == ("resume neg",)
    assert sp.positive_job_example_texts == ("job pos",)
    assert sp.negative_job_example_texts == ("job neg",)
    assert await runner.has_candidate_profile_data("tenant_one", "u1") is True


@pytest.mark.asyncio
async def test_tenant_lock_filesystem_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A test proves filesystem lock creation failure does not allow a cross-process unsafe run."""
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    from filelock import FileLock

    def mock_acquire(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(FileLock, "acquire", mock_acquire)

    config = TenantConfig.model_validate(
        {"tenant_id": "test_lock", "display_name": "Lock", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    summary = await runner.run_tenant("test_lock")
    assert summary.failed == 1
    assert summary.drop_reasons.get("lock_error") == 1


@pytest.mark.asyncio
async def test_tenant_lock_mkdir_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A test proves lock directory creation failure properly fails closed."""
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    original_mkdir = Path.mkdir

    def mock_mkdir(self, *args, **kwargs):
        if "tenant_locks" in str(self):
            raise OSError("Read-only filesystem")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    config = TenantConfig.model_validate(
        {"tenant_id": "test_mkdir_lock", "display_name": "Mkdir Lock", "sources": []}
    )

    runner = TenantRunner.from_tenants([config], base_settings=settings)

    summary = await runner.run_tenant("test_mkdir_lock")
    assert summary.failed == 1
    assert summary.drop_reasons.get("lock_error") == 1


def test_tenant_run_is_active_detects_filelock(tmp_path: Path):
    """Proves tenant_run_is_active respects the filelock."""
    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    from filelock import FileLock

    from job_ftch.application.tenant_locks import tenant_run_is_active

    lock_dir = tmp_path / "tenant_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "test_active.lock"

    # Not active yet
    assert tenant_run_is_active(settings, "test_active") is False

    # Active
    lock = FileLock(str(lock_path))
    lock.acquire()
    try:
        assert tenant_run_is_active(settings, "test_active") is True
    finally:
        lock.release()

    # Not active anymore
    assert tenant_run_is_active(settings, "test_active") is False


@pytest.mark.asyncio
async def test_tenant_run_lock_releases_filelock_after_context(tmp_path: Path):
    """The worker-thread acquisition must be released by the event-loop thread."""
    from filelock import FileLock

    settings = get_settings().model_copy(update={"store_path": tmp_path / "store.db"})

    async with tenant_run_lock(settings, "thread_release"):
        pass

    lock_path = tmp_path / "tenant_locks" / "thread_release.lock"
    lock = FileLock(str(lock_path), timeout=0)
    lock.acquire()
    lock.release()
