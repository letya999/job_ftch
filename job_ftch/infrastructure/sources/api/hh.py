from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_source_v2
from job_ftch.domain import RawItem, SourceKind

from .base import OfficialAPISource

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider, Store
    from job_ftch.domain.source_spec import RestAPISourceSpec


class HHAPISource(OfficialAPISource):
    """HH.ru (HeadHunter) API adapter."""

    def __init__(
        self,
        spec: RestAPISourceSpec,
        auth: AuthProvider,
        store: Store | None = None,
    ) -> None:
        # Default field map for HH if not provided
        if not spec.field_map:
            spec = spec.model_copy(
                update={
                    "field_map": {
                        "external_id": "id",
                        "url": "alternate_url",
                        "text": "snippet.requirement",  # Or description if full vacancy loaded
                        "title": "name",
                        "company": "employer.name",
                        "location": "area.name",
                    }
                }
            )
        super().__init__(spec, auth, store, source_kind=SourceKind.CAREER_SITE)

    def _map_to_raw_item(self, item: dict[str, Any]) -> RawItem:
        return super()._map_to_raw_item(item)


@register_source_v2("hh")
def _create_hh_source(spec: Any, auth: AuthProvider) -> HHAPISource:
    return HHAPISource(spec, auth)
