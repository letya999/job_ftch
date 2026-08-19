"""FastMCP server surface for multi-tenant job_ftch operations."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urljoin, urlsplit

from job_ftch.adapters.mcp import product_surface as mcp_examples
from job_ftch.application.logging import configure_logging
from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings
from job_ftch.domain import ManagedCandidateProfile

_PIPELINE_SCOPES = frozenset({"tenant", "all"})
_RUNTIME_GOALS = frozenset(
    {
        "basic",
        "career_sites",
        "protected_sites",
        "browser",
        "captcha",
        "prefilter",
        "mcp",
        "full",
        "bypass",
    }
)
_HEALTH_KEYS = frozenset(
    {
        "status",
        "failure_streak",
        "last_emitted",
        "last_failed",
        "last_quarantined",
        "degraded",
        "paused",
        "last_error",
        "last_run_at",
        "last_started_at",
        "last_success_at",
        "skipped_runs",
        "success_count",
        "failure_count",
    }
)
_DIAGNOSTIC_KEYS = frozenset(
    {
        "requirements",
        "assessment",
        "freshness",
        "source_assessment",
        "recommended_route",
        "monitor",
        "parser",
        "bypass",
    }
)

# Package -> optional extra mapping for setup recommendations (no secret values).
_PACKAGE_EXTRAS: dict[str, str] = {
    "fastmcp": "mcp",
    "patchright": "browser",
    "cloakbrowser": "browser",
    "nodriver": "nodriver",
    "camoufox": "browser",
    "curl_cffi": "stealth",
    "playwright_stealth": "stealth",
}


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return cast("Any", value).isoformat()
    if hasattr(value, "model_dump"):
        return cast("Any", value).model_dump(mode="json")
    return str(value)


def _tool_annotations(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> Any:
    """Build MCP annotations when the installed SDK exposes them."""
    try:
        from mcp.types import ToolAnnotations
    except ImportError:
        return None
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _public_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if not parsed.hostname:
        return None
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


async def probe_llm_backend(settings: Settings) -> dict[str, Any]:
    """Probe an OpenAI-compatible gateway without generation or secret output."""
    backend = settings.llm_backend
    base_url = settings.openai_base_url
    result: dict[str, Any] = {
        "ok": False,
        "llm_backend": backend,
        "endpoint": _public_endpoint(base_url),
        "openai_model": settings.openai_model,
        "relevance_llm_model": settings.relevance_llm_model,
        "reachable": False,
        "configured_models_available": False,
        "models_sample": [],
        "error": None,
    }
    if backend != "openai":
        result.update(ok=True, error="backend_not_openai_compatible")
        return result
    if not base_url:
        result["error"] = "base_url_missing"
        return result

    root = base_url if base_url.endswith("/") else f"{base_url}/"
    models_url = urljoin(root, "models")
    headers: dict[str, str] = {}
    if settings.openai_api_key is not None:
        headers["Authorization"] = f"Bearer {settings.openai_api_key.get_secret_value()}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(models_url, headers=headers)
        if response.status_code >= 400:
            result["error"] = f"gateway_http_{response.status_code}"
            return result
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        ids = [
            str(item["id"])
            for item in (data if isinstance(data, list) else [])[:20]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        required = {settings.openai_model, settings.relevance_llm_model} - {None, ""}
        result.update(
            ok=True,
            reachable=True,
            models_sample=ids,
            configured_models_available=required.issubset(ids),
        )
        if ids and not result["configured_models_available"]:
            result["error"] = "configured_model_missing"
        return result
    except Exception as exc:  # noqa: BLE001 - public result is intentionally redacted
        result["error"] = f"gateway_unreachable:{type(exc).__name__}"
        return result


def prepare_stdio_logging(level_name: str = "INFO") -> None:
    """Route application logs to stderr so MCP JSON-RPC on stdout stays clean.

    Unconfigured structlog defaults to PrintLogger on stdout, which corrupts
    the stdio JSON-RPC stream. Important errors still go to stderr.
    """
    configure_logging(level_name)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.stream = sys.stderr


def _package_present(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _clear_output_artifacts(settings: Any) -> int:
    removed = 0
    paths = {
        Path(path)
        for path in (
            getattr(settings, "output_path", None),
            getattr(settings, "review_output_path", None),
            getattr(settings, "rejected_output_path", None),
            getattr(settings, "quarantine_output_path", None),
        )
        if path is not None
    }
    for path in paths:
        suffix = "".join(path.suffixes)
        stem = path.name[: -len(suffix)] if suffix else path.name
        candidates = {path, path.with_name(f"{stem}.tmp{suffix}")}
        if path.parent.exists():
            candidates.update(path.parent.glob(f"{stem}.*.staging.jsonl"))
            candidates.update(path.parent.glob(f"{stem}.*.tmp{suffix}"))
        for candidate in candidates:
            if candidate.is_file():
                candidate.unlink()
                removed += 1
    return removed


def _prefilter_requirements_payload(profile_type: str | None = None) -> dict[str, Any]:
    """Static training contract derived from docs/nodes/relevance_prefilter.md."""
    return {
        "profile_type": profile_type,
        "dataset_format": "jsonl",
        "required_fields": {
            "stable_id": "string unique id",
            "text": "string vacancy/candidate text",
            "relevant": "integer 0 or 1 (strings and 'unknown' are ignored)",
        },
        "label_contract": {
            "training_field": "relevant",
            "positive": 1,
            "negative": 0,
            "operator_aliases": {
                "positive": 1,
                "negative": 0,
                "label=positive": 1,
                "label=negative": 0,
            },
            "notes": (
                "Training JSONL uses relevant=0/1. Operator-facing positive/negative "
                "map to those integers; do not mix string labels into the train script "
                "without conversion."
            ),
        },
        "size_requirements": {
            "recommended_total_rows": 2000,
            "recommended_positive_rows": 150,
            "positive_fraction_min": 0.02,
            "positive_fraction_max": 0.50,
            "notes": (
                "Include enough negatives for a meaningful pre-LLM drop gate. "
                "Seeds under the recommended size are fine for experiments but not "
                "for production promotion."
            ),
        },
        "training": {
            "script": "scripts/eval/train_relevance_prefilter.py",
            "dataset_builder": "scripts/eval/build_prefilter_dataset_from_manual_labels.py",
            "artifact_example": "fixtures/prefilter/tfidf_logreg_v1.json",
            "dry_run_first": True,
        },
        "promotion": {
            "automatic_after_example_change": False,
            "require_eval_gate": True,
            "require_explicit_promote": True,
            "notes": (
                "Examples/profile/feedback writes mark prefilter dirty only. "
                "Train, evaluate, then promote explicitly with rollback available. "
                "TF-IDF/LogReg promotion is gated and never automatic."
            ),
        },
        "caveats": [
            "Missing model artifact degrades to passthrough rather than hard-failing the pipeline.",
            "Eval before promotion; promotion without a passing gate is unsafe.",
            "Prefilter and ontology are separate: ontology compile does not retrain prefilter.",
        ],
    }


def _runtime_package_status() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for module_name, extra in _PACKAGE_EXTRAS.items():
        present = _package_present(module_name)
        status[module_name] = {
            "present": present,
            "extra": extra,
            "install_hint": None if present else f"uv sync --extra {extra}",
        }
    return status


def _goal_package_targets(goal: str) -> list[str]:
    if goal in {"basic", "mcp"}:
        return ["fastmcp"]
    if goal == "browser":
        return ["patchright"]
    if goal == "captcha":
        return ["patchright"]
    if goal == "prefilter":
        return []
    if goal == "career_sites":
        return ["patchright", "curl_cffi", "playwright_stealth"]
    if goal in {"protected_sites", "bypass"}:
        return [
            "patchright",
            "curl_cffi",
            "playwright_stealth",
            "nodriver",
            "cloakbrowser",
        ]
    if goal == "full":
        return list(_PACKAGE_EXTRAS)
    return ["fastmcp"]


def _recommend_runtime_setup(
    *,
    goal: str,
    platform: str | None,
    inventory: dict[str, Any] | None,
    source_context: dict[str, Any] | None,
) -> dict[str, Any]:
    packages = _runtime_package_status()
    targets = _goal_package_targets(goal)
    missing_extras: list[str] = []
    commands: list[str] = []
    manual_steps: list[str] = []
    warnings: list[str] = []
    missing_env: list[str] = []
    missing_binaries: list[str] = []

    for module_name in targets:
        info = packages.get(module_name)
        if info is None:
            continue
        if not info["present"]:
            extra = str(info["extra"])
            if extra not in missing_extras:
                missing_extras.append(extra)
            hint = info.get("install_hint")
            if isinstance(hint, str) and hint not in commands:
                commands.append(hint)

    if goal in {"browser", "career_sites", "protected_sites", "bypass", "full", "captcha"}:
        if not _package_present("patchright"):
            missing_binaries.append("patchright chromium")
            commands.append("uv run patchright install chromium")
        else:
            # Browsers may be installed as packages without browser binaries.
            missing_binaries.append("patchright chromium (verify with patchright install chromium)")
            if "uv run patchright install chromium" not in commands:
                commands.append("uv run patchright install chromium")

    if goal in {"captcha", "protected_sites", "full"}:
        missing_env.extend(
            [
                "JOB_FTCH_CAPTCHA_PROVIDER (label only; set provider-specific API key env)",
                "provider key env such as CAPSOLVER_API_KEY / CAPMONSTER_API_KEY (never return values)",
            ]
        )
        manual_steps.append("authorize captcha domains for the chosen provider")
        manual_steps.append("prefer browser_wait when paid captcha is not configured")

    if goal in {"browser", "protected_sites", "full", "bypass"}:
        manual_steps.append("configure browser profile root only if persistent sessions are needed")
        manual_steps.append("sensitive browser/proxy routes require explicit operator approval")

    if goal in {"prefilter", "full"}:
        manual_steps.append(
            "prepare JSONL dataset with stable_id/text/relevant (2000+ rows, 150+ positives)"
        )
        manual_steps.append(
            "train with scripts/eval/train_relevance_prefilter.py then evaluate before promote"
        )
        warnings.append("prefilter promotion is explicit and gated; example writes only mark dirty")

    if goal in {"mcp", "basic", "full"}:
        if "uv sync --extra mcp" not in commands and not packages["fastmcp"]["present"]:
            commands.insert(0, "uv sync --extra mcp")
        manual_steps.append(
            "start with: uv run job_ftch mcp-server --configs-dir <tenants> --transport stdio"
        )

    if inventory is not None:
        caps = inventory.get("capabilities") or []
        unavailable = [
            str(item.get("id") or item.get("engine") or "unknown")
            for item in caps
            if isinstance(item, dict) and item.get("availability") in {"unavailable", "disabled"}
        ]
        if unavailable:
            warnings.append(
                "capability inventory reports unavailable/disabled routes: "
                + ", ".join(unavailable[:12])
                + ("..." if len(unavailable) > 12 else "")
            )

    if source_context is not None:
        requirements = source_context.get("requirements") or {}
        if requirements.get("browser_required"):
            warnings.append(
                "selected source reports browser_required="
                f"{requirements.get('browser_reason') or True}"
            )
            hint = requirements.get("browser_setup_hint")
            if isinstance(hint, str) and hint not in manual_steps:
                manual_steps.append(hint)

    # Deduplicate while preserving order.
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return {
        "goal": goal,
        "platform": platform or sys.platform,
        "missing_extras": _uniq(missing_extras),
        "missing_binaries": _uniq(missing_binaries),
        "missing_env": _uniq(missing_env),
        "commands": _uniq(commands),
        "manual_steps": _uniq(manual_steps),
        "warnings": _uniq(warnings),
        "package_status": packages,
        "notes": [
            "recommendation only; this tool does not install packages or write secrets",
            "secret values and proxy endpoints are never included",
        ],
    }


def _validate_runtime_setup(
    *,
    goal: str,
    inventory: dict[str, Any] | None,
) -> dict[str, Any]:
    packages = _runtime_package_status()
    targets = _goal_package_targets(goal if goal != "bypass" else "protected_sites")
    checks: list[dict[str, Any]] = []
    ok = True

    for module_name in targets:
        present = bool(packages.get(module_name, {}).get("present"))
        checks.append(
            {
                "id": f"package:{module_name}",
                "ok": present,
                "detail": "present"
                if present
                else f"missing; install extra {_PACKAGE_EXTRAS.get(module_name)}",
            }
        )
        ok = ok and present

    if goal in {"browser", "career_sites", "protected_sites", "bypass", "full", "captcha"}:
        browser_pkg = _package_present("patchright")
        checks.append(
            {
                "id": "browser_package",
                "ok": browser_pkg,
                "detail": "patchright importable" if browser_pkg else "no patchright package",
            }
        )
        ok = ok and browser_pkg

    if goal in {"mcp", "basic", "full"}:
        mcp_ok = bool(packages.get("fastmcp", {}).get("present"))
        checks.append(
            {
                "id": "mcp_package",
                "ok": mcp_ok,
                "detail": "fastmcp present" if mcp_ok else "install job-ftch[mcp]",
            }
        )
        ok = ok and mcp_ok

    if goal in {"prefilter", "full"}:
        checks.append(
            {
                "id": "prefilter_contract",
                "ok": True,
                "detail": (
                    "dataset contract available via get_prefilter_requirements; "
                    "artifact promotion is operator-gated"
                ),
            }
        )

    if inventory is not None:
        status = inventory.get("status")
        inv_ok = status == "ok"
        checks.append(
            {
                "id": "bypass_inventory",
                "ok": inv_ok,
                "detail": f"inventory status={status}",
            }
        )
        ok = ok and inv_ok

    return {
        "goal": goal,
        "ok": ok,
        "checks": checks,
        "notes": [
            "read-only validation; does not mutate runtime or disclose secret values",
            "presence of env labels is not checked by value",
        ],
    }


def _strip_source_fields(
    source: dict[str, Any],
    *,
    include_health: bool,
    include_diagnostics: bool,
) -> dict[str, Any]:
    payload = dict(source)
    if not include_health:
        for key in _HEALTH_KEYS:
            payload.pop(key, None)
    if not include_diagnostics:
        for key in _DIAGNOSTIC_KEYS:
            payload.pop(key, None)
    return payload


def _source_degradation_summary(sources: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    degraded_ids: list[str] = []
    failed_ids: list[str] = []
    for item in sources:
        status = str(item.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        source_id = str(item.get("source_id") or "")
        if source_id and (item.get("degraded") or status in {"degraded", "failing"}):
            degraded_ids.append(source_id)
        if source_id and status in {"failed", "error"}:
            failed_ids.append(source_id)
    return {
        "by_status": by_status,
        "degraded_source_ids": degraded_ids,
        "failed_source_ids": failed_ids,
        "degraded_count": len(degraded_ids),
        "failed_count": len(failed_ids),
        "enabled_count": sum(1 for item in sources if item.get("enabled", True)),
        "disabled_count": sum(1 for item in sources if not item.get("enabled", True)),
        "total": len(sources),
    }


class TenantMCPServer:
    def __init__(
        self,
        *,
        configs_dir: Path,
        base_settings: Settings | None = None,
        name: str = "job_ftch",
    ) -> None:
        try:
            from fastmcp import FastMCP
        except ImportError as exc:
            msg = "FastMCP server requires the 'mcp' extra: pip install job-ftch[mcp]"
            raise ImportError(msg) from exc

        self.configs_dir = configs_dir
        self.base_settings = base_settings or get_settings()
        self.name = name
        self.app = FastMCP(name)
        self.runner: TenantRunner | None = None
        self._register_surface()

    async def startup(self) -> None:
        if self.runner is not None:
            return
        tenants = load_tenants(self.configs_dir)
        self.runner = TenantRunner.from_tenants(tenants, base_settings=self.base_settings)

    async def shutdown(self) -> None:
        if self.runner is None:
            return
        await self.runner.close()
        self.runner = None

    def run(
        self,
        *,
        transport: Literal["stdio", "http", "sse", "streamable-http"] = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        if transport == "stdio":
            # FastMCP run_stdio_async rejects unexpected host/port kwargs.
            prepare_stdio_logging(self.base_settings.log_level)
            self.app.run(transport=transport)
            return
        self.app.run(transport=transport, host=host, port=port)

    async def _run_pipeline_impl(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        scope: str,
        source_ids: list[str] | None,
        max_items: int | None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        normalized_scope = (scope or "tenant").strip().lower()
        if normalized_scope not in _PIPELINE_SCOPES:
            return {
                "error": "unsupported_scope",
                "message": f"scope must be one of {sorted(_PIPELINE_SCOPES)}",
                "scope": scope,
            }
        runner = self._require_runner()
        if normalized_scope == "all":
            if tenant_id is not None:
                return {
                    "error": "invalid_arguments",
                    "message": "tenant_id must be omitted when scope='all'",
                    "hint": "use run_pipeline(scope='all') without tenant_id",
                }
            if source_ids:
                return {
                    "error": "unsupported",
                    "message": "source_ids is not supported with scope='all'",
                    "hint": "run a single tenant with source_ids, or omit source_ids for all tenants",
                }
            summaries = await runner.run_all(max_items=max_items, user_id=user_id)
            payloads = [summary.as_dict() for summary in summaries]
            return payloads

        if not tenant_id:
            return {
                "error": "invalid_arguments",
                "message": "tenant_id is required when scope='tenant'",
            }
        summary = await runner.run_tenant(
            tenant_id,
            max_items=max_items,
            user_id=user_id,
            source_ids=source_ids,
        )
        return summary.as_dict()

    async def _get_sources_impl(
        self,
        tenant_id: str,
        *,
        include_health: bool = True,
        include_diagnostics: bool = True,
    ) -> dict[str, Any]:
        runner = self._require_runner()
        sources = await runner.list_sources(tenant_id)
        health_rows: list[dict[str, Any]] | None = None
        if include_health:
            health_rows = await runner.list_source_health(tenant_id)
        items = [
            _strip_source_fields(
                source,
                include_health=include_health,
                include_diagnostics=include_diagnostics,
            )
            for source in sources
        ]
        return {
            "tenant_id": tenant_id,
            "count": len(items),
            "sources": items,
            "health": health_rows if include_health else None,
            "include_health": include_health,
            "include_diagnostics": include_diagnostics,
        }

    async def _get_tenant_status_impl(self, tenant_id: str) -> dict[str, Any]:
        runner = self._require_runner()
        status = await runner.get_status(tenant_id)
        sources = await runner.list_sources(tenant_id)
        runs = await runner.list_runs(tenant_id=tenant_id, limit=1)
        latest_run = runs[0].as_dict() if runs else None
        return {
            "tenant_id": tenant_id,
            "status": None if status is None else status.as_dict(),
            "latest_run": latest_run,
            "source_degradation": _source_degradation_summary(sources),
            "source_count": len(sources),
        }

    async def _browser_capabilities_impl(self) -> dict[str, Any]:
        from job_ftch.application.browser_capability_inventory import (
            inventory_to_public_dict,
        )

        inventory = self._require_runner().list_browser_capabilities()
        return inventory_to_public_dict(inventory)

    async def _browser_routes_impl(
        self,
        tenant_id: str | None = None,
        source_id: str | None = None,
        bypass: str | None = None,
    ) -> dict[str, Any]:
        from job_ftch.application.browser_capability_inventory import (
            explanation_to_public_dict,
        )

        explanation = await self._require_runner().explain_browser_route(
            tenant_id,
            source_id,
            bypass=bypass,
        )
        return explanation_to_public_dict(explanation)

    async def _with_setup_if_needed(
        self,
        payload: dict[str, Any],
        *,
        goal: str = "protected_sites",
    ) -> dict[str, Any]:
        attempts = payload.get("attempts")
        attempt_needs_setup = isinstance(attempts, list) and any(
            isinstance(item, dict)
            and item.get("status") in {"unavailable", "not_implemented", "error"}
            for item in attempts
        )
        needs_setup = (
            payload.get("status")
            in {
                "not_implemented",
                "unsupported",
                "unavailable",
                "degraded",
                "error",
            }
            or bool(payload.get("browser_required"))
            or attempt_needs_setup
        )
        if not needs_setup or payload.get("setup") is not None:
            return payload
        inventory = None
        if goal in {"browser", "bypass", "career_sites", "protected_sites", "captcha", "full"}:
            inventory = await self._browser_capabilities_impl()
        source_context = payload.get("source") if isinstance(payload.get("source"), dict) else None
        payload["setup"] = _recommend_runtime_setup(
            goal=goal,
            platform=None,
            inventory=inventory,
            source_context=source_context,
        )
        return payload

    def _register_surface(self) -> None:
        @self.app.tool
        async def run_pipeline(
            tenant_id: str | None = None,
            user_id: str | None = None,
            scope: str = "tenant",
            source_ids: list[str] | None = None,
            max_items: int | None = None,
        ) -> dict[str, Any] | list[dict[str, Any]]:
            """Run one tenant pipeline (scope='tenant') or all tenants (scope='all')."""
            return await self._run_pipeline_impl(
                tenant_id=tenant_id,
                user_id=user_id,
                scope=scope,
                source_ids=source_ids,
                max_items=max_items,
            )

        @self.app.tool
        async def get_pipeline_status(tenant_id: str) -> dict[str, Any] | None:
            """Return latest pipeline run status for a tenant."""
            summary = await self._require_runner().get_status(tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def get_tenant_status(tenant_id: str) -> dict[str, Any]:
            """Aggregate tenant status, source degradation, and latest run metadata."""
            return await self._get_tenant_status_impl(tenant_id)

        @self.app.tool
        async def get_sources(
            tenant_id: str,
            include_health: bool = True,
            include_diagnostics: bool = True,
        ) -> dict[str, Any]:
            """List tenant sources with optional health and diagnostics."""
            return await self._get_sources_impl(
                tenant_id,
                include_health=include_health,
                include_diagnostics=include_diagnostics,
            )

        @self.app.tool
        async def add_source(
            tenant_id: str,
            link: str,
            source_type: str | None = None,
            limit: int = 100,
            source_name: str | None = None,
        ) -> dict[str, Any]:
            runner = self._require_runner()
            spec = await build_source_spec_from_input(
                link,
                auth_provider=runner.get_runtime(tenant_id).auth_provider,
                source_type=source_type,
                limit=limit,
                source_name=source_name,
            )
            return await runner.add_source_spec(
                tenant_id,
                spec,
                added_via="mcp",
                input_value=link,
            )

        @self.app.tool
        async def disable_source(tenant_id: str, source_id: str) -> dict[str, Any]:
            return await self._require_runner().disable_source(tenant_id, source_id)

        @self.app.tool
        async def list_profiles(tenant_id: str, user_id: str) -> list[dict[str, Any]]:
            return await self._require_runner().list_candidate_profiles(tenant_id, user_id)

        @self.app.tool
        async def save_profile(
            tenant_id: str,
            user_id: str,
            profile_id: str,
            summary: str,
        ) -> dict[str, Any]:
            profile = build_candidate_profile_from_payload(
                user_id=user_id,
                profile_id=profile_id,
                payload={"summary": summary, "name": profile_id},
            )
            saved = await self._require_runner().save_candidate_profile(
                tenant_id,
                ManagedCandidateProfile(
                    user_id=user_id,
                    profile_id=profile_id,
                    profile=profile,
                    updated_at=datetime.now(UTC),
                ),
            )
            await self._require_runner().set_active_candidate_profile(
                tenant_id, user_id, profile_id
            )
            from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

            mark_prefilter_dirty(self._require_runner().get_runtime(tenant_id).settings)
            return saved

        @self.app.tool
        async def activate_profile(
            tenant_id: str,
            user_id: str,
            profile_id: str,
        ) -> dict[str, Any]:
            return await self._require_runner().set_active_candidate_profile(
                tenant_id,
                user_id,
                profile_id,
            )

        @self.app.tool
        async def list_pipeline_runs(
            tenant_id: str | None = None,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            """List recent pipeline runs (optionally tenant-scoped)."""
            summaries = await self._require_runner().list_runs(tenant_id=tenant_id, limit=limit)
            return [summary.as_dict() for summary in summaries]

        @self.app.tool
        async def get_pipeline_run(
            run_id: str,
            tenant_id: str | None = None,
        ) -> dict[str, Any] | None:
            """Fetch a single pipeline run by id."""
            summary = await self._require_runner().get_run(run_id, tenant_id=tenant_id)
            return None if summary is None else summary.as_dict()

        @self.app.tool
        async def list_tenants() -> list[dict[str, Any]]:
            tenants = await self._require_runner().list_tenants()
            return [tenant.model_dump(mode="json") for tenant in tenants]

        @self.app.tool
        async def get_bypass_capabilities() -> dict[str, Any]:
            """Read-only inventory of browser/bypass routes and availability."""
            return await self._browser_capabilities_impl()

        @self.app.tool
        async def get_llm_backend_health() -> dict[str, Any]:
            """Probe CLIProxy/OpenAI-compatible model routing without generation."""
            return await probe_llm_backend(self.base_settings)

        @self.app.tool
        async def get_bypass_routes(
            tenant_id: str | None = None,
            source_id: str | None = None,
            bypass: str | None = None,
        ) -> dict[str, Any]:
            """Explain why a browser/bypass route is selected or unavailable."""
            return await self._browser_routes_impl(tenant_id, source_id, bypass)

        @self.app.tool
        async def probe_source(
            tenant_id: str,
            source_id: str,
            mode: str = "cheap",
            max_items: int = 5,
        ) -> dict[str, Any]:
            """Diagnose a source (cheap) or run a bounded source-scoped ingest (full)."""
            from job_ftch.application.source_operations import probe_source as probe_source_op

            payload = await probe_source_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                mode=mode,
                max_items=max_items,
            )
            return await self._with_setup_if_needed(payload)

        @self.app.tool
        async def run_source(
            tenant_id: str,
            source_id: str,
            max_items: int | None = None,
            parser: str | None = None,
            bypass: str | None = None,
        ) -> dict[str, Any]:
            """Run one source. omit bypass/parser for defaults; pass a name to pin one mechanic."""
            from job_ftch.application.source_operations import run_source as run_source_op

            payload = await run_source_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                max_items=max_items,
                parser=parser,
                bypass=bypass,
            )
            return await self._with_setup_if_needed(payload)

        @self.app.tool
        async def run_source_escalation(
            tenant_id: str,
            source_id: str,
            strategy: str = "recommended",
            max_tier: str | None = None,
            max_items: int = 5,
        ) -> dict[str, Any]:
            """recommended = adaptive ladder; all = bounded per-route sweep with parse diagnosis."""
            from job_ftch.application.source_operations import (
                run_source_escalation as run_source_escalation_op,
            )

            payload = await run_source_escalation_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                strategy=strategy,
                max_tier=max_tier,
                max_items=max_items,
            )
            return await self._with_setup_if_needed(payload, goal="bypass")

        @self.app.tool
        async def probe_bypass_route(
            tenant_id: str,
            source_id: str,
            bypass: str,
            max_items: int = 3,
        ) -> dict[str, Any]:
            """Run one named bypass (listing probe for browsers, pinned ingest otherwise)."""
            from job_ftch.application.source_operations import (
                probe_bypass_route as probe_bypass_route_op,
            )

            payload = await probe_bypass_route_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                bypass=bypass,
                max_items=max_items,
            )
            return await self._with_setup_if_needed(payload, goal="bypass")

        @self.app.tool
        async def run_browser_probe(
            tenant_id: str,
            source_id: str | None = None,
            url: str | None = None,
            probe: str = "listing",
            engine: str = "auto",
            bypass: str | None = None,
            headed: bool = False,
            max_items: int = 5,
            solve: str = "none",
        ) -> dict[str, Any]:
            """Live listing/detail/challenge/fingerprint/custom_safe probe."""
            from job_ftch.application.source_operations import (
                run_browser_probe as run_browser_probe_op,
            )

            payload = await run_browser_probe_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                url=url,
                probe=probe,
                engine=engine,
                bypass=bypass,
                headed=headed,
                max_items=max_items,
                solve=solve,
            )
            return await self._with_setup_if_needed(payload, goal="browser")

        @self.app.tool
        async def open_browser_session(
            tenant_id: str,
            source_id: str | None = None,
            url: str | None = None,
            engine: str = "auto",
            headed: bool = True,
            bypass: str | None = None,
            profile: str = "ephemeral",
            manual_challenge: bool = False,
        ) -> dict[str, Any]:
            """Open an ephemeral, persistent, or domain operator browser session."""
            from job_ftch.application.source_operations import (
                open_browser_session as open_browser_session_op,
            )

            payload = await open_browser_session_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                url=url,
                engine=engine,
                headed=headed,
                bypass=bypass,
                profile=profile,
                manual_challenge=manual_challenge,
            )
            return await self._with_setup_if_needed(payload, goal="browser")

        @self.app.tool
        async def get_browser_session(session_id: str) -> dict[str, Any]:
            """Return a public snapshot of an operator browser session."""
            from job_ftch.application.source_operations import (
                get_browser_session as get_browser_session_op,
            )

            return await get_browser_session_op(self._require_runner(), session_id=session_id)

        @self.app.tool
        async def continue_browser_session(
            session_id: str,
            instruction: str | None = None,
        ) -> dict[str, Any]:
            """wait|reload|wait_challenge|extend|solve|navigate <url> on an open session."""
            from job_ftch.application.source_operations import (
                continue_browser_session as continue_browser_session_op,
            )

            payload = await continue_browser_session_op(
                self._require_runner(),
                session_id=session_id,
                instruction=instruction,
            )
            return await self._with_setup_if_needed(payload, goal="captcha")

        @self.app.tool
        async def capture_browser_artifact(
            session_id: str,
            artifact_type: str = "text",
        ) -> dict[str, Any]:
            """Capture text, truncated html, cookie names, screenshot, or trace."""
            from job_ftch.application.source_operations import (
                capture_browser_artifact as capture_browser_artifact_op,
            )

            return await capture_browser_artifact_op(
                self._require_runner(),
                session_id=session_id,
                artifact_type=artifact_type,
            )

        @self.app.tool
        async def close_browser_session(session_id: str) -> dict[str, Any]:
            """Close an operator browser session and release the runtime."""
            from job_ftch.application.source_operations import (
                close_browser_session as close_browser_session_op,
            )

            return await close_browser_session_op(self._require_runner(), session_id=session_id)

        @self.app.tool
        async def recommend_runtime_setup(
            tenant_id: str | None = None,
            source_id: str | None = None,
            goal: str = "basic",
            platform: str | None = None,
        ) -> dict[str, Any]:
            """Recommend installs/config for MCP, browser, bypass, or prefilter goals.

            Read-only: does not install packages or expose secret values.
            """
            normalized = (goal or "basic").strip().lower()
            if normalized not in _RUNTIME_GOALS:
                return {
                    "error": "unsupported_goal",
                    "message": f"goal must be one of {sorted(_RUNTIME_GOALS)}",
                    "goal": goal,
                }
            inventory: dict[str, Any] | None = None
            source_context: dict[str, Any] | None = None
            if normalized in {
                "browser",
                "bypass",
                "career_sites",
                "protected_sites",
                "captcha",
                "full",
            }:
                inventory = await self._browser_capabilities_impl()
            if tenant_id and source_id:
                sources = await self._require_runner().list_sources(tenant_id)
                for item in sources:
                    if str(item.get("source_id") or "") == source_id:
                        source_context = item
                        break
                if source_context is None:
                    return {
                        "error": "source_not_found",
                        "tenant_id": tenant_id,
                        "source_id": source_id,
                        "goal": normalized,
                    }
            return _recommend_runtime_setup(
                goal=normalized,
                platform=platform,
                inventory=inventory,
                source_context=source_context,
            )

        @self.app.tool
        async def validate_runtime_setup(
            goal: str = "mcp",
            tenant_id: str | None = None,
            source_id: str | None = None,
        ) -> dict[str, Any]:
            """Validate current runtime readiness without disclosing secrets."""
            normalized = (goal or "mcp").strip().lower()
            if normalized not in _RUNTIME_GOALS:
                return {
                    "error": "unsupported_goal",
                    "message": f"goal must be one of {sorted(_RUNTIME_GOALS)}",
                    "goal": goal,
                }
            inventory: dict[str, Any] | None = None
            if normalized in {
                "browser",
                "bypass",
                "career_sites",
                "protected_sites",
                "captcha",
                "full",
            }:
                inventory = await self._browser_capabilities_impl()
            payload = _validate_runtime_setup(goal=normalized, inventory=inventory)
            # tenant_id/source_id reserved for future source-scoped checks.
            payload["tenant_id"] = tenant_id
            payload["source_id"] = source_id
            return payload

        @self.app.tool
        async def get_prefilter_requirements(
            profile_type: str | None = None,
        ) -> dict[str, Any]:
            """Return dataset format/size requirements for TF-IDF/LogReg prefilter training."""
            return _prefilter_requirements_payload(profile_type)

        @self.app.tool
        async def get_prefilter_status(
            tenant_id: str,
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Return dirty flag, current artifact, and training readiness."""
            from job_ftch.application.prefilter_artifacts import get_prefilter_status

            return get_prefilter_status(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                profile_id=profile_id,
            )

        @self.app.tool
        async def prepare_prefilter_dataset(
            tenant_id: str,
            profile_id: str | None = None,
            source: str = "examples",
            output: str | None = None,
            user_id: str = "mcp",
        ) -> dict[str, Any]:
            """Build a training JSONL from examples, feedback, eval dataset, or mixed."""
            from job_ftch.application.prefilter_artifacts import prepare_prefilter_dataset

            return await prepare_prefilter_dataset(
                self._require_runner(),
                tenant_id=tenant_id,
                profile_id=profile_id,
                source=source,
                output=output,
                user_id=user_id,
            )

        @self.app.tool
        async def validate_prefilter_dataset(
            dataset_id_or_path: str,
            tenant_id: str | None = None,
        ) -> dict[str, Any]:
            """Validate JSONL size/label contract without training."""
            from job_ftch.application.prefilter_artifacts import validate_prefilter_dataset

            settings = None
            if tenant_id:
                settings = self._require_runner().get_runtime(tenant_id).settings
            return validate_prefilter_dataset(dataset_id_or_path, settings)

        @self.app.tool
        async def train_prefilter(
            tenant_id: str,
            profile_id: str | None = None,
            dataset_id_or_path: str | None = None,
            dry_run: bool = True,
            threshold: float = 0.30,
        ) -> dict[str, Any]:
            """Train a TF-IDF/LogReg artifact. Default dry_run does not write."""
            from job_ftch.application.prefilter_artifacts import train_prefilter

            return train_prefilter(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                profile_id=profile_id,
                dataset_id_or_path=dataset_id_or_path,
                dry_run=dry_run,
                threshold=threshold,
            )

        @self.app.tool
        async def evaluate_prefilter(
            tenant_id: str,
            artifact_id: str,
            dataset_id_or_path: str | None = None,
            threshold: float | None = None,
        ) -> dict[str, Any]:
            """Evaluate a trained artifact against stored holdout and optional dataset."""
            from job_ftch.application.prefilter_artifacts import evaluate_prefilter

            return evaluate_prefilter(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                dataset_id_or_path=dataset_id_or_path,
                threshold=threshold,
            )

        @self.app.tool
        async def promote_prefilter(
            tenant_id: str,
            artifact_id: str,
            threshold: float | None = None,
            require_gate_pass: bool = True,
        ) -> dict[str, Any]:
            """Promote an artifact to current after an eval gate."""
            from job_ftch.application.prefilter_artifacts import promote_prefilter

            return promote_prefilter(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
                threshold=threshold,
                require_gate_pass=require_gate_pass,
            )

        @self.app.tool
        async def rollback_prefilter(
            tenant_id: str,
            artifact_id: str | None = None,
        ) -> dict[str, Any]:
            """Roll current artifact back to previous or an explicit id."""
            from job_ftch.application.prefilter_artifacts import rollback_prefilter

            return rollback_prefilter(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
            )

        @self.app.tool
        async def list_prefilter_artifacts(
            tenant_id: str,
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """List trained tenant artifacts and which one is current."""
            from job_ftch.application.prefilter_artifacts import list_prefilter_artifacts

            return list_prefilter_artifacts(
                self._require_runner().get_runtime(tenant_id).settings,
                tenant_id=tenant_id,
                profile_id=profile_id,
            )

        @self.app.tool
        async def ingest_resume(
            tenant_id: str,
            user_id: str,
            resume_text: str,
            profile_id: str | None = None,
            activate: bool = True,
        ) -> dict[str, Any]:
            """Ingest resume text into a managed candidate profile (not stored on sessions)."""
            record = await self._require_runner().ingest_resume(
                tenant_id,
                user_id=user_id,
                resume_text=resume_text,
                profile_id=profile_id,
                activate=activate,
            )
            resume = record.profile.resume
            summary = resume.summary if resume is not None else None
            if summary is None and record.profile.search_profiles:
                summary = record.profile.search_profiles[0].name
            sync_error: str | None = None
            try:
                from job_ftch.application.shot_sync import sync_profile_to_shot_store

                await sync_profile_to_shot_store(
                    profile=record,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001 - ingest already persisted
                sync_error = f"{type(exc).__name__}: {exc}"
            from job_ftch.application.prefilter_artifacts import mark_prefilter_dirty

            mark_prefilter_dirty(self._require_runner().get_runtime(tenant_id).settings)
            return {
                "user_id": record.user_id,
                "profile_id": record.profile_id,
                "updated_at": record.updated_at.isoformat(),
                "summary": summary,
                "prefilter_dirty": True,
                "shot_sync_error": sync_error,
            }

        @self.app.tool
        async def get_examples_summary(
            tenant_id: str,
            user_id: str,
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Return example counts for a user profile (resume/vacancy × polarity)."""
            return await mcp_examples.get_examples_summary(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                profile_id=profile_id,
            )

        @self.app.tool
        async def list_examples(
            tenant_id: str,
            user_id: str,
            profile_id: str | None = None,
            kind: str = "all",
            label: str | None = None,
        ) -> dict[str, Any]:
            """List resume/vacancy examples, optionally filtered by kind and label."""
            return await mcp_examples.list_operator_examples(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                profile_id=profile_id,
                kind=kind,
                label=label,
            )

        @self.app.tool
        async def add_example(
            tenant_id: str,
            user_id: str,
            kind: str,
            label: str,
            text: str,
            profile_id: str | None = None,
            refresh_policy: str = "auto",
        ) -> dict[str, Any]:
            """Add a positive/negative resume or vacancy example and refresh learning."""
            return await mcp_examples.add_operator_example(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,
                label=label,
                text=text,
                profile_id=profile_id,
                refresh_policy=refresh_policy,
            )

        @self.app.tool
        async def remove_example(
            tenant_id: str,
            user_id: str,
            kind: str,
            label: str,
            index: int,
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Remove one example by kind, label, and index."""
            return await mcp_examples.remove_operator_example(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,
                label=label,
                index=index,
                profile_id=profile_id,
            )

        @self.app.tool
        async def clear_examples(
            tenant_id: str,
            user_id: str,
            kind: str = "all",
            profile_id: str | None = None,
        ) -> dict[str, Any]:
            """Clear resume, vacancy, or all examples for a profile."""
            return await mcp_examples.clear_operator_examples(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,
                profile_id=profile_id,
            )

        @self.app.tool
        async def create_search_session(
            tenant_id: str,
            user_id: str | None = None,
            profile_id: str | None = None,
            source_scope: list[str] | None = None,
            max_items: int | None = None,
            max_sources: int | None = None,
            result_limit: int = 20,
        ) -> dict[str, Any]:
            """Create a resume-driven search session for plan/approve/run workflow."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().create_search_session(
                tenant_id,
                user_id=user_id,
                profile_id=profile_id,
                source_scope=source_scope,
                max_items=max_items,
                max_sources=max_sources,
                result_limit=result_limit,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def plan_search_session(session_id: str) -> dict[str, Any]:
            """Plan per-source routes for a search session using capability diagnostics."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().plan_source_routes(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def approve_search_session(
            session_id: str,
            approved_source_ids: list[str] | None = None,
            approved_capability_ids: list[str] | None = None,
            approve_all_sensitive: bool = False,
            note: str | None = None,
        ) -> dict[str, Any]:
            """Approve sensitive routes/budgets before running a search session."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().approve_search_session(
                session_id,
                approved_source_ids=approved_source_ids,
                approved_capability_ids=approved_capability_ids,
                approve_all_sensitive=approve_all_sensitive,
                note=note,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def run_search_session(
            session_id: str,
            skip_pipeline: bool = False,
        ) -> dict[str, Any]:
            """Run a search session via existing tenant pipeline + profile ranking."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().run_search_session(
                session_id,
                skip_pipeline=skip_pipeline,
            )
            return session_to_public_dict(session)

        @self.app.tool
        async def get_search_session(session_id: str) -> dict[str, Any]:
            """Return search session status, route plan, and budgets."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().get_search_session_status(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def list_search_session_results(
            session_id: str,
            limit: int | None = None,
        ) -> list[dict[str, Any]]:
            """List ranked job result refs for a search session."""
            refs = await self._require_runner().list_search_results(session_id, limit=limit)
            return [ref.model_dump(mode="json") for ref in refs]

        @self.app.tool
        async def explain_search_session(
            session_id: str,
            source_id: str | None = None,
            job_id: str | None = None,
        ) -> dict[str, Any]:
            """Explain rejected/degraded sources or non-selected jobs."""
            from job_ftch.application.search_session import explanation_to_dict

            explanation = await self._require_runner().explain_search_session(
                session_id,
                source_id=source_id,
                job_id=job_id,
            )
            return explanation_to_dict(explanation)

        @self.app.tool
        async def cancel_search_session(session_id: str) -> dict[str, Any]:
            """Cancel a search session (cooperative if a run is in flight)."""
            from job_ftch.application.search_session import session_to_public_dict

            session = await self._require_runner().cancel_search_session(session_id)
            return session_to_public_dict(session)

        @self.app.tool
        async def search_jobs(
            query: str,
            tenant_id: str | None = None,
            user_id: str | None = None,
            limit: int = 20,
        ) -> list[dict[str, Any]]:
            limit = min(limit, 100)
            groups = await self._require_runner().search_jobs(
                query, tenant_id=tenant_id, user_id=user_id, limit=limit
            )
            from job_ftch.adapters.mcp.product_surface import public_job_group

            return [public_job_group(group) for group in groups]

        @self.app.tool
        async def get_job(job_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
            job = await self._require_runner().get_job(job_id, tenant_id=tenant_id)
            return None if job is None else job.model_dump(mode="json")

        @self.app.tool
        async def get_job_lineage(
            job_id: str,
            tenant_id: str | None = None,
        ) -> dict[str, Any] | None:
            lineage = await self._require_runner().get_job_lineage(job_id, tenant_id=tenant_id)
            return None if lineage is None else lineage.model_dump(mode="json")

        @self.app.tool
        async def reset_tenant(tenant_id: str) -> None:
            await self._require_runner().reset_tenant(tenant_id)

        @self.app.tool
        async def clear_run_data(
            tenant_id: str,
            clear_output_artifacts: bool = True,
        ) -> dict[str, int]:
            """Clear run-scoped state before a /run-like ingest without deleting profiles."""
            runner = self._require_runner()
            counts = await runner.clear_run_data(tenant_id)
            if clear_output_artifacts:
                counts["output_artifacts"] = _clear_output_artifacts(
                    runner.get_runtime(tenant_id).settings
                )
            return counts

        @self.app.resource("jobs://{tenant_id}/latest")
        async def latest_jobs_resource(tenant_id: str) -> str:
            jobs = await self._require_runner().latest_jobs(tenant_id, limit=10)
            return json.dumps(
                [job.model_dump(mode="json") for job in jobs],
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("jobs://{tenant_id}/run_summary")
        async def run_summary_resource(tenant_id: str) -> str:
            summary = await self._require_runner().get_status(tenant_id)
            return json.dumps(
                None if summary is None else summary.as_dict(),
                ensure_ascii=False,
                default=_json_default,
            )

        @self.app.resource("config://{tenant_id}")
        async def config_resource(tenant_id: str) -> str:
            config = await self._require_runner().get_config(tenant_id)
            return json.dumps(config, ensure_ascii=False, default=_json_default)

    def _require_runner(self) -> TenantRunner:
        if self.runner is None:
            msg = "TenantMCPServer.startup() must run before using MCP tools."
            raise RuntimeError(msg)
        return self.runner


def create_server(
    *,
    configs_dir: str | Path,
    base_settings: Settings | None = None,
    name: str = "job_ftch",
) -> TenantMCPServer:
    return TenantMCPServer(
        configs_dir=Path(configs_dir),
        base_settings=base_settings,
        name=name,
    )
