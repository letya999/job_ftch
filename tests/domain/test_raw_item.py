import pytest
from pydantic import ValidationError

from job_ftch.domain import RawItem, SourceKind


@pytest.mark.unit
def test_raw_item_stable_id_generated_on_construction():
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="123", text="test"
    )
    assert item.stable_id != ""
    assert len(item.stable_id) == 64


@pytest.mark.unit
def test_raw_item_rejects_empty_external_id():
    with pytest.raises((ValidationError, ValueError)):
        RawItem(source_kind=SourceKind.DEBUG, source_name="debug", external_id=" ", text="test")


@pytest.mark.unit
def test_raw_item_metadata_defaults_to_empty_dict():
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="123", text="test"
    )
    assert item.metadata == {}


@pytest.mark.unit
def test_raw_item_is_frozen():
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="123", text="test"
    )
    with pytest.raises(ValidationError):
        item.text = "new text"


@pytest.mark.unit
def test_raw_item_url_field_accepts_none():
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="123", text="test", url=None
    )
    assert item.url is None


@pytest.mark.unit
def test_raw_item_schema_version_is_1():
    item = RawItem(
        source_kind=SourceKind.DEBUG, source_name="debug", external_id="123", text="test"
    )
    assert item.schema_version == "1"
