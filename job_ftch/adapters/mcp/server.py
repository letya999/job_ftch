"""FastMCP server surface for multi-tenant job_ftch operations."""
# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import json
import logging
import os
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urljoin, urlsplit

from job_ftch.adapters.mcp import operator_surface as mcp_ops
from job_ftch.adapters.mcp import product_surface as mcp_examples
from job_ftch.application.logging import configure_logging
from job_ftch.application.source_inputs import build_source_spec_from_input
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings, get_settings

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
_SOURCE_ACTIONS = frozenset({"add", "update", "remove"})
_SESSION_ACTIONS = frozenset({"open", "status", "wait", "solve", "goto", "capture", "close"})
_PAGE_WHAT = frozenset({"listing", "detail", "challenge", "fingerprint"})
_ESCALATION_MODES = frozenset({"adaptive", "all"})
_SOLVE_MODES = frozenset({"none", "browser_wait", "provider"})
_CAPTCHA_PROVIDER_ENV: dict[str, str] = {
    "capsolver": "CAPSOLVER_API_KEY",
    "capmonster": "CAPMONSTER_API_KEY",
    "nextcaptcha": "NEXTCAPTCHA_API_KEY",
    "2captcha": "TWOCAPTCHA_API_KEY",
    "anticaptcha": "ANTICAPTCHA_API_KEY",
    "nopecha": "NOPECHA_API_KEY",
}
_RUNTIME_ENGINES: tuple[tuple[str, str, str, str], ...] = (
    ("stealth_browser", "playwright_stealth", "stealth", "stealth_browser"),
    ("playwright", "playwright", "stealth", "stealth_browser"),
    ("patchright", "patchright", "browser", "patchright_browser"),
    ("nodriver", "nodriver", "nodriver", "nodriver"),
    ("camoufox", "camoufox", "browser", "camoufox"),
    ("cloak", "cloakbrowser", "browser", "cloak"),
)
_RESIDENTIAL_PROBE_URL = "https://example.com"
_RESIDENTIAL_PROBE_TIMEOUT = 15.0
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
            for item in (data if isinstance(data, list) else [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        required = {settings.openai_model, settings.relevance_llm_model} - {None, ""}
        result.update(
            ok=True,
            reachable=True,
            models_sample=ids[:20],
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


def _job_id_from_payload(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    job_id = item.get("job_id")
    if isinstance(job_id, str) and job_id:
        return job_id
    nested = item.get("canonical_job")
    if isinstance(nested, dict):
        nested_id = nested.get("job_id")
        if isinstance(nested_id, str) and nested_id:
            return nested_id
    return None


def _residential_yaml_urls() -> list[str]:
    yaml_path = Path(__file__).resolve().parents[3] / "config" / "proxies.yaml"
    if not yaml_path.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - presence check only
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("residential") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _residential_env_urls() -> list[str]:
    env_val = os.environ.get("JOB_FTCH_RESIDENTIAL_PROXY_LIST", "")
    return [item.strip() for item in env_val.split(",") if item.strip()]


def _first_residential_proxy_url(settings: Settings) -> tuple[bool, str | None]:
    """Return (configured, first_url). first_url is never placed in public payloads."""
    urls = [*_residential_env_urls(), *_residential_yaml_urls()]
    gateway = str(getattr(settings, "proxy_gateway", "") or "").strip()
    if gateway:
        from job_ftch.infrastructure.bypass.proxy_bypass import GatewayProxyProvider

        provider = GatewayProxyProvider(
            provider=str(getattr(settings, "proxy_provider", "raw") or "raw"),
            gateway=gateway,
            user=str(getattr(settings, "proxy_user", "") or ""),
            password=str(getattr(settings, "proxy_pass", "") or ""),
            default_country=str(getattr(settings, "proxy_country_default", "") or ""),
            sticky_ttl_seconds=int(getattr(settings, "proxy_sticky_ttl_seconds", 600)),
        )
        urls.append(provider.get_proxy_url(domain="mcp-runtime-probe"))
    configured = bool(urls)
    return configured, (urls[0] if urls else None)


async def _probe_residential_proxy(proxy_url: str) -> tuple[bool, str | None]:
    import httpx

    error_class: str | None = None
    for _attempt in range(2):
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url, timeout=_RESIDENTIAL_PROBE_TIMEOUT
            ) as client:
                response = await client.get(_RESIDENTIAL_PROBE_URL, follow_redirects=True)
            return response.status_code < 500, None
        except Exception as exc:  # noqa: BLE001 - public result is class-only
            error_class = type(exc).__name__
    return False, error_class


def _captcha_runtime_status(settings: Settings) -> list[dict[str, Any]]:
    enabled = {str(name).strip().lower() for name in settings.captcha_enabled_providers}
    provider = str(settings.captcha_provider or "").strip().lower()
    labels = ["browser_wait"]
    for name in sorted(enabled | ({provider} if provider else set())):
        if name and name not in labels:
            labels.append(name)
    solvers: list[dict[str, Any]] = []
    for label in labels:
        if label == "browser_wait":
            solvers.append(
                {
                    "id": "browser_wait",
                    "key_present": True,
                    "reachable": None,
                }
            )
            continue
        env_name = _CAPTCHA_PROVIDER_ENV.get(label)
        present = bool(env_name and os.environ.get(env_name, "").strip())
        solvers.append(
            {
                "id": label,
                "key_present": present,
                "reachable": None,
            }
        )
    return solvers


def _engine_runtime_status(inventory: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    availability_by_engine: dict[str, str] = {}
    capabilities = inventory.get("capabilities") if isinstance(inventory, dict) else None
    if isinstance(capabilities, list):
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            engine = str(item.get("engine") or "")
            availability = item.get("availability")
            if engine and isinstance(availability, str):
                availability_by_engine[engine] = availability
    engines: dict[str, dict[str, Any]] = {}
    for public_id, module_name, extra, inventory_name in _RUNTIME_ENGINES:
        importable = _package_present(module_name)
        availability = availability_by_engine.get(inventory_name)
        available = availability == "available" if availability is not None else None
        payload: dict[str, Any] = {
            "importable": importable,
            "available": available if available is not None else importable,
            "inventory": availability,
        }
        if not importable:
            payload["install_hint"] = f"uv sync --extra {extra}"
        engines[public_id] = payload
    return engines


def _doctor_extras() -> dict[str, dict[str, Any]]:
    extras = dict(_runtime_package_status())
    if "playwright" not in extras:
        present = _package_present("playwright")
        extras["playwright"] = {
            "present": present,
            "extra": "stealth",
            "install_hint": None if present else "uv sync --extra stealth",
        }
    return extras


def _uniq_hints(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_doctor_report(
    *,
    ok: bool,
    extras: dict[str, dict[str, Any]],
    engines: dict[str, dict[str, Any]],
    bypass: dict[str, Any],
    llm: dict[str, Any],
    proxies: dict[str, Any],
    captcha_solvers: list[dict[str, Any]],
    install_hints: list[str],
) -> str:
    """Human-readable diagnosis. Never includes proxy URLs, keys, or cookies."""
    lines: list[str] = [
        f"overall: {'present' if ok else 'degraded'}",
        "",
        "extras:",
    ]
    for name, info in extras.items():
        extra = info.get("extra")
        hint = info.get("install_hint")
        if info.get("present"):
            lines.append(f"- {name}: present (extra {extra})")
            continue
        how = hint or (f"uv sync --extra {extra}" if extra else "install the matching extra")
        lines.append(f"- {name}: missing; {how}")

    lines.extend(["", "engines:"])
    for name, info in engines.items():
        inventory = info.get("inventory")
        hint = info.get("install_hint")
        if not info.get("importable"):
            how = hint or "install the matching extra"
            lines.append(f"- {name}: missing; {how}")
        elif not info.get("available"):
            lines.append(f"- {name}: degraded (importable, inventory={inventory})")
        else:
            inv = f", inventory={inventory}" if inventory is not None else ""
            lines.append(f"- {name}: present (importable{inv})")

    lines.append("")
    bypass_status = bypass.get("status")
    caps = bypass.get("capabilities")
    cap_n = len(caps) if isinstance(caps, list) else int(bypass.get("capability_count") or 0)
    if bypass_status == "ok" and cap_n:
        lines.append(f"bypass: present (status={bypass_status}, capabilities={cap_n})")
    elif bypass_status == "ok":
        lines.append("bypass: degraded (status=ok, no capabilities listed)")
    elif bypass_status:
        lines.append(f"bypass: missing (status={bypass_status})")
    else:
        lines.append("bypass: missing")

    lines.extend(["", "proxies:"])
    lines.append(f"- http list: {'present' if proxies.get('http_list_configured') else 'missing'}")
    lines.append(f"- gateway: {'present' if proxies.get('gateway_configured') else 'missing'}")
    if not proxies.get("residential_configured"):
        lines.append("- residential: missing")
    elif proxies.get("residential_reachable"):
        lines.append("- residential: present (hop reachable)")
    else:
        err = proxies.get("error_class")
        err_bit = f", error_class={err}" if err else ""
        lines.append(f"- residential: degraded (configured, hop not reachable{err_bit})")

    lines.extend(["", "captcha:"])
    for item in captcha_solvers:
        solver_id = str(item.get("id") or "unknown")
        if solver_id == "browser_wait":
            lines.append("- browser_wait: present")
        elif item.get("key_present"):
            lines.append(f"- {solver_id}: present (key present)")
        else:
            lines.append(
                f"- {solver_id}: missing; set the provider API key env (value never returned)"
            )

    lines.extend(["", "llm / CLIProxy:"])
    backend = llm.get("llm_backend")
    if backend != "openai":
        lines.append(f"- backend={backend}: present (no gateway required)")
    elif llm.get("ok") and llm.get("reachable"):
        extra = ""
        if not llm.get("configured_models_available"):
            extra = "; degraded: configured model missing"
        endpoint = llm.get("endpoint")
        loc = f" at {endpoint}" if isinstance(endpoint, str) and endpoint else ""
        lines.append(f"- backend=openai: present (GET /models{loc}){extra}")
    else:
        err = llm.get("error") or "unreachable"
        lines.append(
            "- backend=openai: missing; configure JOB_FTCH_OPENAI_BASE_URL / CLIProxy "
            f"and check GET /models ({err})"
        )

    if install_hints:
        lines.extend(["", "install_hints:"])
        for hint in install_hints:
            lines.append(f"- {hint}")
    return "\n".join(lines)


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

    async def _clear_run_data_impl(
        self,
        tenant_id: str,
        *,
        clear_output_artifacts: bool = True,
    ) -> dict[str, int]:
        runner = self._require_runner()
        counts = await runner.clear_run_data(tenant_id)
        if clear_output_artifacts:
            counts["output_artifacts"] = _clear_output_artifacts(
                runner.get_runtime(tenant_id).settings
            )
        return counts

    async def _get_status_impl(
        self,
        tenant_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        runner = self._require_runner()
        if run_id:
            summary = await runner.get_run(run_id, tenant_id=tenant_id)
            return {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "run": None if summary is None else summary.as_dict(),
            }
        payload = await self._get_tenant_status_impl(tenant_id)
        runs = await runner.list_runs(tenant_id=tenant_id, limit=20)
        payload["recent_runs"] = [item.as_dict() for item in runs]
        return payload

    async def _runtime_readiness(self) -> tuple[dict[str, Any], dict[str, Any] | None]:
        inventory: dict[str, Any] | None = None
        if self.runner is not None:
            inventory = await self._browser_capabilities_impl()
        llm = await probe_llm_backend(self.base_settings)
        configured, first_proxy = _first_residential_proxy_url(self.base_settings)
        if not configured or first_proxy is None:
            residential: dict[str, Any] = {
                "configured": False,
                "reachable": False,
                "error_class": None,
            }
        else:
            reachable, error_class = await _probe_residential_proxy(first_proxy)
            residential = {
                "configured": True,
                "reachable": reachable,
                "error_class": error_class,
            }
        engines = _engine_runtime_status(inventory)
        captcha_solvers = _captcha_runtime_status(self.base_settings)
        hints: list[str] = [
            str(item["install_hint"]) for item in engines.values() if item.get("install_hint")
        ]
        if self.base_settings.llm_backend == "openai" and not llm.get("ok"):
            hints.append("configure JOB_FTCH_OPENAI_BASE_URL / CLIProxy and check /models")
        if not configured:
            hints.append(
                "set JOB_FTCH_RESIDENTIAL_PROXY_LIST, config/proxies.yaml residential, or proxy gateway"
            )
        paid = [item for item in captcha_solvers if item["id"] != "browser_wait"]
        if paid and not any(item.get("key_present") for item in paid):
            hints.append("set the enabled captcha provider API key env (value never returned)")
        payload = {
            "engines": engines,
            "llm": llm,
            "residential_proxies": residential,
            "captcha_solvers": captcha_solvers,
            "install_hints": hints,
        }
        return payload, inventory

    async def _get_runtime_impl(self) -> dict[str, Any]:
        payload, _inventory = await self._runtime_readiness()
        return payload

    async def _doctor_impl(self) -> dict[str, Any]:
        runtime, inventory = await self._runtime_readiness()
        extras = _doctor_extras()
        bypass = inventory if inventory is not None else {"status": "unavailable"}
        residential = runtime["residential_proxies"]
        residential_configured = bool(residential.get("configured"))
        proxies = {
            "http_list_configured": bool(self.base_settings.http_proxy_list),
            "gateway_configured": bool(str(self.base_settings.proxy_gateway or "").strip()),
            "residential_configured": residential_configured,
            "residential_reachable": (
                bool(residential.get("reachable")) if residential_configured else None
            ),
            "error_class": residential.get("error_class") if residential_configured else None,
        }
        engines = runtime["engines"]
        llm = runtime["llm"]
        captcha_solvers = runtime["captcha_solvers"]
        llm_required_failed = self.base_settings.llm_backend == "openai" and (
            not bool(llm.get("ok")) or not bool(llm.get("configured_models_available"))
        )
        browsers_present = any(bool(item.get("importable")) for item in engines.values())
        ok = (not llm_required_failed) and browsers_present
        hints = list(runtime["install_hints"])
        for info in extras.values():
            hint = info.get("install_hint")
            if isinstance(hint, str):
                hints.append(hint)
        install_hints = _uniq_hints(hints)
        report = _build_doctor_report(
            ok=ok,
            extras=extras,
            engines=engines,
            bypass=bypass,
            llm=llm,
            proxies=proxies,
            captcha_solvers=captcha_solvers,
            install_hints=install_hints,
        )
        return {
            "ok": ok,
            "report": report,
            "extras": extras,
            "engines": engines,
            "bypass": bypass,
            "llm": llm,
            "proxies": proxies,
            "captcha_solvers": captcha_solvers,
            "install_hints": install_hints,
        }

    async def _update_source_impl(
        self,
        *,
        tenant_id: str,
        action: str,
        link: str | None = None,
        source_type: str | None = None,
        limit: int | None = None,
        source_name: str | None = None,
        source_id: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        normalized = (action or "").strip().lower()
        if normalized not in _SOURCE_ACTIONS:
            return {
                "error": "invalid_arguments",
                "message": "action must be one of add|update|remove",
                "action": action,
            }
        runner = self._require_runner()
        if normalized == "add":
            if not link or not link.strip():
                return {
                    "error": "invalid_arguments",
                    "message": "link is required for action=add",
                    "action": action,
                }
            spec = await build_source_spec_from_input(
                link,
                auth_provider=runner.get_runtime(tenant_id).auth_provider,
                source_type=source_type,
                limit=100 if limit is None else limit,
                source_name=source_name,
            )
            payload = await runner.add_source_spec(
                tenant_id,
                spec,
                added_via="mcp",
                input_value=link,
            )
            if isinstance(payload, dict):
                payload["action"] = "add"
            return payload
        if not source_id:
            return {
                "error": "invalid_arguments",
                "message": "source_id is required for action=update|remove",
                "action": action,
            }
        if normalized == "remove":
            payload = await mcp_ops.remove_source(
                runner,
                tenant_id=tenant_id,
                source_id=source_id,
            )
            if isinstance(payload, dict):
                payload["action"] = "remove"
            return payload
        patch: dict[str, Any] = {}
        if enabled is not None:
            patch["enabled"] = enabled
        if limit is not None:
            patch["limit"] = limit
        if not patch:
            return {
                "error": "invalid_arguments",
                "message": "enabled and/or limit is required for action=update",
                "action": action,
                "source_id": source_id,
            }
        payload = await mcp_ops.update_source(
            runner,
            tenant_id=tenant_id,
            source_id=source_id,
            patch=patch,
        )
        if isinstance(payload, dict):
            payload["action"] = "update"
        return payload

    async def _get_jobs_impl(
        self,
        *,
        tenant_id: str,
        query: str | None = None,
        job_id: str | None = None,
        limit: int = 20,
        include_lineage: bool = False,
        source_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        runner = self._require_runner()
        limit = min(max(int(limit), 1), 100)
        if source_id or run_id:
            runtime = runner.get_runtime(tenant_id)
            source_names: set[str] = set()
            if source_id:
                for source in await runner.list_sources(tenant_id):
                    if str(source.get("source_id") or "") != source_id:
                        continue
                    for value in (
                        source.get("source_name"),
                        (source.get("spec") or {}).get("source_name")
                        if isinstance(source.get("spec"), dict)
                        else None,
                    ):
                        if value:
                            source_names.add(str(value))
                    break
            candidates = await runtime.job_backend.list_jobs(limit=min(limit * 25, 1000), offset=0)

            def in_scope(job: Any) -> bool:
                if source_id and not source_names:
                    return False
                metadata = getattr(job, "metadata", {}) or {}
                if run_id and str(metadata.get("source_run_id") or "") != run_id:
                    return False
                return not source_names or str(getattr(job, "source_name", "")) in source_names

            scoped = [job for job in candidates if in_scope(job)]
            if job_id:
                scoped = [job for job in scoped if str(getattr(job, "job_id", "")) == job_id]
            if query:
                needle = query.casefold()
                scoped = [
                    job
                    for job in scoped
                    if needle
                    in "\n".join(
                        str(value or "") for value in (job.title, job.company, job.description)
                    ).casefold()
                ]
            jobs = [job.model_dump(mode="json") for job in scoped[:limit]]
            scoped_payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "source_id": source_id,
                "run_id": run_id,
                "scope": "source_run" if source_id and run_id else "scoped",
                "jobs": jobs,
                "count": len(jobs),
            }
            if include_lineage and jobs:
                first_id = _job_id_from_payload(jobs[0])
                if first_id:
                    lineage = await runner.get_job_lineage(first_id, tenant_id=tenant_id)
                    scoped_payload["lineage"] = (
                        None if lineage is None else lineage.model_dump(mode="json")
                    )
            return scoped_payload
        if job_id:
            job = await runner.get_job(job_id, tenant_id=tenant_id)
            payload: dict[str, Any] = {
                "tenant_id": tenant_id,
                "job_id": job_id,
                "job": None if job is None else job.model_dump(mode="json"),
                "jobs": [] if job is None else [job.model_dump(mode="json")],
                "count": 0 if job is None else 1,
            }
            if include_lineage:
                lineage = await runner.get_job_lineage(job_id, tenant_id=tenant_id)
                payload["lineage"] = None if lineage is None else lineage.model_dump(mode="json")
            return payload
        if query:
            groups = await runner.search_jobs(query, tenant_id=tenant_id, limit=limit)
            jobs = [mcp_examples.public_job_group(group) for group in groups]
            payload = {
                "tenant_id": tenant_id,
                "query": query,
                "jobs": jobs,
                "count": len(jobs),
            }
            if include_lineage and jobs:
                first_id = _job_id_from_payload(jobs[0])
                if first_id:
                    lineage = await runner.get_job_lineage(first_id, tenant_id=tenant_id)
                    payload["lineage"] = (
                        None if lineage is None else lineage.model_dump(mode="json")
                    )
            return payload
        latest = await runner.latest_jobs(tenant_id, limit=limit)
        jobs = [job.model_dump(mode="json") for job in latest]
        payload = {
            "tenant_id": tenant_id,
            "jobs": jobs,
            "count": len(jobs),
        }
        if include_lineage and jobs:
            first_id = _job_id_from_payload(jobs[0])
            if first_id:
                lineage = await runner.get_job_lineage(first_id, tenant_id=tenant_id)
                payload["lineage"] = None if lineage is None else lineage.model_dump(mode="json")
        return payload

    async def _run_pipeline_tool_impl(
        self,
        *,
        tenant_id: str | None,
        source_ids: list[str] | None,
        max_items: int | None,
        clear_first: bool,
        user_id: str | None,
        scope: str,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        cleared: dict[str, int] | None = None
        if clear_first:
            if not tenant_id:
                return {
                    "error": "invalid_arguments",
                    "message": "tenant_id is required when clear_first=true",
                }
            cleared = await self._clear_run_data_impl(tenant_id)
        result = await self._run_pipeline_impl(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
            source_ids=source_ids,
            max_items=max_items,
        )
        if cleared is not None and isinstance(result, dict):
            result["cleared"] = cleared
        return result

    async def _get_prefilter_status_tool_impl(
        self,
        tenant_id: str,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        from job_ftch.application.prefilter_artifacts import (
            get_prefilter_status,
            list_prefilter_artifacts,
        )

        settings = self._require_runner().get_runtime(tenant_id).settings
        payload = get_prefilter_status(
            settings,
            tenant_id=tenant_id,
            profile_id=profile_id,
        )
        payload["requirements"] = _prefilter_requirements_payload(profile_id)
        artifacts = list_prefilter_artifacts(
            settings,
            tenant_id=tenant_id,
            profile_id=profile_id,
        )
        payload["artifacts"] = artifacts.get("artifacts")
        payload["artifact_count"] = artifacts.get("count")
        return payload

    async def _promote_prefilter_tool_impl(
        self,
        *,
        tenant_id: str,
        artifact_id: str | None,
        threshold: float | None,
        require_gate_pass: bool,
        rollback: bool,
    ) -> dict[str, Any]:
        from job_ftch.application.prefilter_artifacts import (
            promote_prefilter,
            rollback_prefilter,
        )

        settings = self._require_runner().get_runtime(tenant_id).settings
        if rollback:
            return rollback_prefilter(
                settings,
                tenant_id=tenant_id,
                artifact_id=artifact_id,
            )
        if not artifact_id:
            return {
                "error": "invalid_arguments",
                "message": "artifact_id is required unless rollback=true",
            }
        return promote_prefilter(
            settings,
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            threshold=threshold,
            require_gate_pass=require_gate_pass,
        )

    async def _set_resume_impl(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resume_text: str,
        profile_id: str | None,
        activate: bool,
    ) -> dict[str, Any]:
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

    async def _probe_page_impl(
        self,
        *,
        tenant_id: str,
        url: str | None,
        source_id: str | None,
        what: str,
        engine: str,
        headed: bool,
        solve: str,
        max_items: int,
    ) -> dict[str, Any]:
        normalized = (what or "listing").strip().lower()
        if normalized not in _PAGE_WHAT:
            return {
                "error": "invalid_arguments",
                "message": "what must be one of listing|detail|challenge|fingerprint",
                "what": what,
            }
        solve_mode = (solve or "none").strip().lower()
        if solve_mode not in _SOLVE_MODES:
            return {
                "error": "invalid_arguments",
                "message": "solve must be none|browser_wait|provider",
                "solve": solve,
            }
        from job_ftch.application.source_operations import (
            run_browser_probe as run_browser_probe_op,
        )

        payload = await run_browser_probe_op(
            self._require_runner(),
            tenant_id=tenant_id,
            source_id=source_id,
            url=url,
            probe=normalized,
            engine=engine,
            headed=headed,
            max_items=max_items,
            solve=solve_mode,
        )
        payload["ingest"] = False
        return await self._with_setup_if_needed(payload, goal="browser")

    async def _browser_session_impl(
        self,
        *,
        action: str,
        tenant_id: str | None,
        session_id: str | None,
        source_id: str | None,
        url: str | None,
        engine: str,
        headed: bool,
        bypass: str | None,
        profile: str,
        manual_challenge: bool,
        artifact_type: str,
        solve: str | None,
    ) -> dict[str, Any]:
        from job_ftch.application.source_operations import (
            capture_browser_artifact as capture_browser_artifact_op,
        )
        from job_ftch.application.source_operations import (
            close_browser_session as close_browser_session_op,
        )
        from job_ftch.application.source_operations import (
            continue_browser_session as continue_browser_session_op,
        )
        from job_ftch.application.source_operations import (
            get_browser_session as get_browser_session_op,
        )
        from job_ftch.application.source_operations import (
            open_browser_session as open_browser_session_op,
        )

        normalized = (action or "").strip().lower()
        if normalized not in _SESSION_ACTIONS:
            return {
                "error": "invalid_arguments",
                "message": "action must be one of open|status|wait|solve|goto|capture|close",
                "action": action,
            }
        runner = self._require_runner()
        if normalized == "open":
            if not tenant_id:
                return {
                    "error": "invalid_arguments",
                    "message": "tenant_id is required for action=open",
                }
            payload = await open_browser_session_op(
                runner,
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
        if not session_id:
            return {
                "error": "invalid_arguments",
                "message": f"session_id is required for action={normalized}",
                "action": normalized,
            }
        if normalized == "status":
            return await get_browser_session_op(runner, session_id=session_id)
        if normalized == "capture":
            return await capture_browser_artifact_op(
                runner,
                session_id=session_id,
                artifact_type=artifact_type,
            )
        if normalized == "close":
            return await close_browser_session_op(runner, session_id=session_id)
        if normalized == "wait":
            instruction = "wait"
        elif normalized == "solve":
            mode = (solve or "browser_wait").strip().lower()
            instruction = "solve" if mode in {"", "none", "browser_wait"} else f"solve:{mode}"
        elif normalized == "goto":
            if not url:
                return {
                    "error": "invalid_arguments",
                    "message": "url is required for action=goto",
                    "action": normalized,
                    "session_id": session_id,
                }
            instruction = f"navigate {url}"
        else:
            instruction = None
        payload = await continue_browser_session_op(
            runner,
            session_id=session_id,
            instruction=instruction,
        )
        return await self._with_setup_if_needed(payload, goal="captcha")

    async def _run_source_tool_impl(
        self,
        *,
        tenant_id: str,
        source_id: str,
        engine: str | None,
        bypass: str | None,
        parser: str | None,
        escalation: str,
        solve: str,
        session_id: str | None,
        max_items: int | None,
        max_tier: str | None = None,
        personal_mode: bool = False,
    ) -> dict[str, Any]:
        normalized = (escalation or "adaptive").strip().lower()
        if normalized not in _ESCALATION_MODES:
            return {
                "error": "invalid_arguments",
                "message": "escalation must be adaptive|all",
                "escalation": escalation,
            }
        solve_mode = (solve or "none").strip().lower()
        if solve_mode not in _SOLVE_MODES:
            return {
                "error": "invalid_arguments",
                "message": "solve must be none|browser_wait|provider",
                "solve": solve,
            }
        from job_ftch.application.source_operations import run_source as run_source_op
        from job_ftch.application.source_operations import (
            run_source_escalation as run_source_escalation_op,
        )

        pin = bypass or engine
        if normalized == "all":
            payload = await run_source_escalation_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                strategy="all",
                max_tier=max_tier,
                max_items=5 if max_items is None else max_items,
                parser=parser,
                personal_mode=personal_mode,
                session_id=session_id,
            )
            goal = "bypass"
        else:
            payload = await run_source_op(
                self._require_runner(),
                tenant_id=tenant_id,
                source_id=source_id,
                max_items=max_items,
                parser=parser,
                bypass=pin,
                session_id=session_id,
                personal_mode=personal_mode,
            )
            goal = "protected_sites"
        payload["requested_solve"] = solve_mode
        payload["escalation"] = normalized
        return await self._with_setup_if_needed(payload, goal=goal)

    def _register_surface(self) -> None:
        surface = mcp_examples.resolve_surface()
        register_mass = surface in {"all", "mass"}
        register_personal = surface in {"all", "personal"}

        @self.app.tool
        async def list_tenants() -> list[dict[str, Any]]:
            tenants = await self._require_runner().list_tenants()
            return [tenant.model_dump(mode="json") for tenant in tenants]

        @self.app.tool
        async def get_status(tenant_id: str, run_id: str | None = None) -> dict[str, Any]:
            """Tenant snapshot + latest/recent runs + source degradation. Pass run_id for one run."""
            return await self._get_status_impl(tenant_id, run_id)

        @self.app.tool
        async def get_runtime() -> dict[str, Any]:
            """Live readiness for engines, LLM/CLIProxy, residential proxies, and captcha solvers."""
            return await self._get_runtime_impl()

        @self.app.tool
        async def doctor() -> dict[str, Any]:
            """Written diagnosis of extras, browsers, proxies, captcha, and CLIProxy. No secrets."""
            return await self._doctor_impl()

        @self.app.tool
        async def get_sources(
            tenant_id: str,
            include_health: bool = True,
            include_diagnostics: bool = True,
        ) -> dict[str, Any]:
            """List tenant sources with health, assessment, and recommended_route."""
            return await self._get_sources_impl(
                tenant_id,
                include_health=include_health,
                include_diagnostics=include_diagnostics,
            )

        @self.app.tool
        async def update_source(
            tenant_id: str,
            action: str,
            link: str | None = None,
            source_type: str | None = None,
            limit: int | None = None,
            source_name: str | None = None,
            source_id: str | None = None,
            enabled: bool | None = None,
        ) -> dict[str, Any]:
            """Add, patch enabled/limit, or remove a source."""
            return await self._update_source_impl(
                tenant_id=tenant_id,
                action=action,
                link=link,
                source_type=source_type,
                limit=limit,
                source_name=source_name,
                source_id=source_id,
                enabled=enabled,
            )

        @self.app.tool
        async def set_source_important(
            tenant_id: str,
            source_id: str,
            important: bool = True,
            note: str | None = None,
        ) -> dict[str, Any]:
            """Pin or unpin a source as operator-important. Survives quality windows."""
            try:
                return await self._require_runner().set_source_important(
                    tenant_id,
                    source_id,
                    important=important,
                    set_by="mcp",
                    note=note,
                )
            except (KeyError, ValueError) as exc:
                return {"error": "invalid_arguments", "message": str(exc)}

        @self.app.tool
        async def list_source_quality(tenant_id: str) -> dict[str, Any]:
            """Current important / reliable / rich / high_relevance source lists."""
            return await self._require_runner().list_source_quality(tenant_id)

        @self.app.tool
        async def get_jobs(
            tenant_id: str,
            query: str | None = None,
            job_id: str | None = None,
            limit: int = 20,
            include_lineage: bool = False,
            source_id: str | None = None,
            run_id: str | None = None,
        ) -> dict[str, Any]:
            """Latest/search jobs; source_id+run_id scopes audit reads to one ingest."""
            return await self._get_jobs_impl(
                tenant_id=tenant_id,
                query=query,
                job_id=job_id,
                limit=limit,
                include_lineage=include_lineage,
                source_id=source_id,
                run_id=run_id,
            )

        @self.app.tool
        async def update_shot(
            tenant_id: str,
            user_id: str,
            action: str,
            kind: str | None = None,
            label: str | None = None,
            text: str = "",
            texts: list[str] | None = None,
            index: int | None = None,
            profile_id: str | None = None,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            """List (default all shots), add, remove, clear, or compile resume/vacancy shots."""
            return await mcp_examples.update_operator_shot(
                self._require_runner(),
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                kind=kind,
                label=label,
                text=text,
                texts=texts,
                index=index,
                profile_id=profile_id,
                dry_run=dry_run,
            )

        if register_mass:

            @self.app.tool
            async def run_pipeline(
                tenant_id: str | None = None,
                source_ids: list[str] | None = None,
                max_items: int | None = None,
                clear_first: bool = False,
                user_id: str | None = None,
                scope: str = "tenant",
            ) -> dict[str, Any] | list[dict[str, Any]]:
                """Run one tenant or all tenants. clear_first wipes run state and output files."""
                return await self._run_pipeline_tool_impl(
                    tenant_id=tenant_id,
                    source_ids=source_ids,
                    max_items=max_items,
                    clear_first=clear_first,
                    user_id=user_id,
                    scope=scope,
                )

            @self.app.tool
            async def get_prefilter_status(
                tenant_id: str,
                profile_id: str | None = None,
            ) -> dict[str, Any]:
                """Dirty flag, current/previous artifacts, and dataset contract."""
                return await self._get_prefilter_status_tool_impl(tenant_id, profile_id)

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
                artifact_id: str | None = None,
                threshold: float | None = None,
                require_gate_pass: bool = True,
                rollback: bool = False,
            ) -> dict[str, Any]:
                """Promote an artifact after the eval gate, or rollback when rollback=true."""
                return await self._promote_prefilter_tool_impl(
                    tenant_id=tenant_id,
                    artifact_id=artifact_id,
                    threshold=threshold,
                    require_gate_pass=require_gate_pass,
                    rollback=rollback,
                )

        if register_personal:

            @self.app.tool
            async def set_resume(
                tenant_id: str,
                user_id: str,
                resume_text: str,
                profile_id: str | None = None,
                activate: bool = True,
            ) -> dict[str, Any]:
                """Ingest resume text into a managed profile and mark shots/prefilter dirty."""
                return await self._set_resume_impl(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    resume_text=resume_text,
                    profile_id=profile_id,
                    activate=activate,
                )

            @self.app.tool
            async def probe_page(
                tenant_id: str,
                url: str | None = None,
                source_id: str | None = None,
                what: str = "listing",
                engine: str = "auto",
                headed: bool = False,
                solve: str = "none",
                max_items: int = 5,
            ) -> dict[str, Any]:
                """Live listing/detail/challenge/fingerprint probe. This is not ingest."""
                return await self._probe_page_impl(
                    tenant_id=tenant_id,
                    url=url,
                    source_id=source_id,
                    what=what,
                    engine=engine,
                    headed=headed,
                    solve=solve,
                    max_items=max_items,
                )

            @self.app.tool
            async def browser_session(
                action: str,
                tenant_id: str | None = None,
                session_id: str | None = None,
                source_id: str | None = None,
                url: str | None = None,
                engine: str = "auto",
                headed: bool = True,
                bypass: str | None = None,
                profile: str = "ephemeral",
                manual_challenge: bool = False,
                artifact_type: str = "text",
                solve: str | None = None,
            ) -> dict[str, Any]:
                """Open, poll, wait/solve/goto, capture, or close an operator browser session."""
                return await self._browser_session_impl(
                    action=action,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    source_id=source_id,
                    url=url,
                    engine=engine,
                    headed=headed,
                    bypass=bypass,
                    profile=profile,
                    manual_challenge=manual_challenge,
                    artifact_type=artifact_type,
                    solve=solve,
                )

            @self.app.tool
            async def run_source(
                tenant_id: str,
                source_id: str,
                engine: str | None = None,
                bypass: str | None = None,
                parser: str | None = None,
                escalation: str = "adaptive",
                solve: str = "none",
                session_id: str | None = None,
                max_items: int | None = None,
                max_tier: str | None = None,
                personal_mode: bool = False,
            ) -> dict[str, Any]:
                """Adaptive ingest, pin engine/bypass, or walk the full ladder when escalation=all."""
                return await self._run_source_tool_impl(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    engine=engine,
                    bypass=bypass,
                    parser=parser,
                    escalation=escalation,
                    solve=solve,
                    session_id=session_id,
                    max_items=max_items,
                    max_tier=max_tier,
                    personal_mode=personal_mode,
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
