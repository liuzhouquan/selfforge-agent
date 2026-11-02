"""Independent holdout checks for the documented split maxsplit range in more-itertools."""

import pytest
from more_itertools import split_after, split_at, split_before, split_when


@pytest.mark.parametrize(
    "splitter, args",
    [
        (split_at, ("a,b,c", lambda item: item == ",")),
        (split_before, ("a,b,c", lambda item: item == ",")),
        (split_after, ("a,b,c", lambda item: item == ",")),
        (split_when, ("a,b,c", lambda left, right: left == "," or right == ",")),
    ],
)
@pytest.mark.parametrize("maxsplit", [-2, -5, -10])
def test_values_below_minus_one_are_rejected(splitter, args, maxsplit) -> None:
    with pytest.raises(ValueError, match="maxsplit"):
        list(splitter(*args, maxsplit=maxsplit))


def test_minus_one_and_zero_keep_their_documented_meaning() -> None:
    assert list(split_at("a,b,c", lambda item: item == ",", maxsplit=-1)) == [
        ["a"], ["b"], ["c"]
    ]
    assert list(split_at("a,b,c", lambda item: item == ",", maxsplit=0)) == [
        ["a", ",", "b", ",", "c"]
    ]
    assert list(split_at("a,b,c", lambda item: item == ",", maxsplit=1)) == [
        ["a"], ["b", ",", "c"]
    ]
