from __future__ import annotations

from paritylab.capture_adapters.tshark import tcpip_observation


def test_tshark_syn_export_becomes_privacy_safe_observation() -> None:
    export = [
        {
            "_source": {
                "layers": {
                    "frame": {"frame.time_relative": "0.000"},
                    "ip": {"ip.src": "192.0.2.10", "ip.ttl": "128"},
                    "tcp": {
                        "tcp.srcport": "52123",
                        "tcp.flags.syn": "1",
                        "tcp.flags.ecn": "0",
                        "tcp.window_size_value": "64240",
                        "tcp.option_kind": ["2", "4", "8", "1", "3"],
                        "tcp.options.mss_val": "1460",
                        "tcp.options.wscale.shift": "8",
                    },
                }
            }
        }
    ]
    observation = tcpip_observation(export)
    metadata = observation["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["option_order"] == ("mss", "sack", "timestamps", "nop", "wscale")
    assert metadata["capture_shape_hash"]
    serialized = str(observation)
    assert "192.0.2.10" not in serialized
    assert "52123" not in serialized
