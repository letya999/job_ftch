from pathlib import Path


def test_only_current_and_explicit_legacy_nodes_write_terminal_routing() -> None:
    root = Path(__file__).resolve().parents[2] / "job_ftch" / "nodes"
    writers = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"routing_decision":' in text or "routing_decision =" in text:
            writers.append(path.name)
    # H17/H21 stay as schema-v1 compatibility artifacts. Production graph-v2
    # separately enforces DecisionNode as its sole terminal owner.
    assert sorted(writers) == [
        "decision.py",
        "decision_aggregator.py",
        "review_resolution.py",
    ]
