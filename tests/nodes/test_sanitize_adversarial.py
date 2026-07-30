"""Adversarial tests for SanitizeNode to ensure safety against malicious inputs."""

from __future__ import annotations

from contextlib import suppress

import pytest

from job_ftch.application.rejections import RawItemRejected
from job_ftch.domain import RawItem, SourceKind
from job_ftch.nodes import SanitizeNode


@pytest.mark.unit
@pytest.mark.parametrize(
    "url,should_reject",
    [
        ("javascript:alert(1)", True),
        ("file:///etc/passwd", True),
        ("data:text/html,<script>alert(1)</script>", True),
        ("https://169.254.169.254/latest/meta-data/", True),  # SSRF AWS metadata
        ("https://localhost/jobs/1", True),  # SSRF internal
        ("https://careers.example.com/jobs/1", False),  # Legitimate
    ],
)
@pytest.mark.anyio
async def test_sanitize_node_rejects_dangerous_urls(url: str, should_reject: bool) -> None:
    """SanitizeNode must reject local, file, data, and javascript URLs."""
    node = SanitizeNode(allowed_career_site_hosts=("careers.example.com",))
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.CAREER_SITE,
        source_name="Example",
        external_id="1",
        url=url,  # type: ignore[arg-type]
        text="Valid job text here.",
        metadata={},
    )
    if should_reject:
        with pytest.raises(RawItemRejected):
            await node.process(item)
    else:
        assert await node.process(item) is not None


@pytest.mark.unit
@pytest.mark.anyio
async def test_sanitize_node_handles_overlong_url() -> None:
    """URLs of extreme length (>8KB) should not crash the pipeline."""
    node = SanitizeNode(allowed_career_site_hosts=("careers.example.com",))
    long_url = "https://careers.example.com/jobs/" + "a" * 10000
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.CAREER_SITE,
        source_name="Example",
        external_id="1",
        url=long_url,  # type: ignore[arg-type]
        text="Valid text.",
        metadata={},
    )
    # Must either pass (URL normalized) or raise RawItemRejected, but not RuntimeError
    with suppress(RawItemRejected):
        result = await node.process(item)
        assert result is not None


@pytest.mark.unit
@pytest.mark.anyio
async def test_raw_item_metadata_with_massive_payload_does_not_crash() -> None:
    """Metadata with massive payload (e.g. 1MB) should not crash the pipeline."""
    node = SanitizeNode()
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="1",
        url=None,
        text="valid text",
        metadata={"big": "x" * 1_000_000},
    )
    # Must not raise unexpected exceptions
    with suppress(RawItemRejected):
        result = await node.process(item)
        assert result is not None
