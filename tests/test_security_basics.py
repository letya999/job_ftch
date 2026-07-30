from __future__ import annotations

from pathlib import Path


def test_env_file_is_not_committed() -> None:
    assert not Path(".env").exists() or ".env" in Path(".gitignore").read_text(encoding="utf-8")  # nosec


# Any of these marks a value as "fill this in", so the example carries no real secret.
# Asserting one literal made the test fail when the file switched convention, which says
# nothing about whether a credential leaked.
_PLACEHOLDER_TOKENS = ("replace_me", "change_me", "your_", "xxxx")


def test_env_example_has_placeholders_only() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    lowered = text.casefold()
    assert any(token in lowered for token in _PLACEHOLDER_TOKENS), (  # nosec
        f".env.example must mark secret values with a placeholder from {_PLACEHOLDER_TOKENS}"
    )
    assert "-----BEGIN PRIVATE KEY-----" not in text  # nosec # pragma: allowlist secret
