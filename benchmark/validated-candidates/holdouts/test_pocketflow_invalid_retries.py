"""Independent holdout checks for PocketFlow max_retries validation."""

import pytest
from pocketflow import (
    AsyncBatchNode,
    AsyncNode,
    AsyncParallelBatchNode,
    BatchNode,
    Node,
)


@pytest.mark.parametrize(
    "node_cls",
    [
        Node,
        AsyncNode,
        BatchNode,
        AsyncBatchNode,
        AsyncParallelBatchNode,
    ],
)
@pytest.mark.parametrize("invalid_retries", [0, -1, -5])
def test_invalid_max_retries_rejected(node_cls, invalid_retries):
    with pytest.raises(ValueError, match="max_retries"):
        node_cls(max_retries=invalid_retries)


@pytest.mark.parametrize(
    "node_cls",
    [
        Node,
        AsyncNode,
        BatchNode,
        AsyncBatchNode,
        AsyncParallelBatchNode,
    ],
)
@pytest.mark.parametrize("valid_retries", [1, 2, 5])
def test_positive_max_retries_supported(node_cls, valid_retries):
    node = node_cls(max_retries=valid_retries)
    assert node.max_retries == valid_retries


def test_default_max_retries_and_execution():
    node = Node()
    assert node.max_retries == 1

    called = []

    class SimpleNode(Node):
        def exec(self, prep_res):
            called.append(prep_res)
            return "ok"

    n = SimpleNode()
    shared = {}
    n.run(shared)
    assert called == [None]

