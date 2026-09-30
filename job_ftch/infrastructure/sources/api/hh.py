from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_source_spec
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
        raw = super()._map_to_raw_item(item)
        salary = item.get("salary_range") or item.get("salary")
        metadata = dict(raw.metadata)
        if isinstance(salary, dict):
            metadata["base_salary"] = {
                "min": salary.get("from"),
                "max": salary.get("to"),
                "currency": salary.get("currency"),
                "gross": salary.get("gross"),
                "period": "month"
                if item.get("salary") and not item.get("salary_range")
                else "unknown",
            }
        employer = item.get("employer")
        if isinstance(employer, dict) and employer.get("name"):
            metadata.update(company=employer["name"], company_authoritative=True)
        area = item.get("area")
        if isinstance(area, dict) and area.get("name"):
            metadata["locations"] = [area["name"]]
        metadata["title"] = item.get("name")
        return raw.model_copy(update={"metadata": metadata})


@register_source_spec("hh_api")
def _create_hh(spec: Any, auth: AuthProvider, store: Any = None) -> HHAPISource:
    return HHAPISource(spec, auth)
