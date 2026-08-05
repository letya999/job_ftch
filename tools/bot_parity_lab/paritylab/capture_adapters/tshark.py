from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


class TSharkAdapterError(ValueError):
    pass


def tcpip_observation(export: object) -> dict[str, object]:
    """Convert a TShark `-T json` SYN export to privacy-safe observatory metadata."""
    if not isinstance(export, Sequence) or isinstance(export, (str, bytes)) or not export:
        raise TSharkAdapterError("TShark export must be a non-empty packet array")
    packets = [_layers(item) for item in export]
    syn_packets = [item for item in packets if _scalar(item.get("tcp.flags.syn")) == "1"]
    if not syn_packets:
        raise TSharkAdapterError("TShark export has no TCP SYN packet")
    first = syn_packets[0]
    ip_version = 6 if "ipv6.hlim" in first else 4
    observed_ttl = _int(first.get("ipv6.hlim" if ip_version == 6 else "ip.ttl"))
    options = _strings(first.get("tcp.option_kind"))[:32]
    option_names = tuple(_TCP_OPTION_NAMES.get(value, f"option-{value}") for value in options)
    timestamps = "8" in options
    metadata: dict[str, object] = {
        "ip_version": ip_version,
        "observed_ttl": observed_ttl,
        "tcp_window": _int(first.get("tcp.window_size_value")),
        "window_scale": _int(first.get("tcp.options.wscale.shift")),
        "mss": _int(first.get("tcp.options.mss_val")),
        "option_order": option_names,
        "sack_permitted": "4" in options,
        "timestamps": timestamps,
        "ecn": _scalar(first.get("tcp.flags.ecn")) == "1",
        "syn_retransmissions": max(0, len(syn_packets) - 1),
        "pacing_ms": _pacing_ms(syn_packets),
        "capture_shape_hash": _capture_shape_hash(first),
    }
    return {"source": "tshark-json", "metadata": metadata}


def _layers(packet: object) -> Mapping[str, object]:
    if not isinstance(packet, Mapping):
        raise TSharkAdapterError("packet must be an object")
    source = packet.get("_source")
    if not isinstance(source, Mapping) or not isinstance(source.get("layers"), Mapping):
        raise TSharkAdapterError("packet lacks _source.layers")
    return _flatten(source["layers"])


def _flatten(value: Mapping[object, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        name = str(key)
        if isinstance(item, Mapping):
            result.update(_flatten(item))
        else:
            result[name] = item
    return result


def _scalar(value: object) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (_scalar(value),) if value is not None else ()


def _int(value: object) -> int | None:
    raw = _scalar(value)
    try:
        return int(raw, 0)
    except ValueError:
        return None


def _pacing_ms(packets: list[Mapping[str, object]]) -> list[float]:
    values: list[float] = []
    for packet in packets:
        try:
            values.append(float(_scalar(packet.get("frame.time_relative"))) * 1000)
        except ValueError:
            continue
    return [round(value - values[0], 3) for value in values[:32]] if values else []


def _capture_shape_hash(layers: Mapping[str, object]) -> str:
    safe_shape = {
        key: layers[key]
        for key in sorted(layers)
        if key.startswith(("tcp.flags", "tcp.option", "tcp.window", "ip.version", "ipv6.version"))
    }
    encoded = json.dumps(safe_shape, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_TCP_OPTION_NAMES = {
    "0": "eol",
    "1": "nop",
    "2": "mss",
    "3": "wscale",
    "4": "sack",
    "5": "sack-blocks",
    "8": "timestamps",
}
