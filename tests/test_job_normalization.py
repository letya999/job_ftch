import pytest
from job_ftch.nodes.job_normalization import _clean_title, _clean_company, _strip_html, TitleCompanyNormalizationNode
from job_ftch.domain import JobDraft, WorkMode, Seniority

def test_strip_html():
    assert _strip_html("Hello <b>World</b>") == "Hello World"
    assert _strip_html("Andersen </strong>is hiring an<strong>") == "Andersen is hiring an"
    assert _strip_html("Line 1&nbsp;Line 2") == "Line 1 Line 2"
    assert _strip_html("A &lt; B &amp; C &gt; D") == "A < B & C > D"

def test_clean_title():
    assert _clean_title("Hiring: Python Developer") == "Python Developer"
    assert _clean_title("Vacancy - Java <b>Engineer</b>") == "Java Engineer"
    assert _clean_title("Ищем: Data Scientist") == "Data Scientist"

def test_clean_company():
    assert _clean_company("Google") == "Google"
    assert _clean_company(" Andersen </strong>is hiring an<strong>") == "Andersen is hiring an"
    assert _clean_company("This is a very long company name that is actually a sentence that should definitely be rejected") is None
    assert _clean_company("Short Name") == "Short Name"
    assert _clean_company("A company with many spaces in it because it is prose and long") is None

@pytest.mark.asyncio
async def test_normalization_node_strips_html_from_description():
    class MockNormalizer:
        def infer_role_family(self, title, language="unknown"): return None
        def infer_seniority(self, title): return None
        def normalize_skills(self, skills): return skills

    node = TitleCompanyNormalizationNode(MockNormalizer())
    draft = JobDraft(
        raw_item_id="raw1",
        description_raw="<p>We are <strong>hiring</strong>!</p>",
        title_raw="<b>Developer</b>",
        company_name_raw="Google",
        source_kind="career_site",
        source_name="test",
        canonical_url="http://example.com"
    )
    
    record = await node.process(draft)
    assert record.title == "Developer"
    assert record.description == "We are hiring !"
    assert "description:html_stripped" in record.provenance.normalization
