"""Holdout tests for jmespath replace() function contract with independent controls."""

import jmespath
import pytest
from jmespath import exceptions


@pytest.mark.parametrize(
    "text,search,replacement,expected",
    [
        ("hello world", "world", "there", "hello there"),
        ("foo bar foo", "foo", "baz", "baz bar baz"),
        ("banana", "a", "o", "bonono"),
        ("no change here", "missing", "found", "no change here"),
        ("", "a", "b", ""),
        ("a-b-c", "-", "", "abc"),
        ("xyz", "", "-", "-x-y-z-"),
        ("12345", "234", "99", "1995"),
    ],
)
def test_replace_string_substitutions(text, search, replacement, expected):
    result = jmespath.search(
        "replace(text, search, replacement)",
        {"text": text, "search": search, "replacement": replacement},
    )
    assert result == expected


def test_replace_in_piped_and_nested_expressions():
    data = {"path": "a/b/c", "sep": "/", "rep": "."}
    assert jmespath.search("replace(path, sep, rep)", data) == "a.b.c"
    assert jmespath.search("replace(path, sep, rep) | length(@)", data) == 5


@pytest.mark.parametrize(
    "bad_val,bad_search,bad_rep",
    [
        (123, "a", "b"),
        (["not", "string"], "a", "b"),
        ({"key": "val"}, "a", "b"),
        ("valid", 456, "b"),
        ("valid", None, "b"),
        ("valid", ["bad"], "b"),
        ("valid", "a", 789),
        ("valid", "a", None),
        ("valid", "a", {"bad": "type"}),
    ],
)
def test_replace_type_errors(bad_val, bad_search, bad_rep):
    with pytest.raises(exceptions.JMESPathTypeError):
        jmespath.search(
            "replace(val, search, rep)",
            {"val": bad_val, "search": bad_search, "rep": bad_rep},
        )


def test_replace_arity_errors():
    with pytest.raises(exceptions.ArityError):
        jmespath.search("replace(val)", {"val": "hello"})

    with pytest.raises(exceptions.ArityError):
        jmespath.search("replace(val, search)", {"val": "hello", "search": "l"})

    with pytest.raises(exceptions.ArityError):
        jmespath.search(
            "replace(val, search, rep, extra)",
            {"val": "hello", "search": "l", "rep": "p", "extra": 1},
        )


def test_existing_jmespath_functions_unaffected():
    assert jmespath.search("join(sep, items)", {"sep": "-", "items": ["x", "y"]}) == "x-y"
    assert jmespath.search("reverse(text)", {"text": "abc"}) == "cba"
    assert jmespath.search("length(items)", {"items": [1, 2, 3]}) == 3
