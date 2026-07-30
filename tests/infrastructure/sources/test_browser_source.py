import pytest
from pydantic import TypeAdapter

from job_ftch.domain.source_spec import BrowserSourceSpec, SourceSpec


def test_browser_source_spec_roundtrip():
    raw = {"type": "browser", "url": "https://example.com/careers"}
    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(raw)
    assert isinstance(spec, BrowserSourceSpec)
    assert str(spec.url).startswith("https://example.com")


def test_browser_source_raises_without_patchright():
    import asyncio
    from unittest.mock import MagicMock

    from job_ftch.domain.source_spec import BrowserSourceSpec
    from job_ftch.infrastructure.sources.browser.base import BrowserSource

    spec = BrowserSourceSpec(url="https://example.com/careers")
    source = BrowserSource(spec, MagicMock())
    source._available = False  # simulate no patchright
    with pytest.raises(ImportError, match="patchright"):
        asyncio.run(_collect(source))


async def _collect(source):
    async for _ in source.fetch():
        pass
