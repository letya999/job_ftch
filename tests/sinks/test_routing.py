import pytest

from job_ftch.sinks.routing import RoutingSink


@pytest.mark.anyio
async def test_unmatched_item_is_not_silently_discarded() -> None:
    sink: RoutingSink[str] = RoutingSink([])

    with pytest.raises(RuntimeError, match="matches no configured route"):
        await sink.emit("unrouted")
