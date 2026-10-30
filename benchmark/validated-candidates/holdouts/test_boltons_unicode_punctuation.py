"""Feature checks plus unchanged-letter/symbol and ASCII controls."""

import pytest
from boltons.strutils import slugify, split_punct_ws


@pytest.mark.parametrize("text,words", [
    ("hello—world", ["hello", "world"]),
    ("你好，世界", ["你好", "世界"]),
    ("مرحبا،العالم", ["مرحبا", "العالم"]),
    ("«Bonjour»…Monde", ["Bonjour", "Monde"]),
    ("left（middle）right", ["left", "middle", "right"]),
])
def test_unicode_punctuation_separates_words(text, words):
    assert split_punct_ws(text) == words
    assert slugify(text) == "_".join(words).lower()


@pytest.mark.parametrize("text", ["café", "e\u0301", "alpha∑beta", "hello🙂world", ""])
def test_nonpunctuation_unicode_is_preserved(text):
    assert split_punct_ws(text) == ([text] if text else [])
    assert slugify(text) == text.lower()


def test_existing_ascii_punctuation_and_delimiter_controls():
    assert split_punct_ws("First post! Hi!!!!~1    ") == ["First", "post", "Hi", "1"]
    assert split_punct_ws("a+b$c_d") == ["a", "b", "c", "d"]
    assert slugify("Hello—World", delim="-", lower=False) == "Hello-World"
