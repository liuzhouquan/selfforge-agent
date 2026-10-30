"""Call-time validation with lazy, strict, and single-use iterator controls."""

import pytest
from more_itertools import chunked, sliced


@pytest.mark.parametrize("function", [chunked, sliced])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("source", [[], [1, 2, 3]])
def test_zero_size_rejected_at_call(function, strict, source):
    with pytest.raises(ValueError):
        function(source, 0, strict=strict)


@pytest.mark.parametrize("function", [chunked, sliced])
def test_positive_sizes_keep_incomplete_and_strict_behavior(function):
    assert list(function([1, 2, 3], 2)) == [[1, 2], [3]]
    output = function([1, 2, 3], 2, strict=True)
    assert next(output) == [1, 2]
    with pytest.raises(ValueError):
        next(output)
    assert list(function([1, 2, 3, 4], 2, strict=True)) == [[1, 2], [3, 4]]


def test_chunked_retains_none_and_lazy_single_pass_input():
    seen = []

    def source():
        for value in range(3):
            seen.append(value)
            yield value

    output = chunked(source(), 2)
    assert seen == []
    assert next(output) == [0, 1]
    assert seen == [0, 1]
    assert list(output) == [[2]]
    assert list(chunked(source(), None)) == [[0, 1, 2]]
    with pytest.raises(ValueError):
        chunked(source(), None, strict=True)


def test_sliced_retains_sequence_slice_types():
    assert list(sliced("abcde", 2)) == ["ab", "cd", "e"]
    assert list(sliced((1, 2, 3), 2)) == [(1, 2), (3,)]


@pytest.mark.parametrize("function", [chunked, sliced])
def test_negative_size_errors_preserved(function):
    with pytest.raises(ValueError):
        function([1, 2], -1)
