"""Run the full production pipeline on the eval dataset and report P/R/F1."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import inspect
import io
import json
import logging
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import nullcontext, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from job_ftch.application.dataset_hashing import dataset_hash

if TYPE_CHECKING:
    from job_ftch.application.contracts import SanitizingNode, Stage
    from job_ftch.config import Settings
    from job_ftch.domain import ProfileCatalog
    from job_ftch.nodes.snapshot_filter import SnapshotFilterNode

load_dotenv(".env")
load_dotenv(".env.dev")

_CURRENT_ITEM_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_eval_item_id",
    default=None,
)


def _configure_utf8_stdio() -> None:
    if getattr(sys.stdout, "buffer", None) is not None:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
        )
    if getattr(sys.stderr, "buffer", None) is not None:
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer,
            encoding="utf-8",
            errors="replace",
        )


def _langfuse_env() -> tuple[str, str, str]:
    public_key = os.environ.get("JOB_FTCH_LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("JOB_FTCH_LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("JOB_FTCH_LANGFUSE_HOST", "https://cloud.langfuse.com")
    return public_key, secret_key, host


def _source_revision() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        fallback_rev = datetime.now(UTC).strftime("nogit-%Y%m%dT%H%M%SZ")
        return {
            "commit": fallback_rev,
            "dirty": True,
            "source": "fallback",
            "reason": "git_unavailable",
        }

    return {
        "commit": commit,
        "dirty": bool(run("status", "--porcelain")),
        "source": "git",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full pipeline eval on eval_dataset.jsonl.")
    parser.add_argument(
        "--dataset", default="fixtures/dataset/eval_dataset_fixed140_locked_v1.jsonl"
    )
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--selected-item-ids",
        type=Path,
        default=None,
        help="Newline-delimited stable IDs; preserves order and bypasses random sampling.",
    )
    parser.add_argument("--dataset-name", default="job_ftch_pipeline_eval")
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--item-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--run-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--invalid-out", type=Path, default=None)
    parser.add_argument("--no-eval", action="store_true")
    parser.add_argument(
        "--state-mode",
        choices=("memory", "runtime", "production"),
        default="memory",
        help=(
            "memory: lightweight replay; "
            "runtime: production-like replay with isolated sqlite state and snapshot lifecycle; "
            "production: explicit replay against the tenant's configured production stores."
        ),
    )
    parser.add_argument(
        "--profile-source",
        choices=("fixture", "tenant"),
        default="tenant",
        help=(
            "tenant (default): use active tenant/user profile, DB ontology, dynamic prompts, "
            "and tenant-scoped Qdrant shots while keeping eval state isolated; "
            "fixture: use the static filter profile and isolated in-memory store "
            "(faster, but less representative of production)."
        ),
    )
    parser.add_argument(
        "--tenant-config",
        default="job_ftch/adapters/telegram_bot/config/tenants/ai_jobs.yaml",
        help="Tenant YAML used with --profile-source tenant.",
    )
    parser.add_argument("--tenant-id", default="ai_jobs")
    parser.add_argument("--user-id", default="480637186")
    parser.add_argument(
        "--runtime",
        choices=("dev", "prod"),
        default=None,
        help="Load the same root and adapter env/config layers as run_bot_ingest.",
    )
    parser.add_argument("--graph", default=None, help="Declarative graph YAML for a future replay.")
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="NODE.PARAM=VALUE"
    )
    parser.add_argument("--enable", action="append", default=[])
    parser.add_argument("--disable", action="append", default=[])
    parser.add_argument("--effect", action="append", default=[], metavar="NODE=EFFECT")
    parser.add_argument("--execution", action="append", default=[], metavar="NODE=MODE")
    parser.add_argument("--print-graph", action="store_true")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Read-only graph and tenant-shot preflight; never reads the dataset.",
    )
    parser.add_argument("--compare-to", default=None)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--expected-shot-snapshot-hash", default=None)
    parser.add_argument("--expected-selected-item-ids-hash", default=None)
    parser.add_argument(
        "--validate-selection-only",
        action="store_true",
        help="Validate the binary selected set and ID hash without building runtime dependencies.",
    )
    parser.add_argument(
        "--require-slice",
        action="append",
        default=[],
        metavar="KIND=ITEMS/RELEVANT/RECALL",
        help=(
            "Fail the run unless the given source_kind slice carries at least ITEMS labeled "
            "items and RELEVANT positives, and reaches RECALL. Repeatable. Example: "
            "career_site=100/25/0.85"
        ),
    )
    parser.add_argument(
        "--allow-degraded-prefilter",
        action="store_true",
        help=(
            "Allow the run to proceed even if the relevance prefilter model is missing. "
            "Without this flag, a missing prefilter model exits non-zero."
        ),
    )
    parser.add_argument(
        "--reset-before-run",
        action="store_true",
        help=(
            "Clear tenant run state before running (required for --state-mode production). "
            "Calls the store's tenant-scoped reset, clearing jf_kv, jf_set, jf_observations, "
            "jf_source_snapshots, jf_source_ingest_state, jf_dedup_claims, jf_outbox, and "
            "job groups/jobs. Never touches jf_ontology_* tables."
        ),
    )
    parser.add_argument(
        "--allow-dirty-state",
        action="store_true",
        help=(
            "Bypass the production-mode requirement for --reset-before-run. "
            "The run proceeds but provenance records dirty_state=true."
        ),
    )
    parser.add_argument(
        "--expected-candidates",
        type=int,
        default=None,
        help=(
            "Fail after writing results if the candidate count differs. "
            "Defaults to 486 for the canonical production sample "
            "(--state-mode production --sample 400 --seed 42)."
        ),
    )
    return parser.parse_args()


def _parse_slice_requirement(spec: str) -> tuple[str, int, int, float]:
    kind, _, budget = spec.partition("=")
    parts = budget.split("/")
    if not kind.strip() or len(parts) != 3:
        raise ValueError(
            f"Invalid --require-slice {spec!r}; expected KIND=ITEMS/RELEVANT/RECALL, "
            "for example career_site=100/25/0.85"
        )
    return kind.strip(), int(parts[0]), int(parts[1]), float(parts[2])


def _evaluate_slice_requirements(
    specs: list[str], by_source_kind: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Check that each required source_kind is both measurable and healthy.

    A headline F1 averaged over a dataset that is 78% telegram cannot detect a
    collapse on career sites, which carry ~88% of live traffic. Requiring a
    minimum labeled population per slice makes an unmeasurable regime a failure
    rather than a silent pass.
    """
    checks: list[dict[str, Any]] = []
    for spec in specs:
        kind, min_items, min_relevant, min_recall = _parse_slice_requirement(spec)
        metrics = by_source_kind.get(kind)
        if metrics is None:
            checks.append(
                {
                    "source_kind": kind,
                    "passed": False,
                    "reason": "slice_absent_from_dataset",
                    "requirement": spec,
                }
            )
            continue
        items = int(metrics.get("items", 0))
        relevant = int(metrics.get("tp", 0)) + int(metrics.get("fn", 0))
        recall = float(metrics.get("recall", 0.0))
        failures = []
        if items < min_items:
            failures.append(f"items {items} < {min_items}")
        if relevant < min_relevant:
            failures.append(f"relevant {relevant} < {min_relevant}")
        if recall < min_recall:
            failures.append(f"recall {recall:.3f} < {min_recall:.3f}")
        checks.append(
            {
                "source_kind": kind,
                "passed": not failures,
                "reason": "; ".join(failures) or "ok",
                "requirement": spec,
                "items": items,
                "relevant": relevant,
                "recall": recall,
                "precision": float(metrics.get("precision", 0.0)),
            }
        )
    return checks


