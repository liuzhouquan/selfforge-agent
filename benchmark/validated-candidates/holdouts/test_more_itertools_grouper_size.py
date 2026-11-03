"""Independent checks for non-positive grouper sizes in more-itertools."""

import pytest
from more_itertools import grouper


@pytest.mark.parametrize("incomplete", ["fill", "ignore", "strict"])
@pytest.mark.parametrize("size", [0, -1, -2, -5])
def test_non_positive_size_is_rejected_at_call_time(incomplete: str, size: int) -> None:
    with pytest.raises(ValueError):
        grouper("ABCDE", size, incomplete=incomplete)


def test_positive_sizes_preserve_existing_modes() -> None:
    assert list(grouper("ABCDE", 2, incomplete="fill")) == [
        ("A", "B"),
        ("C", "D"),
        ("E", None),
    ]
    assert list(grouper("ABCDE", 2, incomplete="ignore")) == [
        ("A", "B"),
        ("C", "D"),
    ]
    assert list(grouper("ABCD", 2, incomplete="strict")) == [
        ("A", "B"),
        ("C", "D"),
    ]


def test_invalid_incomplete_mode_still_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Expected fill, strict, or ignore"):
        list(grouper("ABCD", 2, incomplete="bogus"))
