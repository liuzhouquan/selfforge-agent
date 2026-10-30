"""Explicit callback short-circuit contract with independent output controls."""

import pytest
from more_itertools import replace


@pytest.mark.parametrize("window", [1, 2, 5])
def test_zero_limit_never_calls_predicate(window):
    calls = []
    result = list(replace(iter(range(4)), lambda *args: calls.append(args) or True,
                          [99], count=0, window_size=window))
    assert result == [0, 1, 2, 3]
    assert calls == []


@pytest.mark.parametrize("window", [1, 2, 3])
def test_no_callbacks_after_final_replacement(window):
    calls = []

    def predicate(*args):
        calls.append(args)
        return args[0] == 1

    actual = list(replace(iter(range(8)), predicate, iter([90, 91]), count=1,
                          window_size=window))
    assert actual == [0, 90, 91, *range(1 + window, 8)]
    assert len(calls) == 2


def test_limit_counts_matches_not_callback_invocations():
    calls = []

    def predicate(item):
        calls.append(item)
        return item % 2 == 1

    assert list(replace(range(7), predicate, [], count=2)) == [0, 2, 4, 5, 6]
    assert calls == [0, 1, 2, 3]


def test_unlimited_mode_preserves_short_tail_and_callback_exceptions():
    calls = []
    assert list(replace(range(3), lambda *a: calls.append(a) or False, [],
                        window_size=2)) == [0, 1, 2]
    assert calls == [(0, 1), (1, 2), (2,)]

    def broken(_item):
        raise LookupError("before limit")

    with pytest.raises(LookupError, match="before limit"):
        list(replace([1], broken, [], count=1))


def test_input_errors_after_limit_are_not_swallowed():
    def source():
        yield 1
        yield 2
        raise LookupError("source failure")

    output = replace(source(), lambda _x: True, [99], count=1)
    assert next(output) == 99
    assert next(output) == 2
    with pytest.raises(LookupError, match="source failure"):
        next(output)


def test_window_validation_still_runs_with_zero_count():
    with pytest.raises(ValueError, match="window_size"):
        list(replace([1], lambda _x: False, [], count=0, window_size=0))
