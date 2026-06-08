from typing import Any

from application.registry import register_source_v2
from domain import RawItem, SourceKind
from infrastructure.sources.api.base import OfficialAPISource


class LeverAPISource(OfficialAPISource):
    """Lever public job board API. No auth required for public postings."""

    def __init__(
        self,
        spec: Any,
        auth: Any,
        store: Any | None = None,
        source_kind: SourceKind = SourceKind.CAREER_SITE,
    ) -> None:
        # Lever public API base URL
        self.base_url = f"https://api.lever.co/v0/postings/{spec.company}?mode=json&limit=250"
        super().__init__(spec, auth, store, source_kind=source_kind)

    def _map_to_raw_item(self, data: dict[str, Any]) -> RawItem:
        text_parts = [
            data.get("text", ""),
            data.get("descriptionPlain") or data.get("description", ""),
        ]
        text = "\n\n".join(p for p in text_parts if p).strip()
        if not text:
            text = data.get("text", data.get("id", "unknown"))

        categories = data.get("categories", {})
        metadata: dict[str, Any] = {
            "team": categories.get("team"),
            "commitment": categories.get("commitment"),
            "tags": data.get("tags", []),
        }
        source_name = getattr(self.spec, "source_name", None) or "lever"
        return RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=str(data["id"]),
            url=data.get("hostedUrl"),
            text=text or "No description",
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    def _extract_items(self, response_data: Any) -> list[dict[str, Any]]:
        # Lever returns a JSON array at root, not {"data": [...]}
        if isinstance(response_data, list):
            return response_data
        return response_data.get("data", [])

    def _extract_cursor(self, response_data: Any) -> str | None:
        return None  # Lever uses limit param, no cursor pagination for public API


@register_source_v2("lever")
def _create_lever(spec: Any, auth: Any, store: Any | None = None) -> LeverAPISource:
    return LeverAPISource(spec, auth, store, source_kind=SourceKind.CAREER_SITE)
