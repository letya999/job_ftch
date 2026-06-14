import pytest

from job_ftch.application.contracts import ClassificationResult
from job_ftch.domain import PostType
from job_ftch.nodes.post_type import PostTypeClassificationNode


class MockClassifier:
    def __init__(self, label: str, confidence: float):
        self._label = label
        self._confidence = confidence

    async def classify(self, text: str) -> ClassificationResult:
        return ClassificationResult(self._label, self._confidence, "mock_v1")


@pytest.mark.anyio
async def test_post_type_heuristic_detects_candidate_seeking(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Something #резюме something")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.CANDIDATE_SEEKING.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_announcement(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Join our meetup next week")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.ANNOUNCEMENT.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_spam(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Get rich in our casino")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.SPAM.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_job_posting(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Hiring senior ML engineer")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value


@pytest.mark.anyio
async def test_post_type_heuristic_returns_unknown_for_neutral_text(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Just some random words")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.UNKNOWN.value


@pytest.mark.anyio
async def test_post_type_classifier_used_when_confident(make_raw_item):
    classifier = MockClassifier(PostType.SPAM.value, 0.9)
    node = PostTypeClassificationNode(classifier=classifier, confidence_threshold=0.8)
    item = make_raw_item(text="Hiring senior ML engineer")  # normally JOB_POSTING via rules
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.SPAM.value
    assert processed.metadata["preclassified_model"] == "mock_v1"


@pytest.mark.anyio
async def test_post_type_classifier_fallback_when_low_confidence(make_raw_item):
    classifier = MockClassifier(PostType.SPAM.value, 0.5)
    node = PostTypeClassificationNode(classifier=classifier, confidence_threshold=0.8)
    item = make_raw_item(text="Hiring senior ML engineer")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert processed.metadata["preclassified_model"] == "rules_v2"


@pytest.mark.anyio
async def test_post_type_sets_metadata_fields(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Hiring")
    processed = await node.process(item)
    assert "preclassified_post_type" in processed.metadata
    assert "preclassified_confidence" in processed.metadata
    assert "preclassified_model" in processed.metadata
