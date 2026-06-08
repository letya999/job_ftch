from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from domain import RawItem, SourceKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from application.contracts import AuthProvider, Store
    from domain import QuarantinedRawItem
    from domain.source_spec import RestAPISourceSpec

logger = structlog.get_logger(__name__)


class OfficialAPISource:
    """Base for all official API source adapters."""

    def __init__(
        self,
        spec: RestAPISourceSpec,
        auth: AuthProvider,
        store: Store | None = None,
        source_kind: SourceKind = SourceKind.CAREER_SITE,
    ) -> None:
        self.spec = spec
        self.auth = auth
        self.store = store
        self.source_kind = source_kind
        self.source_name = spec.source_name or spec.base_url.host or "api"

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        headers = self.spec.headers.copy()
        # Resolve auth
        if self.spec.auth_source_id:
            creds = self.auth.resolve(self.spec.auth_source_id)
            # Apply auth to headers or params (simple version: add to headers)
            for k, v in creds.items():
                headers[k] = v

        params = self.spec.params.copy()

        # Load incremental cursor
        if self.store:
            cursor_key = f"{self.source_kind}:{self.source_name}:cursor"
            last_cursor = await self.store.get_run_state(cursor_key)
            if last_cursor and self.spec.incremental_cursor_field:
                params[self.spec.incremental_cursor_field] = last_cursor

        url = f"{self.spec.base_url}{self.spec.jobs_endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Simple pagination handling
            # (In a real implementation we would loop and handle cursor/offset/link)
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                # Assume data is a list or has a jobs key
                items = data if isinstance(data, list) else data.get("jobs", [])

                for item in items:
                    yield self._map_to_raw_item(item)

                # Update cursor if needed
                if items and self.spec.incremental_cursor_field:
                    # Very simple: use id of first item as next cursor
                    # (Implementation detail depends on specific API)
                    pass

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
        text = mapped_data.get("text") or item.get("description") or item.get("text") or str(item)

        return RawItem(
            source_kind=self.source_kind,
            source_name=self.source_name,
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
