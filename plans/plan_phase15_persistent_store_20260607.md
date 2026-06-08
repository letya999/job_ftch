# Plan: Phase 15 — Persistent Store (RM-068 to RM-073)

## Goal

Implement a persistent store layer for the job_ftch pipeline so that dedup history, run state, and processed IDs survive restarts. Three-layer hierarchy: `StoreConnector` protocol → `SQLStoreAdapter` (DBMS-agnostic logic) → `SQLiteStore` (aiosqlite) and `PostgreSQLStore` (asyncpg).

## Key constraints (from AGENTS.md and architecture.md)

- `domain/` — zero imports outside pydantic + stdlib. No exceptions.
- `application/` — only domain + stdlib + pydantic.
- `infrastructure/` — may import external clients (aiosqlite, asyncpg).
- `StoreConnector` goes in `application/contracts.py` (it is a protocol/port, not infra).
- New dependency → update `docs/tech_stack.md` first.
- Commits: `feat`, `fix`, `chore`, `docs`, `refactor` only.
- aiosqlite goes in `[sqlite]` extras group, asyncpg in `[postgres]` extras group.
- No secrets in code. DSN comes from env via `AuthProvider`/Settings.

## Architecture

```
StoreConnector  (protocol in application/contracts.py)
  └─ SQLStoreAdapter  (abstract base in infrastructure/stores/sql_adapter.py)
       ├─ SQLiteStore       (infrastructure/stores/sqlite.py, aiosqlite)
       └─ PostgreSQLStore   (infrastructure/stores/postgres.py, asyncpg)
```

`SQLStoreAdapter` implements `Store` (domain port) on top of abstract `StoreConnector` KV+set primitives. Concrete stores only implement 7 low-level methods (`get`, `set`, `delete`, `set_add`, `set_contains`, `set_members`, `ping`) and inherit all `Store` logic for free.

## SQL Schema (in infrastructure/stores/migrations/001_initial_schema.sql)

```sql
-- Key-value store for cursors, run state, serialized records
CREATE TABLE IF NOT EXISTS jf_kv (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Set membership for processed IDs, dedup keys
CREATE TABLE IF NOT EXISTS jf_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);
```

Two variants: SQLite syntax (above) and PostgreSQL syntax (separate file 001_initial_schema_pg.sql) using TIMESTAMPTZ and ON CONFLICT clauses.

## Store mapping (Store protocol → KV+set primitives)

| Store method | KV+set operation |
|---|---|
| `has_processed(item_id)` | `set_contains("processed", item_id)` |
| `mark_processed(item_id)` | `set_add("processed", item_id)` |
| `has_dedup_key(key)` | `set_contains("dedup_keys", key)` |
| `remember_dedup_key(record)` | `set_add("dedup_keys", record.key)` + `set_add("dedup_keys:{kind}", record.key)` + `set("dedup_record:{key}", json)` |
| `list_dedup_keys(kind=None)` | `set_members("dedup_keys" or "dedup_keys:{kind}")` → fetch each JSON |
| `record_duplicate(record)` | `set_add("dup_records", record.item_id)` + `set("dup_record:{item_id}", json)` |
| `list_duplicate_records()` | `set_members("dup_records")` → fetch each JSON |
| `get_run_state(key, ...)` | `get(namespaced_key)` |
| `set_run_state(key, v, ...)` | `set(namespaced_key, v)` |

## Files to create / modify

### 1. `application/contracts.py` — ADD StoreConnector protocol

After the existing `Store` protocol, add:

```python
@runtime_checkable
class StoreConnector(Protocol):
    """Universal KV + set connector — any backend (SQL, Redis, filesystem) implements it."""

    async def get(self, key: str) -> str | None:
        """Fetch a string value by key, or None if absent."""

    async def set(self, key: str, value: str) -> None:
        """Upsert a string value by key."""

    async def delete(self, key: str) -> None:
        """Remove a key-value pair. No-op if absent."""

    async def set_add(self, key: str, member: str) -> None:
        """Add a member to the named set. Idempotent."""

    async def set_contains(self, key: str, member: str) -> bool:
        """Return True if member is in the named set."""

    async def set_members(self, key: str) -> frozenset[str]:
        """Return all members of the named set."""

    async def ping(self) -> bool:
        """Return True if the backend is reachable and ready."""
```

### 2. `infrastructure/stores/in_memory.py` — add StoreConnector methods

Add `_sets: dict[str, set[str]] = {}` to `__init__` and implement:
- `get(key)` → `self._run_state.get(key)`
- `set(key, value)` → `self._run_state[key] = value`
- `delete(key)` → `self._run_state.pop(key, None)`
- `set_add(key, member)` → `self._sets.setdefault(key, set()).add(member)`
- `set_contains(key, member)` → `member in self._sets.get(key, set())`
- `set_members(key)` → `frozenset(self._sets.get(key, set()))`
- `ping()` → `return True`

### 3. `infrastructure/stores/sql_adapter.py` — DBMS-agnostic base

