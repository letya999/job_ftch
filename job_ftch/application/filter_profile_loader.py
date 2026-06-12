import json
from pathlib import Path
from typing import Any

from job_ftch.domain.filter_profile import FilterProfile
from job_ftch.domain.profile import ProfileCatalog


def _load_profile_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml

            return yaml.safe_load(text)
        except ImportError as exc:
            msg = "PyYAML required: pip install pyyaml"
            raise RuntimeError(msg) from exc
    return json.loads(text)


def load_filter_profile(path: Path) -> FilterProfile:
    data = _load_profile_payload(path)
    if isinstance(data, dict) and "profiles" in data:
        catalog = ProfileCatalog.model_validate(data)
        return FilterProfile.model_validate(catalog.profiles[0].model_dump(mode="python"))
    return FilterProfile.model_validate(data)


def load_profile_catalog(path: Path) -> ProfileCatalog:
    data = _load_profile_payload(path)
    if isinstance(data, dict) and "profiles" in data:
        return ProfileCatalog.model_validate(data)
    profile = FilterProfile.model_validate(data)
    return profile.to_catalog()
