# Fix Stale Stub Tests in test_phase21_rss.py

Date: 2026-06-08

## Context

`tests/test_phase21_rss.py` has two test functions that expected `NotImplementedError`
from the old webhook/websocket stub implementations:

- `test_webhook_source_raises_not_implemented` (line 172)
- `test_websocket_source_raises_not_implemented` (line 185)

Both are now real implementations (not stubs). Without `aiohttp` / `websockets` installed,
they raise `ImportError` at `__init__` time — not `NotImplementedError`.

## Fix

In `tests/test_phase21_rss.py`, replace both functions:

### `test_webhook_source_raises_not_implemented` → rename and update assertion

```python
def test_webhook_source_requires_aiohttp_dep():
    from unittest.mock import MagicMock

    from domain.source_spec import WebhookSourceSpec
    from infrastructure.sources.realtime.webhook import WebhookSource

    spec = WebhookSourceSpec(path="/test")
    with pytest.raises(ImportError, match="aiohttp"):
        WebhookSource(spec, MagicMock())
```

### `test_websocket_source_raises_not_implemented` → rename and update assertion

```python
def test_websocket_source_requires_websockets_dep():
    from unittest.mock import MagicMock

    from domain.source_spec import WebSocketSourceSpec
    from infrastructure.sources.realtime.websocket import WebSocketSource

    spec = WebSocketSourceSpec(url="wss://example.com/stream")
    with pytest.raises(ImportError, match="websockets"):
        WebSocketSource(spec, MagicMock())
```

Also remove the unused `_drain` helper function and its `import asyncio` at the top of
each test if they are no longer used by any test in the file.

Check if `_drain` is used by any other test in the file. If not, delete it.
Check if the `import asyncio` inside the test functions can be removed.

## Quality Gates

```
uv run ruff check .       # must be clean
uv run pytest tests/ --ignore=tests/e2e -q  # must pass 209 passed, 0 failed
```
