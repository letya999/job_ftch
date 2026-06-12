import json
import re
from pathlib import Path
from typing import Any

from job_ftch.domain.models import SkillTag


class OntologyNormalizer:
    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = Path(__file__).parent / "data"
        
        self.role_aliases = self._load_json(data_dir / "role_aliases.json")
        self.seniority_aliases = self._load_json(data_dir / "seniority_aliases.json")
        self.skill_aliases = self._load_json(data_dir / "skill_aliases.json")
        
        # Build inverted lookup lists for fast matching. 
        # Keep them as lists of (pattern, canonical) to preserve JSON priority.
        self.role_lookup = self._build_regex_lookup(self.role_aliases)
        self.seniority_lookup = self._build_regex_lookup(self.seniority_aliases)
        self.skill_lookup = self._build_skill_lookup(self.skill_aliases)

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_regex_lookup(self, alias_data: dict[str, Any]) -> list[tuple[re.Pattern, str]]:
        lookup = []
        # JSON order is priority. Management/DevOps/Data usually should come before generic Engineering.
        for canonical, data in alias_data.items():
            aliases = data.get("aliases_en", []) + data.get("aliases_ru", [])
            if not aliases:
                continue
            # Escaping aliases and joining with |
            # We use \b for word boundaries if the alias is alphanumeric
            patterns = []
            for a in aliases:
                a_esc = re.escape(a.strip().casefold())
                if a_esc and a_esc[0].isalnum() and a_esc[-1].isalnum():
                    patterns.append(rf"\b{a_esc}\b")
                else:
                    patterns.append(a_esc)
            
            combined_re = re.compile("|".join(patterns), re.IGNORECASE)
            lookup.append((combined_re, canonical))
        return lookup

    def _build_skill_lookup(self, skill_data: dict[str, Any]) -> dict[str, tuple[str, str]]:
        lookup = {}
        for skill_id, data in skill_data.items():
            canonical_name = data.get("canonical_name", skill_id)
            aliases = data.get("aliases_en", []) + data.get("aliases_ru", [])
            for alias in aliases:
                lookup[alias.casefold()] = (canonical_name, skill_id)
            # Also add canonical name itself to lookup if not present
            if canonical_name.casefold() not in lookup:
                lookup[canonical_name.casefold()] = (canonical_name, skill_id)
        return lookup

    def infer_role_family(self, title: str, language: str = "unknown") -> str | None:
        for pattern, family in self.role_lookup:
            if pattern.search(title):
                return family
        return None

    def infer_seniority(self, title: str) -> str | None:
        for pattern, seniority in self.seniority_lookup:
            if pattern.search(title):
                return seniority
        return None

    def normalize_skill(self, skill_name: str) -> tuple[str, str | None]:
        match = self.skill_lookup.get(skill_name.casefold())
        if match:
            return match
        return skill_name, None

    def normalize_skills(self, skills: tuple[SkillTag, ...]) -> tuple[SkillTag, ...]:
        normalized = []
        for skill in skills:
            canonical_name, skill_id = self.normalize_skill(skill.canonical_name)
            if skill_id != skill.skill_id or canonical_name != skill.canonical_name:
                normalized.append(SkillTag(canonical_name=canonical_name, skill_id=skill_id))
            else:
                normalized.append(skill)
        return tuple(normalized)


_DEFAULT_NORMALIZER: OntologyNormalizer | None = None


def get_default_normalizer() -> OntologyNormalizer:
    global _DEFAULT_NORMALIZER
    if _DEFAULT_NORMALIZER is None:
        _DEFAULT_NORMALIZER = OntologyNormalizer()
    return _DEFAULT_NORMALIZER
