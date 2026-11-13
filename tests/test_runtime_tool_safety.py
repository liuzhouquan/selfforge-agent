"""Security and fail-closed boundary tests for ToolRegistry (SF-RUN-07)."""

import os
from pathlib import Path

from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.tools import ToolRegistry


def test_read_file_rejects_fifo_and_directory_without_blocking(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path)

    # 1. Reject FIFO without hanging/blocking
    fifo_path = tmp_path / "test_pipe"
    os.mkfifo(fifo_path)
    res_fifo = tools.execute("read_file", {"path": "test_pipe"})
    assert not res_fifo.ok
    assert "not a regular file" in res_fifo.content

    # 2. Reject directory
    dir_path = tmp_path / "test_dir"
    dir_path.mkdir()
    res_dir = tools.execute("read_file", {"path": "test_dir"})
    assert not res_dir.ok
    assert "not a regular file" in res_dir.content

    # 3. Reject nonexistent file
    res_missing = tools.execute("read_file", {"path": "missing.py"})
    assert not res_missing.ok
    assert "not a regular file" in res_missing.content

    # 4. Valid file read works as expected
    valid_file = tmp_path / "valid.py"
    valid_file.write_text("print('hello')\n", encoding="utf-8")
    res_valid = tools.execute("read_file", {"path": "valid.py"})
    assert res_valid.ok
    assert res_valid.content == "print('hello')\n"


def test_search_code_enforces_byte_budget_and_truncation_notice(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path)

    # MAX_RANGED_FILE_BYTES is 2_000_000 (2 MB per file limit)
    # MAX_SEARCH_BYTES is 8_000_000 (8 MB total search limit)
    # Create 5 files of ~1.8 MB each (total ~9 MB)
    chunk = "x = 1\n" * 300_000  # ~1.8 MB per file
    for i in range(1, 6):
        fname = f"file_{i}.py"
        (tmp_path / fname).write_text(chunk, encoding="utf-8")

    res = tools.execute("search_code", {"pattern": "TARGET_NOT_FOUND", "glob": "*.py"})
    assert res.ok
    assert "[search limit reached: byte budget exceeded]" in res.content


def test_search_code_handles_oserror_and_invalid_globs(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path)

    # 1. Broken symlink does not crash search
    broken = tmp_path / "broken.py"
    broken.symlink_to("nonexistent_target.py")

    # Regular searchable file
    target = tmp_path / "target.py"
    target.write_text("def find_me(): pass\n", encoding="utf-8")

    res = tools.execute("search_code", {"pattern": "find_me", "glob": "*.py"})
    assert res.ok
    assert "target.py:1: def find_me(): pass" in res.content

    # 2. Invalid or empty glob is rejected
    assert not tools.execute("search_code", {"pattern": "find_me", "glob": ""}).ok
    assert not tools.execute("search_code", {"pattern": "find_me", "glob": "   "}).ok
    assert not tools.execute("search_code", {"pattern": "", "glob": "*.py"}).ok


def test_edit_file_rejects_fifo_directory_and_broken_symlink(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path)

    # 1. Target is a FIFO
    fifo_path = tmp_path / "edit_pipe"
    os.mkfifo(fifo_path)
    res_fifo = tools.execute("edit_file", {"path": "edit_pipe", "old_text": "a", "new_text": "b"})
    assert not res_fifo.ok
    assert "not a regular file" in res_fifo.content

    # 2. Target is a directory
    dir_path = tmp_path / "edit_dir"
    dir_path.mkdir()
    res_dir = tools.execute("edit_file", {"path": "edit_dir", "old_text": "a", "new_text": "b"})
    assert not res_dir.ok
    assert "not a regular file" in res_dir.content

    # 3. Target is a broken symlink
    broken = tmp_path / "broken_edit.py"
    broken.symlink_to("missing.py")
    res_sym = tools.execute("edit_file", {"path": "broken_edit.py", "old_text": "", "new_text": "print(1)"})
    assert not res_sym.ok
    assert "symlink" in res_sym.content


def test_run_tests_rejects_fifo_target(tmp_path: Path) -> None:
    tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))

    fifo_path = tmp_path / "tests_pipe"
    os.mkfifo(fifo_path)

    res = tools.execute("run_tests", {"path": "tests_pipe"})
    assert not res.ok
    assert "not a regular file or directory" in res.content
