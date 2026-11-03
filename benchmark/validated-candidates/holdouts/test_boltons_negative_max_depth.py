"""Independent checks for iter_find_files depth validation."""

import pytest
from boltons.fileutils import iter_find_files


@pytest.mark.parametrize("depth", [-1, -2, -10])
def test_negative_max_depth_is_rejected(tmp_path, depth: int) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    with pytest.raises(ValueError, match="max_depth"):
        list(iter_find_files(tmp_path, "*.txt", max_depth=depth))


def test_zero_depth_still_includes_root_files(tmp_path) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "nested.txt").write_text("nested", encoding="utf-8")
    assert list(iter_find_files(tmp_path, "*.txt", max_depth=0)) == [
        str(tmp_path / "root.txt")
    ]


def test_positive_and_none_depth_preserved(tmp_path) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "nested.txt").write_text("nested", encoding="utf-8")
    subnested = nested / "subnested"
    subnested.mkdir()
    (subnested / "subnested.txt").write_text("subnested", encoding="utf-8")

    res_1 = sorted(iter_find_files(tmp_path, "*.txt", max_depth=1))
    assert res_1 == sorted([str(nested / "nested.txt"), str(tmp_path / "root.txt")])

    res_none = sorted(iter_find_files(tmp_path, "*.txt", max_depth=None))
    assert res_none == sorted([
        str(nested / "nested.txt"),
        str(tmp_path / "root.txt"),
        str(subnested / "subnested.txt"),
    ])
