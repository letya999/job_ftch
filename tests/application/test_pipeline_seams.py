import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.pipeline import Pipeline
from job_ftch.application.tenant_runner import TenantStore
from job_ftch.domain import (
    JobDraft,
    JobRecord,
    JobReviewReason,
    LanguageCode,
    MatchDecision,
    PostType,
    ProfileMatchScore,
    RawItem,
    RejectedOutcome,
    SkillTag,
    SourceKind,
    TriageRejectionReason,
)
from job_ftch.domain.profile import ProfileCatalog, SearchProfile
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.infrastructure.stores.sqlite import SQLiteStore
from job_ftch.nodes.dedup import DedupNode
from job_ftch.nodes.extraction_validation import ExtractionValidationNode
from job_ftch.nodes.hard_filter import HardFilterNode
from job_ftch.nodes.job_normalization import SkillNormalizationNode, TitleCompanyNormalizationNode
from job_ftch.nodes.match_scoring import MultiProfileMatchNode
from job_ftch.nodes.post_type import PostTypeClassificationNode
from job_ftch.nodes.quality import JobValidationNode, QualityScoringNode
from job_ftch.nodes.routing import RoutingNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.nodes.semantic_prefilter import SemanticPrefilterNode


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def source_factory(stub_source_factory):
    return stub_source_factory


