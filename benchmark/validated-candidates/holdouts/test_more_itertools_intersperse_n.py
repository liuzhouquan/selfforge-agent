"""Independent checks for intersperse parameter validation in more-itertools."""

import pytest
from more_itertools import intersperse


@pytest.mark.parametrize("n", [-1, -2, -5, -10])
@pytest.mark.parametrize("items", [[1, 2, 3], []])
def test_intersperse_negative_n_rejected(n: int, items: list[int]) -> None:
    with pytest.raises(ValueError, match="n must be > 0"):
        intersperse("!", items, n=n)


@pytest.mark.parametrize("items", [[1, 2, 3], []])
def test_intersperse_zero_n_rejected(items: list[int]) -> None:
    with pytest.raises(ValueError, match="n must be > 0"):
        intersperse("!", items, n=0)


def test_intersperse_positive_n_preserved() -> None:
    assert list(intersperse("!", [1, 2, 3])) == [1, "!", 2, "!", 3]
    assert list(intersperse("x", [1, 2, 3, 4], n=2)) == [1, 2, "x", 3, 4]
    assert list(intersperse("x", [], n=2)) == []
    assert list(intersperse("x", [1], n=2)) == [1]
