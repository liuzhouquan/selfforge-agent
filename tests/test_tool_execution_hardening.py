"""Hardening and resilience tests for ToolRegistry and execution (SF-TOOL-02)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

from selfforge_agent.runtime.execution import HostExecutor
from selfforge_agent.runtime.tools import (
    EditPolicy,
    ToolArgs,
    ToolDefinition,
    ToolOutcome,
    ToolRegistry,
)


class TestToolArgumentValidationAndSchema:
    def test_execute_rejects_non_dict_arguments(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        for bad_args in ("not-a-dict", [1, 2, 3], 12345, None, True):
            res = tools.execute("read_file", bad_args)  # type: ignore[arg-type]
            assert not res.ok
            assert "arguments must be a dictionary" in res.content

    def test_execute_rejects_invalid_tool_names(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        for bad_name in ("", "   ", "read_file\x00inject", "read file", "read/file", 123, None, "a" * 100):
            res = tools.execute(bad_name, {"path": "test.py"})  # type: ignore[arg-type]
            assert not res.ok
            assert "tool name" in res.content or "unknown tool" in res.content

    def test_execute_rejects_unknown_tool(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        res = tools.execute("nonexistent_tool", {"foo": "bar"})
        assert not res.ok
        assert "unknown tool 'nonexistent_tool'" in res.content

    def test_execute_rejects_extra_undeclared_fields(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        test_file = tmp_path / "sample.py"
        test_file.write_text("x = 1\n", encoding="utf-8")

        res = tools.execute("read_file", {"path": "sample.py", "extra_injected_field": "evil"})
        assert not res.ok
        assert "extra" in res.content.lower() or "validation error" in res.content.lower()


class TestInjectionAndPathSanitizationHardening:
    def test_bash_command_rejects_null_bytes(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
        res = tools.execute("bash", {"command": "echo hello\x00world"})
        assert not res.ok
        assert "null byte" in res.content.lower()

    def test_bash_command_rejects_empty_or_whitespace_only(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
        for bad_cmd in ("", "   ", "\n\t"):
            res = tools.execute("bash", {"command": bad_cmd})
            assert not res.ok
            assert "empty" in res.content.lower()

    def test_run_tests_keyword_rejects_leading_dash_option_injection(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
        for bad_kw in ("-s", "-v", "--pdb", "--capture=no", "-k safe"):
            res = tools.execute("run_tests", {"path": "tests", "keyword": bad_kw})
            assert not res.ok
            assert "option" in res.content.lower() or "keyword" in res.content.lower() or "dash" in res.content.lower()

    def test_run_tests_keyword_rejects_null_bytes_and_newlines(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path, HostExecutor(tmp_path, trusted=True))
        for bad_kw in ("test\x00kw", "test\nkw", "test\rkw"):
            res = tools.execute("run_tests", {"path": "tests", "keyword": bad_kw})
            assert not res.ok
            assert "null" in res.content.lower() or "newline" in res.content.lower() or "keyword" in res.content.lower()

    def test_path_validation_rejects_null_bytes_and_empty_paths(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        for bad_path in ("", "   ", "a\x00b.py", "/absolute/path.py", "C:\\windows\\path.py", "../escape.py"):
            res = tools.execute("read_file", {"path": bad_path})
            assert not res.ok
            assert "path" in res.content.lower() or "workspace" in res.content.lower() or "null" in res.content.lower()


class TestPayloadSizeAndDoSDefense:
    def test_edit_file_rejects_oversized_payload_before_modification(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path, edit_policy=EditPolicy(max_file_bytes=1000))
        target = tmp_path / "existing.py"
        target.write_text("orig\n", encoding="utf-8")

        # new_text exceeds max_file_bytes
        res = tools.execute(
            "edit_file",
            {"path": "existing.py", "old_text": "orig\n", "new_text": "x" * 2000},
        )
        assert not res.ok
        assert "exceeds size limit" in res.content.lower()

    def test_edit_policy_rejects_invalid_size_limits(self) -> None:
        for bad_size in (0, -1, -100, 16 * 1024 * 1024 + 1, True, False, "1000"):
            with pytest.raises(ValueError, match="limit|positive|bytes"):
                EditPolicy(max_file_bytes=bad_size)  # type: ignore[arg-type]

    def test_search_code_rejects_null_bytes_and_overlong_inputs(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)
        # 1. Null byte in pattern
        res1 = tools.execute("search_code", {"pattern": "find\x00me"})
        assert not res1.ok
        assert "invalid" in res1.content.lower() or "null" in res1.content.lower()

        # 2. Null byte in glob
        res2 = tools.execute("search_code", {"pattern": "find", "glob": "*.py\x00"})
        assert not res2.ok
        assert "invalid" in res2.content.lower() or "null" in res2.content.lower()

        # 3. Overlong pattern (> 200)
        res3 = tools.execute("search_code", {"pattern": "x" * 201})
        assert not res3.ok
        assert "invalid" in res3.content.lower()

        # 4. Overlong glob (> 100)
        res4 = tools.execute("search_code", {"pattern": "find", "glob": "x" * 101})
        assert not res4.ok
        assert "invalid" in res4.content.lower()


class TestOutputBoundingAndExceptionIsolation:
    def test_execute_isolates_arbitrary_exceptions(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)

        class CustomArgs(ToolArgs):
            action: str

        def crashing_handler(_args: ToolArgs) -> ToolOutcome:
            raise AttributeError("unexpected attribute failure in tool implementation")

        tools.definitions["crash_tool"] = ToolDefinition(
            "crash_tool", "Tool that raises unhandled AttributeError", CustomArgs, crashing_handler
        )

        res = tools.execute("crash_tool", {"action": "run"})
        assert not res.ok
        assert "Tool error: AttributeError" in res.content or "unexpected attribute failure" in res.content

    def test_execute_isolates_zero_division_and_index_error(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)

        class CustomArgs(ToolArgs):
            action: str

        def div_zero_handler(_args: ToolArgs) -> ToolOutcome:
            return 1 / 0  # type: ignore[return-value]

        tools.definitions["div_tool"] = ToolDefinition(
            "div_tool", "Tool that divides by zero", CustomArgs, div_zero_handler
        )

        res = tools.execute("div_tool", {"action": "calc"})
        assert not res.ok
        assert "division by zero" in res.content.lower()

    def test_execute_sanitizes_null_bytes_in_output(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)

        class CustomArgs(ToolArgs):
            msg: str

        def null_handler(args: ToolArgs) -> ToolOutcome:
            assert isinstance(args, CustomArgs)
            return ToolOutcome("result with \x00 null \x00 bytes", True)

        tools.definitions["null_tool"] = ToolDefinition(
            "null_tool", "Tool that returns null bytes", CustomArgs, null_handler
        )

        res = tools.execute("null_tool", {"msg": "hello"})
        assert res.ok
        assert "\x00" not in res.content

    def test_execute_strictly_bounds_output_length(self, tmp_path: Path) -> None:
        tools = ToolRegistry(tmp_path)

        class CustomArgs(ToolArgs):
            count: int

        def huge_handler(args: ToolArgs) -> ToolOutcome:
            assert isinstance(args, CustomArgs)
            return ToolOutcome("A" * (args.count), True)

        tools.definitions["huge_tool"] = ToolDefinition(
            "huge_tool", "Tool that returns huge output", CustomArgs, huge_handler
        )

        res = tools.execute("huge_tool", {"count": 50_000})
        assert res.ok
        assert "[output truncated]" in res.content
        assert len(res.content) <= 6_000 + 30
