"""Independent checks for node transition and action parameter validation in PocketFlow."""

import pytest
from pocketflow import Node


@pytest.fixture()
def node() -> Node:
    return Node()


@pytest.fixture()
def target() -> Node:
    return Node()


@pytest.mark.parametrize("action", [123, None, ["act"], 1.5, False])
def test_next_invalid_action_rejected(node: Node, target: Node, action: object) -> None:
    with pytest.raises(TypeError, match="Action must be a string"):
        node.next(target, action=action)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_node", ["not_a_node", 123, None, [], object()])
def test_next_invalid_node_rejected(node: Node, invalid_node: object) -> None:
    with pytest.raises(TypeError, match="Successor must be an instance of BaseNode"):
        node.next(invalid_node)  # type: ignore[arg-type]


def test_rshift_invalid_node_rejected(node: Node) -> None:
    with pytest.raises(TypeError, match="Successor must be an instance of BaseNode"):
        _ = node >> "not_a_node"  # type: ignore[operator]


def test_conditional_rshift_invalid_node_rejected(node: Node) -> None:
    with pytest.raises(TypeError, match="Successor must be an instance of BaseNode"):
        _ = (node - "custom") >> "not_a_node"  # type: ignore[operator]


def test_valid_transitions_preserved(node: Node, target: Node) -> None:
    n3 = Node()
    assert node.next(target) is target
    assert (target >> n3) is n3
    n4 = Node()
    assert (node - "branch" >> n4) is n4
    assert node.successors["default"] is target
    assert node.successors["branch"] is n4
    assert target.successors["default"] is n3
