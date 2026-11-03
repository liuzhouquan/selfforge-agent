"""Independent holdout checks for retry-wait validation in PocketFlow."""

import math

import pytest
from pocketflow import (
    AsyncBatchNode,
    AsyncNode,
    AsyncParallelBatchNode,
    BatchNode,
    Node,
)


@pytest.mark.parametrize(
    "node_type",
    [
        Node,
        AsyncNode,
        BatchNode,
        AsyncBatchNode,
        AsyncParallelBatchNode,
    ],
)
@pytest.mark.parametrize("value", ["1", None, True, [1]])
def test_non_numeric_wait_is_rejected(node_type, value) -> None:
    with pytest.raises(TypeError, match="wait"):
        node_type(wait=value)


@pytest.mark.parametrize(
    "node_type",
    [
        Node,
        AsyncNode,
        BatchNode,
        AsyncBatchNode,
        AsyncParallelBatchNode,
    ],
)
@pytest.mark.parametrize("value", [-0.1, -1, math.inf, -math.inf, math.nan])
def test_negative_or_non_finite_wait_is_rejected(node_type, value) -> None:
    with pytest.raises(ValueError, match="wait"):
        node_type(wait=value)


@pytest.mark.parametrize(
    "node_type",
    [
        Node,
        AsyncNode,
        BatchNode,
        AsyncBatchNode,
        AsyncParallelBatchNode,
    ],
)
@pytest.mark.parametrize("value", [0, 0.01, 1, 2.5])
def test_finite_non_negative_wait_is_supported(node_type, value) -> None:
    assert node_type(wait=value).wait == value
