from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_source_spec
from job_ftch.domain import SourceKind

from .base import OfficialAPISource

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider, Store
    from job_ftch.domain.source_spec import RestAPISourceSpec


class GenericRestAPISource(OfficialAPISource):
    """Generic REST API source using RestAPISourceSpec."""

    pass


class GreenhouseAPISource(OfficialAPISource):
    """Greenhouse-specific API adapter."""

    def __init__(
        self,
        spec: RestAPISourceSpec,
        auth: AuthProvider,
        store: Store | None = None,
    ) -> None:
        # Default field map for Greenhouse if not provided
        if not spec.field_map:
            spec = spec.model_copy(
                update={
                    "field_map": {
                        "external_id": "id",
                        "url": "absolute_url",
                        "text": "content",
                        "title": "title",
                        "location": "location.name",
                    }
                }
            )
        super().__init__(spec, auth, store, source_kind=SourceKind.CAREER_SITE)


@register_source_spec("rest_api")
def _create_rest_generic(spec: Any, auth: AuthProvider, store: Any = None) -> GenericRestAPISource:
    return GenericRestAPISource(spec, auth)


@register_source_spec("greenhouse_api")
def _create_greenhouse(spec: Any, auth: AuthProvider, store: Any = None) -> GreenhouseAPISource:
    return GreenhouseAPISource(spec, auth)
