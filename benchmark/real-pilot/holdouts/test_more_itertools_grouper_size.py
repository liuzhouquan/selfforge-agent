"""Independent checks for non-positive grouper sizes in more-itertools."""

import pytest
from more_itertools import grouper


@pytest.mark.parametrize("incomplete", ["fill", "ignore", "strict"])
@pytest.mark.parametrize("size", [0, -2])
def test_non_positive_size_is_rejected(incomplete: str, size: int) -> None:
    with pytest.raises(ValueError):
        grouper("ABCDE", size, incomplete=incomplete)


def test_positive_sizes_preserve_existing_modes() -> None:
    assert list(grouper("ABCDE", 2, incomplete="fill")) == [
        ("A", "B"), ("C", "D"), ("E", None),
    ]
    assert list(grouper("ABCDE", 2, incomplete="ignore")) == [
        ("A", "B"), ("C", "D"),
    ]
    assert list(grouper("ABCD", 2, incomplete="strict")) == [
        ("A", "B"), ("C", "D"),
    ]
