from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from pydantic import AnyHttpUrl

from job_ftch.application.watermark import IncrementalCursor
from job_ftch.domain import RawItem, SourceKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.application.contracts import AuthProvider, Store, StoreConnector
    from job_ftch.domain import QuarantinedRawItem

logger = structlog.get_logger(__name__)


class OfficialAPISource:
    """Base for all official API source adapters."""

    def __init__(
        self,
        spec: Any,
        auth: AuthProvider,
        store: Store | None = None,
        source_kind: SourceKind = SourceKind.CAREER_SITE,
    ) -> None:
        self.spec = spec
        self.auth = auth
        self.store = store
        self.source_kind = source_kind
        self.source_name = getattr(spec, "source_name", None)
        if not self.source_name:
            base_url = getattr(self, "base_url", None) or getattr(spec, "base_url", None)
            if base_url:
                from pydantic import TypeAdapter

                url_adapter = TypeAdapter(AnyHttpUrl)
                url_obj = url_adapter.validate_python(str(base_url))
                self.source_name = url_obj.host or "api"
            else:
                self.source_name = "api"

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        headers = self.spec.headers.copy()
        # Resolve auth
        if self.spec.auth_source_id:
            creds = self.auth.resolve(self.spec.auth_source_id)
            # Apply auth to headers or params (simple version: add to headers)
            for k, v in creds.items():
                headers[k] = v

        params = self.spec.params.copy()
        cursor_source_id = f"{self.source_kind}:{self.source_name}"

        # Load incremental cursor
        if self.store and self.spec.incremental_cursor_field:
            last_cursor = await IncrementalCursor(cast("StoreConnector", self.store)).get(
                cursor_source_id
            )
            if last_cursor:
                params[self.spec.incremental_cursor_field] = last_cursor

        url = f"{self.spec.base_url}{self.spec.jobs_endpoint}"

        from job_ftch.config import get_settings
        async with httpx.AsyncClient(timeout=get_settings().api_timeout_seconds) as client:
            # Simple pagination handling
            # (In a real implementation we would loop and handle cursor/offset/link)
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                # Assume data is a list or has a jobs/objects key
                items = (
                    data
                    if isinstance(data, list)
                    else (data.get("jobs") or data.get("objects") or [])
                )

                for item in items:
                    try:
                        yield self._map_to_raw_item(item)
                    except Exception as item_exc:
                        logger.debug(
                            "skipping_unmappable_item",
                            extra={"keys": list(item.keys()), "error": str(item_exc)},
                        )
                        continue

                # Persist incremental cursor (id of first item as next lower bound)
                if items and self.spec.incremental_cursor_field and self.store:
                    first_id = items[0].get("id")
                    if first_id is not None:
                        await IncrementalCursor(cast("StoreConnector", self.store)).set(
                            cursor_source_id, str(first_id)
                        )

            except Exception as e:
                logger.exception("api_fetch_failed", url=url, error=str(e))
                raise

    def _map_to_raw_item(self, item: dict[str, Any]) -> RawItem:
        # Simple field mapping
        mapped_data = {}
        for target, source_path in self.spec.field_map.items():
            # Basic path support (e.g. "metadata.title")
            val = self._get_by_path(item, source_path)
            if val is not None:
                mapped_data[target] = val

        # Mandatory fields
        # If 'text' is not explicitly mapped, compose it from title/description/company if available
        text = mapped_data.get("text")
        if not text:
            parts = []
            for key in ["title", "company", "location", "description"]:
                if mapped_data.get(key):
                    parts.append(str(mapped_data[key]))
            if not parts:
                parts = [item.get("description") or item.get("text") or str(item)]
            text = "\n".join(parts)

        return RawItem(
            source_kind=self.source_kind,
            source_name=self.source_name or "api",
            external_id=str(item.get("id") or mapped_data.get("external_id") or ""),
            url=mapped_data.get("url") or item.get("url") or item.get("absolute_url"),
            text=text,
            metadata=item,
        )

    def _get_by_path(self, data: dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        val: Any = data
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            elif isinstance(val, list) and part.isdigit():
                idx = int(part)
                val = val[idx] if idx < len(val) else None
            else:
                return None
        return val
