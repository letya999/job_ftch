from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_ftch.domain.source_spec import CareerSiteSpec, RestAPISourceSpec, TelegramChannelSpec
from job_ftch.domain.tenant import TenantConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bypass_config", {"nested": {"session_token": "secret"}}),
        ("monitor_config", {"request": {"cookies": "secret"}}),
        ("scraper_config", {"proxy-url": "http://user:pass@proxy"}),  # pragma: allowlist secret
    ],
)
def test_source_configs_reject_nested_credentials(field: str, value: dict[str, object]) -> None:
    model = TelegramChannelSpec if field == "bypass_config" else CareerSiteSpec
    kwargs = (
        {"entity": "channel"} if model is TelegramChannelSpec else {"url": "https://example.com"}
    )
    kwargs[field] = value

    with pytest.raises(ValidationError):
        model(**kwargs)


def test_rest_api_headers_reject_variant_credential_names() -> None:
    with pytest.raises(ValidationError):
        RestAPISourceSpec(
            base_url="https://example.com",
            jobs_endpoint="/jobs",
            headers={"X-Auth-Token": "secret"},
        )


def test_tenant_config_rejects_store_dsn() -> None:
    with pytest.raises(ValidationError):
        TenantConfig(tenant_id="tenant", display_name="Tenant", store_dsn="postgresql://secret")