def _write_invalid_report(path: Path, payload: dict[str, Any]) -> None:
    """Persist a terminal invalidation record without exposing a partial PASS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def _close_eval_resources(resources: list[Any]) -> None:
    """Close runtime clients after both successful and invalid eval runs."""
    for resource in reversed(resources):
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        result = close()
        if inspect.isawaitable(result):
            await result


def _compile_requested_graph(args: argparse.Namespace) -> Any:
    from job_ftch.application.graph import apply_overrides, compile_graph, load_graph

    if not args.graph:
        raise ValueError("--graph is required with --compile-only/--print-graph")
    spec = apply_overrides(
        load_graph(args.graph),
        params=args.overrides,
        enable=args.enable,
        disable=args.disable,
        effects=args.effect,
        executions=args.execution,
    )
    return compile_graph(spec)


def _preflight_shots(
    settings: Any,
    *,
    tenant_id: str,
    user_id: str,
    bgem3_provider: Any | None = None,
) -> dict[str, Any]:
    """Read only the exact user-scoped BGE shots required by comparative eval."""
    from job_ftch.application.graph.preflight import shot_snapshot_from_vectors

    mode = str(settings.relevance_shot_source_mode).strip().lower()
    if mode != "user_db_only":
        raise ValueError("comparative graph eval requires relevance_shot_source_mode=user_db_only")
    backend = str(settings.relevance_shot_backend).strip().lower()
    role_prefix = f"user:{user_id}@tenant:{tenant_id}:"
    if backend == "memory":
        from job_ftch.infrastructure.relevance import shot_registry

        store = shot_registry.get_store()
        if store is None:
            raise RuntimeError("user_db_only shot store is unavailable")
        positive, negative, _, _ = store.load(
            tenant_id=tenant_id,
            user_id=user_id,
            categories=("vacancy",),
        )
    elif backend == "qdrant":
        try:
            from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider
            from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeQdrantShotStore
        except ImportError as exc:
            raise RuntimeError("BGE-M3 preflight requires job-ftch[bgem3] and qdrant") from exc
        provider = bgem3_provider or BgeMThreeProvider(settings.bgem3_model)
        store = BgeMThreeQdrantShotStore(
            url=str(settings.qdrant_url),
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
            collection=settings.relevance_shot_collection_bgem3,
            provider=provider,
        )
        positive, negative, _, _ = store.load(
            role_prefix=role_prefix,
            tenant_id=tenant_id,
            user_id=user_id,
            categories=("vacancy",),
        )
    else:
        raise ValueError(f"unsupported relevance_shot_backend for comparative eval: {backend!r}")
    snapshot = shot_snapshot_from_vectors(
        tenant_id=tenant_id,
        user_id=user_id,
        source_mode=mode,
        collection=settings.relevance_shot_collection_bgem3,
        positive=positive,
        negative=negative,
        model_id=settings.bgem3_model,
    )
    return snapshot.__dict__


def _graph_consumes_bgem3_shots(graph: Any) -> bool:
    """Only BGE graphs need a live vector-shot preflight.

    A compact LLM-only graph records its dynamic prompt hashes separately and
    must not load BGE-M3 just to inspect an unused Qdrant collection.
    """
    return any(node.enabled and node.node == "bgem3_embed" for node in graph.spec.nodes)


def _build_historical_bindings(
    settings: Any,
    catalog: Any,
    store: Any,
    llm: Any,
    *,
    tenant_id: str,
    user_id: str,
    relevance_prompts: dict[str, str | None] | None = None,
    bgem3_provider: Any | None = None,
    include_shot_anchors: bool = True,
    include_bgem3: bool = True,
) -> dict[str, Any]:
    """Build only the legacy nodes absent from the current target builder."""
    from job_ftch.nodes.accept_template_presentation import AcceptTemplatePresentationNode
    from job_ftch.nodes.decision_aggregator import DecisionAggregatorNode
    from job_ftch.nodes.decision_extraction import DecisionExtractionNode
    from job_ftch.nodes.full_extraction import FullExtractionNode
    from job_ftch.nodes.is_job_classifier import IsJobNode
    from job_ftch.nodes.lexical_evidence import LexicalEvidenceNode
    from job_ftch.nodes.llm_relevance_classification import LLMRelevanceClassificationNode
    from job_ftch.nodes.parallel_scoring import ParallelScoringNode
    from job_ftch.nodes.presentable_text import PresentableTextNode
    from job_ftch.nodes.profile_semantic_evidence import ProfileSemanticEvidenceNode
    from job_ftch.nodes.review_resolution import ReviewResolutionNode
    from job_ftch.nodes.routing import RoutingNode
    from job_ftch.nodes.triage_extraction import TriageExtractionNode
    from job_ftch.nodes.uncertainty_router import UncertaintyRouterNode

    bindings: dict[str, Any] = {}
    if include_bgem3:
        import numpy as np

        from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider
        from job_ftch.infrastructure.relevance.shot_anchor import BgeMThreeQdrantShotStore
        from job_ftch.nodes.bge_embed_node import BgeMThreeNode

        # ``build_nodes`` has already created the graph's BGE encoder. Reuse
        # it when the selected graph actually contains a BGE-dependent node.
        provider = bgem3_provider or BgeMThreeProvider(settings.bgem3_model)
        if include_shot_anchors:
            qdrant = BgeMThreeQdrantShotStore(
                url=str(settings.qdrant_url),
                api_key=settings.qdrant_api_key.get_secret_value()
                if settings.qdrant_api_key
                else None,
                collection=settings.relevance_shot_collection_bgem3,
                provider=provider,
            )
            positive, negative, positive_sparse, negative_sparse = qdrant.load(
                role_prefix=f"user:{user_id}@tenant:{tenant_id}:",
                tenant_id=tenant_id,
                user_id=user_id,
                categories=("vacancy",),
            )
            if positive.shape[0] == 0 or negative.shape[0] == 0:
                raise RuntimeError(
                    "historical graph requires non-empty user positive and negative shots"
                )
        else:
            positive = negative = np.zeros((0, provider.dim), dtype=np.float32)
            positive_sparse = []
            negative_sparse = []
    profiles = tuple(catalog.profiles)
    if include_bgem3:
        roles = [role for profile in profiles for role in profile.target_roles]
        role_vectors = (
            [encoded["dense"] for encoded in provider.encode_batch(roles)] if roles else []
        )
        positive_queries = [
            phrase
            for profile in profiles
            for phrase in (
                *profile.target_roles,
                *profile.target_domains,
                *profile.project_types,
                *(skill.canonical_name for skill in profile.required_skills),
                *(skill.canonical_name for skill in profile.preferred_skills),
            )
        ]
        negative_queries = [
            phrase
            for profile in profiles
            for phrase in (*profile.anti_preferences, *profile.blocked_domains)
        ]
        positive_query_vectors = (
            np.vstack([encoded["dense"] for encoded in provider.encode_batch(positive_queries)])
            if positive_queries
            else np.zeros((0, provider.dim), dtype=np.float32)
        )
        negative_query_vectors = (
            np.vstack([encoded["dense"] for encoded in provider.encode_batch(negative_queries)])
            if negative_queries
            else np.zeros((0, provider.dim), dtype=np.float32)
        )
    from job_ftch.application.builder import build_llm

    relevance_settings = settings.model_copy(update={"openai_model": settings.relevance_llm_model})
    relevance_base = build_llm(relevance_settings)
    relevance_llm = (
        _EvalLLMMeter(relevance_base, parent=llm)
        if isinstance(llm, _EvalLLMMeter)
        else relevance_base
    )
    target_roles = tuple(
        dict.fromkeys(
            role for profile in catalog.profiles for role in profile.target_roles if role.strip()
        )
    )[:60]
    bindings.update(
        {
            "is_job": IsJobNode(),
            "llm_relevance": LLMRelevanceClassificationNode(
                llm=relevance_llm,
                store=store,
                catalog=catalog,
                low_threshold=settings.llm_relevance_low_threshold,
                high_threshold=settings.llm_relevance_high_threshold,
                max_per_run=settings.llm_relevance_max_per_run,
                tenant_id=tenant_id,
                relevance_prompts=relevance_prompts,
            ),
            "decision_extraction": DecisionExtractionNode(
                llm,
                store,
                catalog,
                relevance_prompts=relevance_prompts,
                max_calls=settings.pipeline_full_extraction_max_calls_per_run,
                target_roles=target_roles,
                min_search_relevance=settings.extraction_min_search_relevance,
                min_hiring_intent=settings.extraction_min_hiring_intent,
                capture_payloads=settings.tracing_capture_payloads,
                scope="core",
            ),
            "decision_aggregator": DecisionAggregatorNode(),
            "review_resolution": ReviewResolutionNode(
                relevance_llm,
                decision_brief=(relevance_prompts or {}).get(profiles[0].profile_id)
                if profiles
                else None,
            ),
            "triage_extraction": TriageExtractionNode(llm, target_roles=target_roles),
            "uncertainty_router": UncertaintyRouterNode(),
            "full_extraction": FullExtractionNode(
                llm,
                max_calls=settings.pipeline_full_extraction_max_calls_per_run,
                target_roles=target_roles,
                capture_payloads=settings.tracing_capture_payloads,
            ),
            "legacy_routing": RoutingNode(
                accept_threshold=settings.routing_accept_threshold,
                review_threshold=settings.routing_review_threshold,
                quality_override_threshold=settings.routing_quality_override_threshold,
            ),
            "presentable_text": PresentableTextNode(
                llm=llm,
                store=store,
                max_per_run=settings.llm_presentable_max_per_run,
            ),
            "accept_template_presentation": AcceptTemplatePresentationNode(),
        }
    )
    if include_bgem3:
        bindings.update(
            {
                "bgem3": BgeMThreeNode(provider),
                "parallel_scoring": ParallelScoringNode(
                    pos_dense=positive,
                    neg_dense=negative,
                    pos_sparse=positive_sparse,
                    neg_sparse=negative_sparse,
                    role_anchors=role_vectors,
                    margin_k=20,
                ),
                "profile_semantic_evidence": ProfileSemanticEvidenceNode(
                    positive_query_vectors, negative_query_vectors
                ),
                "lexical_evidence": LexicalEvidenceNode(profiles[0]) if profiles else None,
            }
        )
    return bindings


def _build_eval_state_paths(args: argparse.Namespace) -> dict[str, str]:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    root = Path("results") / "prod_like_eval_state" / f"{args.tenant_id}_{args.user_id}_{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": str(root),
        "store_path": str(root / "store.db"),
        "job_store_path": str(root / "job_groups.db"),
        "output_path": str(root / "jobs.json"),
        "review_output_path": str(root / "review.jsonl"),
        "rejected_output_path": str(root / "rejected.jsonl"),
        "quarantine_output_path": str(root / "quarantine.jsonl"),
    }


def _is_accept(decision: Any) -> bool:
    return str(decision) == "accept"


def _is_delivered(decision: Any) -> bool:
    return _is_accept(decision)


def _default_out_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return Path(f"results/pipeline_eval_{stamp}.json")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"WARN: {path}:{line_no}: invalid JSON: {exc}", file=sys.stderr)
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _has_binary_relevance_label(row: dict[str, Any]) -> bool:
    """Keep append-only ``unknown`` repairs out of binary eval sampling."""
    value = row.get("relevant")
    return type(value) is int and value in (0, 1)


def _load_selected_item_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not ids:
        raise ValueError(f"selected item ids file is empty: {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"selected item ids file contains duplicates: {path}")
    return ids


def _select_labeled_rows(
    rows: list[dict[str, Any]],
    *,
    sample: int,
    seed: int,
    full: bool,
    selected_item_ids: Path | None,
) -> list[dict[str, Any]]:
    labelled = [row for row in rows if _has_binary_relevance_label(row)]
    if selected_item_ids is not None:
        requested = _load_selected_item_ids(selected_item_ids)
        by_id = {str(row.get("stable_id", "")): row for row in labelled}
        missing = [stable_id for stable_id in requested if stable_id not in by_id]
        if missing:
            raise ValueError(
                "selected item ids are missing or not binary-labelled: " + ", ".join(missing[:5])
            )
        return [by_id[stable_id] for stable_id in requested]
    if not full and sample > 0:
        rng = random.Random(seed)
        return rng.sample(labelled, min(sample, len(labelled)))
    return labelled


class _EvalLLMMeter:
    def __init__(self, inner: Any, *, parent: _EvalLLMMeter | None = None) -> None:
        from job_ftch.application.llm_usage import collect_llm_usage, pricing_version

        self._inner = inner
        self._parent = parent
        self._collect_llm_usage = collect_llm_usage
        self._pricing_version = pricing_version()
        self._usage_complete = True
        self._unknown_pricing_models: set[str] = set()
        self._provider_name = type(inner).__name__
        self._provider_module = type(inner).__module__
        self._is_external_billed = "heuristic" not in self._provider_module
        self._model = (
            str(getattr(inner, "_model", "unknown")) if self._is_external_billed else "heuristic"
        )
        self._method_models: dict[str, str] = {}
        if parent is not None:
            parent._method_models.setdefault("_provider:" + self._provider_name, self._model)
        self.total = {
            "calls": 0,
            "tokens_in": 0,
            "cached_tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": 0,
            "cost_usd": 0.0,
            "by_method": defaultdict(
                lambda: {
                    "calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "latency_ms": 0,
                    "cost_usd": 0.0,
                }
            ),
            "by_provider": defaultdict(
                lambda: {
                    "calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "latency_ms": 0,
                    "cost_usd": 0.0,
                }
            ),
        }
        self.by_item: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
                "cost_usd": 0.0,
                "by_method": defaultdict(
                    lambda: {
                        "calls": 0,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "latency_ms": 0,
                        "cost_usd": 0.0,
                    }
                ),
                "by_provider": defaultdict(
                    lambda: {
                        "calls": 0,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "latency_ms": 0,
                        "cost_usd": 0.0,
                    }
                ),
            }
        )

    def _record(self, method: str, usage: Any, latency_ms: int) -> None:
        self._method_models[method] = self._model
        if self._parent is not None:
            self._parent._method_models[method] = self._model
        if self._is_external_billed:
            tokens_in = int(usage.tokens_in)
            cached_tokens_in = int(usage.cached_tokens_in)
            tokens_out = int(usage.tokens_out)
            cost_usd = float(usage.cost_usd)
            usage_complete = bool(usage.requests == 1 and usage.cost_is_complete)
            self._usage_complete = self._usage_complete and usage_complete
            self._unknown_pricing_models.update(usage.unknown_pricing_models)
            if self._parent is not None:
                self._parent._usage_complete = self._parent._usage_complete and usage_complete
                self._parent._unknown_pricing_models.update(usage.unknown_pricing_models)
        else:
            tokens_in = 0
            cached_tokens_in = 0
            tokens_out = 0
            cost_usd = 0.0

        self._add_record(
            method,
            tokens_in,
            cached_tokens_in,
            tokens_out,
            latency_ms,
            cost_usd,
            self._provider_name,
        )
        if self._parent is not None:
            self._parent._add_record(
                method,
                tokens_in,
                cached_tokens_in,
                tokens_out,
                latency_ms,
                cost_usd,
                self._provider_name,
            )

    def _add_record(
        self,
        method: str,
        tokens_in: int,
        cached_tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        cost_usd: float,
        provider: str,
    ) -> None:
        self.total["calls"] += 1
        self.total["tokens_in"] += tokens_in
        self.total["cached_tokens_in"] += cached_tokens_in
        self.total["tokens_out"] += tokens_out
        self.total["latency_ms"] += latency_ms
        self.total["cost_usd"] += cost_usd
        total_method = self.total["by_method"][method]
        total_method["calls"] += 1
        total_method["tokens_in"] += tokens_in
        total_method["tokens_out"] += tokens_out
        total_method["latency_ms"] += latency_ms
        total_method["cost_usd"] += cost_usd
        provider_metrics = self.total["by_provider"][provider]
        provider_metrics["calls"] += 1
        provider_metrics["tokens_in"] += tokens_in
        provider_metrics["tokens_out"] += tokens_out
        provider_metrics["latency_ms"] += latency_ms
        provider_metrics["cost_usd"] += cost_usd

        item_id = _CURRENT_ITEM_ID.get()
        if not item_id:
            return
        item_metrics = self.by_item[item_id]
        item_metrics["calls"] += 1
        item_metrics["tokens_in"] += tokens_in
        item_metrics["tokens_out"] += tokens_out
        item_metrics["latency_ms"] += latency_ms
        item_metrics["cost_usd"] += cost_usd
        method_metrics = item_metrics["by_method"][method]
        method_metrics["calls"] += 1
        method_metrics["tokens_in"] += tokens_in
        method_metrics["tokens_out"] += tokens_out
        method_metrics["latency_ms"] += latency_ms
        method_metrics["cost_usd"] += cost_usd
        provider_metrics = item_metrics["by_provider"][provider]
        provider_metrics["calls"] += 1
        provider_metrics["tokens_in"] += tokens_in
        provider_metrics["tokens_out"] += tokens_out
        provider_metrics["latency_ms"] += latency_ms
        provider_metrics["cost_usd"] += cost_usd

    async def extract(self, text: str, schema: type[Any]) -> Any:
        started = time.perf_counter()
        with self._collect_llm_usage() as usage:
            result = await self._inner.extract(text, schema)
        self._record("extract", usage, int((time.perf_counter() - started) * 1000))
        return result

    async def classify(self, prompt: str, schema: type[Any]) -> Any:
        started = time.perf_counter()
        with self._collect_llm_usage() as usage:
            result = await self._inner.classify(prompt, schema)
        self._record("classify", usage, int((time.perf_counter() - started) * 1000))
        return result

    async def present(self, job_payload: str, schema: type[Any]) -> Any:
        started = time.perf_counter()
        with self._collect_llm_usage() as usage:
            result = await self._inner.present(job_payload, schema)
        self._record("present", usage, int((time.perf_counter() - started) * 1000))
        return result

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
    ) -> str:
        started = time.perf_counter()
        with self._collect_llm_usage() as usage:
            result = await self._inner.generate_text(
                system_prompt,
                user_prompt,
                temperature=temperature,
            )
        self._record(
            "generate_text",
            usage,
            int((time.perf_counter() - started) * 1000),
        )
        return result

    def snapshot_for_item(self, item_id: str) -> dict[str, Any]:
        metrics = self.by_item.get(item_id)
        if not metrics:
            return {
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
                "cost_usd": 0.0,
                "by_method": {},
                "by_provider": {},
            }
        return {
            "calls": int(metrics["calls"]),
            "tokens_in": int(metrics["tokens_in"]),
            "tokens_out": int(metrics["tokens_out"]),
            "latency_ms": int(metrics["latency_ms"]),
            "cost_usd": float(metrics["cost_usd"]),
            "by_method": {
                name: {
                    "calls": int(bucket["calls"]),
                    "tokens_in": int(bucket["tokens_in"]),
                    "tokens_out": int(bucket["tokens_out"]),
                    "latency_ms": int(bucket["latency_ms"]),
                    "cost_usd": float(bucket["cost_usd"]),
                }
                for name, bucket in sorted(metrics["by_method"].items())
            },
            "by_provider": {
                name: {
                    "calls": int(bucket["calls"]),
                    "tokens_in": int(bucket["tokens_in"]),
                    "tokens_out": int(bucket["tokens_out"]),
                    "latency_ms": int(bucket["latency_ms"]),
                    "cost_usd": float(bucket["cost_usd"]),
                }
                for name, bucket in sorted(metrics["by_provider"].items())
            },
            "method_models": dict(sorted(self._method_models.items())),
        }

    def summary(self) -> dict[str, Any]:
        models_used = sorted(
            {
                model
                for name, model in self._method_models.items()
                if not name.startswith("_provider:")
            }
        )
        return {
            # ``self._model`` can be the general pipeline model while a child
            # meter records relevance through a separately configured model.
            # The release contract treats ``configured_model`` as the model
            # bound to the calls in this report, not that unrelated root
            # default. Preserve the latter under an explicit field.
            "model": models_used[0]
            if len(models_used) == 1
            else ("mixed" if models_used else self._model),
            "configured_model": models_used[0]
            if len(models_used) == 1
            else ("mixed" if models_used else self._model),
            "root_configured_model": self._model,
            "models_used": models_used,
            "provider": self._provider_name,
            "provider_module": self._provider_module,
            "external_billed": self._is_external_billed,
            "calls": int(self.total["calls"]),
            "tokens_in": int(self.total["tokens_in"]),
            "cached_tokens_in": int(self.total["cached_tokens_in"]),
            "tokens_out": int(self.total["tokens_out"]),
            "latency_ms": int(self.total["latency_ms"]),
            "cost_usd": float(self.total["cost_usd"]),
            "cost_is_complete": self._usage_complete,
            "cost_pricing_version": self._pricing_version,
            "cost_unknown_models": sorted(self._unknown_pricing_models),
            "by_method": {
                name: {
                    "calls": int(bucket["calls"]),
                    "tokens_in": int(bucket["tokens_in"]),
                    "tokens_out": int(bucket["tokens_out"]),
                    "latency_ms": int(bucket["latency_ms"]),
                    "cost_usd": float(bucket["cost_usd"]),
                }
                for name, bucket in sorted(self.total["by_method"].items())
            },
            "by_provider": {
                name: {
                    "calls": int(bucket["calls"]),
                    "tokens_in": int(bucket["tokens_in"]),
                    "tokens_out": int(bucket["tokens_out"]),
                    "latency_ms": int(bucket["latency_ms"]),
                    "cost_usd": float(bucket["cost_usd"]),
                }
                for name, bucket in sorted(self.total["by_provider"].items())
            },
            "method_models": dict(sorted(self._method_models.items())),
        }


async def _build_full_pipeline(
    args: argparse.Namespace,
    *,
    bgem3_provider: object | None = None,
) -> tuple[
    Settings,
    ProfileCatalog,
    SanitizingNode[Any],
    SnapshotFilterNode | None,
    list[Stage[Any, Any]],
    Any | None,
    _EvalLLMMeter | None,
    dict[str, Any],
    list[object],
]:
    from job_ftch.application.builder import (
        _build_runtime_ontology_payload,
        build_llm,
        build_nodes,
        build_store,
        load_profile_catalog,
        load_tenant_config,
        merge_derived_ontology,
        tenant_to_settings,
    )
    from job_ftch.application.registry import create_job_group_store_with_fallback
    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.application.tenant_store import TenantStore
    from job_ftch.config import get_settings
    from job_ftch.domain.profile import ProfileCatalog
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore
    from job_ftch.infrastructure.stores.job_group_store import InMemoryJobGroupStore

    settings = get_settings()
    build_context: dict[str, Any] = {"profile_source": args.profile_source}
    cleanup_resources: list[object] = []
    store = InMemoryStore()
    job_group_store = InMemoryJobGroupStore(settings)
    ontology_store: object | None = None
    relevance_prompts: dict[str, str | None] | None = None
    derived_ontology_override: dict[str, Any] | None = None
    shared_bgem3_provider: object | None = bgem3_provider

    if args.profile_source == "tenant":
        tenant = load_tenant_config(Path(args.tenant_config))
        if args.graph:
            # Historical graph replays use their explicitly declared BGE node.
            # Do not also build the tenant's generic embedding provider: it is
            # absent from the graph and may load a second BGE-M3 model.
            tenant = tenant.model_copy(update={"embedding_enabled": False, "vector_backend": None})
        settings = tenant_to_settings(tenant, settings)
        if args.graph:
            settings = settings.model_copy(
                update={
                    "embedding_prefilter_enabled": False,
                    "bgem3_enabled": False,
                    # The selected graph owns relevance explicitly.  Keep the
                    # compatibility builder from probing its unrelated E5
                    # shot collection during an LLM-only control run.
                    "relevance_backend": "keywords",
                }
            )
        runner = TenantRunner.from_tenants(
            [tenant], base_settings=settings, bgem3_provider=shared_bgem3_provider
        )
        runtime = runner.get_runtime(args.tenant_id)
        shared_bgem3_provider = runtime.bgem3_provider
        settings = runtime.settings
        catalog, relevance_prompts = await runner._build_runtime_catalog(  # noqa: SLF001
            runtime,
            user_id=args.user_id,
        )
        ontology_store = runtime.ontology_store
        if ontology_store is not None:
            derived_ontology_override = await _build_runtime_ontology_payload(ontology_store)
        # Match TenantRunner's production composition: a V2 graph must see
        # the same live ontology-enriched catalog as the compatibility nodes.
        # This is live tenant state, never a fixture or backup reconstruction.
        catalog = merge_derived_ontology(catalog, derived_ontology_override or {})
        if runtime.llm_provider and runtime.store:
            from job_ftch.application.prompt_builder import build_relevance_prompts_for_catalog

            relevance_prompts = await build_relevance_prompts_for_catalog(
                catalog,
                runtime.llm_provider,
                runtime.store,
            )
        base_llm = runtime.llm_provider
        if args.state_mode == "production":
            # Explicit live-store replay is used for release canaries where the
            # exact frozen dataset must exercise the same persistence and
            # post-accept path as the Telegram bot. Callers are responsible for
            # clearing prior run data before selecting this mode.
            store = runtime.store
            job_group_store = runtime.job_group_store
            state_paths = {}
            cleanup_resources.append(runner)
        elif args.state_mode == "runtime":
            state_paths = _build_eval_state_paths(args)
            settings = settings.model_copy(
                update={
                    "store_backend": "sqlite",
                    "job_group_store_backend": "sqlite",
                    "store_path": Path(state_paths["store_path"]),
                    "job_store_path": Path(state_paths["job_store_path"]),
                    "output_path": Path(state_paths["output_path"]),
                    "review_output_path": Path(state_paths["review_output_path"]),
                    "rejected_output_path": Path(state_paths["rejected_output_path"]),
                    "quarantine_output_path": Path(state_paths["quarantine_output_path"]),
                }
            )
            store = await build_store(settings)
            cleanup_resources.append(store)
            if not isinstance(store, TenantStore):
                store = TenantStore(settings.tenant_id or "default", store)
            job_group_store = InMemoryJobGroupStore(settings)
            with suppress(Exception):
                job_group_store = await create_job_group_store_with_fallback(settings)
            cleanup_resources.append(job_group_store)
        else:
            state_paths = {}
            job_group_store = InMemoryJobGroupStore(settings)
        build_context.update(
            {
                "tenant_id": args.tenant_id,
                "user_id": args.user_id,
                "tenant_config": args.tenant_config,
                "state_mode": args.state_mode,
                "state_paths": state_paths,
                "runtime_ontology": {
                    "skills": len((derived_ontology_override or {}).get("skills", [])),
                    "roles": len((derived_ontology_override or {}).get("roles", [])),
                    "positive_keywords": len(
                        (derived_ontology_override or {}).get("positive_keywords", [])
                    ),
                    "negative_keywords": len(
                        (derived_ontology_override or {}).get("negative_keywords", [])
                    ),
                    "anti_patterns": len(
                        (derived_ontology_override or {}).get("anti_patterns", [])
                    ),
                },
                "runtime_ontology_hash": hashlib.sha256(
                    json.dumps(
                        derived_ontology_override or {}, sort_keys=True, default=str
                    ).encode()
                ).hexdigest(),
                "dynamic_relevance_prompts": {
                    key: bool(value) for key, value in (relevance_prompts or {}).items()
                },
                "relevance_prompt_hashes": {
                    key: hashlib.sha256(value.encode()).hexdigest()
                    for key, value in (relevance_prompts or {}).items()
                    if value
                },
                "shot_source_mode": settings.relevance_shot_source_mode,
                "shot_collections": {
                    "live_bgem3": settings.relevance_shot_collection_bgem3,
                    "seed_bgem3": settings.relevance_shot_seed_collection_bgem3,
                },
            }
        )
    else:
        settings.store_backend = "memory"
        settings.job_group_store_backend = "memory"
        settings.relevance_backend = "shots"
        if settings.filter_profile_path is None:
            settings.filter_profile_path = Path("config/profiles/ai_jobs_ru_kz.yaml")

        catalog = load_profile_catalog(settings)
        if not catalog.profiles:
            catalog = ProfileCatalog(catalog_name="empty", profiles=())
        base_llm = build_llm(settings)

    llm_meter = _EvalLLMMeter(base_llm) if base_llm is not None else None
    llm = llm_meter or base_llm
    requested_graph = _compile_requested_graph(args) if args.graph else None
    needs_shot_anchors = False
    uses_bgem3_shots = False
    if requested_graph is not None and any(
        node.node == "bgem3_embed" for node in requested_graph.spec.nodes
    ):
        # Historical graph construction is explicitly opt-in and never changes
        # the production runtime. Missing BGE/shots remain hard preflight errors.
        needs_shot_anchors = any(
            node.enabled and node.node == "parallel_scoring" for node in requested_graph.spec.nodes
        )
        uses_bgem3_shots = _graph_consumes_bgem3_shots(requested_graph)
        settings = settings.model_copy(
            update={
                "bgem3_enabled": True,
                # SemanticPrefilterNode consumes the same BGE/Qdrant scorer as
                # ParallelScoringNode. Do not silently turn BGE graphs into a
                # keyword-only control merely because they omit the legacy
                # parallel scoring stage.
                "relevance_backend": "shots" if uses_bgem3_shots else "keywords",
                "llm_relevance_max_per_run": max(1, settings.llm_relevance_max_per_run),
            }
        )
    sanitize_node, snapshot_filter, nodes = build_nodes(
        settings,
        store,
        llm,
        job_group_store,
        catalog,
        tenant_id=getattr(settings, "tenant_id", "default"),
        user_id=args.user_id if args.profile_source == "tenant" else None,
        ontology_store=ontology_store,
        relevance_prompts=relevance_prompts,
        derived_ontology_override=derived_ontology_override,
        bgem3_provider=shared_bgem3_provider,
        run_id=(
            f"eval-{int(time.time())}"
            if args.state_mode in {"runtime", "production"} or requested_graph is not None
            else None
        ),
    )
    graph_executor: Any | None = None
    if requested_graph is not None:
        from job_ftch.application.graph import RuntimeContext
        from job_ftch.application.graph.executor import GraphExecutor
        from job_ftch.application.graph.registry import bind_node_instances

        is_v2_graph = requested_graph.spec.metadata.get("graph_schema") == "v2"
        needs_historical_runtime = not is_v2_graph and any(
            node.node
            in {
                "bgem3_embed",
                "parallel_scoring",
                "presentable_text",
                "legacy_routing",
                "decision_aggregator",
            }
            for node in requested_graph.spec.nodes
        )
        historical_bgem3_nodes = {
            "bgem3_embed",
            "parallel_scoring",
            "profile_semantic_evidence",
            "lexical_evidence",
        }
        needs_historical_bgem3 = any(
            node.node in historical_bgem3_nodes for node in requested_graph.spec.nodes
        )
        graph_bgem3_provider = next(
            (
                getattr(node, "_provider", None)
                for node in nodes
                if node.__class__.__name__ == "BgeMThreeNode"
            ),
            None,
        )
        extra_bindings = (
            _build_historical_bindings(
                settings,
                catalog,
                store,
                llm,
                tenant_id=args.tenant_id,
                user_id=args.user_id,
                relevance_prompts=relevance_prompts,
                bgem3_provider=graph_bgem3_provider,
                include_shot_anchors=needs_shot_anchors,
                include_bgem3=needs_historical_bgem3,
            )
            if needs_historical_runtime
            else {}
        )
        # A YAML experiment may choose a different id for the same typed node.
        # Bind by declared node kind as a fallback, while preserving explicit ids.
        for graph_node in requested_graph.spec.nodes:
            if graph_node.id not in extra_bindings and graph_node.node in extra_bindings:
                extra_bindings[graph_node.id] = extra_bindings[graph_node.node]
        if is_v2_graph:
            from job_ftch.application.builder import build_llm, build_v2_typed_bindings
            from job_ftch.application.graph import build_v2_executor

            relevance_settings = settings.model_copy(
                update={"openai_model": settings.relevance_llm_model}
            )
            relevance_base = build_llm(relevance_settings)
            relevance_llm = (
                _EvalLLMMeter(relevance_base, parent=llm)
                if isinstance(llm, _EvalLLMMeter)
                else relevance_base
            )
            typed_bindings = build_v2_typed_bindings(
                nodes=list(nodes),
                store=store,
                catalog=catalog,
                relevance_llm=relevance_llm,
                low_threshold=settings.llm_relevance_low_threshold,
                high_threshold=settings.llm_relevance_high_threshold,
                max_per_run=settings.llm_relevance_max_per_run,
                relevance_prompts=relevance_prompts,
                tenant_id=args.tenant_id,
                graph_hash=requested_graph.graph_hash,
            )
            graph_executor = build_v2_executor(
                requested_graph,
                nodes=list(nodes),
                sanitize_node=sanitize_node,
                catalog=catalog,
                typed_bindings=typed_bindings,
                tenant_id=args.tenant_id,
                user_id=args.user_id,
                runtime_resources={
                    "active_store": store,
                    "active_llm": relevance_llm,
                    "active_shots": build_context.get("shot_source_mode"),
                },
            )
        else:
            available = bind_node_instances([sanitize_node, *nodes])
            explicit_bindings: dict[str, Any] = {}
            for graph_node in requested_graph.spec.nodes:
                if graph_node.id in extra_bindings:
                    explicit_bindings[graph_node.id] = extra_bindings[graph_node.id]
                elif graph_node.node in available:
                    explicit_bindings[graph_node.id] = available[graph_node.node]
            runtime_resources = {
                "active_profile": catalog,
                "active_store": store,
                "active_llm": llm,
                "active_shots": build_context.get("shot_source_mode"),
            }
            graph_executor = GraphExecutor.from_runtime_context(
                requested_graph,
                RuntimeContext(
                    resources=runtime_resources,
                    node_instances=explicit_bindings,
                    metadata={"tenant_id": args.tenant_id, "user_id": args.user_id},
                ),
            )
    build_context.update(
        {
            "_active_store": store,
            "_active_job_group_store": job_group_store,
            "catalog_name": catalog.catalog_name,
            "profile_count": len(catalog.profiles),
            "profile_example_counts": [
                {
                    "profile_id": profile.profile_id,
                    "positive_resume": len(profile.positive_example_texts),
                    "negative_resume": len(profile.negative_example_texts),
                    "positive_jobs": len(profile.positive_job_example_texts),
                    "negative_jobs": len(profile.negative_job_example_texts),
                    "target_roles": len(profile.target_roles),
                    "preferred_skills": len(profile.preferred_skills),
                }
                for profile in catalog.profiles
            ],
            "node_names": [
                "SanitizeNode",
                *[node.__class__.__name__ for node in nodes],
            ],
            "snapshot_filter_active": snapshot_filter is not None,
            "graph_hash": graph_executor.graph.graph_hash if graph_executor is not None else None,
        }
    )
    runtime_ontology = derived_ontology_override or {}
    build_context.setdefault(
        "runtime_ontology",
        {
            "skills": len(runtime_ontology.get("skills", [])),
            "roles": len(runtime_ontology.get("roles", [])),
            "positive_keywords": len(runtime_ontology.get("positive_keywords", [])),
            "negative_keywords": len(runtime_ontology.get("negative_keywords", [])),
            "anti_patterns": len(runtime_ontology.get("anti_patterns", [])),
        },
    )
    build_context.setdefault(
        "runtime_ontology_hash",
        hashlib.sha256(
            json.dumps(runtime_ontology, sort_keys=True, default=str).encode()
        ).hexdigest(),
    )
    return (
        settings,
        catalog,
        sanitize_node,
        snapshot_filter,
        list(nodes),
        graph_executor,
        llm_meter,
        build_context,
        cleanup_resources,
    )


async def _run_item_graph(
    row: dict[str, Any], executor: Any, llm_meter: _EvalLLMMeter | None = None
) -> dict[str, Any]:
    from job_ftch.domain import RawItem, SourceKind

    raw = RawItem.model_validate(
        {
            "stable_id": row.get("stable_id", ""),
            "source_kind": row.get("source_kind", SourceKind.DEBUG.value),
            "source_name": row.get("source_name", "eval_dataset"),
            "external_id": row.get("stable_id", ""),
            "url": row.get("url") or f"https://placeholder/{row.get('stable_id', 'missing')}",
            "text": row.get("text", "") or "",
            "created_at": row.get("created_at") or None,
            "metadata": row.get("metadata") or {},
        }
    )
    from job_ftch.application.dedup_settlement import (
        DedupSettlementCoordinator,
        SettlementOutcome,
    )

    settlement_participants = getattr(executor, "settlement_participants", None)
    participants = settlement_participants() if callable(settlement_participants) else ()
    coordinator = DedupSettlementCoordinator(participants)
    item_id = str(row.get("stable_id", ""))
    token = _CURRENT_ITEM_ID.set(item_id or None)
    try:
        reports = await executor.run_many(raw)
    except Exception:
        if item_id:
            await coordinator.settle(item_id, SettlementOutcome.RELEASE)
        raise
    finally:
        _CURRENT_ITEM_ID.reset(token)
    if item_id:
        has_deferred = any(r.status == "DEFERRED" for r in reports)
        outcome = SettlementOutcome.RELEASE if has_deferred else SettlementOutcome.COMMIT
        await coordinator.settle(item_id, outcome)
    candidates: list[dict[str, Any]] = []
    for report in reports:
        item = report.item
        decision = getattr(item, "routing_decision", None)
        metadata = getattr(item, "metadata", {}) or {}

        def _score(key: str, values: dict[str, Any] = metadata) -> float | None:
            value = values.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        scores = {
            key: _score(key)
            for key in (
                "parallel_final_score",
                "parallel_score_dense",
                "parallel_score_sparse",
                "parallel_score_role",
                "parallel_neg_sim",
                "parallel_dense_margin",
                "parallel_sparse_margin",
                "quality_score",
            )
        }
        llm_relevance = metadata.get("_llm_relevance")
        scores["llm_relevance"] = llm_relevance if isinstance(llm_relevance, dict) else None
        policy_trace = metadata.get("decision_policy_trace")
        scores["decision_policy"] = policy_trace if isinstance(policy_trace, dict) else None
        aggregator_trace = metadata.get("decision_aggregator_trace")
        scores["decision_aggregator"] = (
            aggregator_trace if isinstance(aggregator_trace, dict) else None
        )
        review_trace = metadata.get("review_resolution")
        scores["review_resolution"] = review_trace if isinstance(review_trace, dict) else None
        report_events = getattr(report, "node_events", {}) or {}
        execution_order = tuple(
            getattr(getattr(executor, "graph", None), "execution_order", ())
            or report_events.keys()
            or getattr(report, "node_stats", {}).keys()
        )
        ordered_events = [
            dict(report_events[node_id]) for node_id in execution_order if node_id in report_events
        ]
        item_llm_metrics = llm_meter.snapshot_for_item(item_id) if llm_meter else None
        method_by_node = {
            "extraction": "extract",
            "llm_relevance": "classify",
            "presentable_text": "present",
        }
        for event in ordered_events:
            method = method_by_node.get(str(event.get("node_id")))
            event["model_calls"] = (
                {method: item_llm_metrics.get("by_method", {}).get(method, {})}
                if method and item_llm_metrics
                else {}
            )
            event["model_id"] = (
                llm_meter._method_models.get(method)  # noqa: SLF001
                if method and llm_meter
                else None
            )
            event["degradation"] = (
                metadata.get("llm_relevance_degradation") if method == "classify" else None
            )
        classification = _classification_for_result(report.status == "ACCEPT", row)
        first_loss = next(
            (
                event["node_id"]
                for event in ordered_events
                if event.get("outcome") in {"drop", "error", "timeout"}
                or event.get("terminal_status") in {"REJECT", "REVIEW", "DEFERRED"}
            ),
            None,
        )
        accepted = report.status == "ACCEPT"
        candidates.append(
            {
                "candidate_id": str(getattr(item, "stable_id", row.get("stable_id", ""))),
                "exit_stage": "emitted" if accepted else (first_loss or "graph"),
                "pipeline_accepted": accepted,
                "pipeline_delivered": accepted,
                "routing_decision": str(decision) if decision else None,
                "routing_reasons": getattr(item, "review_reasons", ()),
                "error": None,
                "duration_ms": sum(
                    stat["elapsed_ms"] for stat in getattr(report, "node_stats", {}).values()
                ),
                "stage_events": [{"stage": event["node_id"], **event} for event in ordered_events],
                "diagnostics": getattr(report, "diagnostics", []),
                "classification": classification,
                "first_loss_node": first_loss,
                "decision_trace": {
                    "terminal_status": report.status,
                    "routing_decision": str(decision) if decision else None,
                    "routing_reasons": list(getattr(item, "review_reasons", ()) or ()),
                    "metadata_keys": sorted(str(key) for key in metadata),
                    "source_attribution": {
                        "source_kind": str(getattr(item, "source_kind", "unknown")),
                        "source_name": str(getattr(item, "source_name", "unknown")),
                        "source_family": str(metadata.get("source_family") or "unknown"),
                        "observation_kind": str(metadata.get("observation_kind") or "unknown"),
                    },
                    "post_type": {
                        "label": str(metadata.get("preclassified_post_type") or "unknown"),
                        "model": str(metadata.get("preclassified_model") or "unknown"),
                        "distribution": metadata.get("post_type_distribution") or {},
                        "evidence": str(metadata.get("post_type_evidence") or "unknown"),
                    },
                    "scores": scores,
                    "typed_evidence": getattr(report, "artifacts", {}).get("typed_evidence"),
                    "evidence": [
                        {
                            "claim": patch.claim,
                            "producer": patch.producer,
                            "independence_group": patch.independence_group,
                            "features": patch.features,
                            "recommendation": patch.recommendation,
                            "reliability": patch.reliability,
                            "reason": patch.reason,
                        }
                        for patch in getattr(getattr(report, "evidence", None), "patches", ())
                    ],
                },
                "llm_metrics": item_llm_metrics,
                "scores": scores,
            }
        )
    aggregate = candidates[0]
    return {**aggregate, "candidate_results": candidates}


async def _run_item_full(
    row: dict[str, Any],
    sanitize_node: SanitizingNode[Any],
    snapshot_filter: SnapshotFilterNode | None,
    nodes: list[Stage[Any, Any]],
    *,
    lf: Any | None = None,
    llm_meter: _EvalLLMMeter | None = None,
) -> dict[str, Any]:
    from job_ftch.application.drops import RawItemDropped
    from job_ftch.application.rejections import RawItemRejected
    from job_ftch.domain import RawItem, SourceKind

    raw_payload = {
        "stable_id": row.get("stable_id", ""),
        "source_kind": row.get("source_kind", SourceKind.DEBUG.value),
        "source_name": row.get("source_name", "eval_dataset"),
        "external_id": row.get("stable_id", ""),
        "url": row.get("url") or f"https://placeholder/{row.get('stable_id', 'missing')}",
        "text": row.get("text", "") or "",
        "created_at": row.get("created_at") or None,
        "metadata": row.get("metadata") or {},
    }

    item_id = str(row.get("stable_id", ""))
    stage_events: list[dict[str, Any]] = []
    token = _CURRENT_ITEM_ID.set(item_id or None)
    item_started = time.perf_counter()

    def _final_payload(**extra: Any) -> dict[str, Any]:
        payload = {
            "stage_events": stage_events,
            "duration_ms": int((time.perf_counter() - item_started) * 1000),
            "llm_metrics": llm_meter.snapshot_for_item(item_id) if llm_meter else None,
        }
        payload.update(extra)
        _CURRENT_ITEM_ID.reset(token)
        return payload

    try:
        item = RawItem.model_validate(raw_payload)
    except Exception as exc:  # noqa: BLE001
        return _final_payload(
            exit_stage="invalid",
            pipeline_accepted=False,
            pipeline_delivered=False,
            routing_decision=None,
            error=str(exc),
        )

    sanitize_started = time.perf_counter()
    try:
        current = await sanitize_node.process(item)
    except RawItemRejected as exc:
        stage_events.append(
            {
                "stage": "SanitizeNode",
                "outcome": "error",
                "duration_ms": int((time.perf_counter() - sanitize_started) * 1000),
            }
        )
        return _final_payload(
            exit_stage="SanitizeNode",
            pipeline_accepted=False,
            pipeline_delivered=False,
            routing_decision=None,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        stage_events.append(
            {
                "stage": "SanitizeNode",
                "outcome": "error",
                "duration_ms": int((time.perf_counter() - sanitize_started) * 1000),
            }
        )
        return _final_payload(
            exit_stage="SanitizeNode_Error",
            pipeline_accepted=False,
            pipeline_delivered=False,
            routing_decision=None,
            error=str(exc),
        )

    if current is None:
        stage_events.append(
            {
                "stage": "SanitizeNode",
                "outcome": "drop",
                "duration_ms": int((time.perf_counter() - sanitize_started) * 1000),
            }
        )
        return _final_payload(
            exit_stage="SanitizeNode",
            pipeline_accepted=False,
            pipeline_delivered=False,
            routing_decision=None,
            error=None,
        )

    stage_events.append(
        {
            "stage": "SanitizeNode",
            "outcome": "pass",
            "duration_ms": int((time.perf_counter() - sanitize_started) * 1000),
        }
    )

    pipeline_nodes: list[Any] = list(nodes)

    for node in pipeline_nodes:
        stage_name = node.__class__.__name__
        stage_started = time.perf_counter()
        span_cm = (
            lf.start_as_current_observation(name=stage_name, as_type="span")
            if lf
            else nullcontext()
        )
        with span_cm:
            try:
                result = await node.process(current)
            except RawItemDropped as exc:
                stage_events.append(
                    {
                        "stage": stage_name,
                        "outcome": "drop",
                        "duration_ms": int((time.perf_counter() - stage_started) * 1000),
                    }
                )
                return _final_payload(
                    exit_stage=stage_name,
                    pipeline_accepted=False,
                    pipeline_delivered=False,
                    routing_decision=None,
                    error=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                stage_events.append(
                    {
                        "stage": stage_name,
                        "outcome": "error",
                        "duration_ms": int((time.perf_counter() - stage_started) * 1000),
                    }
                )
                return _final_payload(
                    exit_stage=f"{stage_name}_Error",
                    pipeline_accepted=False,
                    pipeline_delivered=False,
                    routing_decision=None,
                    error=str(exc),
                )
            if result is None:
                stage_events.append(
                    {
                        "stage": stage_name,
                        "outcome": "drop",
                        "duration_ms": int((time.perf_counter() - stage_started) * 1000),
                    }
                )
                return _final_payload(
                    exit_stage=stage_name,
                    pipeline_accepted=False,
                    pipeline_delivered=False,
                    routing_decision=None,
                    error=None,
                )
            if getattr(node, "is_fan_out_stage", False):
                spans = tuple(result)
                if not spans:
                    return _final_payload(
                        exit_stage=stage_name,
                        pipeline_accepted=False,
                        pipeline_delivered=False,
                        routing_decision=None,
                        error=None,
                    )
                materialize = getattr(spans[0], "materialize_raw_item", None)
                if not callable(materialize):
                    return _final_payload(
                        exit_stage=f"{stage_name}_Error",
                        pipeline_accepted=False,
                        pipeline_delivered=False,
                        routing_decision=None,
                        error="fan-out output has no materialize_raw_item()",
                    )
                # The full Pipeline evaluates every child independently. The
                # compact eval envelope keeps one row per observation, so use
                # the first child as the deterministic representative and
                # retain the count for segmentation coverage diagnostics.
                current = materialize()
                stage_events[-1]["fanout_count"] = len(spans)
                continue
            stage_events.append(
                {
                    "stage": stage_name,
                    "outcome": "pass",
                    "duration_ms": int((time.perf_counter() - stage_started) * 1000),
                }
            )
            current = result

    decision = getattr(current, "routing_decision", None)
    accepted = _is_accept(decision)
    delivered = _is_delivered(decision)
    meta = getattr(current, "metadata", {}) or {}

    def _score(key: str) -> float | None:
        value = meta.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return _final_payload(
        exit_stage="emitted",
        pipeline_accepted=accepted,
        pipeline_delivered=delivered,
        routing_decision=str(decision) if decision is not None else None,
        routing_reasons=list(getattr(current, "review_reasons", ()) or ()),
        error=None,
        scores={
            "parallel_final_score": _score("parallel_final_score"),
            "bge_reranker_max_score": _score("bge_reranker_max_score"),
            "parallel_score_dense": _score("parallel_score_dense"),
            "parallel_score_role": _score("parallel_score_role"),
            "parallel_neg_sim": _score("parallel_neg_sim"),
            "parallel_dense_margin": _score("parallel_dense_margin"),
            "parallel_sparse_margin": _score("parallel_sparse_margin"),
            "quality_score": float(current.quality_score)
            if getattr(current, "quality_score", None) is not None
            else None,
            "llm_relevance_decision": (meta.get("_llm_relevance") or {}).get("decision"),
            "llm_relevance_confidence": (meta.get("_llm_relevance") or {}).get("confidence"),
            "llm_relevance_reasoning": (meta.get("_llm_relevance") or {}).get("reasoning"),
            "llm_relevance_rescued": bool((meta.get("_llm_relevance") or {}).get("rescued")),
        },
    )


def _compute_binary_metrics(results: list[dict[str, Any]], positive_key: str) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for result in results:
        gold_relevant = int(result["gold_relevant"]) == 1
        predicted_positive = bool(result[positive_key])
        if predicted_positive and gold_relevant:
            tp += 1
        elif predicted_positive and not gold_relevant:
            fp += 1
        elif not predicted_positive and gold_relevant:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _classification_for_result(predicted_positive: bool, row: dict[str, Any]) -> str:
    gold_positive = int(row.get("relevant", 0)) == 1
    if predicted_positive and gold_positive:
        return "TP"
    if predicted_positive:
        return "FP"
    if gold_positive:
        return "FN"
    return "TN"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


def _compute_binary_metrics_adjusted(
    results: list[dict[str, Any]], positive_key: str
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for result in results:
        gold_relevant = int(result["gold_relevant"]) == 1
        predicted_positive = bool(result[positive_key])
        if not predicted_positive and gold_relevant and result.get("exit_stage") == "DedupNode":
            predicted_positive = True
        if predicted_positive and gold_relevant:
            tp += 1
        elif predicted_positive and not gold_relevant:
            fp += 1
        elif not predicted_positive and gold_relevant:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _compute_funnel(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_stage: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "relevant_fn": 0, "irrel_tn": 0}
    )
    for result in results:
        stage = str(result["exit_stage"])
        by_stage[stage]["total"] += 1
        if result.get("pipeline_delivered"):
            continue
        if int(result["gold_relevant"]) == 1:
            by_stage[stage]["relevant_fn"] += 1
        else:
            by_stage[stage]["irrel_tn"] += 1
    ordered: list[dict[str, Any]] = []
    for stage, counters in sorted(by_stage.items(), key=lambda item: (-item[1]["total"], item[0])):
        dropped = counters["relevant_fn"] + counters["irrel_tn"]
        fn_pct = counters["relevant_fn"] / dropped if dropped else None
        ordered.append(
            {
                "stage": stage,
                "total": counters["total"],
                "relevant_fn": counters["relevant_fn"],
                "irrel_tn": counters["irrel_tn"],
                "fn_pct": fn_pct,
            }
        )
    return ordered


def _compute_node_stats(
    results: list[dict[str, Any]],
    ordered_stage_names: list[str],
) -> list[dict[str, Any]]:
    buckets = {
        stage: {
            "stage": stage,
            "entered": 0,
            "passed": 0,
            "dropped": 0,
            "skipped": 0,
            "errors": 0,
            "total_duration_ms": 0,
        }
        for stage in ordered_stage_names
    }
    for result in results:
        for event in result.get("stage_events", ()):
            stage = str(event.get("stage") or event.get("node_id"))
            if stage not in buckets:
                continue
            bucket = buckets[stage]
            bucket["entered"] += 1
            bucket["total_duration_ms"] += int(
                event.get("duration_ms") or event.get("elapsed_ms") or 0
            )
            outcome = str(event.get("outcome"))
            if outcome == "pass":
                bucket["passed"] += 1
            elif outcome == "drop":
                bucket["dropped"] += 1
            elif outcome.startswith("skipped_") or outcome == "deferred":
                bucket["skipped"] += 1
            else:
                bucket["errors"] += 1
    rows: list[dict[str, Any]] = []
    for stage in ordered_stage_names:
        bucket = buckets[stage]
        entered = int(bucket["entered"])
        rows.append(
            {
                "stage": stage,
                "entered": entered,
                "passed": int(bucket["passed"]),
                "dropped": int(bucket["dropped"]),
                "skipped": int(bucket["skipped"]),
                "errors": int(bucket["errors"]),
                "conversion": bucket["passed"] / entered if entered else 0.0,
                "avg_duration_ms": bucket["total_duration_ms"] / entered if entered else 0.0,
                "total_duration_ms": int(bucket["total_duration_ms"]),
            }
        )
    return rows


def _collect_runtime_node_stats(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    runtime_stats: dict[str, dict[str, Any]] = {}
    for node in nodes:
        stats = getattr(node, "stats", None)
        if isinstance(stats, dict):
            runtime_stats[node.__class__.__name__] = dict(stats)
    return runtime_stats


def _build_provenance(
    *,
    graph_path: str | None,
    graph_hash: str | None,
    ontology_hash: str | None,
    ontology_counts: dict[str, int] | None,
    state_mode: str,
    store_backend: str,
    dirty_state: bool,
    reset_info: dict[str, Any] | None,
    candidate_counts: dict[str, int] | None,
    git_rev: str | None,
    git_dirty: bool,
    dataset_path: str,
    dataset_sha256: str,
    sample_size: int,
    seed: int,
    models: dict[str, str],
    flags: dict[str, Any],
    config_resolution: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the provenance block that makes eval runs reproducible and comparable.

    The recipe_id captures the decision recipe (graph + ontology + models +
    thresholds). The comparison_key captures the experimental setup (graph +
    state_mode + store_backend + dataset + sample + seed). Two runs are
    comparable if and only if their comparison_key matches.
    """
    recipe_parts = json.dumps(
        {
            "graph_hash": graph_hash,
            "ontology_hash": ontology_hash,
            "state_mode": state_mode,
            "store_backend": store_backend,
            "models": models,
            "flags": flags,
        },
        sort_keys=True,
        default=str,
    )
    recipe_id = hashlib.sha256(recipe_parts.encode()).hexdigest()[:16]

    comparison_parts = json.dumps(
        {
            "graph_hash": graph_hash,
            "state_mode": state_mode,
            "store_backend": store_backend,
            "dataset_sha256": dataset_sha256,
            "sample_size": sample_size,
            "seed": seed,
        },
        sort_keys=True,
        default=str,
    )
    comparison_key = hashlib.sha256(comparison_parts.encode()).hexdigest()[:16]

    incomplete = False
    if candidate_counts is not None:
        incomplete = bool(candidate_counts.get("incomplete_candidate_set", False))

    return {
        "recipe_id": recipe_id,
        "comparison_key": comparison_key,
        "graph_path": graph_path,
        "graph_hash": graph_hash,
        "ontology_hash": ontology_hash,
        "ontology_counts": ontology_counts,
        "state_mode": state_mode,
        "store_backend": store_backend,
        "dirty_state": dirty_state,
        "reset": reset_info,
        "candidate_counts": candidate_counts,
        "incomplete_candidate_set": incomplete,
        "git_rev": git_rev,
        "git_dirty": git_dirty,
        "dataset_path": dataset_path,
        "dataset_sha256": dataset_sha256,
        "sample_size": sample_size,
        "seed": seed,
        "models": models,
        "flags": flags,
        "config_resolution": config_resolution,
    }


