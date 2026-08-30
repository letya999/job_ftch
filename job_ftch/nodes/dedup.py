"""Raw-item deduplication and duplicate explainability."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING
from uuid import uuid4

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RawItem,
    RememberedDedupKey,
    dedup_company_for_raw_item,
    dedup_content_key_for_raw_item,
    dedup_similarity_text_for_raw_item,
    dedup_title_for_raw_item,
    dedup_url_for_raw_item,
)
from job_ftch.domain.observation import content_hash_for_raw_item

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store


class DedupNode:
    def __init__(
        self,
        store: Store,
        *,
        near_duplicate_score_cutoff: float = 91.0,
        title_score_cutoff: float = 85.0,
        defer_commit: bool = False,
        claim_ttl_seconds: int = 300,
        cache_max_entries: int = 10_000,
        personal_mode: bool = False,
    ) -> None:
        self._store = store
        self._near_duplicate_score_cutoff = near_duplicate_score_cutoff
        self._title_score_cutoff = title_score_cutoff
        self._defer_commit = defer_commit
        self._claim_ttl_seconds = claim_ttl_seconds
        self._claim_owner = uuid4().hex
        self._claims: dict[str, tuple[RememberedDedupKey, ...]] = {}
        self._dedup_cache: OrderedDict[str, RememberedDedupKey] = OrderedDict()
        self._cache_max_entries = max(100, cache_max_entries)
        self._personal_mode = personal_mode
        self._lock = asyncio.Lock()
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_size": len(self._dedup_cache),
        }

    async def process(self, item: RawItem) -> RawItem | None:
        if self._personal_mode:
            return item
        duplicate = await self._find_duplicate(item)
        if duplicate is None:
            records = self._records_for_item(item)
            if self._defer_commit:
                claim_keys = tuple(
                    record.key
                    for record in records
                    if record.kind in (DedupKeyKind.URL, DedupKeyKind.CONTENT)
                )
                reservation = await self._store.compare_and_reserve(
                    claim_keys, self._claim_owner, ttl_seconds=self._claim_ttl_seconds
                )
                if not reservation.acquired:
                    raise RuntimeError("dedup claim is held; retry item later")
                async with self._lock:
                    self._claims[item.stable_id] = records
            else:
                await self._remember_records(records)
                async with self._lock:
                    for record in records:
                        self._cache_record(record)
        if duplicate is not None:
            await self._store.record_duplicate(duplicate)
            raise RawItemDropped(
                reason=duplicate.reason,
                details=duplicate.details,
                item=item,
            )
        return item

    async def commit_claim(self, item_id: str) -> None:
        async with self._lock:
            records = self._claims.pop(item_id, ())
        await self._remember_records(records)
        async with self._lock:
            for record in records:
                self._cache_record(record)
            await self._release_claim_records(records)

    async def release_claim(self, item_id: str) -> None:
        async with self._lock:
            records = self._claims.pop(item_id, ())
        await self._release_claim_records(records)

    async def _release_claim_records(self, records: tuple[RememberedDedupKey, ...]) -> None:
        for record in records:
            if record.kind in (DedupKeyKind.URL, DedupKeyKind.CONTENT):
                await self._store.release_dedup_claim(record.key, self._claim_owner)

    async def _find_duplicate(self, item: RawItem) -> DuplicateRecord | None:
        item_id = item.stable_id
        url = dedup_url_for_raw_item(item)
        if url is not None:
            url_key = f"url:{url}:{content_hash_for_raw_item(item)}"
            match = await self._lookup_dedup_key(url_key)
            if match is not None:
                return DuplicateRecord(
                    item_id=item_id,
                    source_kind=item.source_kind,
                    source_name=item.source_name,
                    reason=DuplicateRejectionReason.DUPLICATE_URL,
                    duplicate_key=url_key,
                    matched_key=match.key,
                    matched_item_id=match.item_id,
                    matched_source_kind=match.source_kind,
                    matched_source_name=match.source_name,
                    details="Raw item reuses an already-seen canonical job URL.",
                )
        content_key = dedup_content_key_for_raw_item(item)
        match = await self._lookup_dedup_key(content_key)
        if match is not None:
            return DuplicateRecord(
                item_id=item_id,
                source_kind=item.source_kind,
                source_name=item.source_name,
                reason=DuplicateRejectionReason.DUPLICATE_CONTENT,
                duplicate_key=content_key,
                matched_key=match.key,
                matched_item_id=match.item_id,
                matched_source_kind=match.source_kind,
                matched_source_name=match.source_name,
                details="Raw item matches a previously remembered normalized content signature.",
            )
        return None

    def _records_for_item(self, item: RawItem) -> tuple[RememberedDedupKey, ...]:
        url = dedup_url_for_raw_item(item)
        if url is not None:
            url_record = RememberedDedupKey(
                key=f"url:{url}:{content_hash_for_raw_item(item)}",
                kind=DedupKeyKind.URL,
                item_id=item.stable_id,
                source_kind=item.source_kind,
                source_name=item.source_name,
                url=url,
            )
            records = [url_record]
        else:
            records = []
        content_record = RememberedDedupKey(
            key=dedup_content_key_for_raw_item(item),
            kind=DedupKeyKind.CONTENT,
            item_id=item.stable_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
        )
        records.append(content_record)
        fingerprint_record = RememberedDedupKey(
            key=f"fingerprint:{item.stable_id}",
            kind=DedupKeyKind.FINGERPRINT,
            item_id=item.stable_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
            match_text=self._fingerprint_payload(item),
            url=url,
        )
        records.append(fingerprint_record)
        return tuple(records)

    async def _remember_records(self, records: tuple[RememberedDedupKey, ...]) -> None:
        for record in records:
            await self._store.remember_dedup_key(record)

    async def _lookup_dedup_key(self, key: str) -> RememberedDedupKey | None:
        async with self._lock:
            cached = self._dedup_cache.get(key)
        if cached is not None:
            self._cache_hits += 1
            return cached
        self._cache_misses += 1
        record = await self._store.get_dedup_key(key)
        if record is not None:
            async with self._lock:
                self._cache_record(record)
        return record

    def _cache_record(self, record: RememberedDedupKey) -> None:
        self._dedup_cache[record.key] = record
        self._dedup_cache.move_to_end(record.key)
        while len(self._dedup_cache) > self._cache_max_entries:
            self._dedup_cache.popitem(last=False)

    def _fingerprint_payload(self, item: RawItem) -> str:
        return " || ".join(
            [
                dedup_title_for_raw_item(item),
                dedup_company_for_raw_item(item),
                dedup_similarity_text_for_raw_item(item),
            ]
        ).strip()
