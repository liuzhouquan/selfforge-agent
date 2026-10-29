"""Independent checks for iter_find_files depth validation."""

import pytest
from boltons.fileutils import iter_find_files


def test_negative_max_depth_is_rejected(tmp_path) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    with pytest.raises(ValueError, match="max_depth"):
        list(iter_find_files(tmp_path, "*.txt", max_depth=-1))


def test_zero_depth_still_includes_root_files(tmp_path) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "nested.txt").write_text("nested", encoding="utf-8")
    assert list(iter_find_files(tmp_path, "*.txt", max_depth=0)) == [
        str(tmp_path / "root.txt")
    ]
