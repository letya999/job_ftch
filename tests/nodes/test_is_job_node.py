import pytest

from job_ftch.domain import JobRecord, PostType, SourceKind


def make_record(title="", description=""):
    return JobRecord.model_construct(
        title=title,
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        raw_item_id="1",
        post_type=PostType.JOB_POSTING,
        description=description,
        metadata={},
    )


@pytest.mark.anyio
async def test_is_job_node_matches_russian_keyword():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    result = await node.process(make_record(title="Вакансия Python Developer"))
    assert result.metadata["is_job_prototype"] is True


@pytest.mark.anyio
async def test_is_job_node_matches_english_keyword():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    result = await node.process(make_record(title="We are hiring a Senior Engineer"))
    assert result.metadata["is_job_prototype"] is True


@pytest.mark.anyio
async def test_is_job_node_no_match_returns_false():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    result = await node.process(
        make_record(title="Курс по Python", description="Обучение программированию")
    )
    assert result.metadata["is_job_prototype"] is False


@pytest.mark.anyio
async def test_is_job_node_empty_text_returns_false():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    result = await node.process(make_record())
    assert result.metadata["is_job_prototype"] is False


@pytest.mark.anyio
async def test_is_job_node_keyword_in_description_matches():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    result = await node.process(make_record(title="Объявление", description="Ищем разработчика"))
    assert result.metadata["is_job_prototype"] is True


@pytest.mark.anyio
async def test_is_job_node_preserves_existing_metadata():
    from job_ftch.nodes.is_job_classifier import IsJobNode

    node = IsJobNode()
    item = make_record(title="hiring")
    item = item.model_copy(update={"metadata": {"existing_key": 42}})
    result = await node.process(item)
    assert result.metadata["existing_key"] == 42
    assert "is_job_prototype" in result.metadata
