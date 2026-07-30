"""Stable node inventory. Implementations remain in ``job_ftch.nodes``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .contracts import CompiledGraph, NodeManifest, ParamSpec, RuntimeContext

Factory = Callable[[dict[str, Any]], Any]
ContextFactory = Callable[[dict[str, Any], RuntimeContext], Any]
_MANIFESTS: dict[str, NodeManifest] = {}
_FACTORIES: dict[str, Factory] = {}
_CONTEXT_FACTORIES: dict[str, ContextFactory] = {}


def register(
    manifest: NodeManifest,
    factory: Factory | None = None,
    context_factory: ContextFactory | None = None,
) -> None:
    if manifest.node_id in _MANIFESTS:
        raise ValueError(f"duplicate graph node: {manifest.node_id}")
    _MANIFESTS[manifest.node_id] = manifest
    if factory is not None:
        _FACTORIES[manifest.node_id] = factory
    if context_factory is not None:
        _CONTEXT_FACTORIES[manifest.node_id] = context_factory


def manifest(node_id: str) -> NodeManifest:
    try:
        return _MANIFESTS[node_id]
    except KeyError as exc:
        raise KeyError(f"unregistered graph node: {node_id}") from exc


get_manifest = manifest


def manifests() -> tuple[NodeManifest, ...]:
    return tuple(_MANIFESTS.values())


def factory(
    node_id: str,
    params: dict[str, Any] | None = None,
    context: RuntimeContext | None = None,
) -> Any:
    if context is not None and node_id in _CONTEXT_FACTORIES:
        return _CONTEXT_FACTORIES[node_id](validate_params(node_id, params or {}), context)
    try:
        creator = _FACTORIES[node_id]
    except KeyError as exc:
        raise RuntimeError(f"node {node_id} has no runnable factory") from exc
    return creator(params or {})


get_factory = factory


def build_runtime_bindings(graph: CompiledGraph, context: RuntimeContext) -> dict[str, Any]:
    """Build every graph node from explicit id/context resources.

    This is the runtime path for YAML graphs.  It deliberately does not use
    implementation class names: a node is resolved by its graph id first,
    then by its registered context factory.
    """
    bindings: dict[str, Any] = {}
    for node in graph.spec.nodes:
        params = validate_params(node.node, node.params)
        if node.id in context.node_instances:
            instance = context.node_instances[node.id]
            configure = getattr(instance, "configure_graph_params", None)
            if node.params and not callable(configure):
                raise RuntimeError(
                    f"{node.id}: explicit runtime instance cannot apply graph params"
                )
            if callable(configure):
                configured = configure(params)
                if configured is not None:
                    instance = configured
            bindings[node.id] = instance
            continue
        creator = context.factories.get(node.node) or _CONTEXT_FACTORIES.get(node.node)
        if creator is None:
            raise RuntimeError(
                f"{node.id}: no RuntimeContext factory or explicit instance for {node.node}"
            )
        bindings[node.id] = creator(params, context)
    return bindings


def bind_node_instances(instances: list[Any]) -> dict[str, Any]:
    """Bind already-built production nodes to manifest ids without re-instantiation."""
    by_factory = {type(instance).__name__: instance for instance in instances}
    bindings: dict[str, Any] = {}
    for item in manifests():
        instance = by_factory.get(item.factory)
        if instance is not None:
            bindings[item.node_id] = instance
    return bindings


def validate_params(node_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and materialize effective params; unknown params are never ignored."""
    item = manifest(node_id)
    unknown = sorted(set(params) - set(item.param_schema))
    if unknown:
        raise ValueError(f"{node_id}: unsupported graph parameters: {', '.join(unknown)}")
    effective = {
        name: spec.default for name, spec in item.param_schema.items() if spec.default is not None
    }
    effective.update(params)
    for name, spec in item.param_schema.items():
        if name not in effective and spec.required:
            raise ValueError(f"{node_id}: required graph parameter missing: {name}")
        if name in effective:
            spec.validate(name, effective[name])
    return effective


