import pytest

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


@pytest.mark.anyio
async def test_match_scoring_empty_catalog_returns_none_scores(make_job_record):
    # ProfileCatalog model_validator prevents empty profiles tuple
    with pytest.raises(ValueError, match="ProfileCatalog must contain at least one profile"):
        ProfileCatalog(profiles=())


@pytest.mark.anyio
async def test_match_scoring_title_match_scores_high(minimal_catalog, make_job_record):
    node = MultiProfileMatchNode(minimal_catalog)
    job = make_job_record(title="Senior ML Engineer", post_type=PostType.JOB_POSTING)
    processed = await node.process(job)
    score = processed.profile_scores[0]
    assert score.title_score == 1.0
    assert score.final_score >= 0.5


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
    profile = SearchProfile(
        profile_id="p", embedding_vector=None, relevance_threshold=0.1
    )
    catalog = ProfileCatalog(profiles=[profile])
    node = MultiProfileMatchNode(catalog)
    job = make_job_record(
        metadata={"embedding_role_match": 0.85}, post_type=PostType.JOB_POSTING
    )
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
        round(res_posting.profile_scores[0].final_score - res_other.profile_scores[0].final_score, 2)
        == 0.05
    )


def test_string_overlap_exact_substring():
    assert _string_overlap_score("Python Developer", ("Python",)) == 1.0
    assert _string_overlap_score("Python Developer", ("Java",)) == 0.0
    assert _string_overlap_score("Software Engineer", ("Software Engineer",)) == 1.0  # exact match
    assert _string_overlap_score("DevOps Engineer", ("AI Engineer",)) == 0.0  # "AI" (discriminating) not in "DevOps Engineer"
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
