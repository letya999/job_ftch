"""Career-site runtime defaults: thin wrapper over the site_parser registry.

All host-specific defaults live in the `site_parsers` package (per ADR-033).
This module is a thin pass-through that resolves a registered site parser for
the spec URL and applies its `runtime_defaults(url)`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


def _spec_dict_from_defaults(defaults: object) -> dict[str, Any]:
    """Translate a `SiteRuntimeDefaults` to a `monitor_config`-shaped dict."""
    from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

    if isinstance(defaults, dict):
        dict_defaults = dict(defaults)
        expand_links = dict_defaults.get("expand_links")
        if isinstance(expand_links, tuple):
            dict_defaults["expand_links"] = list(expand_links)
        extra = dict_defaults.pop("extra", None)
        if isinstance(extra, dict):
            dict_defaults.update(extra)
        return dict_defaults
    if not isinstance(defaults, SiteRuntimeDefaults):
        return {}
    out: dict[str, Any] = {}
    if defaults.url_filter is not None:
        out["url_filter"] = defaults.url_filter
    if defaults.render is not None:
        out["render"] = defaults.render
    if defaults.wait is not None:
        out["wait"] = defaults.wait
    if defaults.include_if_detail_page is not None:
        out["include_if_detail_page"] = defaults.include_if_detail_page
    if defaults.expand_links is not None:
        out["expand_links"] = list(defaults.expand_links)
    if defaults.extra:
        out.update(defaults.extra)
    return out


def apply_runtime_defaults(spec: CareerSiteSpec) -> CareerSiteSpec:
    """Inject safe domain defaults for generic career-site URLs.

    Resolution order:
      1. Look up a registered `SiteParser` for `spec.url` via the
         `register_site_parser` registry (per ADR-033).
      2. Ask it for `runtime_defaults(url)`. If `None`, return the spec unchanged.

    Explicit spec values always win. This keeps manual single-URL runs aligned
    with the richer YAML source configs used in normal operation.
    """

    from job_ftch.application.registry import resolve_site_parser

    parser = resolve_site_parser(spec.url)
    if parser is None:
        return spec

    runtime_defaults = getattr(parser, "runtime_defaults", None)
    if runtime_defaults is None:
        return spec

    defaults = runtime_defaults(spec.url)
    if defaults is None:
        return spec

    default_monitor_config = _spec_dict_from_defaults(defaults)
    if not default_monitor_config:
        return spec

    monitor_config = dict(default_monitor_config)
    monitor_config.update(spec.monitor_config)

    url_filter = spec.url_filter
    if url_filter is None:
        url_filter = monitor_config.get("url_filter")

    return spec.model_copy(
        update={
            "monitor_config": monitor_config,
            "url_filter": url_filter,
        }
    )