def _seed_inventory() -> None:
    entries = {
        "sanitize": (
            "SanitizeNode",
            "RawItem",
            "RawItem",
            ("gate",),
            ("sequential",),
            ("gate",),
            True,
        ),
        "snapshot_filter": (
            "SnapshotFilterNode",
            "RawItem",
            "RawItem",
            ("gate",),
            ("sequential",),
            ("gate",),
            True,
        ),
        "source_context": (
            "SourceContextNode",
            "RawItem",
            "RawItem",
            ("transform",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "ontology_snapshot": (
            "OntologySnapshotNode",
            "RawItem",
            "RawItem",
            ("transform",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "candidate_segmentation": (
            "CandidateSegmentationNode",
            "RawItem",
            "CandidateSpan",
            ("fan_out",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "dedup": ("DedupNode", "RawItem", "RawItem", ("gate",), ("sequential",), ("gate",), True),
        "completeness_gate": (
            "CompletenessGateNode",
            "RawItem",
            "RawItem",
            ("gate",),
            ("sequential",),
            ("observe", "gate"),
            True,
        ),
        "extraction": (
            "ExtractionNode",
            "RawItem",
            "JobDraft",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "decision_extraction": (
            "DecisionExtractionNode",
            "RawItem",
            "JobDraft",
            ("transform", "evidence_producer"),
            ("sequential",),
            ("observe",),
            True,
        ),
        "extraction_validation": (
            "ExtractionValidationNode",
            "JobDraft",
            "JobDraft",
            ("gate",),
            ("sequential",),
            ("gate",),
            False,
        ),
        "job_normalization": (
            "TitleCompanyNormalizationNode",
            "JobDraft",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "skill_normalization": (
            "SkillNormalizationNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "location_work_mode_normalization": (
            "LocationWorkModeNormalizationNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "compensation": (
            "CompensationParsingNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "garbage_filter": (
            "GarbageFilterNode",
            "RawItem",
            "RawItem",
            ("evidence_producer", "gate"),
            ("sequential", "parallel", "background"),
            ("observe", "gate"),
            False,
        ),
        "post_type": (
            "PostTypeClassificationNode",
            "RawItem",
            "RawItem",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "hard_filter": (
            "HardFilterNode",
            "RawItem",
            "RawItem",
            ("evidence_producer", "gate"),
            ("sequential", "parallel", "background"),
            ("observe", "gate"),
            False,
        ),
        "bgem3_embed": (
            "BgeMThreeNode",
            "RawItem",
            "RawItem",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "embedding_prefilter": (
            "EmbeddingPrefilterNode",
            "RawItem",
            "RawItem",
            ("evidence_producer", "gate"),
            ("sequential", "parallel"),
            ("observe", "gate"),
            False,
        ),
        "tfidf_logreg_prefilter": (
            "TfidfLogregRelevancePrefilterNode",
            "RawItem",
            "RawItem",
            ("evidence_producer", "gate"),
            ("sequential",),
            ("observe", "gate"),
            True,
        ),
        "semantic_prefilter": (
            "SemanticPrefilterNode",
            "RawItem",
            "RawItem",
            ("evidence_producer", "gate"),
            ("sequential", "parallel"),
            ("observe", "gate"),
            False,
        ),
        "raw_jobness": (
            "RawJobnessEvidenceNode",
            "RawItem",
            "RawItem",
            ("evidence_producer",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "parallel_scoring": (
            "ParallelScoringNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "profile_semantic_evidence": (
            "ProfileSemanticEvidenceNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "lexical_evidence": (
            "LexicalEvidenceNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential",),
            ("observe",),
            False,
        ),
        # The settings-backed production builder uses the explicit
        # post-extraction contract. Raw jobness remains a separate stage.
        "jobness": (
            "JobnessEvidenceProducer",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel"),
            ("observe",),
            False,
        ),
        "llm_relevance": (
            "LLMRelevanceClassificationNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "deferred"),
            ("observe",),
            False,
        ),
        "llm_relevance_evidence": (
            "LLMRelevanceEvidenceNode",
            "AssessedJob",
            "AssessedJob",
            ("evidence_producer",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "profile_match": (
            "MultiProfileMatchNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "evidence_fanout": (
            "EvidenceFanOutNode",
            "JobRecord",
            "AssessedJob",
            ("transform",),
            ("sequential",),
            ("observe",),
            False,
        ),
        "evidence_decision": (
            "EvidenceDecisionNode",
            "JobRecord",
            "JobRecord",
            ("terminal_decision",),
            ("sequential",),
            ("terminal_decision",),
            False,
        ),
        "risk": (
            "RiskScoringNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer", "gate"),
            ("sequential", "parallel", "background"),
            ("observe", "gate"),
            False,
        ),
        "quality": (
            "QualityScoringNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer", "gate"),
            ("sequential", "parallel", "background"),
            ("observe", "gate"),
            False,
        ),
        "lifecycle": (
            "JobLifecycleNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer", "gate"),
            ("sequential", "parallel", "background"),
            ("observe", "gate"),
            False,
        ),
        "job_validation": (
            "JobValidationNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer", "gate"),
            ("sequential", "parallel"),
            ("observe", "gate"),
            False,
        ),
        "reranker": (
            "RerankerNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "bge_reranker": (
            "BgeRerankerNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            False,
        ),
        "language_detection": (
            "LanguageDetectionNode",
            "JobRecord",
            "JobRecord",
            ("transform", "evidence_producer"),
            ("sequential", "parallel", "background"),
            ("observe",),
            True,
        ),
        "translation": (
            "TranslationNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential", "deferred"),
            ("observe",),
            True,
        ),
        "embedding": (
            "EmbeddingNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer",),
            ("sequential", "parallel", "background"),
            ("observe",),
            True,
        ),
        "full_extraction": (
            "FullExtractionNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential", "deferred"),
            ("observe",),
            True,
        ),
        "triage_extraction": (
            "TriageExtractionNode",
            "RawItem",
            "JobDraft",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "uncertainty_router": (
            "UncertaintyRouterNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "presentable_text": (
            "PresentableTextNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential", "deferred", "post_accept"),
            ("observe",),
            True,
        ),
        "accept_template_presentation": (
            "AcceptTemplatePresentationNode",
            "JobRecord",
            "JobRecord",
            ("transform",),
            ("sequential",),
            ("observe",),
            True,
        ),
        "is_job": (
            "IsJobNode",
            "JobRecord",
            "JobRecord",
            ("evidence_producer", "gate"),
            ("sequential", "parallel"),
            ("observe", "gate"),
            False,
        ),
        "decision": (
            "DecisionNode",
            "AssessedJob",
            "DecisionResult",
            ("terminal_decision",),
            ("sequential",),
            ("terminal_decision",),
            False,
        ),
        "legacy_routing": (
            "RoutingNode",
            "JobRecord",
            "JobRecord",
            ("terminal_decision",),
            ("sequential",),
            ("terminal_decision",),
            False,
        ),
        "decision_aggregator": (
            "DecisionAggregatorNode",
            "JobRecord",
            "JobRecord",
            ("terminal_decision",),
            ("sequential",),
            ("observe", "terminal_decision"),
            False,
        ),
        "review_resolution": (
            "ReviewResolutionNode",
            "JobRecord",
            "JobRecord",
            ("terminal_decision",),
            ("sequential",),
            ("terminal_decision",),
            False,
        ),
        "aggregation": (
            "JobAggregationNode",
            "JobRecord",
            "JobRecord",
            ("side_effect",),
            ("sequential", "post_accept"),
            ("side_effect", "stateful_checkpoint"),
            True,
        ),
        "final_group_update": (
            "_FinalGroupUpdateNode",
            "JobRecord",
            "JobRecord",
            ("side_effect",),
            ("sequential",),
            ("side_effect",),
            True,
        ),
        "post_accept_enrichment": (
            "PostAcceptEnrichment",
            "JobRecord",
            "JobRecord",
            ("side_effect",),
            ("deferred", "post_accept"),
            ("side_effect",),
            False,
        ),
    }
    for node_id, (impl, inp, out, caps, execution, effects, mutates) in entries.items():
        register(
            NodeManifest(
                node_id=node_id,
                factory=impl,
                input_type=inp,
                output_type=out,
                capabilities=caps,
                allowed_execution=execution,
                allowed_effects=effects,
                mutates_payload=mutates,
                side_effects="side_effect" in caps,
                terminal_eligible="terminal_decision" in caps,
                decision_capability=bool(set(caps) & {"gate", "terminal_decision"}),
                param_schema=_param_schema(node_id),
            )
        )


def _param_schema(node_id: str) -> dict[str, ParamSpec]:
    common = {
        "max_chars": ParamSpec("int", minimum=1, maximum=1_000_000),
    }
    return {
        "bgem3_embed": {"model": ParamSpec("str", default="BAAI/bge-m3"), **common},
        "tfidf_logreg_prefilter": {
            "threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "mode": ParamSpec("str", enum=("gate", "shadow")),
            "model_path": ParamSpec("str"),
        },
        "semantic_prefilter": {
            "dense_margin_threshold": ParamSpec("float", minimum=-1.0, maximum=1.0),
            "rescue_logic": ParamSpec("str", enum=("any_positive", "strong_ai_signal", "none")),
        },
        "parallel_scoring": {
            "margin_k": ParamSpec("float", minimum=0.01, maximum=100.0),
            "w_dense": ParamSpec("float", minimum=0.0, maximum=1.0),
            "w_sparse": ParamSpec("float", minimum=0.0, maximum=1.0),
            "w_role": ParamSpec("float", minimum=0.0, maximum=1.0),
        },
        "extraction": {
            "extraction_mode": ParamSpec(
                "str",
                default="llm_or_structured",
                enum=("llm_or_structured", "structured_or_heuristic"),
            ),
        },
        "llm_relevance": {
            "low_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "high_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "max_per_run": ParamSpec("int", minimum=0),
            "max_ambiguity_resolutions": ParamSpec("int", minimum=0),
            "max_precision_confirmations": ParamSpec("int", minimum=0),
            "prompt": ParamSpec("str"),
            "call_policy": ParamSpec(
                "str", default="threshold", enum=("threshold", "force_all", "uncertainty_only")
            ),
            "classification_mode": ParamSpec(
                "str", default="legacy_decision", enum=("legacy_decision", "compact_evidence")
            ),
        },
        "llm_relevance_evidence": {
            "low_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "high_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "max_per_run": ParamSpec("int", minimum=0),
            "max_ambiguity_resolutions": ParamSpec("int", minimum=0),
            "max_precision_confirmations": ParamSpec("int", minimum=0),
            "prompt": ParamSpec("str"),
            "call_policy": ParamSpec(
                "str", default="threshold", enum=("threshold", "force_all", "uncertainty_only")
            ),
            "classification_mode": ParamSpec(
                "str", default="legacy_decision", enum=("legacy_decision", "compact_evidence")
            ),
        },
        "post_type": {
            "confidence_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
        },
        "risk": {
            "review_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
        },
        "quality": {
            "review_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
        },
        "job_validation": {
            "min_quality_score": ParamSpec("float", minimum=0.0, maximum=1.0),
            "min_relevance_score": ParamSpec("float", minimum=0.0, maximum=1.0),
            "llm_band_floor": ParamSpec("float", minimum=0.0, maximum=1.0),
            "enforce_policy": ParamSpec("bool"),
        },
        "presentable_text": {
            "max_per_run": ParamSpec("int", minimum=0),
            "enabled": ParamSpec("bool"),
        },
        "decision_extraction": {
            "max_per_run": ParamSpec("int", minimum=0),
            "prompt_mode": ParamSpec("str", default="full", enum=("full", "compact")),
            "brief_max_chars": ParamSpec("int", default=2200, minimum=200, maximum=10000),
        },
        "triage_extraction": {},
        "uncertainty_router": {
            "low_threshold": ParamSpec("float", default=0.20, minimum=0.0, maximum=1.0),
            "high_threshold": ParamSpec("float", default=0.50, minimum=0.0, maximum=1.0),
        },
        "legacy_routing": {
            "accept_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "review_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
            "quality_override_threshold": ParamSpec("float", minimum=0.0, maximum=1.0),
        },
        "decision_aggregator": {
            "accept_profile_score": ParamSpec("float", default=0.55, minimum=0.0, maximum=1.0),
            "review_profile_score": ParamSpec("float", default=0.35, minimum=0.0, maximum=1.0),
            "accept_llm_confidence": ParamSpec("float", default=0.55, minimum=0.0, maximum=1.0),
            "allow_missing_llm_rescue": ParamSpec("bool", default=True),
            "allow_reject_rescue": ParamSpec("bool", default=True),
            "require_no_profile_conflict": ParamSpec("bool", default=False),
        },
        "review_resolution": {
            "accept_confidence": ParamSpec("float", default=0.70, minimum=0.0, maximum=1.0),
            "max_calls": ParamSpec("int", default=40, minimum=0),
        },
    }.get(node_id, {})


_seed_inventory()
