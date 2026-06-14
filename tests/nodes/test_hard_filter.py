import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import LanguageCode, PostType, TriageRejectionReason
from job_ftch.domain.profile import ProfileCatalog, SearchProfile
from job_ftch.nodes.hard_filter import HardFilterNode


@pytest.fixture
def empty_catalog():
    return ProfileCatalog(profiles=[SearchProfile(profile_id="p1")])


@pytest.mark.anyio
async def test_hard_filter_passes_job_posting(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.JOB_POSTING.value})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_drops_candidate_seeking(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.CANDIDATE_SEEKING.value})
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == TriageRejectionReason.IRRELEVANT_CONTENT


@pytest.mark.anyio
async def test_hard_filter_drops_announcement(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.ANNOUNCEMENT.value})
    with pytest.raises(RawItemDropped):
        await node.process(item)


@pytest.mark.anyio
async def test_hard_filter_drops_spam(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.SPAM.value})
    with pytest.raises(RawItemDropped):
        await node.process(item)


@pytest.mark.anyio
async def test_hard_filter_passes_unknown_post_type(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.UNKNOWN.value})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_language_block_drops_item(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "en"})
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert "Language 'en' is not allowed" in exc.value.details


@pytest.mark.anyio
async def test_hard_filter_language_allows_unknown(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "unknown"})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_blocked_company_in_text_drops_item(make_raw_item):
    profile = SearchProfile(profile_id="p1", blocked_companies=("EvilCorp",))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(text="Join EvilCorp today!")
    with pytest.raises(RawItemDropped):
        await node.process(item)


@pytest.mark.anyio
async def test_hard_filter_no_profiles_allows_all_languages(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=())
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "en"})
    assert await node.process(item) is item
