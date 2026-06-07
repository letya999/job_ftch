import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from application.registry import create_source_from_spec
from application.source_loader import load_sources
from config import Settings
from domain.source_spec import LocalFixtureSpec, SourceSpec, TelegramChannelSpec
from infrastructure.auth.env_auth import EnvAuthProvider
from infrastructure.sources.local_fixture import LocalFixtureSource
from infrastructure.stores.in_memory import InMemoryStore


def test_source_spec_telegram_channel_valid():
    data = {"type": "telegram_channel", "entity": "ai_jobs"}
    spec = TypeAdapter(SourceSpec).validate_python(data)
    assert isinstance(spec, TelegramChannelSpec)
    assert spec.entity == "ai_jobs"


def test_source_spec_discriminated_union():
    data = [
        {"type": "telegram_channel", "entity": "ai_jobs"},
        {"type": "local_fixture", "path": "test.json"},
    ]
    specs = TypeAdapter(list[SourceSpec]).validate_python(data)
    assert len(specs) == 2
    assert isinstance(specs[0], TelegramChannelSpec)
    assert isinstance(specs[1], LocalFixtureSpec)


def test_source_spec_invalid_type_rejected():
    data = {"type": "unknown_type", "entity": "foo"}
    with pytest.raises(ValidationError):
        TypeAdapter(SourceSpec).validate_python(data)


def test_load_sources_from_json(tmp_path: Path):
    path = tmp_path / "sources.json"
    data = [{"type": "local_fixture", "path": "test.json"}]
    path.write_text(json.dumps(data))

    specs = load_sources(path)
    assert len(specs) == 1
    assert specs[0].path == "test.json"


def test_load_sources_from_dict_with_sources_key(tmp_path: Path):
    path = tmp_path / "sources.json"
    data = {"sources": [{"type": "local_fixture", "path": "test.json"}]}
    path.write_text(json.dumps(data))

    specs = load_sources(path)
    assert len(specs) == 1
    assert specs[0].path == "test.json"


def test_settings_sources_file_path():
    settings = Settings(sources_file_path="config/sources.json")
    assert settings.sources_file_path == Path("config/sources.json")


def test_env_auth_provider_resolve(monkeypatch):
    monkeypatch.setenv("JOB_FTCH_AUTH_MY_SOURCE_API_ID", "12345")
    monkeypatch.setenv("JOB_FTCH_AUTH_MY_SOURCE_API_HASH", "abcde")

    provider = EnvAuthProvider()
    creds = provider.resolve("my-source")
    assert creds == {"api_id": "12345", "api_hash": "abcde"}


def test_create_source_from_spec_local_fixture(tmp_path: Path):
    spec = LocalFixtureSpec(type="local_fixture", path=str(tmp_path / "test.json"))
    source = create_source_from_spec(spec)
    assert isinstance(source, LocalFixtureSource)


@pytest.mark.asyncio
async def test_store_namespaced_run_state():
    store = InMemoryStore()
    await store.set_run_state("last_id", "100", source_kind="telegram", source_name="jobs")

    val = await store.get_run_state("last_id", source_kind="telegram", source_name="jobs")
    assert val == "100"

    # Check it's not visible without namespace
    assert await store.get_run_state("last_id") is None

    # Check different namespace
    assert await store.get_run_state("last_id", source_kind="telegram", source_name="other") is None


def test_career_site_config_from_spec_detects_greenhouse():
    from domain.source_spec import DeclarativeHtmlSpec
    from infrastructure.sources.declarative import CareerSiteConfig

    spec = DeclarativeHtmlSpec(url="https://boards.greenhouse.io/myco")
    config = CareerSiteConfig.from_spec(spec)
    assert config.kind == "greenhouse"
    assert config.href_contains == "/jobs/"


def test_career_site_config_from_spec_generic_fallback():
    from domain.source_spec import DeclarativeHtmlSpec
    from infrastructure.sources.declarative import CareerSiteConfig

    spec = DeclarativeHtmlSpec(url="https://jobs.example.com")
    config = CareerSiteConfig.from_spec(spec)
    assert config.kind == "generic"


def test_career_site_config_from_spec_explicit_greenhouse_kind():
    from domain.source_spec import DeclarativeHtmlSpec
    from infrastructure.sources.declarative import CareerSiteConfig

    spec = DeclarativeHtmlSpec(url="https://other.com", parser_kind="greenhouse")
    config = CareerSiteConfig.from_spec(spec)
    assert config.kind == "greenhouse"


def test_create_source_from_spec_unknown_type_raises():
    from unittest.mock import MagicMock

    from application.registry import create_source_from_spec, load_extensions

    load_extensions()
    fake_spec = MagicMock()
    fake_spec.type = "does_not_exist"
    with pytest.raises(ValueError, match="Unsupported source type"):
        create_source_from_spec(fake_spec)


def test_env_auth_provider_resolve_no_matching_vars(monkeypatch):
    import os

    # Remove any env vars that might match
    for key in list(os.environ.keys()):
        if key.startswith("JOB_FTCH_AUTH_NONEXISTENT_"):
            monkeypatch.delenv(key)
    provider = EnvAuthProvider()
    creds = provider.resolve("nonexistent-source")
    assert creds == {}


def test_load_sources_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json at all {{{")
    with pytest.raises(json.JSONDecodeError):
        load_sources(path)
