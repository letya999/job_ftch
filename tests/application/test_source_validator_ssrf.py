import pytest

from job_ftch.infrastructure.source_validation import check_url_reachable


@pytest.mark.anyio
async def test_reachability_probe_blocks_link_local_metadata_address() -> None:
    ok, reason = await check_url_reachable("http://169.254.169.254/latest/meta-data/", timeout=0.1)

    assert not ok
    assert "SSRF guard blocked" in reason
