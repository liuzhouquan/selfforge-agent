"""Independent checks for retry-count type validation."""

import pytest
from pocketflow import AsyncBatchNode, AsyncNode, AsyncParallelBatchNode, BatchNode, Node


@pytest.mark.parametrize(
    "node_type", [Node, AsyncNode, BatchNode, AsyncBatchNode, AsyncParallelBatchNode]
)
@pytest.mark.parametrize("value", [1.5, True, False, "2", None, 3.14])
def test_non_integer_retry_count_is_rejected_at_construction(node_type, value) -> None:
    with pytest.raises(TypeError, match="max_retries"):
        node_type(max_retries=value)


@pytest.mark.parametrize(
    "node_type", [Node, AsyncNode, BatchNode, AsyncBatchNode, AsyncParallelBatchNode]
)
@pytest.mark.parametrize("value", [1, 3, 10])
def test_positive_integer_retry_count_remains_supported(node_type, value) -> None:
    assert node_type(max_retries=value).max_retries == value
