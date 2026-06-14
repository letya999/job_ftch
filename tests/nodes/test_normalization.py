import pytest

from job_ftch.domain import JobDraft, JobRecord, Seniority, SkillTag, SourceKind
from job_ftch.infrastructure.ontology.normalizer import OntologyNormalizer
from job_ftch.nodes.job_normalization import SkillNormalizationNode, TitleCompanyNormalizationNode


@pytest.fixture
def normalizer():
    return OntologyNormalizer()


def test_role_family_inferred_from_english_title(normalizer):
    assert normalizer.infer_role_family("Senior Backend Engineer") == "engineering"
    assert normalizer.infer_role_family("Data Scientist") == "data"
    assert normalizer.infer_role_family("DevOps Engineer") == "devops"


def test_role_family_inferred_from_russian_title(normalizer):
    assert normalizer.infer_role_family("Старший разработчик Python") == "engineering"
    assert normalizer.infer_role_family("Аналитик данных") == "analytics"


def test_seniority_inferred_from_title(normalizer):
    assert normalizer.infer_seniority("Senior Product Manager") == "senior"
    assert normalizer.infer_seniority("Junior Developer") == "junior"
    assert normalizer.infer_seniority("Tech Lead") == "lead"


def test_seniority_inferred_ru(normalizer):
    assert normalizer.infer_seniority("Джуниор разработчик") == "junior"
    assert normalizer.infer_seniority("Стажер") == "intern"
    assert normalizer.infer_seniority("Тимлид") == "lead"


def test_skill_normalization_known_skill(normalizer):
    name, skill_id = normalizer.normalize_skill("python3")
    assert name == "Python"
    assert skill_id == "python"

    name, skill_id = normalizer.normalize_skill("JS")
    assert name == "JavaScript"
    assert skill_id == "javascript"


def test_skill_normalization_unknown_skill_preserved(normalizer):
    name, skill_id = normalizer.normalize_skill("obscure_framework")
    assert name == "obscure_framework"
    assert skill_id is None


@pytest.mark.anyio
async def test_skill_normalization_node_enriches_record(normalizer):
    node = SkillNormalizationNode(normalizer=normalizer)
    record = JobRecord(
        raw_item_id="raw1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="test desc",
        skills_explicit=(SkillTag(canonical_name="javascript"),),
    )
    processed = await node.process(record)
    assert processed.skills_explicit[0].skill_id == "javascript"
    assert processed.skills_explicit[0].canonical_name == "JavaScript"
    assert "skills:normalized" in processed.provenance.normalization


@pytest.mark.anyio
async def test_title_normalization_uses_ontology(normalizer):
    node = TitleCompanyNormalizationNode(normalizer=normalizer)
    draft = JobDraft(
        raw_item_id="raw1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title_raw="ML Engineer",
        description_raw="test desc",
    )
    record = await node.process(draft)
    assert record.role_family == "engineering"
    assert record.seniority == Seniority.UNKNOWN

    draft_sr = JobDraft(
        raw_item_id="raw1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title_raw="Senior Developer",
        description_raw="test desc",
    )
    record_sr = await node.process(draft_sr)
    assert record_sr.role_family == "engineering"
    assert record_sr.seniority == Seniority.SENIOR


def test_ontology_normalizer_no_match_returns_none(normalizer):
    assert normalizer.infer_role_family("Coordinator of Special Projects") is None
    assert normalizer.infer_seniority("Person") is None
