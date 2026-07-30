"""Closed, three-valued condition DSL for type-preserving graph stages."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ConditionResult(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


_COMPARATORS = frozenset({"eq", "in", "lt", "lte", "gt", "gte", "exists"})
_OPERATORS = frozenset({"all", "any", "not", *_COMPARATORS})
_ALLOWED_REFS = (
    "claims.",
    "assessment.",
)


def validate_condition(condition: Any) -> None:
    """Reject executable or metadata-based conditions at graph compile time."""
    if not isinstance(condition, dict) or len(condition) != 1:
        raise ValueError("condition must be a single-operation mapping")
    operation, value = next(iter(condition.items()))
    if operation not in _OPERATORS:
        raise ValueError(f"unsupported condition operation: {operation!r}")
    if operation in {"all", "any"}:
        if not isinstance(value, list) or not value:
            raise ValueError(f"condition {operation} requires a non-empty list")
        for child in value:
            validate_condition(child)
        return
    if operation == "not":
        validate_condition(value)
        return
    if not isinstance(value, dict) or set(value) - {"ref", "value"}:
        raise ValueError(f"condition {operation} requires ref and optional value")
    ref = value.get("ref")
    if not isinstance(ref, str) or not ref.startswith(_ALLOWED_REFS):
        raise ValueError("condition ref must target allowlisted claims or assessment fields")
    if operation != "exists" and "value" not in value:
        raise ValueError(f"condition {operation} requires value")
    if operation == "in" and not isinstance(value.get("value"), list):
        raise ValueError("condition in requires a list value")


def evaluate_condition(condition: dict[str, Any], item: Any) -> ConditionResult:
    """Evaluate without exceptions; absent or incompatible values are unknown."""
    operation, argument = next(iter(condition.items()))
    if operation == "all":
        results = [evaluate_condition(child, item) for child in argument]
        if ConditionResult.FALSE in results:
            return ConditionResult.FALSE
        return (
            ConditionResult.TRUE
            if all(x == ConditionResult.TRUE for x in results)
            else ConditionResult.UNKNOWN
        )
    if operation == "any":
        results = [evaluate_condition(child, item) for child in argument]
        if ConditionResult.TRUE in results:
            return ConditionResult.TRUE
        return (
            ConditionResult.FALSE
            if all(x == ConditionResult.FALSE for x in results)
            else ConditionResult.UNKNOWN
        )
    if operation == "not":
        result = evaluate_condition(argument, item)
        return {
            ConditionResult.TRUE: ConditionResult.FALSE,
            ConditionResult.FALSE: ConditionResult.TRUE,
            ConditionResult.UNKNOWN: ConditionResult.UNKNOWN,
        }[result]
    value, exists = _resolve(item, argument["ref"])
    if operation == "exists":
        return ConditionResult.TRUE if exists and value is not None else ConditionResult.FALSE
    if not exists or value is None:
        return ConditionResult.UNKNOWN
    expected = argument["value"]
    try:
        if operation == "eq":
            matched = value == expected
        elif operation == "in":
            matched = value in expected
        elif operation == "lt":
            matched = value < expected
        elif operation == "lte":
            matched = value <= expected
        elif operation == "gt":
            matched = value > expected
        else:
            matched = value >= expected
    except (TypeError, KeyError):
        return ConditionResult.UNKNOWN
    return ConditionResult.TRUE if matched else ConditionResult.FALSE


def _resolve(item: Any, reference: str) -> tuple[Any, bool]:
    if reference.startswith("claims."):
        _, claim_name, *parts = reference.split(".")
        assessments = getattr(item, "assessments", ())
        matches = [
            assessment
            for assessment in assessments
            if str(getattr(assessment, "claim", "")).split(".")[-1] == claim_name
        ]
        # Multiple profiles/subjects cannot be reduced by a generic condition.
        # A graph must publish an explicit aggregate claim if it needs one.
        if len(matches) != 1:
            return None, False
        current: Any = matches[0]
        for part in parts:
            if not hasattr(current, part):
                return None, False
            current = getattr(current, part)
        return current, True
    resolved: Any = item
    for part in reference.split("."):
        if isinstance(resolved, dict):
            if part not in resolved:
                return None, False
            resolved = resolved[part]
        else:
            if not hasattr(resolved, part):
                return None, False
            resolved = getattr(resolved, part)
    return resolved, True