@pytest.fixture
def sink():
    from tests.conftest import CollectSink

    return CollectSink()


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_01_source_sanitize_sink_emits_valid_item(
    source_factory, sink, store, make_raw_item
):
    item = make_raw_item(text="Valid job description")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 1
    assert sink.items[0].text == "Valid job description"


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_02_dedup_drops_seen_item(source_factory, sink, store, make_raw_item):
    from tests.conftest import CollectSink

    item = make_raw_item(
        external_id="dup1",
        url="https://careers.example.com/job/1",
        source_kind=SourceKind.CAREER_SITE,
    )

    first_sink = CollectSink()
    p1 = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store)],
        sink=first_sink,
        store=store,
    )
    await p1.run()
    assert len(first_sink.items) == 1  # first run: emitted

    second_sink = CollectSink()
    p2 = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store)],
        sink=second_sink,
        store=store,
    )
    await p2.run()
    assert len(second_sink.items) == 0  # second run: deduped


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_03_dedup_passes_new_item_and_marks_store(
    source_factory, sink, store, make_raw_item
):
    item = make_raw_item(
        external_id="new1",
        url="https://careers.example.com/job/2",
        source_kind=SourceKind.CAREER_SITE,
    )
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 1
    keys = await store.list_dedup_keys()
    assert len(keys) > 0  # something was remembered


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_04_sanitize_quarantines_invalid_item(
    source_factory, sink, store, make_raw_item
):
    # RawItem constructor rejects blank text, so use model_construct to bypass validation
    # so SanitizeNode can catch it.
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        external_id="bad1",
        url=None,
        text="   ",  # blank — passes construction, caught by SanitizeNode
        metadata={},
    )
    from tests.conftest import CollectSink

    quarantine = CollectSink()
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=store,
        quarantine_sink=quarantine,
    )
    await pipeline.run()
    assert len(sink.items) == 0
    assert len(quarantine.items) == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_05_post_type_to_hard_filter_candidate_seeking_dropped(
    source_factory, sink, store, make_raw_item, minimal_catalog
):
    item = make_raw_item(text="#резюме")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[PostTypeClassificationNode(), HardFilterNode(minimal_catalog)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_06_only_job_posting_reaches_extraction(
    source_factory, sink, store, make_raw_item, minimal_catalog
):
    item1 = make_raw_item(text="Hiring ML engineer")
    item2 = make_raw_item(text="#резюме")

    class MockExtractionNode:
        def __init__(self):
            self.called_with = []

        async def process(self, item):
            self.called_with.append(item)
            return item

    extractor = MockExtractionNode()
    pipeline = Pipeline(
        source=source_factory([item1, item2]),
        sanitize_node=SanitizeNode(),
        nodes=[PostTypeClassificationNode(), HardFilterNode(minimal_catalog), extractor],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(extractor.called_with) == 1
    assert "Hiring" in extractor.called_with[0].text


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_07_language_detection_to_hard_filter_drops_disallowed(
    source_factory, sink, store, make_raw_item
):
    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])

    class MockLangNode:
        async def process(self, item):
            metadata = {**item.metadata, "detected_language": "en"}
            return item.model_copy(update={"metadata": metadata})

    item = make_raw_item(text="English job")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[MockLangNode(), HardFilterNode(catalog)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_08_translation_then_extraction(source_factory, sink, store, make_raw_item):
    class MockTranslationNode:
        async def process(self, item):
            return item.model_copy(update={"text": "Translated Text"})

    class MockExtractionNode:
        async def process(self, item):
            return JobDraft(
                title_raw=item.text,
                company_name_raw="Mock",
                description_raw=item.text or "placeholder",
                source_kind=item.source_kind,
                source_name=item.source_name,
                raw_item_id=item.stable_id or "r1",
            )

    item = make_raw_item(text="Original Text")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[MockTranslationNode(), MockExtractionNode()],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert sink.items[0].title_raw == "Translated Text"


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_09_extraction_validation_drops_no_title_draft(
    source_factory, sink, store, make_raw_item
):
    draft = JobDraft(
        title_raw=None,
        company_name_raw=None,
        description_raw="valid description length but core fields missing" * 2,
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
        canonical_url=None,
    )

    class FakeExtractor:
        async def process(self, item):
            return draft

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[FakeExtractor(), ExtractionValidationNode()],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_10_normalization_chain_feeds_matching(
    source_factory, sink, store, make_raw_item, minimal_catalog
):
    draft = JobDraft(
        title_raw="ML Eng",
        company_name_raw="OpenAI",
        description_raw="This is a sufficiently long description for validation node." * 3,
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        raw_item_id="r1",
    )

    class FakeExtractor:
        async def process(self, item):
            return draft

    class MockNormalizer:
        def infer_role_family(self, title, language="unknown"):
            return None

        def infer_seniority(self, title):
            return None

        def normalize_skills(self, skills):
            return skills

    normalizer = MockNormalizer()

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[
            FakeExtractor(),
            TitleCompanyNormalizationNode(normalizer),
            SkillNormalizationNode(normalizer),
            MultiProfileMatchNode(minimal_catalog),
        ],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 1
    assert isinstance(sink.items[0], JobRecord)
    assert sink.items[0].profile_scores is not None


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_11_match_scoring_to_routing_accept(
    source_factory, sink, store, make_raw_item, make_job_record
):
    score = ProfileMatchScore(
        profile_id="test_ml", profile_name="ML", final_score=0.9, decision=MatchDecision.ACCEPT
    )

    class FakeMatcherRecord:
        async def process(self, item):
            return make_job_record(profile_scores=(score,), quality_score=0.9)

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[FakeMatcherRecord(), RoutingNode(accept_threshold=0.8)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert sink.items[0].routing_decision == MatchDecision.ACCEPT


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_12_match_scoring_to_routing_review(
    source_factory, sink, store, make_raw_item, make_job_record
):
    score = ProfileMatchScore(
        profile_id="p1", profile_name="P1", final_score=0.6, decision=MatchDecision.REVIEW
    )

    class FakeMatcher:
        async def process(self, item):
            return make_job_record(profile_scores=(score,))

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[FakeMatcher(), RoutingNode(accept_threshold=0.8, review_threshold=0.5)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert sink.items[0].routing_decision == MatchDecision.REVIEW


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_13_risk_signals_demote_routing_to_review(
    source_factory, sink, store, make_raw_item, make_job_record
):
    score = ProfileMatchScore(
        profile_id="p1", profile_name="P1", final_score=0.9, decision=MatchDecision.ACCEPT
    )

    class FakeMatcher:
        async def process(self, item):
            return make_job_record(profile_scores=(score,), quality_score=0.4)  # low quality

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[FakeMatcher(), RoutingNode(quality_override_threshold=0.6)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert sink.items[0].routing_decision == MatchDecision.REVIEW
    assert JobReviewReason.LOW_QUALITY_SCORE.value in sink.items[0].review_reasons


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_14_low_quality_job_dropped_at_validation(
    source_factory, sink, store, make_raw_item, make_job_record
):
    class FakeScorer:
        async def process(self, item):
            return make_job_record(quality_score=0.1, title="Too short")

    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[FakeScorer(), JobValidationNode(min_quality_score=0.25)],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert len(sink.items) == 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_15_fanout_sink_delivers_to_all_sinks(source_factory, store, make_raw_item):
    from tests.conftest import CollectSink

    s1 = CollectSink()
    s2 = CollectSink()
    pipeline = Pipeline(
        source=source_factory([make_raw_item()]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=[s1, s2],
        store=store,
    )
    await pipeline.run()
    assert len(s1.items) == 1
    assert len(s2.items) == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_16_rejected_item_goes_to_rejected_sink(
    source_factory, sink, store, make_raw_item, minimal_catalog
):
    item = make_raw_item(metadata={"preclassified_post_type": PostType.SPAM.value})
    from tests.conftest import CollectSink

    rejected_sink = CollectSink()
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[HardFilterNode(minimal_catalog)],
        sink=sink,
        store=store,
        rejected_sink=rejected_sink,
    )
    await pipeline.run()
    assert len(sink.items) == 0
    assert len(rejected_sink.items) == 1


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_17_node_exception_quarantines_not_crashes_pipeline(
    source_factory, sink, store, make_raw_item
):
    from tests.conftest import CollectSink

    rejected_sink = CollectSink()
    item1 = make_raw_item(external_id="1")
    item2 = make_raw_item(external_id="2")

    class SelectiveCrashingNode:
        async def process(self, item):
            if item.external_id == "1":
                raise RuntimeError("Boom")
            return item

    pipeline = Pipeline(
        source=source_factory([item1, item2]),
        sanitize_node=SanitizeNode(),
        nodes=[SelectiveCrashingNode()],
        sink=sink,
        store=store,
        rejected_sink=rejected_sink,
    )
    await pipeline.run()
    assert len(sink.items) == 1
    assert len(rejected_sink.items) == 1
    assert rejected_sink.items[0].outcome == RejectedOutcome.FAILED


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_18_store_protocol_equivalence_in_memory(store):
    await store.mark_processed("k1")
    assert await store.has_processed("k1")
    assert not await store.has_processed("k2")


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_18_store_protocol_equivalence_sqlite(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path)
    await store.mark_processed("k1")
    assert await store.has_processed("k1")
    assert not await store.has_processed("k2")


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_19_tenant_store_isolation_dedup_keys(store):
    t1 = TenantStore("t1", store)
    t2 = TenantStore("t2", store)

    await t1.mark_processed("key")
    assert await t1.has_processed("key")
    assert not await t2.has_processed("key")


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_20_tenant_store_cursor_namespaced(store):
    t1 = TenantStore("t1", store)
    t2 = TenantStore("t2", store)

    await t1.set_run_state("cursor", "val1")
    assert await t1.get_run_state("cursor") == "val1"
    assert await t2.get_run_state("cursor") is None


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_21_pipeline_saves_cursor_to_store(source_factory, sink, store, make_raw_item):
    item = make_raw_item(external_id="123")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    stored = await store.get_run_state("pipeline.last_processed_key")
    assert stored is not None
    assert f"raw:{item.stable_id}" == stored


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_22_local_fixture_source_pipeline_emits_all_items(sink, store, repo_root):
    from job_ftch.infrastructure.sources.local_fixture import LocalFixtureSource

    fixture_path = repo_root / "fixtures/debug/raw_items.json"
    if not fixture_path.exists():
        pytest.skip("Fixture not found")

    source = LocalFixtureSource(fixture_path)
    pipeline = Pipeline(
        source=source, sanitize_node=SanitizeNode(), nodes=[], sink=sink, store=store
    )
    summary = await pipeline.run()
    assert summary.fetched > 0
    assert len(sink.items) == summary.fetched


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_23_compensation_parsed_improves_quality_score(make_job_record):
    from job_ftch.domain import CompensationRange

    node = QualityScoringNode()
    job_no_comp = make_job_record(compensation=None)
    job_with_comp = make_job_record(compensation=CompensationRange(min_amount=1000, currency="USD"))

    res_no = await node.process(job_no_comp)
    res_with = await node.process(job_with_comp)
    assert res_with.quality_score > res_no.quality_score


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_24_normalized_skills_affect_profile_match(make_job_record, minimal_profile):
    catalog = ProfileCatalog(profiles=[minimal_profile])
    node = MultiProfileMatchNode(catalog)

    job = make_job_record(
        skills_explicit=(SkillTag(canonical_name="python"),), post_type=PostType.JOB_POSTING
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].skills_score > 0


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_25_catalog_from_yaml_drives_match(tmp_path, make_job_record):
    from job_ftch.application.filter_profile_loader import load_profile_catalog

    yaml_content = """
catalog_name: yaml_cat
profiles:
  - profile_id: yaml_p
    name: YAML Profile
    target_roles: ["Specialist"]
    relevance_threshold: 0.1
"""
    path = tmp_path / "cat.yaml"
    path.write_text(yaml_content)

    catalog = load_profile_catalog(path)
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(title="Specialist", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.best_profile_id == "yaml_p"


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_26_filter_profile_language_blocks_item(make_raw_item):
    profile = SearchProfile(profile_id="p", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)

    item = make_raw_item(metadata={"detected_language": "en"})
    with pytest.raises(RawItemDropped):
        await node.process(item)


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_27_source_classifier_labels_propagate_to_output(
    source_factory, sink, store, make_raw_item
):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Hiring ML Engineer")
    pipeline = Pipeline(
        source=source_factory([item]),
        sanitize_node=SanitizeNode(),
        nodes=[node],
        sink=sink,
        store=store,
    )
    await pipeline.run()
    assert sink.items[0].metadata["preclassified_post_type"] == PostType.JOB_POSTING.value


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_28_aggregation_merges_multi_source_summary(
    source_factory, sink, store, make_raw_item
):
    item1 = make_raw_item(source_name="src1", external_id="1")
    item2 = make_raw_item(source_name="src2", external_id="2")

    pipeline = Pipeline(
        source=source_factory([item1, item2]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=sink,
        store=store,
    )
    summary = await pipeline.run()
    assert summary.fetched == 2
    # The summary by_source_id keys are kind:name
    assert "debug:src1" in summary.by_source_id
    assert "debug:src2" in summary.by_source_id


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_29_pipeline_max_items_stops_early(source_factory, sink, store, make_raw_item):
    items = [make_raw_item(external_id=str(i)) for i in range(10)]
    pipeline = Pipeline(
        source=source_factory(items), sanitize_node=SanitizeNode(), nodes=[], sink=sink, store=store
    )
    await pipeline.run(max_items=3)
    assert len(sink.items) == 3


@pytest.mark.anyio
@pytest.mark.integration
async def test_seam_30_semantic_prefilter_drops_low_signal_before_extraction(
    source_factory, sink, store, make_raw_item, minimal_catalog
):
    # Minimal profile wants ML Engineer
    node = SemanticPrefilterNode(minimal_catalog)
    item = make_raw_item(text="Something completely unrelated to ML")

    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == TriageRejectionReason.LOW_RELEVANCE_PREFILTER
