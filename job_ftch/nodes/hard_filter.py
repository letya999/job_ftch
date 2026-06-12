"""Hard gate after post-type classification."""

from __future__ import annotations

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import PostType, RawItem, TriageRejectionReason
from job_ftch.domain.profile import ProfileCatalog  # noqa: TC001


class HardFilterNode:
    def __init__(self, catalog: ProfileCatalog) -> None:
        self._catalog = catalog

    async def process(self, item: RawItem) -> RawItem | None:
        metadata = item.metadata
        post_type = metadata.get("preclassified_post_type", PostType.UNKNOWN.value)
        if post_type in {
            PostType.CANDIDATE_SEEKING.value,
            PostType.ANNOUNCEMENT.value,
            PostType.SPAM.value,
        }:
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details=f"Fast classifier rejected item as {post_type!r}.",
                item=item,
            )

        language = str(metadata.get("detected_language", "unknown"))
        if not self._language_allowed(language):
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details=f"Language {language!r} is not allowed by active profile catalog.",
                item=item,
            )

        lowered = item.text.casefold()
        for profile in self._catalog.profiles:
            if profile.blocked_companies and any(company.casefold() in lowered for company in profile.blocked_companies):
                raise RawItemDropped(
                    reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                    details="Item mentions a blocked company.",
                    item=item,
                )
        return item

    def _language_allowed(self, language: str) -> bool:
        allowed = {
            lang.value
            for profile in self._catalog.profiles
            for lang in profile.allowed_languages
        }
        return not allowed or language in allowed or language == "unknown"
