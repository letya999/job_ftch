"""Tests for external job source."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain import SourceKind
from infrastructure.sources.external_source import ExternalJobSource


@pytest.mark.asyncio
async def test_external_source_uses_career_site_kind():
    """Verify external jobs are marked as CAREER_SITE."""
    source = ExternalJobSource(sources=["remotive"], max_jobs_per_source=1)
    await source._load_all_jobs()

    for item in source._items:
        assert item.source_kind == SourceKind.CAREER_SITE
        # job_type больше не используется!
        assert "job_type" not in item.metadata


@pytest.mark.asyncio
async def test_external_source_initialization():
    """Test ExternalJobSource initialization with defaults."""
    source = ExternalJobSource()
    assert source.max_concurrent == 2
    assert source._retry_count == 3
    assert source._client is not None
    assert source._semaphore is not None
    assert source._semaphore._value == 2  # max_concurrent


@pytest.mark.asyncio
async def test_external_source_concurrency_limit():
    """Test that semaphore limits concurrent requests."""
    source = ExternalJobSource(max_concurrent=2)
    counter = 0
    max_concurrent_seen = 0

    async def track_concurrency():
        nonlocal counter, max_concurrent_seen
        counter += 1
        max_concurrent_seen = max(max_concurrent_seen, counter)
        await asyncio.sleep(0.01)  # Simulate work
        counter -= 1

    # захватываем семафор перед каждой задачей
    tasks = []
    for _ in range(10):
        async def wrapped():
            async with source._semaphore:
                await track_concurrency()
        tasks.append(asyncio.create_task(wrapped()))

    await asyncio.gather(*tasks)

    # Максимум конкурентных задач не должен превышать max_concurrent
    assert max_concurrent_seen <= source.max_concurrent


@pytest.mark.asyncio
async def test_fetch_with_retry_success():
    """Test successful fetch returns response."""
    source = ExternalJobSource()

    # Создаем моки без mocker fixture
    mock_response = AsyncMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response == mock_response
    mock_client.get.assert_called_once_with("https://example.com")


@pytest.mark.asyncio
async def test_fetch_with_retry_429_with_retry_after():
    """Test 429 response uses Retry-After header."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "0.1"}  # Small delay for test

    mock_response2 = AsyncMock()
    mock_response2.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.side_effect = [mock_response, mock_response2]
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response == mock_response2
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_with_retry_429_exponential_backoff():
    """Test 429 response without Retry-After uses exponential backoff."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 429
    mock_response.headers = {}  # No Retry-After

    mock_response2 = AsyncMock()
    mock_response2.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.side_effect = [mock_response, mock_response2]
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response == mock_response2
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_with_retry_5xx():
    """Test 5xx responses trigger retry with exponential backoff."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 503  # Service Unavailable

    mock_response2 = AsyncMock()
    mock_response2.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.side_effect = [mock_response, mock_response2]
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response == mock_response2
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_with_retry_client_error_no_retry():
    """Test 4xx client errors (except 429) don't retry."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 404  # Not Found

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response is None
    assert mock_client.get.call_count == 1  # No retry


@pytest.mark.asyncio
async def test_fetch_with_retry_timeout():
    """Test timeout triggers retry."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.side_effect = [
        TimeoutError(),  # First attempt timeout
        mock_response,   # Second success
    ]
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response is not None
    assert response.status_code == 200
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_with_retry_all_fail():
    """Test when all retry attempts fail."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 503

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    source._client = mock_client

    response = await source._fetch_with_retry("https://example.com", "test")

    assert response is None
    assert mock_client.get.call_count == source._retry_count


@pytest.mark.asyncio
async def test_rss_fetch_with_retry_success():
    """Test RSS fetch with retry returns parsed feed."""
    source = ExternalJobSource()

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = "<rss><channel><title>Test</title></channel></rss>"

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    source._client = mock_client

    # Mock feedparser.parse result
    mock_feed = MagicMock()
    mock_feed.entries = []
    mock_feed.feed = {"title": "Test"}

    # Используем patch для замены asyncio.to_thread
    import asyncio as asyncio_module

    original_to_thread = asyncio_module.to_thread

    async def mock_to_thread(func, *args, **kwargs):
        return mock_feed

    asyncio_module.to_thread = mock_to_thread

    try:
        feed = await source._fetch_rss_with_retry("https://example.com/rss", "test")
        assert feed is not None
        assert hasattr(feed, "entries")
    finally:
        # Restore original
        asyncio_module.to_thread = original_to_thread


@pytest.mark.asyncio
async def test_parse_salary_various_formats():
    """Test salary parsing from different formats."""
    source = ExternalJobSource()
    
    # Single values
    assert source._parse_salary("") == (0, 0)
    assert source._parse_salary(None) == (0, 0)
    assert source._parse_salary(120000) == (120000, 0)
    assert source._parse_salary("$120,000") == (120000, 0)
    assert source._parse_salary("120k") == (120000, 0)
    assert source._parse_salary("78k") == (78000, 0)
    assert source._parse_salary("95k") == (95000, 0)
    
    # Ranges with k
    assert source._parse_salary("120-140k") == (120000, 140000)
    assert source._parse_salary("78-95k") == (78000, 95000)
    assert source._parse_salary("125000-180000") == (125000, 180000)
    assert source._parse_salary("$120,000 - $150,000") == (120000, 150000)
    assert source._parse_salary("120k - 140k") == (120000, 140000)
    
    # Ranges without k
    assert source._parse_salary("125000-180000") == (125000, 180000)
    
    # Edge cases with text
    assert source._parse_salary("invalid") == (0, 0)
    assert source._parse_salary("from 120k") == (120000, 0)
    assert source._parse_salary("up to 150k") == (0, 150000)
    assert source._parse_salary("salary range 100k - 130k") == (100000, 130000)
    assert source._parse_salary("between 90k and 120k") == (90000, 120000)
    assert source._parse_salary("120000") == (120000, 0)


@pytest.mark.asyncio
async def test_extract_company_from_title():
    """Test company extraction from job titles."""
    source = ExternalJobSource()

    assert source._extract_company_from_title("Python Developer at Google") == "Google"
    assert source._extract_company_from_title("Senior Engineer - Microsoft") == "Microsoft"
    assert source._extract_company_from_title("Job Title No Company") == "Unknown"
    # Исправлено: обрезаем до 50 символов [:50]
    long_title = "Software Engineer at Very Long Company Name That Exceeds Fifty Characters"
    expected = "Very Long Company Name That Exceeds Fifty Characte"  # 50 символов
    result = source._extract_company_from_title(long_title)
    assert result == expected, f"Expected '{expected}', got '{result}'"
    # Дополнительная проверка длины
    assert len(result) == 50