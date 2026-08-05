from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.bypass.tls_client_bypass import _resolve_timeout_seconds


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [
        (15, 15),
        (15.0, 15),
        (0.2, 1),
        (SimpleNamespace(read=2.1, connect=30.0, write=30.0, pool=30.0), 3),
    ],
)
def test_tls_client_timeout_seconds_is_int(timeout: object, expected: int) -> None:
    assert _resolve_timeout_seconds(SimpleNamespace(timeout=timeout)) == expected


def test_tls_client_timeout_seconds_falls_back_to_settings_type() -> None:
    timeout = _resolve_timeout_seconds(SimpleNamespace(timeout=None))

    assert isinstance(timeout, int)
