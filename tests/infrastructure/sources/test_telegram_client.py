from typing import Any
from unittest.mock import patch

from pydantic import SecretStr

from job_ftch.application.contracts import AuthProvider
from job_ftch.infrastructure.sources.telegram import _build_client_v2


class DummyAuth(AuthProvider):
    def resolve(self, auth_id: str) -> dict[str, Any]:
        return {"api_id": "12345", "api_hash": SecretStr("real-secret-hash")}


@patch("telethon.TelegramClient")
def test_telegram_client_unwraps_secret_str(mock_client_cls: Any) -> None:
    _build_client_v2("dummy_auth", DummyAuth())
    mock_client_cls.assert_called_once()
    args, kwargs = mock_client_cls.call_args
    assert args[2] == "real-secret-hash"
    assert "SecretStr" not in str(args[2])