Abstract class `SQLStoreAdapter`. Has abstract methods:
```python
async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None: ...
async def _fetchone(self, sql: str, params: tuple[object, ...] = ()) -> tuple[str, ...] | None: ...
async def _fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[str, ...]]: ...
async def _initialize(self) -> None: ...  # apply schema + CREATE TABLE IF NOT EXISTS
```

And abstract SQL constants (class-level, overridden per-backend):
```python
_SQL_KV_GET: str
_SQL_KV_UPSERT: str
_SQL_KV_DELETE: str
_SQL_SET_ADD: str
_SQL_SET_CONTAINS: str
_SQL_SET_MEMBERS: str
```

Implements all `StoreConnector` methods using the abstract SQL+execute primitives.
Implements all `Store` methods using `StoreConnector` methods + JSON serialization for Pydantic models.

Namespace helper: `_ns(source_kind, source_name, key) -> str` same as InMemoryStore.

JSON helpers: use `model.model_dump_json()` and `ModelClass.model_validate_json(raw)` for RememberedDedupKey and DuplicateRecord.

### 4. `infrastructure/stores/sqlite.py` — SQLiteStore

```python
import aiosqlite

class SQLiteStore(SQLStoreAdapter):
    _SQL_KV_GET = "SELECT value FROM jf_kv WHERE key = ?"
    _SQL_KV_UPSERT = """
        INSERT INTO jf_kv (key, value, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """
    _SQL_KV_DELETE = "DELETE FROM jf_kv WHERE key = ?"
    _SQL_SET_ADD = "INSERT OR IGNORE INTO jf_set (key, member) VALUES (?, ?)"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = ? AND member = ?"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = ?"

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        async with self._init_lock:
            if self._conn is None:
                self._conn = await aiosqlite.connect(self._path)
                self._conn.row_factory = aiosqlite.Row
                await self._initialize()

    async def _initialize(self) -> None:
        # Read and execute the migration SQL
        schema_path = Path(__file__).parent / "migrations" / "001_initial_schema.sql"
        await self._conn.executescript(schema_path.read_text())
        await self._conn.commit()

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        await self._ensure_initialized()
        await self._conn.execute(sql, params)
        await self._conn.commit()

    async def _fetchone(self, sql: str, params: tuple[object, ...] = ()) -> tuple[str, ...] | None:
        await self._ensure_initialized()
        async with self._conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return tuple(row) if row else None

    async def _fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[str, ...]]:
        await self._ensure_initialized()
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [tuple(row) for row in rows]

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def ping(self) -> bool:
        try:
            await self._ensure_initialized()
            await self._fetchone("SELECT 1")
            return True
        except Exception:
            return False
```

Register as `@register_store("sqlite")`.

### 5. `infrastructure/stores/postgres.py` — PostgreSQLStore

```python
import asyncpg

class PostgreSQLStore(SQLStoreAdapter):
    _SQL_KV_GET = "SELECT value FROM jf_kv WHERE key = $1"
    _SQL_KV_UPSERT = """
        INSERT INTO jf_kv (key, value, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """
    _SQL_KV_DELETE = "DELETE FROM jf_kv WHERE key = $1"
    _SQL_SET_ADD = "INSERT INTO jf_set (key, member) VALUES ($1, $2) ON CONFLICT DO NOTHING"
    _SQL_SET_CONTAINS = "SELECT 1 FROM jf_set WHERE key = $1 AND member = $2"
    _SQL_SET_MEMBERS = "SELECT member FROM jf_set WHERE key = $1"

    def __init__(
        self, dsn: str, pool_min: int = 2, pool_max: int = 10,
        fallback_on_error: bool = True
    ) -> None:
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._fallback_on_error = fallback_on_error
        self._pool: asyncpg.Pool | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        async with self._init_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=self._pool_min, max_size=self._pool_max
                )
                await self._initialize()

    async def _initialize(self) -> None:
        schema_path = Path(__file__).parent / "migrations" / "001_initial_schema_pg.sql"
        async with self._pool.acquire() as conn:
            await conn.execute(schema_path.read_text())

    async def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        await self._ensure_initialized()
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def _fetchone(self, sql: str, params: tuple[object, ...] = ()) -> tuple[str, ...] | None:
        await self._ensure_initialized()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return tuple(str(v) for v in row) if row else None

    async def _fetchall(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[str, ...]]:
        await self._ensure_initialized()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [tuple(str(v) for v in row) for row in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        try:
            await self._ensure_initialized()
            await self._fetchone("SELECT 1")
            return True
        except Exception:
            return False
```

Register as `@register_store("postgres")`.

### 6. `infrastructure/stores/migrations/001_initial_schema.sql` (SQLite)

```sql
CREATE TABLE IF NOT EXISTS jf_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS jf_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);
```

### 7. `infrastructure/stores/migrations/001_initial_schema_pg.sql` (PostgreSQL)

```sql
CREATE TABLE IF NOT EXISTS jf_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jf_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);
```

### 8. `config.py` — add store settings

Add to `Settings`:
```python
store_path: Path = Path(".runtime/job_ftch.db")   # for SQLiteStore
store_dsn: str | None = None                        # for PostgreSQLStore
store_pool_min: int = Field(default=2, gt=0)
store_pool_max: int = Field(default=10, gt=0)
store_fallback_on_error: bool = True
```

