"""Holdout tests for jmespath split() function contract with independent controls."""

import jmespath
import pytest
from jmespath import exceptions


@pytest.mark.parametrize("text,sep,expected", [
    ("a,b,c", ",", ["a", "b", "c"]),
    ("apple--banana--cherry", "--", ["apple", "banana", "cherry"]),
    ("no_separator_here", "|", ["no_separator_here"]),
    ("a,,b", ",", ["a", "", "b"]),
    (",start_and_end,", ",", ["", "start_and_end", ""]),
    ("", ",", [""]),
])
def test_split_with_delimiters(text, sep, expected):
    result = jmespath.search("split(text, sep)", {"text": text, "sep": sep})
    assert result == expected


@pytest.mark.parametrize("text,expected", [
    ("hello", ["h", "e", "l", "l", "o"]),
    ("", []),
    ("你好世界", ["你", "好", "世", "界"]),
    ("a 1!", ["a", " ", "1", "!"]),
])
def test_split_empty_separator_yields_characters(text, expected):
    result = jmespath.search("split(text, empty_sep)", {"text": text, "empty_sep": ""})
    assert result == expected


def test_split_in_piped_and_indexed_expressions():
    data = {"csv": "alpha,beta,gamma", "delim": ","}
    assert jmespath.search("split(csv, delim)[0]", data) == "alpha"
    assert jmespath.search("split(csv, delim)[1]", data) == "beta"
    assert jmespath.search("split(csv, delim) | length(@)", data) == 3


@pytest.mark.parametrize("bad_val,bad_sep", [
    (123, ","),
    (["not", "string"], ","),
    ({"key": "val"}, ","),
    ("valid", 456),
    ("valid", None),
    ("valid", ["bad"]),
])
def test_split_type_errors(bad_val, bad_sep):
    with pytest.raises(exceptions.JMESPathTypeError):
        jmespath.search("split(val, sep)", {"val": bad_val, "sep": bad_sep})


def test_split_arity_errors():
    with pytest.raises(exceptions.ArityError):
        jmespath.search("split(val)", {"val": "hello"})

    with pytest.raises(exceptions.ArityError):
        jmespath.search("split(val, sep, extra)", {"val": "hello", "sep": ",", "extra": 1})


def test_existing_jmespath_functions_unaffected():
    assert jmespath.search("join(sep, items)", {"sep": "-", "items": ["x", "y"]}) == "x-y"
    assert jmespath.search("reverse(text)", {"text": "abc"}) == "cba"
    assert jmespath.search("length(items)", {"items": [1, 2, 3]}) == 3

