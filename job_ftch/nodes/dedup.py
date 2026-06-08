"""Raw-item deduplication and duplicate explainability."""

from __future__ import annotations

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from job_ftch.application.contracts import Store


class DedupNode:
    def __init__(
        self,
        store: Store,
        *,
        near_duplicate_score_cutoff: float = 91.0,
        title_score_cutoff: float = 85.0,
    ) -> None:
        self._store = store
        self._near_duplicate_score_cutoff = near_duplicate_score_cutoff
        self._title_score_cutoff = title_score_cutoff

    async def process(self, item: RawItem) -> RawItem | None:
        duplicate = await self._find_duplicate(item)
        if duplicate is not None:
            await self._store.record_duplicate(duplicate)
            raise RawItemDropped(
                reason=duplicate.reason,
                details=duplicate.details,
                item=item,
            )
        await self._remember_item(item)
        return item

    async def _find_duplicate(self, item: RawItem) -> DuplicateRecord | None:
        item_id = item.stable_id
        url = dedup_url_for_raw_item(item)
        if url is not None:
            url_key = f"url:{url}"
            if await self._store.has_dedup_key(url_key):
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
        if await self._store.has_dedup_key(content_key):
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
        return await self._find_near_duplicate(item)

    async def _find_near_duplicate(self, item: RawItem) -> DuplicateRecord | None:
        fingerprint = self._fingerprint_payload(item)
        if not fingerprint:
            return None
        title = dedup_title_for_raw_item(item)
        best_match: RememberedDedupKey | None = None
        best_score = 0.0
        for record in await self._store.list_dedup_keys(DedupKeyKind.FINGERPRINT.value):
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

    async def _remember_item(self, item: RawItem) -> None:
        url = dedup_url_for_raw_item(item)
        if url is not None:
            await self._store.remember_dedup_key(
                RememberedDedupKey(
                    key=f"url:{url}",
                    kind=DedupKeyKind.URL,
                    item_id=item.stable_id,
                    source_kind=item.source_kind,
                    source_name=item.source_name,
                    url=url,
                )
            )
        await self._store.remember_dedup_key(
            RememberedDedupKey(
                key=dedup_content_key_for_raw_item(item),
                kind=DedupKeyKind.CONTENT,
                item_id=item.stable_id,
                source_kind=item.source_kind,
                source_name=item.source_name,
            )
        )
        await self._store.remember_dedup_key(
            RememberedDedupKey(
                key=f"fingerprint:{item.stable_id}",
                kind=DedupKeyKind.FINGERPRINT,
                item_id=item.stable_id,
                source_kind=item.source_kind,
                source_name=item.source_name,
                match_text=self._fingerprint_payload(item),
                url=url,
            )
        )

    async def _lookup_dedup_key(self, key: str) -> RememberedDedupKey | None:
        for record in await self._store.list_dedup_keys():
            if record.key == key:
                return record
        return None

    def _fingerprint_payload(self, item: RawItem) -> str:
        return " || ".join(
            [
                dedup_title_for_raw_item(item),
                dedup_company_for_raw_item(item),
                dedup_similarity_text_for_raw_item(item),
            ]
        ).strip()
