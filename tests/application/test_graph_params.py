from __future__ import annotations

import pytest

from job_ftch.application.graph.params import float_param, int_param


def test_graph_numeric_params_accept_validated_numbers_and_strings() -> None:
    assert float_param({"threshold": 0.7}, "threshold", 0.5) == 0.7
    assert float_param({"threshold": "0.7"}, "threshold", 0.5) == 0.7
    assert int_param({"limit": 7}, "limit", 5) == 7
    assert int_param({"limit": "7"}, "limit", 5) == 7


@pytest.mark.parametrize("value", [True, None, [], {}, "invalid"])
def test_graph_float_param_rejects_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError, match="threshold must be a float"):
        float_param({"threshold": value}, "threshold", 0.5)


@pytest.mark.parametrize("value", [True, None, 1.5, [], {}, "1.5"])
def test_graph_int_param_rejects_non_integer_values(value: object) -> None:
    with pytest.raises(ValueError, match="limit must be an integer"):
        int_param({"limit": value}, "limit", 5)
