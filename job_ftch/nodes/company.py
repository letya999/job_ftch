"""Company name canonicalization node (RM-136)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from rapidfuzz import fuzz

from job_ftch.domain.company import normalize_company_name

if TYPE_CHECKING:
    from pathlib import Path

    from job_ftch.domain import Job

_DEFAULT_THRESHOLD = 85  # token_set_ratio threshold for fuzzy alias matching


class CompanyCanonicalizer:
    def __init__(
        self,
        aliases_path: Path | None = None,
        *,
        fuzzy_threshold: int = _DEFAULT_THRESHOLD,
    ) -> None:
        self._threshold = fuzzy_threshold
        # _aliases: {normalized_alias -> canonical_name}
        self._aliases: dict[str, str] = {}
        if aliases_path and aliases_path.exists():
            self._load_aliases(aliases_path)

    def _load_aliases(self, path: Path) -> None:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for canonical, alias_list in data.items():
            # Register canonical itself
            self._aliases[normalize_company_name(canonical)] = canonical
            for alias in alias_list or []:
                self._aliases[normalize_company_name(alias)] = canonical

    def _resolve(self, raw: str) -> str | None:
        normalized = normalize_company_name(raw)
        if normalized in self._aliases:
            return self._aliases[normalized]
        # Fuzzy fallback
        for alias_key, canonical in self._aliases.items():
            score = fuzz.token_set_ratio(normalized, alias_key)
            if score >= self._threshold:
                return canonical
        return None

    async def process(self, item: Job) -> Job | None:
        if not item.company:
            return item
        canonical = self._resolve(item.company)
        if canonical and canonical != item.company_canonical:
            return item.model_copy(update={"company_canonical": canonical})
        return item
