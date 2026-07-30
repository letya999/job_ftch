"""Tests for YAML layout loading and parsing."""

from __future__ import annotations

from pathlib import Path

from job_ftch.publication.layout import BlockSpec, CardLayout, load_layout


class TestLoadLayout:
    def test_default_layout(self) -> None:
        layout = load_layout()
        assert isinstance(layout, CardLayout)
        assert layout.version == 1
        assert len(layout.blocks) > 0
        assert layout.formatting.leading_emoji is False
        assert layout.formatting.italic is False

    def test_from_yaml_file(self) -> None:
        yaml_path = Path(__file__).resolve().parents[2] / "config" / "publication" / "card.yaml"
        if not yaml_path.exists():
            return
        layout = load_layout(yaml_path)
        assert layout.version == 1
        assert len(layout.blocks) >= 5
        assert layout.formatting.leading_emoji is False

    def test_missing_file_returns_default(self) -> None:
        layout = load_layout("/nonexistent/path.yaml")
        assert isinstance(layout, CardLayout)
        assert layout.version == 1

    def test_banlist_loaded(self) -> None:
        layout = load_layout()
        assert len(layout.banlist) > 0
        assert "Войти и откликнуться" in layout.banlist

    def test_profiles_loaded(self) -> None:
        layout = load_layout()
        assert "channel" in layout.profiles
        assert "control_bot" in layout.profiles
        assert layout.profiles["channel"].feedback is False
        assert layout.profiles["control_bot"].feedback is True

    def test_footer_link_labels(self) -> None:
        layout = load_layout()
        assert "career_site" in layout.footer.link_labels
        assert "default" in layout.footer.link_labels

    def test_blocks_have_expected_fields(self) -> None:
        layout = load_layout()
        field_names = [b.field for b in layout.blocks if b.field]
        assert "role" in field_names
        assert "conditions" in field_names

    def test_conditions_block_has_order(self) -> None:
        layout = load_layout()
        cond = next(b for b in layout.blocks if b.field == "conditions")
        assert "location" in cond.order
        assert "salary" in cond.order

    def test_block_spec_defaults(self) -> None:
        block = BlockSpec()
        assert block.field is None
        assert block.spacer is False
        assert block.omit_if_empty is False
