"""Independent checks for retry-count type validation."""

import pytest
from pocketflow import AsyncNode, Node


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
@pytest.mark.parametrize("value", [1.5, True, "2", None])
def test_non_integer_retry_count_is_rejected_at_construction(node_type, value) -> None:
    with pytest.raises(TypeError, match="max_retries"):
        node_type(max_retries=value)


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
def test_positive_integer_retry_count_remains_supported(node_type) -> None:
    assert node_type(max_retries=1).max_retries == 1
    assert node_type(max_retries=3).max_retries == 3
