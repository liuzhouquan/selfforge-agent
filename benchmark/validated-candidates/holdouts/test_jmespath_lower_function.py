"""Holdout tests for jmespath lower() function contract with independent controls."""

import jmespath
import pytest
from jmespath import exceptions


@pytest.mark.parametrize(
    "text,expected",
    [
        ("HELLO WORLD", "hello world"),
        ("Hello World", "hello world"),
        ("foo_bar-123", "foo_bar-123"),
        ("already lower", "already lower"),
        ("ALL CAPS STRING WITH SYMBOLS #!$", "all caps string with symbols #!$"),
        ("", ""),
        ("CafÉ", "café"),
        ("ÄÖÜ", "äöü"),
    ],
)
def test_lower_basic_casing(text, expected):
    result = jmespath.search("lower(@)", text)
    assert result == expected


def test_lower_in_projections_and_pipes():
    data = {
        "users": [
            {"username": "ALICE_DOE", "role": "ADMIN"},
            {"username": "BOB_SMITH", "role": "USER"},
        ],
        "meta": {"tag": "RELEASE_CANDIDATE_1"},
    }
    assert jmespath.search("users[*].lower(username)", data) == ["alice_doe", "bob_smith"]
    assert jmespath.search("lower(meta.tag)", data) == "release_candidate_1"
    assert jmespath.search("lower(meta.tag) | length(@)", data) == 19


@pytest.mark.parametrize(
    "bad_val",
    [
        123,
        45.67,
        True,
        False,
        ["not", "a", "string"],
        [],
        {"key": "val"},
        {},
        None,
    ],
)
def test_lower_type_errors(bad_val):
    with pytest.raises(exceptions.JMESPathTypeError):
        jmespath.search("lower(@)", bad_val)


def test_lower_arity_errors():
    with pytest.raises(exceptions.ArityError):
        jmespath.search("lower()", "hello")

    with pytest.raises(exceptions.ArityError):
        jmespath.search("lower(@, @)", "hello")

    with pytest.raises(exceptions.ArityError):
        jmespath.search("lower(@, @, @)", "hello")


def test_existing_jmespath_functions_unaffected():
    assert jmespath.search("join(sep, items)", {"sep": "-", "items": ["x", "y"]}) == "x-y"
    assert jmespath.search("reverse(text)", {"text": "abc"}) == "cba"
    assert jmespath.search("length(items)", {"items": [1, 2, 3]}) == 3

