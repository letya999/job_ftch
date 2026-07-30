import httpx
import pytest

from job_ftch.infrastructure.bypass.proxy_bypass import ProxyBypass, ProxyHealth


@pytest.mark.asyncio
async def test_proxy_bypass_blocks_ssrf() -> None:
    bypass = ProxyBypass()
    bypass._current = ProxyHealth(url="http://proxy:8080")
    client = await bypass.apply_http(httpx.AsyncClient())

    with pytest.raises(
        httpx.LocalProtocolError, match="SSRF guard blocked request to private host"
    ):
        await client.get("http://localhost/api")

    with pytest.raises(
        httpx.LocalProtocolError, match="SSRF guard blocked request to private host"
    ):
        await client.get("http://127.0.0.1:8080/admin")

    with pytest.raises(
        httpx.LocalProtocolError, match="SSRF guard blocked request to private host"
    ):
        await client.get("http://169.254.169.254/latest/meta-data")
