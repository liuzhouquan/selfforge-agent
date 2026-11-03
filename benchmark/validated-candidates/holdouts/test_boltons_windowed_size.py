"""Independent checks for windowed size validation in boltons."""

import pytest
from boltons.iterutils import windowed, windowed_iter


@pytest.mark.parametrize("size", [0, -1, -2, -5])
def test_windowed_non_positive_size_rejected(size: int) -> None:
    with pytest.raises(ValueError, match="window size"):
        windowed(range(4), size)

    with pytest.raises(ValueError, match="window size"):
        windowed(range(4), size, fill=None)


@pytest.mark.parametrize("size", [0, -1, -2, -5])
def test_windowed_iter_non_positive_size_rejected(size: int) -> None:
    with pytest.raises(ValueError, match="window size"):
        list(windowed_iter(range(4), size))

    with pytest.raises(ValueError, match="window size"):
        list(windowed_iter(range(4), size, fill=None))


def test_positive_window_sizes_preserved() -> None:
    assert windowed(range(4), 2) == [(0, 1), (1, 2), (2, 3)]
    assert windowed(range(4), 3, fill=None) == [
        (0, 1, 2),
        (1, 2, 3),
        (2, 3, None),
        (3, None, None),
    ]
    assert windowed([], 3) == []
    assert windowed([1], 1) == [(1,)]
    assert list(windowed_iter(range(3), 1)) == [(0,), (1,), (2,)]
