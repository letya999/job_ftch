from pathlib import Path

import pytest
import yaml

from domain import Job, SourceKind
from domain.company import normalize_company_name
from nodes.company import CompanyCanonicalizer


@pytest.fixture
def alias_file(tmp_path):
    path = tmp_path / "aliases.yaml"
    data = {
        "Yandex": ["Яндекс", "ООО Яндекс", "Yandex N.V."],
        "Sberbank": ["Сбер", "ПАО Сбербанк", "Sber"],
    }
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


@pytest.fixture
def sample_job():
    return Job(
        raw_item_id="item_1",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test_site",
        title="ML Engineer",
        company="ООО Яндекс",
        description="ML Engineer role at Yandex.",
    )


def test_normalizes_legal_suffix_ooo():
    assert normalize_company_name("ООО Яндекс") == "яндекс"


def test_normalizes_legal_suffix_pao():
    assert normalize_company_name("ПАО Сбербанк") == "сбербанк"


@pytest.mark.asyncio
async def test_alias_exact_match(alias_file, sample_job):
    node = CompanyCanonicalizer(aliases_path=alias_file)
    result = await node.process(sample_job)
    assert result.company_canonical == "Yandex"


@pytest.mark.asyncio
async def test_alias_fuzzy_match(alias_file, sample_job):
    node = CompanyCanonicalizer(aliases_path=alias_file)
    job = sample_job.model_copy(update={"company": "Сбер"})
    result = await node.process(job)
    assert result.company_canonical == "Sberbank"


@pytest.mark.asyncio
async def test_no_alias_file_is_noop(sample_job):
    node = CompanyCanonicalizer(aliases_path=Path("non_existent.yaml"))
    result = await node.process(sample_job)
    assert result.company_canonical is None


@pytest.mark.asyncio
async def test_already_canonical_unchanged(alias_file, sample_job):
    job = sample_job.model_copy(update={"company_canonical": "Yandex"})
    node = CompanyCanonicalizer(aliases_path=alias_file)
    result = await node.process(job)
    assert result is job


def test_normalizer_domain_function_purity():
    raw = "  ООО  Тинькофф Банк  "
    assert normalize_company_name(raw) == "тинькофф банк"

    # English suffix
    assert normalize_company_name("Google Inc.") == "google"
    # Suffix with no dot
    assert normalize_company_name("Apple LLC") == "apple"
    # Case insensitive
    assert normalize_company_name("Yandex llc") == "yandex"
