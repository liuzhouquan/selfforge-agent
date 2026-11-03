"""Independent checks for chunked count validation in boltons."""

import pytest
from boltons.iterutils import chunked


@pytest.mark.parametrize("count", [-1, -2, -5, -10])
def test_chunked_negative_count_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="expected a positive integer chunk count"):
        chunked(range(10), 3, count=count)


@pytest.mark.parametrize("count", [-1, -2, -5, -10])
def test_chunked_empty_src_negative_count_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="expected a positive integer chunk count"):
        chunked([], 3, count=count)


@pytest.mark.parametrize("count", [-1, -2])
def test_chunked_fill_negative_count_rejected(count: int) -> None:
    with pytest.raises(ValueError, match="expected a positive integer chunk count"):
        chunked(range(10), 3, count=count, fill=None)


def test_chunked_valid_count_preserved() -> None:
    assert chunked(range(10), 3, count=0) == []
    assert chunked(range(10), 3, count=1) == [[0, 1, 2]]
    assert chunked(range(10), 3, count=2) == [[0, 1, 2], [3, 4, 5]]
    assert chunked(range(10), 3, count=None) == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
    assert chunked([], 3, count=0) == []
    assert chunked([], 3, count=2) == []
