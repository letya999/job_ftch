"""Bounded, task-owned capture of API responses from browser pages."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

API_HINT_RE = re.compile(r"(fetch\(|axios|graphql|/api/|vacanc|job[s_/:-])", re.IGNORECASE)
_REPLAY_HEADER_ALLOWLIST = frozenset(
    {"accept", "content-type", "origin", "referer", "x-requested-with"}
)


@dataclass(slots=True)
class CapturedResponse:
    url: str
    data: Any
    method: str = "GET"
    request_headers: dict[str, str] | None = None
    post_data: str | bytes | None = None
    replay_cookie_header: str | None = field(default=None, repr=False)


class BoundedResponseCollector:
    """Own response-decode tasks and enforce response/memory limits."""

    def __init__(
        self,
        *,
        max_responses: int,
        max_single_bytes: int,
        max_total_bytes: int,
        decode_concurrency: int,
        api_pattern: str | None = None,
    ) -> None:
        self.max_responses = max_responses
        self.max_single_bytes = max_single_bytes
        self.max_total_bytes = max_total_bytes
        self.api_pattern = api_pattern
        self.payloads: list[CapturedResponse] = []
        self.total_bytes = 0
        self.truncated = False
        self.scheduled_count = 0
        self._semaphore = asyncio.Semaphore(decode_concurrency)
        self._retention_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> BoundedResponseCollector:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info
        await self.cancel()

    def schedule(self, response: Any) -> None:
        if self.scheduled_count >= self.max_responses:
            self.truncated = True
            return
        self.scheduled_count += 1
        task = asyncio.create_task(self._capture(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _capture(self, response: Any) -> None:
        async with self._semaphore:
            try:
                resp_url = str(response.url)
                if self.api_pattern and not re.search(self.api_pattern, resp_url, re.IGNORECASE):
                    return
                headers = await response.all_headers()
                content_type = str(headers.get("content-type", ""))
                if "json" not in content_type.lower() and not API_HINT_RE.search(resp_url):
                    return
                declared = int(headers.get("content-length", "0") or 0)
                if declared > self.max_single_bytes:
                    self.truncated = True
                    return

                body_reader = getattr(response, "body", None)
                if callable(body_reader):
                    materialized = await body_reader()
                    raw = bytes(materialized)
                    if len(raw) > self.max_single_bytes:
                        self.truncated = True
                        return
                    data = json.loads(raw)
                    size = len(raw)
                else:
                    data = await response.json()
                    size = declared or len(
                        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
                    )
                    if size > self.max_single_bytes:
                        self.truncated = True
                        return
                request = getattr(response, "request", None)
                method = str(getattr(request, "method", "GET") or "GET").upper()
                request_headers_raw = getattr(request, "headers", {}) or {}
                all_headers = getattr(request, "all_headers", None)
                if callable(all_headers):
                    resolved = all_headers()
                    if hasattr(resolved, "__await__"):
                        resolved = await resolved
                    request_headers_raw = resolved
                request_headers = {
                    str(key).lower(): str(value)
                    for key, value in dict(request_headers_raw).items()
                    if str(key).lower() in _REPLAY_HEADER_ALLOWLIST
                }
                post_data = getattr(request, "post_data", None)
                replay_cookie_header = next(
                    (
                        str(value)
                        for key, value in dict(request_headers_raw).items()
                        if str(key).lower() == "cookie"
                    ),
                    None,
                )
                # Decode runs concurrently, but retaining a payload is one
                # atomic accounting operation; otherwise two decoders can
                # both pass the total-byte check and exceed the memory cap.
                async with self._retention_lock:
                    if self.total_bytes + size > self.max_total_bytes:
                        self.truncated = True
                        return
                    self.total_bytes += size
                    self.payloads.append(
                        CapturedResponse(
                            url=resp_url,
                            data=data,
                            method=method,
                            request_headers=request_headers,
                            post_data=post_data,
                            replay_cookie_header=replay_cookie_header,
                        )
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                return
            except Exception:
                return

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def cancel(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
