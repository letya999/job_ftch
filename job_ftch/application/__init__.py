"""Application layer — pipeline engine, contracts (Protocols), use cases."""

from job_ftch.application.builder import PipelineBuilder, configure, run
from job_ftch.application.contracts import (
    EmbeddingProvider,
    FlushableSink,
    JobPersistenceBackend,
    LLMProvider,
    ManagedShotBackend,
    PipelineNode,
    PluginMetadata,
    ProcessingNode,
    SanitizingNode,
    SearchBackend,
    Sink,
    Source,
    Stage,
    Store,
    VectorBackend,
)
from job_ftch.application.pipeline import Pipeline, RunSummary
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import TenantConfig

__all__ = [
    "EmbeddingProvider",
    "FlushableSink",
    "JobPersistenceBackend",
    "LLMProvider",
    "ManagedShotBackend",
    "PipelineNode",
    "Pipeline",
    "PipelineBuilder",
    "PluginMetadata",
    "ProcessingNode",
    "RunSummary",
    "SanitizingNode",
    "SearchBackend",
    "Sink",
    "Stage",
    "Source",
    "Store",
    "TenantConfig",
    "TenantRunner",
    "VectorBackend",
    "configure",
    "run",
]
