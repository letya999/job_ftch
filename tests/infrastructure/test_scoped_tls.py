from __future__ import annotations

import hashlib
import ssl

import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.career_site import _make_ssl_context
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults


def test_tbank_ca_is_scoped_and_verification_stays_enabled() -> None:
    default = _make_ssl_context()
    scoped = _make_ssl_context("tbank-russian-root")
    assert scoped.verify_mode == ssl.CERT_REQUIRED
    assert scoped.check_hostname is True
    # Public CA pin from the official bank download, not an authentication secret.
    fingerprint = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"  # pragma: allowlist secret
    assert fingerprint not in {
        hashlib.sha256(c).hexdigest() for c in default.get_ca_certs(binary_form=True)
    }
    assert fingerprint in {
        hashlib.sha256(c).hexdigest() for c in scoped.get_ca_certs(binary_form=True)
    }
    for name in ["../tbank-russian-root", "/tmp/credentials", ""]:
        with pytest.raises(ValueError, match="bundle name"):
            _make_ssl_context(name)


def test_tbank_defaults_scope_ca_to_listing_and_details() -> None:
    bank = apply_runtime_defaults(CareerSiteSpec(url="https://www.tbank.ru/career/"))
    assert bank.monitor_config["tls_ca_bundle"] == "tbank-russian-root"
    assert bank.scraper_config == {}
    other = apply_runtime_defaults(CareerSiteSpec(url="https://example.org/jobs"))
    assert "tls_ca_bundle" not in other.monitor_config
    assert "tls_ca_bundle" not in other.scraper_config
