from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from job_ftch.infrastructure.bypass.proxy_bypass import verify_proxy


@pytest.mark.asyncio
async def test_verify_proxy_returns_expected_payload() -> None:
    with patch(
        "job_ftch.infrastructure.bypass.proxy_bypass.get_public_ip",
        new_callable=AsyncMock,
    ) as mock_get_ip:
        mock_get_ip.side_effect = ["1.2.3.4", "5.6.7.8"]

        result = await verify_proxy("http://proxy:8080")

    assert result == {
        "direct_ip": "1.2.3.4",
        "proxy_ip": "5.6.7.8",
        "verified": True,
        "error": None,
    }


@pytest.mark.asyncio
async def test_verify_proxy_rejects_same_ip() -> None:
    with patch(
        "job_ftch.infrastructure.bypass.proxy_bypass.get_public_ip",
        new_callable=AsyncMock,
    ) as mock_get_ip:
        mock_get_ip.return_value = "1.2.3.4"

        result = await verify_proxy("http://proxy:8080")

    assert result is not None
    assert result["verified"] is False
    assert "did not change IP" in str(result["error"])


@pytest.mark.asyncio
async def test_verify_proxy_reports_missing_ips() -> None:
    with patch(
        "job_ftch.infrastructure.bypass.proxy_bypass.get_public_ip",
        new_callable=AsyncMock,
    ) as mock_get_ip:
        mock_get_ip.return_value = None

        result = await verify_proxy("http://proxy:8080")

    assert result is not None
    assert result["verified"] is False
    assert result["error"] == "could not determine one or both IPs"


@pytest.mark.asyncio
async def test_residential_proxy_bypass_strict_geo() -> None:
    from job_ftch.infrastructure.bypass.proxy_bypass import ResidentialProxyBypass

    config = {"proxy_geo": "JP"}

    with (
        patch("job_ftch.config.get_settings") as mock_get_settings,
        patch("job_ftch.infrastructure.bypass.proxy_bypass._load_residential_proxies") as mock_load,
    ):
        # Setup settings with strict geo enabled and a dummy gateway configuration
        class DummySettings:
            proxy_strict_geo = True
            proxy_provider = "raw"
            proxy_gateway = ""
            proxy_user = ""
            proxy_pass = ""
            proxy_country_default = ""
            proxy_sticky_ttl_seconds = 600
            proxy_gb_budget = 0.0

        mock_get_settings.return_value = DummySettings()
        # Mock load proxies with some proxies, but we'll mock their geo detection
        mock_load.return_value = ["http://1.1.1.1:80", "http://2.2.2.2:80"]

        bypass = ResidentialProxyBypass(bypass_config=config)

        # In raw mode, no proxies are tagged with geo yet, so it won't find a matching proxy for 'JP'
        # With strict geo enabled, it should raise RuntimeError on apply_http and apply_browser_args

        class DummyClient:
            _domain_hint = "example.com"
            timeout = 10.0

        with pytest.raises(
            RuntimeError,
            match="Strict geo-binding enforced, but no suitable proxy found for domain example.com",
        ):
            await bypass.apply_http(DummyClient())

        with pytest.raises(
            RuntimeError,
            match="Strict geo-binding enforced, but no suitable proxy found for domain example.com",
        ):
            bypass.apply_browser_args({"_domain_hint": "example.com"})
