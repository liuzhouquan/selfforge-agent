"""Private evaluator behavior check; copied only after the coding run."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pocketflow import AsyncNode, Node


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
@pytest.mark.parametrize("retries", [0, -1])
def test_invalid_retry_count_is_rejected(node_type: type[Node], retries: int) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        node_type(max_retries=retries)


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
def test_positive_retry_count_is_supported(node_type: type[Node]) -> None:
    node = node_type(max_retries=2)
    assert node.max_retries == 2
