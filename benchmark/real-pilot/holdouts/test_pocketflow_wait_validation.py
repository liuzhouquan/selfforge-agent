"""Independent checks for retry-wait validation."""

import math

import pytest
from pocketflow import AsyncNode, Node


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
@pytest.mark.parametrize("value", ["1", None, True])
def test_non_numeric_wait_is_rejected(node_type, value) -> None:
    with pytest.raises(TypeError, match="wait"):
        node_type(wait=value)


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
@pytest.mark.parametrize("value", [-0.1, math.inf, -math.inf, math.nan])
def test_negative_or_non_finite_wait_is_rejected(node_type, value) -> None:
    with pytest.raises(ValueError, match="wait"):
        node_type(wait=value)


@pytest.mark.parametrize("node_type", [Node, AsyncNode])
def test_finite_non_negative_wait_is_supported(node_type) -> None:
    assert node_type(wait=0).wait == 0
    assert node_type(wait=0.01).wait == 0.01
