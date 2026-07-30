"""Per-tenant runtime state (per the v0.0.4 MVP cleanup).

`TenantRuntime` is the in-memory record that `TenantRunner` builds per
tenant at `from_tenants` time. It wires together:

- the per-tenant `Settings` (env-prefixed; cloned from the base settings)
- the resolved `AuthProvider` (env / file / future vault)
- the tenant-namespaced `TenantStore` (per-tenant key prefix in the
  shared physical Store)
- the `PipelineBuilder` (lazily constructed; one per tenant)
- the per-tenant `LLMProvider`, `JobGroupStore`, `SearchBackend`,
  `JobPersistenceBackend`, `VectorBackend`, `EmbeddingProvider`
- the per-tenant source list (base + runtime overlay + disabled filter)

The class is a plain dataclass; it is not part of the public pipeline
API. `TenantRunner.from_tenants` constructs one `TenantRuntime` per
tenant and the runner methods (`run_tenant`, `get_runtime`,
`list_tenants`, etc.) look them up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.builder import PipelineBuilder
    from job_ftch.application.contracts import (
        AuthProvider,
        BgeMThreeProviderPort,
        JobGroupStore,
        JobPersistenceBackend,
        LLMProvider,
        SearchBackend,
    )
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.config import Settings
    from job_ftch.domain import RuntimeSourceRecord, TenantConfig
    from job_ftch.domain.source_spec import SourceSpec


@dataclass
class TenantRuntime:
    tenant: TenantConfig
    settings: Settings
    auth_provider: AuthProvider
    store: TenantStore
    builder: PipelineBuilder
    llm_provider: LLMProvider
    job_group_store: JobGroupStore
    search_backend: SearchBackend
    job_backend: JobPersistenceBackend
    vector_backend: object | None = None
    embedding_provider: object | None = None
    ontology_store: object | None = None
    bgem3_provider: BgeMThreeProviderPort | None = None
    base_sources: tuple[SourceSpec, ...] = field(default_factory=tuple)
    runtime_sources: dict[str, RuntimeSourceRecord] = field(default_factory=dict)
    disabled_source_ids: set[str] = field(default_factory=set)
    sources_loaded: bool = False