Also add to `strip_optional_strings` validator: `"store_dsn"`.

### 9. `pyproject.toml` — add extras groups

Add:
```toml
[project.optional-dependencies]
sqlite = ["aiosqlite>=0.20"]
postgres = ["asyncpg>=0.29"]
```

### 10. `application/registry.py` — register stores + health fallback

In `load_extensions()`, add:
```python
"infrastructure.stores.sqlite",
"infrastructure.stores.postgres",
```

Modify `create_store()` to do async health check with fallback:
NOTE: `create_store()` is currently synchronous. Since `ping()` is async, we need to be careful.
Approach: create the store object synchronously (no connection yet), return it. The first actual I/O call will initialize.

For the fallback (RM-072), add a helper `async def create_store_with_fallback(settings: Settings) -> Store:` that calls `ping()` and falls back to InMemoryStore if needed. This function is called from `app.py` during pipeline setup.

### 11. `tests/test_phase15_persistent_store.py` — regression tests

RM-073 requirements:
1. Run same fixture twice with SQLiteStore in-memory → second run emits 0 items
2. Test tenant namespace isolation: two different sources don't collide
3. Test SQLStoreAdapter independently testable with SQLite in-memory connection
4. Test ping() returns True for SQLiteStore
5. Test fallback: SQLiteStore with invalid path → ping() returns False

Test structure:
```python
async def test_sqlite_store_idempotency():
    store = SQLiteStore(":memory:")
    # First run: mark items as processed
    assert not await store.has_processed("item-1")
    await store.mark_processed("item-1")
    # Second run: same item already processed
    assert await store.has_processed("item-1")

async def test_sqlite_store_dedup_keys():
    store = SQLiteStore(":memory:")
    record = RememberedDedupKey(...)
    await store.remember_dedup_key(record)
    keys = await store.list_dedup_keys()
    assert record in keys

async def test_sqlite_store_run_state_namespacing():
    store = SQLiteStore(":memory:")
    await store.set_run_state("cursor", "v1", source_kind="telegram", source_name="ch1")
    await store.set_run_state("cursor", "v2", source_kind="telegram", source_name="ch2")
    assert await store.get_run_state("cursor", source_kind="telegram", source_name="ch1") == "v1"
    assert await store.get_run_state("cursor", source_kind="telegram", source_name="ch2") == "v2"

async def test_sqlite_store_ping():
    store = SQLiteStore(":memory:")
    assert await store.ping() is True

async def test_sqlite_store_connector_interface():
    """Verifies StoreConnector is independently testable with SQLite."""
    store = SQLiteStore(":memory:")
    await store.set("foo", "bar")
    assert await store.get("foo") == "bar"
    await store.set_add("myset", "member1")
    assert await store.set_contains("myset", "member1") is True
    assert await store.set_members("myset") == frozenset({"member1"})
    await store.delete("foo")
    assert await store.get("foo") is None

async def test_store_connector_protocol_isinstance():
    """InMemoryStore satisfies StoreConnector protocol."""
    from application.contracts import StoreConnector
    store = InMemoryStore()
    assert isinstance(store, StoreConnector)
```

Also add marker `@pytest.mark.skipif("POSTGRES_DSN" not in os.environ, reason="no POSTGRES_DSN")` for PostgreSQL tests.

### 12. `docs/tech_stack.md` — update with new deps

Add `aiosqlite>=0.20` to the stores table (Phase 15, [sqlite] extras).
Add `asyncpg>=0.29` to the stores table (Phase 15, [postgres] extras).

## Implementation notes

### SQLStoreAdapter._initialize() in abstract class
The abstract class should declare `_initialize()` as abstract. Each concrete store implements it to apply the correct schema (SQLite vs PostgreSQL syntax).

### Atomicity in aiosqlite
aiosqlite's connection is NOT thread-safe. But since we use asyncio (single-threaded event loop), a single `Connection` object is safe. We use `asyncio.Lock` only for the initialization phase.

### asyncpg fetchrow vs fetch
- `fetchrow` → single record (or None)
- `fetch` → list of records
- asyncpg passes params as `*args` after the query string

### aiosqlite cursor vs connection
- Use `connection.execute(sql, params)` → returns cursor context manager
- `await cursor.fetchone()` → tuple or None
- `await cursor.fetchall()` → list of tuples

### PostgreSQLStore not required in tests
The PostgreSQL tests should be skipped by default (no POSTGRES_DSN env var). The SQLiteStore tests prove the shared logic from SQLStoreAdapter works correctly (RM-073: "confirms DBMS-agnostic design").

### Migration file path
Use `Path(__file__).parent / "migrations" / "001_initial_schema.sql"` — relative to the store module's directory. This is robust regardless of cwd.

## Flow name
Use defaultFlow from flow.config.json.

## Test command after implementation
```
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/test_phase15_persistent_store.py -v
uv run pytest tests/ -v
```

## Commit message
```
feat(phase-15): persistent store — StoreConnector + SQLiteStore + PostgreSQLStore (RM-068..073)
```
