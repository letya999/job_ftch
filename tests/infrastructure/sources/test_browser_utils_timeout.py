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


class _EnumLike:
    def __init__(self, value: str) -> None:
        self.value = value


def test_json_safe_cookies_flattens_enums_and_keeps_valid_keys() -> None:
    from job_ftch.infrastructure.sources.browser_utils import _json_safe_cookies

    cookies = [
        {
            "name": "cf_clearance",
            "value": "tok",
            "domain": ".example.test",
            "path": "/",
            "sameSite": _EnumLike("None"),
            "extra_key": "dropped",
        }
    ]
    safe = _json_safe_cookies(cookies)
    assert safe == [{"name": "cf_clearance", "value": "tok", "domain": ".example.test", "path": "/", "sameSite": "None"}]


def test_json_safe_cookies_drops_incomplete_entries() -> None:
    from job_ftch.infrastructure.sources.browser_utils import _json_safe_cookies

    assert _json_safe_cookies([{"name": None, "value": 1}, "broken"]) == []
