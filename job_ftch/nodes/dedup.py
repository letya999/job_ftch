"""Raw-item deduplication and duplicate explainability."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from rapidfuzz import fuzz

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
    ) -> None:
        self._store = store
        self._near_duplicate_score_cutoff = near_duplicate_score_cutoff
        self._title_score_cutoff = title_score_cutoff
        self._defer_commit = defer_commit
        self._claim_ttl_seconds = claim_ttl_seconds
        self._claim_owner = uuid4().hex
        self._claims: dict[str, tuple[RememberedDedupKey, ...]] = {}
        self._dedup_cache: dict[str, RememberedDedupKey] = {}
        self._fingerprint_records: tuple[RememberedDedupKey, ...] | None = None
        self._lock = asyncio.Lock()

    async def process(self, item: RawItem) -> RawItem | None:
        async with self._lock:
            duplicate = await self._find_duplicate(item)
            if duplicate is None:
                records = self._records_for_item(item)
                if self._defer_commit:
                    claim_keys = tuple(
                        record.key
                        for record in records
                        if record.kind in (DedupKeyKind.URL, DedupKeyKind.CONTENT)
                    )
                    acquired_keys: list[str] = []
                    for claim_key in claim_keys:
                        acquired = await self._store.acquire_dedup_claim(
                            claim_key, self._claim_owner, ttl_seconds=self._claim_ttl_seconds
                        )
                        if not acquired:
                            for acquired_key in acquired_keys:
                                await self._store.release_dedup_claim(
                                    acquired_key, self._claim_owner
                                )
                            raise RuntimeError("dedup claim is held; retry item later")
                        acquired_keys.append(claim_key)
                    self._claims[item.stable_id] = records
                else:
                    await self._remember_records(records)
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
            await self._release_claim_records(records)

    async def release_claim(self, item_id: str) -> None:
        async with self._lock:
            await self._release_claim_records(self._claims.pop(item_id, ()))

    async def _release_claim_records(self, records: tuple[RememberedDedupKey, ...]) -> None:
        for record in records:
            if record.kind in (DedupKeyKind.URL, DedupKeyKind.CONTENT):
                await self._store.release_dedup_claim(record.key, self._claim_owner)

    async def _find_duplicate(self, item: RawItem) -> DuplicateRecord | None:
        item_id = item.stable_id
        url = dedup_url_for_raw_item(item)
        if url is not None:
            # A URL is a locator.  It becomes an exact raw duplicate only
            # together with the immutable content version observed there.
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
        # Near-match similarity is evidence for post-extraction identity
        # resolution, not safe raw suppression: changed salary, role, or
        # employment terms frequently share almost all boilerplate.
        return None

    async def _find_near_duplicate(self, item: RawItem) -> DuplicateRecord | None:
        fingerprint = self._fingerprint_payload(item)
        if not fingerprint:
            return None
        title = dedup_title_for_raw_item(item)
        best_match: RememberedDedupKey | None = None
        best_score = 0.0
        for record in await self._get_fingerprint_records():
            if record.item_id == item.stable_id or not record.match_text:
                continue
            content_score = fuzz.token_set_ratio(fingerprint, record.match_text)
            if content_score < self._near_duplicate_score_cutoff:
                continue
            title_score = (
                fuzz.ratio(title, record.match_text.split(" || ", maxsplit=1)[0]) if title else 0.0
            )
            if title_score < self._title_score_cutoff:
                continue
            if content_score > best_score:
                best_match = record
                best_score = content_score
        if best_match is None:
            return None
        if best_match.source_kind != item.source_kind or best_match.source_name != item.source_name:
            # Cross-posted jobs are common. Preserve them long enough for the
            # later extraction/grouping stages to merge instead of dropping
            # them at raw-item time.
            return None
        return DuplicateRecord(
            item_id=item.stable_id,
            source_kind=item.source_kind,
            source_name=item.source_name,
            reason=DuplicateRejectionReason.DUPLICATE_NEAR_MATCH,
            duplicate_key=dedup_content_key_for_raw_item(item),
            matched_key=best_match.key,
            matched_item_id=best_match.item_id,
            matched_source_kind=best_match.source_kind,
            matched_source_name=best_match.source_name,
            score=round(best_score, 2),
            details="Raw item is a near-duplicate of a previously remembered job signal.",
        )

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
            self._cache_record(record)

    async def _lookup_dedup_key(self, key: str) -> RememberedDedupKey | None:
        cached = self._dedup_cache.get(key)
        if cached is not None:
            return cached
        record = await self._store.get_dedup_key(key)
        if record is not None:
            self._cache_record(record)
        return record

    async def _get_fingerprint_records(self) -> tuple[RememberedDedupKey, ...]:
        if self._fingerprint_records is None:
            records = await self._store.list_dedup_keys(DedupKeyKind.FINGERPRINT.value)
            self._fingerprint_records = tuple(records)
            for record in records:
                self._dedup_cache.setdefault(record.key, record)
        return self._fingerprint_records

    def _cache_record(self, record: RememberedDedupKey) -> None:
        self._dedup_cache[record.key] = record
        if (
            record.kind is DedupKeyKind.FINGERPRINT
            and self._fingerprint_records is not None
            and not any(existing.key == record.key for existing in self._fingerprint_records)
        ):
            self._fingerprint_records = (*self._fingerprint_records, record)

    def _fingerprint_payload(self, item: RawItem) -> str:
        return " || ".join(
            [
                dedup_title_for_raw_item(item),
                dedup_company_for_raw_item(item),
                dedup_similarity_text_for_raw_item(item),
            ]
        ).strip()
