"""Tests for the inline (config-driven step DSL) monitor."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from job_ftch.infrastructure.sources.monitors.inline import (
    _extract_urls_from_json,
    _nested_get,
    discover,
)


def test_inline_monitor_registered_via_package_import() -> None:
    """Regression: inline must register when the monitors *package* is imported,
    not only when its module is imported directly (as this test file does).

    Runs in a clean subprocess so the direct ``inline`` import above cannot mask
    a missing entry in ``monitors/__init__.py``.
    """
    import subprocess
    import sys

    code = (
        "import job_ftch.infrastructure.sources.monitors;"
        "from job_ftch.application.registry import resolve_monitor;"
        "e = resolve_monitor('inline');"
        "assert e.cost == 60 and e.rich is False;"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"inline not registered via package import:\n{result.stderr}"
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(url: str, steps: list[dict[str, Any]]) -> Any:
    spec = MagicMock()
    spec.url = url
    spec.monitor_config = {"steps": steps}
    return spec


def _json_response(data: Any, status: int = 200) -> httpx.Response:
    body = json.dumps(data).encode()
    return httpx.Response(
        status,
        content=body,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://example.com/api/jobs"),
    )


def _html_response(html: str, url: str = "https://example.com/jobs") -> httpx.Response:
    return httpx.Response(
        200,
        content=html.encode(),
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", url),
    )


# ---------------------------------------------------------------------------
# Unit: _nested_get
# ---------------------------------------------------------------------------


def test_nested_get_single_key() -> None:
    assert _nested_get({"jobs": [1, 2]}, "jobs") == [1, 2]


def test_nested_get_dotted_path() -> None:
    assert _nested_get({"data": {"jobs": [1, 2]}}, "data.jobs") == [1, 2]


def test_nested_get_missing_key_returns_none() -> None:
    assert _nested_get({"data": {}}, "data.jobs") is None


def test_nested_get_non_dict_intermediate_returns_early() -> None:
    result = _nested_get({"data": "not-a-dict"}, "data.jobs")
    assert result == "not-a-dict"


# ---------------------------------------------------------------------------
# Unit: _extract_urls_from_json
# ---------------------------------------------------------------------------


def test_extract_urls_from_json_flat_list() -> None:
    data = [{"url": "https://a.com/job/1"}, {"url": "https://a.com/job/2"}]
    urls = _extract_urls_from_json(data, items_path=None, url_field="url")
    assert urls == {"https://a.com/job/1", "https://a.com/job/2"}


def test_extract_urls_from_json_nested_path() -> None:
    data = {"data": {"jobs": [{"url": "https://a.com/job/1"}]}}
    urls = _extract_urls_from_json(data, items_path="data.jobs", url_field="url")
    assert urls == {"https://a.com/job/1"}


def test_extract_urls_from_json_custom_field() -> None:
    data = [{"absolute_url": "https://a.com/job/1"}]
    urls = _extract_urls_from_json(data, items_path=None, url_field="absolute_url")
    assert urls == {"https://a.com/job/1"}


def test_extract_urls_from_json_skips_non_http() -> None:
    data = [{"url": "/relative/path"}, {"url": "https://a.com/job/1"}]
    urls = _extract_urls_from_json(data, items_path=None, url_field="url")
    assert urls == {"https://a.com/job/1"}


def test_extract_urls_from_json_empty_items_returns_empty() -> None:
    assert _extract_urls_from_json([], items_path=None, url_field="url") == set()


# ---------------------------------------------------------------------------
# Integration: discover()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_fetch_json_flat_list() -> None:
    data = [{"url": "https://example.com/jobs/1"}, {"url": "https://example.com/jobs/2"}]
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_json_response(data))

    spec = _spec(
        "https://example.com/",
        [{"type": "fetch_json", "url": "https://example.com/api/jobs"}],
    )
    urls = await discover(spec, client)

    assert urls == {"https://example.com/jobs/1", "https://example.com/jobs/2"}


@pytest.mark.asyncio
async def test_discover_fetch_json_nested_path() -> None:
    data = {"results": {"openings": [{"url": "https://example.com/jobs/3"}]}}
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_json_response(data))

    spec = _spec(
        "https://example.com/",
        [
            {
                "type": "fetch_json",
                "url": "https://example.com/api/openings",
                "items_path": "results.openings",
                "url_field": "url",
            }
        ],
    )
    urls = await discover(spec, client)
    assert "https://example.com/jobs/3" in urls


@pytest.mark.asyncio
async def test_discover_fetch_html_with_url_filter() -> None:
    html = (
        '<a href="https://example.com/jobs/dev-123">Dev</a>'
        '<a href="https://example.com/about">About</a>'
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_html_response(html, "https://example.com/careers"))

    spec = _spec(
        "https://example.com/careers",
        [
            {
                "type": "fetch_html",
                "url": "https://example.com/careers",
                "url_filter": r"/jobs/",
            }
        ],
    )
    urls = await discover(spec, client)
    assert "https://example.com/jobs/dev-123" in urls
    assert "https://example.com/about" not in urls


@pytest.mark.asyncio
async def test_discover_multiple_steps_union_results() -> None:
    json_data = [{"url": "https://example.com/jobs/1"}]
    html = '<a href="https://example.com/jobs/2">Role</a>'

    responses = [_json_response(json_data), _html_response(html, "https://example.com/careers")]
    call_count = 0

    async def _get(url: str, **kwargs: Any) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = _get

    spec = _spec(
        "https://example.com/",
        [
            {"type": "fetch_json", "url": "https://example.com/api/jobs"},
            {"type": "fetch_html", "url": "https://example.com/careers", "url_filter": r"/jobs/"},
        ],
    )
    urls = await discover(spec, client)
    assert "https://example.com/jobs/1" in urls
    assert "https://example.com/jobs/2" in urls


@pytest.mark.asyncio
async def test_discover_no_steps_returns_empty() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    spec = _spec("https://example.com/", [])
    urls = await discover(spec, client)
    assert urls == set()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_discover_non_list_steps_returns_empty() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    spec = _spec("https://example.com/", [])
    spec.monitor_config = {"steps": "not-a-list"}
    urls = await discover(spec, client)
    assert urls == set()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_discover_non_dict_step_skipped() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    spec = _spec("https://example.com/", ["not-a-dict", 42])
    urls = await discover(spec, client)
    assert urls == set()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_discover_unknown_step_type_skipped() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    spec = _spec(
        "https://example.com/",
        [{"type": "future_unsupported_step", "url": "https://example.com/api/jobs"}],
    )
    urls = await discover(spec, client)
    assert urls == set()
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_discover_board_url_excluded_from_results() -> None:
    board_url = "https://example.com/careers/"
    data = [{"url": board_url}, {"url": "https://example.com/careers/job/1"}]
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=_json_response(data))

    spec = _spec(board_url, [{"type": "fetch_json"}])
    urls = await discover(spec, client)

    assert board_url not in urls
    assert board_url.rstrip("/") not in urls
    assert "https://example.com/careers/job/1" in urls


@pytest.mark.asyncio
async def test_discover_fetch_json_http_error_returns_empty() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "https://example.com/api"),
            response=httpx.Response(404, request=httpx.Request("GET", "https://example.com/api")),
        )
    )

    spec = _spec(
        "https://example.com/",
        [{"type": "fetch_json", "url": "https://example.com/api/jobs"}],
    )
    urls = await discover(spec, client)
    assert urls == set()
