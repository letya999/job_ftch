"""Low-cardinality CLIProxy dispatch telemetry without payloads or identities."""

from opentelemetry.metrics import get_meter

meter = get_meter("job_ftch.cliproxy_dispatch")
attempts = meter.create_counter("job_ftch.cliproxy.attempts")
outcomes = meter.create_counter("job_ftch.cliproxy.outcomes")
waits = meter.create_counter("job_ftch.cliproxy.waits")
transitions = meter.create_counter("job_ftch.cliproxy.circuit_transitions")
latency = meter.create_histogram("job_ftch.cliproxy.request_seconds", unit="s")
queue_age = meter.create_histogram("job_ftch.cliproxy.queue_oldest_seconds", unit="s")
