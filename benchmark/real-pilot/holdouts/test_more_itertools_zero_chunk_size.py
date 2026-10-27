"""Independent checks for zero-size chunked and sliced calls."""

import pytest
from more_itertools import chunked, sliced


@pytest.mark.parametrize("strict", [False, True])
def test_chunked_rejects_zero_at_call_time(strict: bool) -> None:
    with pytest.raises(ValueError):
        chunked("ABCDE", 0, strict=strict)


@pytest.mark.parametrize("strict", [False, True])
def test_sliced_rejects_zero_at_call_time(strict: bool) -> None:
    with pytest.raises(ValueError):
        sliced("ABCDE", 0, strict=strict)


def test_positive_sizes_and_chunked_none_still_work() -> None:
    assert list(chunked("ABCDE", 2)) == [["A", "B"], ["C", "D"], ["E"]]
    assert list(chunked("ABCDE", None)) == [["A", "B", "C", "D", "E"]]
    assert list(sliced("ABCDE", 2)) == ["AB", "CD", "E"]