def _compare_runs(
    current_path: Path,
    compare_path: Path,
) -> int:
    """Compare two eval result files by their comparison_key.

    Prints NOT COMPARABLE with the differing fields if the keys do not match,
    and prints metric deltas if they do. Returns 0 on success, 1 on
    incomparability or missing provenance.
    """
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read current result: {exc}", file=sys.stderr)
        return 1
    try:
        baseline = json.loads(compare_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read baseline result: {exc}", file=sys.stderr)
        return 1

    prov_current = current.get("provenance")
    prov_baseline = baseline.get("provenance")

    if prov_current is None or prov_baseline is None:
        missing = []
        if prov_current is None:
            missing.append(str(current_path))
        if prov_baseline is None:
            missing.append(str(compare_path))
        print(f"NOT COMPARABLE: missing provenance in {', '.join(missing)}")
        return 1

    key_current = prov_current.get("comparison_key")
    key_baseline = prov_baseline.get("comparison_key")

    if key_current != key_baseline:
        # Identify which fields differ
        comparison_fields = [
            "graph_hash",
            "state_mode",
            "store_backend",
            "dataset_sha256",
            "sample_size",
            "seed",
        ]
        diffs = []
        for field in comparison_fields:
            val_c = prov_current.get(field)
            val_b = prov_baseline.get(field)
            if val_c != val_b:
                diffs.append(f"  {field}: {val_b!r} -> {val_c!r}")
        print("NOT COMPARABLE")
        print(f"  comparison_key: {key_baseline!r} vs {key_current!r}")
        if diffs:
            print("  differing fields:")
            for diff in diffs:
                print(f"    {diff}")
        return 1

    # Keys match - print metric deltas
    metrics_b = baseline.get("metrics_delivered", {})
    metrics_c = current.get("metrics_delivered", {})
    print(f"COMPARABLE (comparison_key={key_current})")
    for metric in ("precision", "recall", "f1"):
        val_b = float(metrics_b.get(metric, 0))
        val_c = float(metrics_c.get(metric, 0))
        delta = val_c - val_b
        direction = "+" if delta >= 0 else ""
        print(f"  {metric}: {val_b:.3f} -> {val_c:.3f} ({direction}{delta:.3f})")

    llm_b = baseline.get("llm", {})
    llm_c = current.get("llm", {})
    calls_b = int(llm_b.get("calls", 0))
    calls_c = int(llm_c.get("calls", 0))
    cost_b = float(llm_b.get("cost_usd", 0))
    cost_c = float(llm_c.get("cost_usd", 0))
    print(f"  calls: {calls_b} -> {calls_c} ({calls_c - calls_b:+d})")
    print(f"  cost_usd: {cost_b:.4f} -> {cost_c:.4f} ({cost_c - cost_b:+.4f})")
    return 0


async def _reset_production_state(
    store: Any,
    job_group_store: Any,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Clear tenant run state before a controlled eval.

    Returns the counts of deleted rows per table for provenance.
    Never touches jf_ontology_* tables.
    """
    clear = getattr(store, "clear_run_artifacts", None)
    if clear is None:
        # TenantStore wraps clear_run_artifacts
        tenant_store = store
        clear = getattr(tenant_store, "clear_run_artifacts", None)
    if clear is None:
        raise RuntimeError(
            f"Store backend {type(store).__name__} does not support clear_run_artifacts"
        )

    # The clear_run_artifacts on TenantStore takes no args (it uses self._tenant_id).
    # The raw store takes (prefix, tenant_id).
    import inspect as _inspect

    sig = _inspect.signature(clear)
    if len(sig.parameters) == 0:
        counts = await clear()
    else:
        counts = await clear(f"{tenant_id}:", tenant_id)

    # Clear job group store
    jg_clear = getattr(job_group_store, "clear", None)
    job_groups_deleted = 0
    if callable(jg_clear):
        job_groups_deleted = await jg_clear()
    counts["job_groups"] = job_groups_deleted

    return {
        "performed": True,
        "counts": counts,
        "at": datetime.now(UTC).isoformat(),
    }


async def _count_source_snapshots(store: Any, *, tenant_id: str) -> int | None:
    """Return tenant snapshot count for stores used by eval, if queryable."""
    wrapped = getattr(store, "_store", store)
    count = getattr(store, "count_source_snapshots", None)
    if callable(count):
        return int(await count(tenant_id))
    count = getattr(wrapped, "count_source_snapshots", None)
    if callable(count):
        return int(await count(tenant_id))

    ensure_initialized = getattr(wrapped, "_ensure_initialized", None)
    if not callable(ensure_initialized):
        return None

    conn_or_pool = await ensure_initialized()
    acquire = getattr(conn_or_pool, "acquire", None)
    if callable(acquire):
        async with conn_or_pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM jf_source_snapshots WHERE tenant_id = $1",
                tenant_id,
            )
            return int(value or 0)

    execute = getattr(conn_or_pool, "execute", None)
    if callable(execute):
        async with conn_or_pool.execute(
            "SELECT COUNT(*) FROM jf_source_snapshots WHERE tenant_id = ?",
            (tenant_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0

    return None


def _expected_candidate_count(args: argparse.Namespace) -> int | None:
    if args.expected_candidates is not None:
        return args.expected_candidates
    if args.state_mode == "production" and args.sample == 400 and args.seed == 42:
        return 486
    return None


def _runtime_json_context(build_context: dict[str, Any]) -> dict[str, Any]:
    """Drop live handles that are useful during the run but not serializable."""
    return {key: value for key, value in build_context.items() if not key.startswith("_")}


def _missing_provenance_fields(provenance: dict[str, Any]) -> list[str]:
    required = (
        "recipe_id",
        "comparison_key",
        "graph_hash",
        "ontology_hash",
        "state_mode",
        "store_backend",
        "dirty_state",
        "candidate_counts",
        "incomplete_candidate_set",
        "git_rev",
        "git_dirty",
        "dataset_path",
        "dataset_sha256",
        "sample_size",
        "seed",
        "models",
        "flags",
    )
    return [field for field in required if provenance.get(field) is None]


def _warm_bgem3_provider(provider: Any | None) -> dict[str, Any] | None:
    """Load BGE-M3 before timing items so p95 reflects steady-state latency."""
    if provider is None:
        return None
    started = time.perf_counter()
    provider.encode(
        "vacancy title: evaluation warmup\ndescription: initialize BGE-M3 runtime",
        max_length=1024,
        return_sparse=True,
    )
    return {
        "excluded_from_item_latency": True,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _compute_confusion_sets(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {"tp": [], "fp": [], "fn": [], "tn": []}
    for result in results:
        gold_relevant = int(result["gold_relevant"]) == 1
        predicted_positive = bool(result["pipeline_delivered"])
        if predicted_positive and gold_relevant:
            bucket = "tp"
        elif predicted_positive and not gold_relevant:
            bucket = "fp"
        elif not predicted_positive and gold_relevant:
            bucket = "fn"
        else:
            bucket = "tn"
        buckets[bucket].append(
            {
                "stable_id": result.get("stable_id"),
                "source_kind": result.get("source_kind"),
                "source_name": result.get("source_name"),
                "exit_stage": result.get("exit_stage"),
                "routing_decision": result.get("routing_decision"),
                "error": result.get("error"),
                "duration_ms": result.get("duration_ms"),
                "scores": result.get("scores"),
            }
        )
    return buckets


def _slice_metrics(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        buckets[str(result.get(key) or "unknown")].append(result)
    return {
        name: (
            _compute_binary_metrics(rows, "pipeline_accepted")
            | {
                "items": len(rows),
                "delivered_precision": _compute_binary_metrics(rows, "pipeline_delivered")[
                    "precision"
                ],
                "delivered_recall": _compute_binary_metrics(rows, "pipeline_delivered")["recall"],
                "delivered_f1": _compute_binary_metrics(rows, "pipeline_delivered")["f1"],
            }
        )
        for name, rows in sorted(buckets.items())
    }


def _print_report(
    results: list[dict[str, Any]],
    accept_metrics: dict[str, Any],
    accept_metrics_adjusted: dict[str, Any],
    delivered_metrics: dict[str, Any],
    funnel: list[dict[str, Any]],
    node_stats: list[dict[str, Any]],
    elapsed_s: float,
    llm_summary: dict[str, Any] | None,
    runtime_node_stats: dict[str, dict[str, Any]] | None = None,
) -> None:
    relevant = sum(1 for row in results if int(row["gold_relevant"]) == 1)
    not_relevant = len(results) - relevant
    emitted = [row for row in results if row["exit_stage"] == "emitted"]
    routing_counts: dict[str, int] = defaultdict(int)
    for row in emitted:
        routing_counts[str(row.get("routing_decision") or "unknown")] += 1

    print("\n=== PIPELINE EVAL RESULTS ===")
    print(f"Items:     {len(results)}  (relevant={relevant}, not_relevant={not_relevant})")
    print("\nACCEPT-only metrics (strict precision target):")
    print(f"Precision: {accept_metrics['precision']:.3f}")
    print(
        f"Recall:    {accept_metrics['recall']:.3f} (Adjusted: {accept_metrics_adjusted['recall']:.3f})"
    )
    print(f"F1:        {accept_metrics['f1']:.3f} (Adjusted: {accept_metrics_adjusted['f1']:.3f})")
    print("\nDELIVERED metrics:")
    print(f"Precision: {delivered_metrics['precision']:.3f}")
    print(f"Recall:    {delivered_metrics['recall']:.3f}")
    print(f"F1:        {delivered_metrics['f1']:.3f}")
    print("\n=== ROUTING DECISIONS AMONG EMITTED ITEMS ===")
    print(f"Emitted: {len(emitted)}")
    print(f"ACCEPT:  {routing_counts['accept']}")
    print(f"REVIEW:  {routing_counts['review']} (should be 0 in binary relevance mode)")
    print(f"REJECT:  {routing_counts['reject']}")

    print("\n=== NODE FUNNEL (where relevant vacancies are dropped) ===")
    print(f"{'Node':24} {'Total_dropped':>13} {'Relevant_FN':>12} {'Irrel_TN':>10} {'FN%':>8}")
    for row in funnel:
        if row["stage"] == "emitted":
            fn_pct = "-"
        else:
            fn_pct = f"{row['fn_pct']:.0%}" if row["fn_pct"] is not None else "-"
        print(
            f"{row['stage'][:24]:24} {row['total']:13} {row['relevant_fn']:12} "
            f"{row['irrel_tn']:10} {fn_pct:>8}"
        )

    print("\n=== NODE CONVERSION ===")
    print(
        f"{'Node':24} {'Entered':>8} {'Passed':>8} {'Dropped':>8} "
        f"{'Skipped':>8} {'Errors':>8} {'Conv%':>8} {'Avg_ms':>10}"
    )
    for row in node_stats:
        conversion_pct = f"{row['conversion']:.0%}"
        print(
            f"{row['stage'][:24]:24} {row['entered']:8} {row['passed']:8} {row['dropped']:8} "
            f"{row['skipped']:8} {row['errors']:8} {conversion_pct:>8} "
            f"{row['avg_duration_ms']:10.1f}"
        )

    print("\n=== TIME / COST ===")
    print(f"Wall time: {elapsed_s:.2f}s")
    print(f"Avg per item: {(elapsed_s / len(results)) * 1000:.1f} ms")
    if llm_summary is not None:
        print(
            f"LLM calls={llm_summary['calls']} tokens_in={llm_summary['tokens_in']} "
            f"tokens_out={llm_summary['tokens_out']} cost_usd~{llm_summary['cost_usd']:.4f}"
        )
    stats_by_node = runtime_node_stats or {}
    relevance_stats = stats_by_node.get("LLMRelevanceEvidenceNode") or stats_by_node.get(
        "LLMRelevanceClassificationNode"
    )
    if relevance_stats:
        print(
            "LLM relevance: "
            f"calls={relevance_stats.get('llm_relevance_calls', 0)} "
            f"cache_hits={relevance_stats.get('llm_relevance_cache_hits', 0)} "
            f"fallback={relevance_stats.get('llm_relevance_fallback', 0)} "
            f"failures={relevance_stats.get('llm_relevance_failures', 0)}"
        )


async def main(*, bgem3_provider: object | None = None) -> int:
    args = parse_args()

    # Standalone comparison mode: compare two existing result files without
    # running an eval. Invoked as:
    #   --out FILE_A --compare-to FILE_B
    # or shorthand (both files as positional-like args):
    if args.compare_to and args.out:
        out = Path(args.out)
        compare = Path(args.compare_to)
        if out.exists() and compare.exists():
            return _compare_runs(out, compare)
        if not out.exists():
            print(f"ERROR: --out file not found: {out}", file=sys.stderr)
            return 1
        if not compare.exists():
            print(f"ERROR: --compare-to file not found: {compare}", file=sys.stderr)
            return 1

    if args.runtime:
        from scripts.run_bot_ingest import _select_runtime

        _select_runtime(args.runtime)
    graph = _compile_requested_graph(args) if args.graph else None
    if args.compile_only or args.print_graph:
        if graph is None:
            raise ValueError("--graph is required with --compile-only/--print-graph")
        from job_ftch.application.graph.inspection import print_graph

        print(print_graph(graph, "json"))
        return 0
    if args.preflight_only:
        if not args.graph:
            raise ValueError("--preflight-only requires --graph")
        from job_ftch.application.builder import build_llm, load_tenant_config, tenant_to_settings
        from job_ftch.application.graph.manifests import build_run_manifest
        from job_ftch.config import get_settings

        if graph is None:
            raise ValueError("--preflight-only requires --graph")
        tenant = load_tenant_config(Path(args.tenant_config))
        settings = tenant_to_settings(tenant, get_settings())
        relevance_settings = settings.model_copy(
            update={"openai_model": settings.relevance_llm_model}
        )
        relevance_provider = build_llm(relevance_settings)
        actual_model = str(
            getattr(relevance_provider, "_model", getattr(relevance_provider, "model", ""))
        )
        if actual_model != settings.relevance_llm_model:
            raise RuntimeError(
                "Configured relevance model does not match the bound provider model: "
                f"configured={settings.relevance_llm_model!r}, actual={actual_model!r}"
            )
        if any(node.node == "bgem3_embed" for node in graph.spec.nodes):
            try:
                from job_ftch.infrastructure.embeddings.bgem3 import BgeMThreeProvider

                BgeMThreeProvider(settings.bgem3_model)
            except ImportError as exc:
                raise RuntimeError("historical graph requires job-ftch[bgem3]") from exc
        shots = (
            _preflight_shots(settings, tenant_id=args.tenant_id, user_id=args.user_id)
            if _graph_consumes_bgem3_shots(graph)
            else {"status": "not_consumed"}
        )
        manifest = build_run_manifest(
            graph=graph,
            dataset=args.dataset,
            seed=args.seed,
            selected_item_ids=[],
            runtime={
                "tenant_id": args.tenant_id,
                "user_id": args.user_id,
                "tenant_config": args.tenant_config,
                "profile_source": args.profile_source,
                "state_mode": args.state_mode,
            },
            shot_snapshot=shots,
        )
        manifest["mode"] = "preflight_only"
        manifest["relevance_provider"] = {
            "provider": relevance_provider.__class__.__name__,
            "configured_model": settings.relevance_llm_model,
            "actual_model": actual_model,
        }
        # Relevance prefilter preflight: check if the graph includes the
        # prefilter node and whether the model artifact is available.
        prefilter_in_graph = any(node.node == "tfidf_logreg_prefilter" for node in graph.spec.nodes)
        if prefilter_in_graph:
            from job_ftch.nodes.tfidf_logreg_prefilter import TfidfLogregRelevancePrefilterNode

            prefilter_params = next(
                (node.params for node in graph.spec.nodes if node.node == "tfidf_logreg_prefilter"),
                {},
            )
            prefilter_node = TfidfLogregRelevancePrefilterNode(
                model_path=prefilter_params.get(
                    "model_path", "fixtures/prefilter/tfidf_logreg_v1.json"
                ),
                threshold=prefilter_params.get("threshold", 0.30),
            )
            manifest["relevance_prefilter"] = prefilter_node.preflight_status()
            if prefilter_node._degraded is not None and not args.allow_degraded_prefilter:
                print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
                print(
                    f"ERROR: relevance prefilter is degraded "
                    f"({prefilter_node._degraded}). "
                    f"Pass --allow-degraded-prefilter to proceed anyway.",
                    file=sys.stderr,
                )
                return 1
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
        return 0
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"ERROR: dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    rows = _select_labeled_rows(
        _read_jsonl(dataset_path),
        sample=args.sample,
        seed=args.seed,
        full=args.full,
        selected_item_ids=args.selected_item_ids,
    )
    if not rows:
        print("ERROR: no labeled rows found in dataset.", file=sys.stderr)
        return 1

    # The explicit ID file is the replay contract.  Fingerprint its exact bytes:
    # this detects both ordering drift and newline/encoding edits before runtime
    # dependencies are constructed.
    selected_item_ids = (
        _load_selected_item_ids(args.selected_item_ids)
        if args.selected_item_ids is not None
        else [str(row.get("stable_id", "")) for row in rows]
    )
    selected_item_ids_hash = (
        hashlib.sha256(args.selected_item_ids.read_bytes()).hexdigest()
        if args.selected_item_ids is not None
        else hashlib.sha256("\n".join(selected_item_ids).encode()).hexdigest()
    )
    if (
        args.expected_selected_item_ids_hash
        and selected_item_ids_hash != args.expected_selected_item_ids_hash
    ):
        raise RuntimeError(
            "selected item ids drifted: "
            f"{args.expected_selected_item_ids_hash} -> {selected_item_ids_hash}"
        )
    if args.validate_selection_only:
        print(
            json.dumps(
                {
                    "mode": "validate_selection_only",
                    "dataset_sha256": dataset_hash(dataset_path),
                    "selected_item_count": len(selected_item_ids),
                    "selected_item_ids_hash": selected_item_ids_hash,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Loaded {len(rows)} labeled items from {dataset_path}")

    (
        settings,
        catalog,
        sanitize_node,
        snapshot_filter,
        nodes,
        graph_executor,
        llm_meter,
        build_context,
        cleanup_resources,
    ) = await _build_full_pipeline(args, bgem3_provider=bgem3_provider)
    active_store = build_context.get("_active_store")
    active_job_group_store = build_context.get("_active_job_group_store")

    # Production state guard: require explicit reset or dirty-state acknowledgment
    reset_info: dict[str, Any] | None = None
    dirty_state = False
    if args.state_mode == "production":
        if args.reset_before_run:
            if active_store is None or active_job_group_store is None:
                print(
                    "ERROR: production reset requires active store handles, "
                    "but the pipeline build did not expose them.",
                    file=sys.stderr,
                )
                await _close_eval_resources(cleanup_resources)
                return 1
            reset_info = await _reset_production_state(
                active_store,
                active_job_group_store,
                tenant_id=args.tenant_id,
            )
            print("\n=== PRODUCTION STATE RESET ===")
            for table, count in sorted(reset_info["counts"].items()):
                print(f"  {table}: {count} rows deleted")
            print(f"  reset_at: {reset_info['at']}")
            remaining_snapshots = await _count_source_snapshots(
                active_store,
                tenant_id=args.tenant_id,
            )
            if remaining_snapshots is None:
                reset_info["source_snapshots_after_reset"] = None
                print(
                    "ERROR: could not verify jf_source_snapshots count after reset.",
                    file=sys.stderr,
                )
                await _close_eval_resources(cleanup_resources)
                return 1
            reset_info["source_snapshots_after_reset"] = remaining_snapshots
            print(f"  jf_source_snapshots_after_reset: {remaining_snapshots}")
            if remaining_snapshots != 0:
                print(
                    "ERROR: jf_source_snapshots is not empty after reset; "
                    "controlled production eval would be incomplete.",
                    file=sys.stderr,
                )
                await _close_eval_resources(cleanup_resources)
                return 1
        elif args.allow_dirty_state:
            dirty_state = True
            print(
                "\nWARNING: --allow-dirty-state: production state was not reset. "
                "Provenance will record dirty_state=true.",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: --state-mode production requires --reset-before-run to ensure "
                "a clean measurement baseline. Leftover jf_source_snapshots silently "
                "remove candidates before they reach the graph, making the eval "
                "non-reproducible. Pass --reset-before-run to clear tenant run state, "
                "or --allow-dirty-state to proceed with a dirty_state=true provenance.",
                file=sys.stderr,
            )
            await _close_eval_resources(cleanup_resources)
            return 1

    experiment_manifest: dict[str, Any] = {
        "dataset_sha256": dataset_hash(dataset_path),
        "seed": args.seed,
        "selected_item_count": len(selected_item_ids),
        "selected_item_ids_hash": selected_item_ids_hash,
        "source_revision": _source_revision(),
        "state_mode": args.state_mode,
        "profile_source": args.profile_source,
    }
    if graph_executor is not None:
        experiment_manifest["graph"] = graph_executor.graph.as_dict()
        if _graph_consumes_bgem3_shots(graph):
            graph_bgem3_provider = next(
                (
                    getattr(node, "_provider", None)
                    for node in nodes
                    if node.__class__.__name__ == "BgeMThreeNode"
                ),
                None,
            )
            warmup = _warm_bgem3_provider(graph_bgem3_provider)
            if warmup is not None:
                experiment_manifest["bgem3_warmup"] = warmup
            experiment_manifest["shots"] = _preflight_shots(
                settings,
                tenant_id=args.tenant_id,
                user_id=args.user_id,
                bgem3_provider=graph_bgem3_provider,
            )
            if (
                args.expected_shot_snapshot_hash
                and experiment_manifest["shots"]["snapshot_hash"]
                != args.expected_shot_snapshot_hash
            ):
                raise RuntimeError(
                    "shot snapshot drifted: "
                    f"{args.expected_shot_snapshot_hash} -> {experiment_manifest['shots']['snapshot_hash']}"
                )
        else:
            experiment_manifest["shots"] = {"status": "not_consumed"}
    print(f"Profile catalog: {catalog.catalog_name} ({len(catalog.profiles)} profiles)")
    print(f"Profile source: {build_context.get('profile_source')}")
    if build_context.get("tenant_id"):
        print(
            f"Tenant runtime: tenant={build_context.get('tenant_id')} "
            f"user={build_context.get('user_id')}"
        )
        print(f"Runtime ontology: {build_context.get('runtime_ontology')}")
        print(f"Profile examples: {build_context.get('profile_example_counts')}")
    print(f"Relevance backend: {settings.relevance_backend}")
    print(f"Shot backend: {settings.relevance_shot_backend}")
    print(f"LLM backend: {settings.llm_backend}")
    print(f"State mode: {args.state_mode}")
    print(f"Pipeline nodes: {len(nodes) + 1}")

    run_name = args.run_name or f"pipeline_eval_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}"
    eval_tracer: Any | None = None
    observability_context_tokens: dict[str, Any] = {}
    if not args.no_langfuse:
        from opentelemetry import trace
        from structlog.contextvars import bind_contextvars

        # Eval is a release gate for the production graph. Runtime mode keeps
        # persistence isolated, but must export the same Langfuse traces and
        # OpenObserve logs/metrics as the bot so telemetry regressions cannot
        # hide behind an otherwise green offline report.
        from job_ftch.infrastructure.observability import configure_observability

        configure_observability(settings)
        eval_tracer = trace.get_tracer("job_ftch.eval")
        observability_context_tokens = bind_contextvars(
            source_run_id=run_name,
            tenant_id=args.tenant_id,
            graph_hash=(
                graph_executor.graph.graph_hash if graph_executor is not None else "legacy"
            ),
            source_id=args.dataset_name,
            source_kind="eval_dataset",
            source_name=args.dataset_name,
        )
        logging.getLogger("job_ftch.eval").info("pipeline_eval_started")

    results: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    last_completed_stable_id: str | None = None

    def invalid_payload(reason: str, completed_parent_items: int, cleanup: str) -> dict[str, Any]:
        return {
            "status": "INVALID",
            "run_id": run_name,
            "invalid_reason": reason,
            "last_completed_stable_id": last_completed_stable_id,
            "completed_parent_items": completed_parent_items,
            "expected_parent_items": len(rows),
            "elapsed_seconds": time.perf_counter() - started_at,
            "graph_hash": graph_executor.graph.graph_hash
            if graph_executor is not None
            else "legacy",
            "relevance_llm_model": settings.relevance_llm_model,
            "experiment_manifest": experiment_manifest,
            "cleanup": cleanup,
        }

    for index, row in enumerate(rows, 1):
        if time.perf_counter() - started_at > args.run_timeout_seconds:
            invalid_path = args.invalid_out or Path(args.out or "pipeline_eval.invalid.json")
            await _close_eval_resources(cleanup_resources)
            if observability_context_tokens:
                from structlog.contextvars import reset_contextvars

                reset_contextvars(**observability_context_tokens)
            _write_invalid_report(
                invalid_path,
                invalid_payload("run_timeout", index - 1, "resources_closed"),
            )
            return 2
        obs_ctx = (
            eval_tracer.start_as_current_span("eval.item")
            if eval_tracer is not None
            else nullcontext(None)
        )

        with obs_ctx as eval_item_span:
            if eval_item_span is not None:
                eval_item_span.set_attribute("job_ftch.eval.run_name", run_name)
                eval_item_span.set_attribute("job_ftch.eval.dataset", args.dataset_name)
                eval_item_span.set_attribute("job_ftch.eval.stable_id", str(row.get("stable_id")))
                eval_item_span.set_attribute(
                    "job_ftch.source_kind", str(row.get("source_kind") or "unknown")
                )
                eval_item_span.set_attribute(
                    "job_ftch.source_name", str(row.get("source_name") or "unknown")
                )
            try:
                run_result = await asyncio.wait_for(
                    _run_item_graph(row, graph_executor, llm_meter)
                    if graph_executor is not None
                    else _run_item_full(
                        row, sanitize_node, snapshot_filter, nodes, lf=None, llm_meter=llm_meter
                    ),
                    timeout=args.item_timeout_seconds,
                )
            except (TimeoutError, asyncio.CancelledError, Exception) as exc:
                invalid_path = args.invalid_out or Path(args.out or "pipeline_eval.invalid.json")
                await _close_eval_resources(cleanup_resources)
                if observability_context_tokens:
                    from structlog.contextvars import reset_contextvars

                    reset_contextvars(**observability_context_tokens)
                _write_invalid_report(
                    invalid_path,
                    invalid_payload(type(exc).__name__, index - 1, "resources_closed"),
                )
                raise
            exit_stage = str(run_result["exit_stage"])
            pipeline_accepted = bool(run_result["pipeline_accepted"])
            pipeline_delivered = bool(run_result.get("pipeline_delivered", pipeline_accepted))
            routing_decision = run_result.get("routing_decision")
            gold_relevant = int(row["relevant"])
            relevance_match = pipeline_delivered == (gold_relevant == 1)

            if eval_item_span is not None:
                eval_item_span.set_attribute("job_ftch.eval.exit_stage", exit_stage)
                eval_item_span.set_attribute("job_ftch.eval.pipeline_accepted", pipeline_accepted)
                eval_item_span.set_attribute("job_ftch.eval.pipeline_delivered", pipeline_delivered)
                eval_item_span.set_attribute(
                    "job_ftch.eval.routing_decision", str(routing_decision or "unknown")
                )
                eval_item_span.set_attribute("job_ftch.eval.gold_relevant", gold_relevant)
                eval_item_span.set_attribute("job_ftch.eval.relevance_match", relevance_match)

        for candidate_result in run_result.get("candidate_results", [run_result]):
            results.append(
                {
                    "stable_id": candidate_result.get("candidate_id", row.get("stable_id")),
                    "parent_stable_id": row.get("stable_id"),
                    "source_kind": row.get("source_kind"),
                    "source_name": row.get("source_name"),
                    "gold_relevant": gold_relevant,
                    "pipeline_accepted": candidate_result["pipeline_accepted"],
                    "pipeline_delivered": candidate_result.get(
                        "pipeline_delivered", candidate_result["pipeline_accepted"]
                    ),
                    "routing_decision": candidate_result.get("routing_decision"),
                    "routing_reasons": candidate_result.get("routing_reasons"),
                    "exit_stage": candidate_result["exit_stage"],
                    "error": candidate_result.get("error"),
                    "duration_ms": candidate_result.get("duration_ms"),
                    "stage_events": candidate_result.get("stage_events"),
                    "llm_metrics": candidate_result.get("llm_metrics"),
                    "scores": candidate_result.get("scores"),
                    "diagnostics": candidate_result.get("diagnostics"),
                    "decision_trace": candidate_result.get("decision_trace"),
                    "classification": candidate_result.get("classification"),
                    "first_loss_node": candidate_result.get("first_loss_node"),
                }
            )
        last_completed_stable_id = str(row.get("stable_id") or "")
        if index % max(1, args.progress_every) == 0 or index == len(rows):
            elapsed = time.perf_counter() - started_at
            calls = llm_meter.summary()["calls"] if llm_meter is not None else 0
            print(f"  processed {index}/{len(rows)} elapsed={elapsed:.1f}s calls={calls}")

    if snapshot_filter is not None:
        with suppress(Exception):
            await snapshot_filter.save_and_purge()

    accept_metrics = _compute_binary_metrics(results, "pipeline_accepted")
    accept_metrics_adjusted = _compute_binary_metrics_adjusted(results, "pipeline_accepted")
    delivered_metrics = _compute_binary_metrics(results, "pipeline_delivered")
    funnel = _compute_funnel(results)
    if graph_executor is not None:
        # Graph runs must report the graph's actual node ids.  The builder
        # inventory contains compatibility/runtime nodes that may not be in
        # the selected graph and therefore produced misleading zero rows.
        stage_names = list(graph_executor.graph.execution_order)
    else:
        stage_names = ["SanitizeNode"]
        stage_names.extend(n.__class__.__name__ for n in nodes)
    node_stats = _compute_node_stats(results, stage_names)
    runtime_nodes = (
        list(graph_executor.factories.values()) if graph_executor is not None else list(nodes)
    )
    runtime_node_stats = _collect_runtime_node_stats(runtime_nodes)
    confusion_sets = _compute_confusion_sets(results)
    by_source_kind = _slice_metrics(results, "source_kind")
    by_source_name = _slice_metrics(results, "source_name")
    elapsed_s = time.perf_counter() - started_at
    llm_summary = llm_meter.summary() if llm_meter is not None else None
    item_latencies = [float(row.get("duration_ms") or 0.0) for row in results]
    first_loss_counts: dict[str, int] = defaultdict(int)
    for row in results:
        if row.get("first_loss_node"):
            first_loss_counts[str(row["first_loss_node"])] += 1
    _print_report(
        results,
        accept_metrics,
        accept_metrics_adjusted,
        delivered_metrics,
        funnel,
        node_stats,
        elapsed_s,
        llm_summary,
        runtime_node_stats,
    )

    slice_checks = _evaluate_slice_requirements(args.require_slice, by_source_kind)
    if slice_checks:
        print("\n=== SLICE GATES ===")
        for check in slice_checks:
            status = "PASS" if check["passed"] else "FAIL"
            print(f"  [{status}] {check['requirement']}: {check['reason']}")

    print("\n=== BY source_kind ===")
    for name, slice_metrics in by_source_kind.items():
        print(
            f"  {name}: items={slice_metrics['items']} "
            f"P={slice_metrics['precision']:.3f} R={slice_metrics['recall']:.3f} F1={slice_metrics['f1']:.3f}"
        )
    print("\n=== BY source_name ===")
    for name, slice_metrics in list(by_source_name.items())[:20]:
        print(
            f"  {name}: items={slice_metrics['items']} "
            f"P={slice_metrics['precision']:.3f} R={slice_metrics['recall']:.3f} F1={slice_metrics['f1']:.3f}"
        )

    # Build provenance block
    ds_hash = dataset_hash(dataset_path)
    source_rev = _source_revision()
    current_graph_hash = graph_executor.graph.graph_hash if graph_executor is not None else None
    ontology_hash_val = build_context.get("runtime_ontology_hash")
    ontology_counts_val = build_context.get("runtime_ontology")
    config_resolution: dict[str, dict[str, str]] | None = None
    try:
        from job_ftch.application.config_resolution import (
            resolution_for_provenance,
            resolve_config_layers,
        )
        from job_ftch.config import _resolve_runtime_config_files

        runtime_config_paths = [Path(path) for path in (_resolve_runtime_config_files() or ())]
        config_resolution = resolution_for_provenance(
            resolve_config_layers(
                tenant_path=Path(args.tenant_config),
                runtime_paths=runtime_config_paths,
                settings=settings,
            )
        )
    except Exception as exc:
        config_resolution = {
            "_error": {
                "layer": "config_resolution",
                "value": f"{exc.__class__.__name__}: {exc}",
            }
        }

    # Candidate counts for provenance
    parent_items = len(rows)
    total_candidates = len(results)
    expected_candidates = _expected_candidate_count(args)
    incomplete_candidate_set = (
        expected_candidates is not None and total_candidates != expected_candidates
    )
    candidate_counts = {
        "parent_items": parent_items,
        "total_candidates": total_candidates,
        "expected_total_candidates": expected_candidates,
        "incomplete_candidate_set": incomplete_candidate_set,
    }
    # Count by source_kind
    kind_counts: dict[str, int] = defaultdict(int)
    for r in results:
        kind_counts[str(r.get("source_kind") or "unknown")] += 1
    candidate_counts["by_source_kind"] = dict(sorted(kind_counts.items()))

    provenance = _build_provenance(
        graph_path=args.graph,
        graph_hash=current_graph_hash,
        ontology_hash=ontology_hash_val,
        ontology_counts=ontology_counts_val,
        state_mode=args.state_mode,
        store_backend=settings.store_backend,
        dirty_state=dirty_state,
        reset_info=reset_info,
        candidate_counts=candidate_counts,
        git_rev=source_rev.get("commit"),
        git_dirty=bool(source_rev.get("dirty")),
        dataset_path=str(dataset_path),
        dataset_sha256=ds_hash,
        sample_size=args.sample if args.sample > 0 else parent_items,
        seed=args.seed,
        models={
            "openai_model": settings.openai_model,
            "relevance_llm_model": settings.relevance_llm_model,
        },
        flags={
            "embedding_prefilter_enabled": settings.embedding_prefilter_enabled,
            "bgem3_enabled": settings.bgem3_enabled,
            "relevance_backend": settings.relevance_backend,
            "llm_presentable_enabled": settings.llm_presentable_enabled,
        },
        config_resolution=config_resolution,
    )
    missing_provenance = _missing_provenance_fields(provenance)
    if missing_provenance:
        print(
            "ERROR: incomplete eval provenance: " + ", ".join(missing_provenance),
            file=sys.stderr,
        )
        await _close_eval_resources(cleanup_resources)
        return 1

    out_path = Path(args.out) if args.out else _default_out_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_payload = {
        "run_at": datetime.now(UTC).isoformat(),
        "trace_schema_version": "1.0",
        "dataset": str(dataset_path),
        "dataset_name": args.dataset_name,
        "run_name": run_name,
        "provenance": provenance,
        "experiment_manifest": experiment_manifest,
        "settings": {
            "profile_source": args.profile_source,
            "state_mode": args.state_mode,
            "tenant_id": getattr(settings, "tenant_id", None),
            "store_backend": settings.store_backend,
            "job_group_store_backend": settings.job_group_store_backend,
            "llm_backend": settings.llm_backend,
            "openai_model": settings.openai_model,
            "relevance_llm_model": settings.relevance_llm_model,
            "embedding_prefilter_enabled": settings.embedding_prefilter_enabled,
            "bgem3_enabled": settings.bgem3_enabled,
            "relevance_backend": settings.relevance_backend,
            "relevance_shot_backend": settings.relevance_shot_backend,
            "relevance_shot_source_mode": settings.relevance_shot_source_mode,
            "relevance_shot_collection_bgem3": settings.relevance_shot_collection_bgem3,
            "relevance_shot_seed_collection_bgem3": settings.relevance_shot_seed_collection_bgem3,
            "relevance_shot_threshold": settings.relevance_shot_threshold,
            "llm_presentable_enabled": settings.llm_presentable_enabled,
        },
        "build_context": _runtime_json_context(build_context),
        "summary": {
            "items": len(results),
            "relevant": sum(1 for row in results if row["gold_relevant"] == 1),
            "not_relevant": sum(1 for row in results if row["gold_relevant"] == 0),
            "elapsed_seconds": elapsed_s,
            "avg_item_ms": (elapsed_s / len(results)) * 1000 if results else 0.0,
            "p50_item_ms": _percentile(item_latencies, 0.50),
            "p95_item_ms": _percentile(item_latencies, 0.95),
            "confusion_matrix": delivered_metrics,
            "first_loss_nodes": dict(sorted(first_loss_counts.items())),
        },
        "llm": llm_summary,
        "metrics_accept": accept_metrics,
        "metrics_accept_dedup_adjusted": accept_metrics_adjusted,
        "metrics_delivered": delivered_metrics,
        "funnel": funnel,
        "node_stats": node_stats,
        "runtime_node_stats": runtime_node_stats,
        "confusion_sets": confusion_sets,
        "by_source_kind": by_source_kind,
        "by_source_name": by_source_name,
        "slice_gates": slice_checks,
        "results": results,
    }
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to {out_path}")
    if incomplete_candidate_set:
        print(
            "\nCANDIDATE SET INCOMPLETE: "
            f"expected {expected_candidates}, got {total_candidates}. "
            "Artifact was written with provenance.incomplete_candidate_set=true "
            "and must not be promoted to champion.",
            file=sys.stderr,
        )

    if eval_tracer is not None:
        from opentelemetry import trace

        with eval_tracer.start_as_current_span("eval.run.summary") as span:
            span.set_attribute("job_ftch.eval.run_name", run_name)
            span.set_attribute("job_ftch.eval.items", len(results))
            span.set_attribute("job_ftch.eval.precision", delivered_metrics["precision"])
            span.set_attribute("job_ftch.eval.recall", delivered_metrics["recall"])
            span.set_attribute("job_ftch.eval.f1", delivered_metrics["f1"])
            span.set_attribute(
                "job_ftch.eval.graph_hash",
                graph_executor.graph.graph_hash if graph_executor is not None else "legacy",
            )
            if llm_summary is not None:
                span.set_attribute("job_ftch.eval.llm_cost_usd", llm_summary["cost_usd"])
                span.set_attribute(
                    "job_ftch.eval.llm_cost_is_complete", llm_summary["cost_is_complete"]
                )
        if args.state_mode == "production":
            import structlog

            structlog.get_logger(__name__).info(
                "pipeline_eval_complete",
                run_name=run_name,
                items=len(results),
                precision=delivered_metrics["precision"],
                recall=delivered_metrics["recall"],
                f1=delivered_metrics["f1"],
                llm_cost_usd=llm_summary["cost_usd"] if llm_summary is not None else None,
            )
        logging.getLogger("job_ftch.eval").info(
            "pipeline_eval_completed p=%.6f r=%.6f f1=%.6f items=%d",
            delivered_metrics["precision"],
            delivered_metrics["recall"],
            delivered_metrics["f1"],
            len(results),
        )
        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis=10_000)
        print(f"[Langfuse/OTLP] Logged {len(results)} item traces, run={run_name!r}")

    await _close_eval_resources(cleanup_resources)

    if observability_context_tokens:
        from structlog.contextvars import reset_contextvars

        reset_contextvars(**observability_context_tokens)

    # Handle --compare-to after results are saved
    if args.compare_to:
        compare_path = Path(args.compare_to)
        if not compare_path.exists():
            print(f"ERROR: compare-to file not found: {compare_path}", file=sys.stderr)
            return 1
        print("\n=== COMPARISON ===")
        compare_result = _compare_runs(out_path, compare_path)
        if compare_result != 0:
            return compare_result

    failed_slices = [check for check in slice_checks if not check["passed"]]
    if failed_slices:
        names = ", ".join(str(check["source_kind"]) for check in failed_slices)
        print(f"\nSLICE GATE FAILED for: {names}")
        return 1
    if incomplete_candidate_set:
        return 1
    return 0


if __name__ == "__main__":
    _configure_utf8_stdio()
    raise SystemExit(asyncio.run(main()))
