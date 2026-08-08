import pytest

from job_ftch.infrastructure.sources.browser_utils import normalize_browser_timeout_ms


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (15, 15),
        (15.0, 15),
        (15000.0, 15000),
        ("15000", 15000),
        ("15000.0", 15000),
    ],
)
def test_normalize_browser_timeout_ms_accepts_integer_like_values(
    raw: object, expected: int
) -> None:
    assert normalize_browser_timeout_ms(raw) == expected


@pytest.mark.parametrize("raw", [0, -1, "", True, object()])
def test_normalize_browser_timeout_ms_rejects_invalid_values(raw: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        normalize_browser_timeout_ms(raw)
