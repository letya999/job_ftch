from domain import RawItem
from domain.source_spec import LeverSourceSpec
from infrastructure.sources.api.lever import LeverAPISource


def test_lever_api_source_maps_fields():
    spec = LeverSourceSpec(company="acme", source_name="test_lever")
    source = LeverAPISource(spec, auth=None)  # type: ignore

    data = {
        "id": "123",
        "hostedUrl": "https://jobs.lever.co/acme/123",
        "text": "Software Engineer",
        "descriptionPlain": "We are looking for a dev.",
        "categories": {"team": "Engineering", "commitment": "Full-time"},
        "tags": ["python", "remote"],
    }

    item = source._map_to_raw_item(data)

    assert isinstance(item, RawItem)
    assert item.external_id == "123"
    assert str(item.url) == "https://jobs.lever.co/acme/123"
    assert "Software Engineer" in item.text
    assert "We are looking for a dev." in item.text
    assert item.metadata["team"] == "Engineering"
    assert item.metadata["commitment"] == "Full-time"
    assert "python" in item.metadata["tags"]
    assert item.source_name == "test_lever"


def test_lever_source_spec_roundtrip():
    data = {"type": "lever", "company": "acme"}
    spec = LeverSourceSpec(**data)
    assert spec.company == "acme"
    assert spec.type == "lever"


def test_lever_extract_items_handles_root_array():
    spec = LeverSourceSpec(company="acme")
    source = LeverAPISource(spec, auth=None)  # type: ignore

    response_data = [{"id": "1"}, {"id": "2"}]
    items = source._extract_items(response_data)
    assert len(items) == 2
    assert items[0]["id"] == "1"

    response_data_wrapped = {"data": [{"id": "3"}]}
    items_wrapped = source._extract_items(response_data_wrapped)
    assert len(items_wrapped) == 1
    assert items_wrapped[0]["id"] == "3"
