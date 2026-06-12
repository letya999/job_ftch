"""Tests for JobLifecycleNode - status detection in English and Russian."""
import pytest
from job_ftch.domain.models import JobRecord, JobStatus, SourceKind
from job_ftch.nodes.lifecycle import JobLifecycleNode


def make_record(**kwargs) -> JobRecord:
    """Minimal JobRecord fixture."""
    defaults = dict(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="TestSource",
        title="Python Developer",
        company="Acme",
        description="We are hiring a Python developer.",
    )
    defaults.update(kwargs)
    return JobRecord(**defaults)


# ---------------------------------------------------------------------------
# English closed markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("This position has been filled.", JobStatus.FILLED),
    ("vacancy closed", JobStatus.FILLED),
    ("Position closed. Thank you.", JobStatus.FILLED),
    ("hiring complete", JobStatus.FILLED),
    ("We are actively hiring Python engineers.", JobStatus.OPEN),
])
@pytest.mark.asyncio
async def test_english_status_markers(text, expected):
    node = JobLifecycleNode()
    record = make_record(description=text)
    result = await node.process(record)
    assert result.status == expected, f"text={text!r}: expected {expected}, got {result.status}"


# ---------------------------------------------------------------------------
# Russian closed markers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_status", [
    ("Роль закрыта. Спасибо за заявки.", JobStatus.FILLED),
    ("Вакансия закрыта.", JobStatus.FILLED),
    ("Позиция закрыта, поиск окончен.", JobStatus.FILLED),
    ("Набор закрыт.", JobStatus.FILLED),
    ("Ищем Python разработчика в нашу команду.", JobStatus.OPEN),
])
@pytest.mark.asyncio
async def test_russian_closed_markers(text, expected_status):
    """lifecycle.py defines RU markers - this tests them."""
    node = JobLifecycleNode()
    record = make_record(description=text)
    result = await node.process(record)
    assert result.status == expected_status, (
        f"RU text={text!r}: expected {expected_status}, got {result.status}"
    )


# ---------------------------------------------------------------------------
# Metadata / boolean flag paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metadata,expected_status", [
    ({"status": "closed"}, JobStatus.FILLED),
    ({"status": "filled"}, JobStatus.FILLED),
    ({"status": "expired"}, JobStatus.FILLED),
    ({"status": "archived"}, JobStatus.FILLED),
    ({"closed": True}, JobStatus.FILLED),
    ({"closed": False}, JobStatus.OPEN),
    ({"status": "active"}, JobStatus.OPEN),
    ({"status": "published"}, JobStatus.OPEN),
    ({}, JobStatus.OPEN),  # no signal -> stays open (default)
])
@pytest.mark.asyncio
async def test_metadata_status_signals(metadata, expected_status):
    """Tests metadata dict-based lifecycle signal paths."""
    node = JobLifecycleNode()
    record = make_record(metadata=metadata)
    result = await node.process(record)
    assert result.status == expected_status


# ---------------------------------------------------------------------------
# OPEN override: metadata says open -> override closed text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_metadata_overrides_closed_text():
    """If metadata explicitly says 'open', text-based closed detection is overridden."""
    node = JobLifecycleNode()
    record = make_record(
        description="Вакансия закрыта.",
        metadata={"status": "open"},
    )
    result = await node.process(record)
    # lifecycle.py checks metadata first, then boolean flags, then text.
    assert result.status == JobStatus.OPEN


# ---------------------------------------------------------------------------
# EXPIRED: verify mapping to FILLED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_mapped_to_filled():
    """lifecycle.py maps 'expired' string to JobStatus.FILLED."""
    node = JobLifecycleNode()
    record = make_record(metadata={"job_status": "expired"})
    result = await node.process(record)
    assert result.status == JobStatus.FILLED
