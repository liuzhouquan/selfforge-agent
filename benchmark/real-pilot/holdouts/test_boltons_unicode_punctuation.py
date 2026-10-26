"""Independent checks for Unicode punctuation in boltons.strutils."""

from boltons.strutils import slugify, split_punct_ws


def test_em_dash_separates_words() -> None:
    assert split_punct_ws("hello—world") == ["hello", "world"]
    assert slugify("hello—world") == "hello_world"


def test_chinese_comma_preserves_letters() -> None:
    assert split_punct_ws("你好，世界") == ["你好", "世界"]
    assert slugify("你好，世界") == "你好_世界"


def test_arabic_comma_preserves_letters() -> None:
    assert split_punct_ws("مرحبا،العالم") == ["مرحبا", "العالم"]


def test_multiple_unicode_punctuation_marks() -> None:
    assert slugify("«Bonjour»·Monde") == "bonjour_monde"


def test_existing_ascii_behavior() -> None:
    assert split_punct_ws("First post! Hi!!!!~1    ") == ["First", "post", "Hi", "1"]
    assert slugify("First post! Hi!!!!~1    ") == "first_post_hi_1"
