import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from infrastructure.auth.env_auth import EnvAuthProvider
from infrastructure.auth.file_auth import FileAuthProvider
from infrastructure.ingest.polling import PollingMode


def test_env_auth_provider():
    os.environ["JOB_FTCH_AUTH_TEST_API_KEY"] = "secret123"
    provider = EnvAuthProvider()
    creds = provider.resolve("test")
    assert creds == {"api_key": "secret123"}


def test_file_auth_provider(tmp_path):
    secrets_file = tmp_path / "secrets.yaml"
    secrets_file.write_text("test_source:\n  api_key: secret456\n")

    provider = FileAuthProvider(secrets_file)
    creds = provider.resolve("test_source")
    assert creds == {"api_key": "secret456"}


@pytest.mark.asyncio
async def test_polling_mode():
    source = MagicMock()
    source.fetch = MagicMock()

    # Mock async generator
    async def mock_fetch():
        yield "item1"
        yield "item2"

    source.fetch.return_value = mock_fetch()

    on_item = AsyncMock()
    mode = PollingMode()

    await mode.run(source, on_item)

    assert on_item.call_count == 2
    on_item.assert_any_call("item1")
    on_item.assert_any_call("item2")
