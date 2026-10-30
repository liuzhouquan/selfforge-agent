"""Depth limits must bound traversal, not only filter its yielded files."""

import os
from pathlib import Path

import pytest
from boltons import fileutils


def tree(root):
    for name in ("root.txt", "child/a.txt", "child/deep/b.txt", "other/c.log"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")


@pytest.mark.parametrize("depth", [0, 1])
@pytest.mark.parametrize("trailing_separator", [False, True])
def test_no_directory_scanned_below_limit(tmp_path, monkeypatch, depth, trailing_separator):
    tree(tmp_path)
    scanned = []
    scandir = os.scandir

    def observe(path):
        scanned.append(Path(path).relative_to(tmp_path).parts)
        return scandir(path)

    monkeypatch.setattr(os, "scandir", observe)
    directory = str(tmp_path) + (os.sep if trailing_separator else "")
    names = {Path(p).relative_to(tmp_path).as_posix()
             for p in fileutils.iter_find_files(directory, "*.txt", max_depth=depth)}
    assert names == ({"root.txt"} if depth == 0 else {"root.txt", "child/a.txt"})
    assert scanned and max(map(len, scanned)) <= depth


def test_boundary_directories_remain_visible(tmp_path):
    tree(tmp_path)
    names = {Path(p).relative_to(tmp_path).as_posix() for p in
             fileutils.iter_find_files(tmp_path, "*", include_dirs=True, max_depth=0)}
    assert names == {"root.txt", "child", "other"}


def test_unlimited_traversal_and_patterns_unchanged(tmp_path):
    tree(tmp_path)
    names = {Path(p).relative_to(tmp_path).as_posix() for p in
             fileutils.iter_find_files(tmp_path, ["*.txt", "*.log"], ignored="a.*")}
    assert names == {"root.txt", "child/deep/b.txt", "other/c.log"}


def test_ignored_directory_name_does_not_prune_descendants(tmp_path):
    tree(tmp_path)
    names = {Path(p).relative_to(tmp_path).as_posix() for p in
             fileutils.iter_find_files(tmp_path, "*", ignored="child", include_dirs=True,
                                       max_depth=1)}
    assert "child" not in names
    assert {"child/a.txt", "child/deep"} <= names


def test_unrelated_directories_are_not_pruned_early(tmp_path):
    tree(tmp_path)
    names = {Path(p).relative_to(tmp_path).as_posix() for p in
             fileutils.iter_find_files(tmp_path, "*.txt", max_depth=2)}
    assert names == {"root.txt", "child/a.txt", "child/deep/b.txt"}
