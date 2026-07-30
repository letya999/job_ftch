"""Typed conversion helpers for validated graph parameters."""

from __future__ import annotations


def float_param(params: dict[str, object], name: str, default: float) -> float:
    """Read a numeric graph parameter while rejecting booleans and containers."""
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"graph parameter {name} must be a float")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"graph parameter {name} must be a float") from exc


def int_param(params: dict[str, object], name: str, default: int) -> int:
    """Read an integer graph parameter without silently truncating floats."""
    value = params.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"graph parameter {name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"graph parameter {name} must be an integer") from exc
    raise ValueError(f"graph parameter {name} must be an integer")


def str_param(params: dict[str, object], name: str, default: str) -> str:
    """Read a string graph parameter while rejecting booleans and containers."""
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"graph parameter {name} must be a string")
    return value
