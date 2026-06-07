import json
from pathlib import Path

from domain.filter_profile import FilterProfile


def load_filter_profile(path: Path) -> FilterProfile:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml

            data = yaml.safe_load(text)
        except ImportError as exc:
            msg = "PyYAML required: pip install pyyaml"
            raise RuntimeError(msg) from exc
    else:
        data = json.loads(text)
    return FilterProfile.model_validate(data)
