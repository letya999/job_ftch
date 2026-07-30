import contextlib
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from job_ftch.domain import (
    MatchDecision,
    PostType,
    ProfileCatalog,
    SearchProfile,
    Seniority,
    SkillTag,
)
from job_ftch.nodes.match_scoring import (
    MultiProfileMatchNode,
    _cosine_sim,
    _skill_overlap,
    _string_overlap_score,
)


@contextlib.contextmanager
def _tracing_context() -> Iterator[InMemorySpanExporter]:
    """Patch trace.get_tracer to use an in-memory exporter for span capture.

    OTel SDK forbids replacing the global TracerProvider after first set, so we
    patch get_tracer directly instead of touching the global provider.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def _get_tracer(name: str, **kwargs: object) -> object:
        return provider.get_tracer(name)

    with patch("opentelemetry.trace.get_tracer", side_effect=_get_tracer):
        yield exporter
    provider.shutdown()


@pytest.mark.anyio
async def test_match_scoring_empty_catalog_returns_none_scores(make_job_record):
    # Empty catalog is now allowed; MultiProfileMatchNode should handle gracefully
    catalog = ProfileCatalog(profiles=())
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.profile_scores == ()


@pytest.mark.anyio
async def test_match_scoring_title_match_scores_high(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(title="Senior ML Engineer", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    score = processed.profile_scores[0]
    assert score.title_score == 1.0
    assert score.final_score >= 0.35


@pytest.mark.anyio
async def test_match_scoring_required_skills_full_overlap(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(
        skills_explicit=(SkillTag(canonical_name="python"),), post_type=PostType.JOB_POSTING
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].skills_score >= 0.65


@pytest.mark.anyio
async def test_match_scoring_required_skills_zero_overlap(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(
        skills_explicit=(SkillTag(canonical_name="java"),), post_type=PostType.JOB_POSTING
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].skills_score == 0.0
    assert processed.metadata["hard_constraint_states"]["test_ml"]["required_skills"] == "unknown"


@pytest.mark.anyio
async def test_match_scoring_marks_explicit_language_conflict(make_job_record):
    from job_ftch.domain import LanguageCode

    profile = SearchProfile(profile_id="en", allowed_languages=(LanguageCode.EN,))
    processed = await MultiProfileMatchNode(ProfileCatalog(profiles=[profile])).process(
        make_job_record(language=LanguageCode.RU)
    )

    assert processed.metadata["hard_constraint_states"]["en"]["language"] == "contradicted"


@pytest.mark.anyio
async def test_match_scoring_seniority_exact_match(make_job_record):
    profile = SearchProfile(
        profile_id="p", seniority_min=Seniority.SENIOR, seniority_max=Seniority.SENIOR
    )
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(seniority=Seniority.SENIOR, post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.profile_scores[0].seniority_score == 1.0


@pytest.mark.anyio
async def test_match_scoring_seniority_out_of_range_penalizes(make_job_record):
    profile = SearchProfile(profile_id="p", seniority_min=Seniority.SENIOR)
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(seniority=Seniority.INTERN, post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.profile_scores[0].hard_pass is False
    assert processed.profile_scores[0].decision == MatchDecision.REJECT


@pytest.mark.anyio
async def test_match_scoring_hard_reject_propagates(make_job_record):
    profile = SearchProfile(profile_id="p", blocked_companies=("EvilCorp",))
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(company="EvilCorp", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.profile_scores[0].hard_pass is False
    assert processed.profile_scores[0].decision == MatchDecision.REJECT


@pytest.mark.anyio
async def test_match_scoring_above_threshold_accepts(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(
        title="ML Engineer",
        skills_explicit=(SkillTag(canonical_name="python"),),
        post_type=PostType.JOB_POSTING,
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].decision == MatchDecision.ACCEPT


@pytest.mark.anyio
async def test_match_scoring_vector_cosine_used_when_available(make_job_record):
    profile = SearchProfile(
        profile_id="p", embedding_vector=(1.0, 0.0, 0.0), relevance_threshold=0.1
    )
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(
        metadata={"embedding_vector": (1.0, 0.0, 0.0)}, post_type=PostType.JOB_POSTING
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].vector_score == 1.0


@pytest.mark.anyio
async def test_match_scoring_role_anchor_fallback(make_job_record):
    # If no profile centroid, use metadata embedding_role_match
    profile = SearchProfile(profile_id="p", embedding_vector=None, relevance_threshold=0.1)
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(metadata={"embedding_role_match": 0.85}, post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.profile_scores[0].vector_score == 0.85


@pytest.mark.anyio
async def test_match_scoring_negative_vector_penalizes(make_job_record):
    profile = SearchProfile(
        profile_id="p", negative_embedding_vectors=((1.0, 0.0, 0.0),), relevance_threshold=0.1
    )
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(
        metadata={"embedding_vector": (1.0, 0.0, 0.0)}, post_type=PostType.JOB_POSTING
    )
    processed = await node.process(job)
    assert processed.profile_scores[0].neg_vector_penalty == 0.6


@pytest.mark.anyio
async def test_match_scoring_risk_signals_reduce_final_score(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job_clean = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
    job_risky = make_job_record(
        title="ML Engineer", post_type=PostType.JOB_POSTING, risk_signals=("signal1", "signal2")
    )

    res_clean = await node.process(job_clean)
    res_risky = await node.process(job_risky)

    assert res_risky.profile_scores[0].final_score < res_clean.profile_scores[0].final_score


@pytest.mark.anyio
async def test_match_scoring_best_profile_selected(make_job_record):
    p1 = SearchProfile(profile_id="low", target_roles=("Designer",), relevance_threshold=0.1)
    p2 = SearchProfile(profile_id="high", target_roles=("Software",), relevance_threshold=0.1)
    catalog = ProfileCatalog(profiles=[p1, p2])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(title="Software Engineer", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    assert processed.best_profile_id == "high"


@pytest.mark.anyio
async def test_multi_profile_accept_beats_higher_score_review(make_job_record):
    # `strict` has the higher score but its own threshold keeps it REVIEW;
    # `permissive` accepts at a lower calibrated threshold.
    strict = SearchProfile(profile_id="strict", target_roles=("Software",), relevance_threshold=0.9)
    permissive = SearchProfile(
        profile_id="permissive", target_roles=("Python",), relevance_threshold=0.1
    )
    job = make_job_record(title="Python Software Engineer", post_type=PostType.JOB_POSTING)
    processed = await MultiProfileMatchNode(ProfileCatalog(profiles=[strict, permissive])).process(
        job
    )

    assert processed.routing_decision is None
    assert any(score.decision is MatchDecision.ACCEPT for score in processed.profile_scores)
    assert processed.best_profile_id == "permissive"


@pytest.mark.anyio
async def test_match_scoring_engineer_stopword_no_cross_match(make_job_record):
    # DevOps Engineer should NOT match AI Engineer just because of "engineer"
    p = SearchProfile(profile_id="p", target_roles=("AI Engineer",), relevance_threshold=0.1)
    catalog = ProfileCatalog(profiles=[p])
    node = MultiProfileMatchNode(catalog)

    # "Engineer" is a stop-word, so only "DevOps" vs "AI" matters.
    job = make_job_record(title="DevOps Engineer", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    # Title score should be 0 because discriminating token "DevOps" not in "AI Engineer"
    assert processed.profile_scores[0].title_score == 0.0


@pytest.mark.anyio
async def test_match_scoring_vacancy_bonus(make_job_record):
    p = SearchProfile(profile_id="p", target_roles=("ML",), relevance_threshold=0.1)
    catalog = ProfileCatalog(profiles=[p])
    node = MultiProfileMatchNode(catalog)

    # Job posting should have a slight bonus (+0.05) over other types
    job_posting = make_job_record(title="ML", post_type=PostType.JOB_POSTING)
    job_other = make_job_record(title="ML", post_type=PostType.UNKNOWN)

    res_posting = await node.process(job_posting)
    res_other = await node.process(job_other)

    assert res_posting.profile_scores[0].vacancy_type_score == 1.0
    assert res_other.profile_scores[0].vacancy_type_score == 0.0
    # Final score for job_posting should be 0.05 higher
    assert (
        round(
            res_posting.profile_scores[0].final_score - res_other.profile_scores[0].final_score, 2
        )
        == 0.05
    )


def test_string_overlap_exact_substring():
    assert _string_overlap_score("Python Developer", ("Python",)) == 1.0
    assert _string_overlap_score("Python Developer", ("Java",)) == 0.0
    assert _string_overlap_score("Software Engineer", ("Software Engineer",)) == 1.0  # exact match
    assert (
        _string_overlap_score("DevOps Engineer", ("AI Engineer",)) == 0.0
    )  # "AI" (discriminating) not in "DevOps Engineer"
    assert (
        _string_overlap_score("Python Developer", ("Python specialist",)) > 0.0
    )  # partial token overlap


def test_skill_overlap_partial():
    job_skills = (SkillTag(canonical_name="python"), SkillTag(canonical_name="fastapi"))
    required = (SkillTag(canonical_name="python"), SkillTag(canonical_name="go"))
    assert _skill_overlap(job_skills, required) == 0.5


def test_cosine_sim_parallel_and_orthogonal():
    # Test orthogonal vectors (cosine = 0.0)
    assert _cosine_sim((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    # Test parallel vectors (cosine = 1.0)
    assert _cosine_sim((1.0, 1.0), (1.0, 1.0)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# OTel tracing tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tracing_span_name_and_node_attributes(minimal_catalog, make_job_record):
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(minimal_catalog)
        job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
        await node.process(job)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "match_scoring.score"
    assert span.attributes["job_ftch.node"] == "MultiProfileMatchNode"
    assert span.attributes["job_ftch.node.type"] == "scoring"


@pytest.mark.anyio
async def test_tracing_profile_count_attribute(make_job_record):
    profiles = [
        SearchProfile(profile_id="p1", target_roles=("ML",)),
        SearchProfile(profile_id="p2", target_roles=("DevOps",)),
    ]
    catalog = ProfileCatalog(profiles=profiles)
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(catalog)
        job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
        await node.process(job)

    span = exporter.get_finished_spans()[0]
    assert span.attributes["job_ftch.profile_count"] == 2


@pytest.mark.anyio
async def test_tracing_empty_catalog_does_not_set_routing_decision(make_job_record):
    catalog = ProfileCatalog(profiles=())
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(catalog)
        job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
        result = await node.process(job)

    span = exporter.get_finished_spans()[0]
    assert span.attributes["job_ftch.profile_count"] == 0
    assert "job_ftch.routing_decision" not in span.attributes
    # Empty catalog: no best_score or best_profile_id attributes
    assert "job_ftch.best_score" not in span.attributes
    assert "job_ftch.best_profile_id" not in span.attributes
    # Result still correct
    assert result.routing_decision is None


@pytest.mark.anyio
async def test_tracing_best_score_and_profile_id_attributes(make_job_record):
    p1 = SearchProfile(profile_id="low", target_roles=("Designer",), relevance_threshold=0.1)
    p2 = SearchProfile(profile_id="high", target_roles=("Software",), relevance_threshold=0.1)
    catalog = ProfileCatalog(profiles=[p1, p2])
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(catalog)
        job = make_job_record(title="Software Engineer", post_type=PostType.JOB_POSTING)
        result = await node.process(job)

    span = exporter.get_finished_spans()[0]
    assert span.attributes["job_ftch.best_profile_id"] == "high"
    assert span.attributes["job_ftch.best_score"] == pytest.approx(result.best_score, abs=1e-6)
    assert "job_ftch.routing_decision" not in span.attributes
    assert result.routing_decision is None


@pytest.mark.anyio
async def test_tracing_scoring_does_not_make_hard_decision(make_job_record):
    # Hard constraint evidence is consumed later by DecisionNode.
    profile = SearchProfile(profile_id="p", blocked_companies=("EvilCorp",))
    catalog = ProfileCatalog(profiles=[profile])
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(catalog)
        job = make_job_record(company="EvilCorp", post_type=PostType.JOB_POSTING)
        result = await node.process(job)

    span = exporter.get_finished_spans()[0]
    assert "job_ftch.routing_decision" not in span.attributes
    assert result.routing_decision is None


@pytest.mark.anyio
async def test_tracing_score_does_not_make_review_decision(make_job_record):
    # The score is evidence; the DecisionNode chooses REVIEW later.
    profile = SearchProfile(
        profile_id="p",
        target_roles=("ML",),
        relevance_threshold=0.8,  # high threshold → likely REVIEW for partial match
    )
    catalog = ProfileCatalog(profiles=[profile])
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(catalog)
        job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
        result = await node.process(job)

    span = exporter.get_finished_spans()[0]
    assert "job_ftch.routing_decision" not in span.attributes
    assert result.routing_decision is None
    assert span.attributes["job_ftch.best_profile_id"] == "p"


@pytest.mark.anyio
async def test_tracing_noop_without_provider(minimal_catalog, make_job_record):
    # Without any custom provider the node must not raise and result must be correct.
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
    result = await node.process(job)
    assert result is not None
    assert result.profile_scores


@pytest.mark.anyio
async def test_tracing_single_span_per_process_call(minimal_catalog, make_job_record):
    """process() must produce exactly one span regardless of catalog size."""
    with _tracing_context() as exporter:
        node = MultiProfileMatchNode(minimal_catalog)
        job = make_job_record(title="ML Engineer", post_type=PostType.JOB_POSTING)
        await node.process(job)
        await node.process(job)

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert all(s.name == "match_scoring.score" for s in spans)
