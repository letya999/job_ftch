from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults


def test_airi_runtime_defaults_scope_tls_relaxation_to_the_source() -> None:
    spec = apply_runtime_defaults(CareerSiteSpec(url="https://airi.net/ru/hr/"))

    assert spec.monitor_config["skip_ssl"] is True
    assert spec.monitor_config["force_monitor"] == "dom"
    assert spec.url_filter == r"airi\.net/ru/hr/[^/?#]+_\d+/?$"
