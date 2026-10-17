"""Validated coding tools confined to one workspace."""

import fnmatch
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from selfforge_agent.runtime.execution import Executor

MAX_TOOL_OUTPUT = 6_000
PROTECTED_NAMES = {".git", ".venv", ".env", ".local", ".selfforge"}


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadFile(ToolArgs):
    path: str


class EditFile(ToolArgs):
    path: str
    old_text: str = Field(description="Exact text to replace; empty only when creating a new file")
    new_text: str


class SearchCode(ToolArgs):
    pattern: str
    path: str = "."
    glob: str = "*.py"


class Bash(ToolArgs):
    command: str
    timeout: float = Field(default=20, gt=0, le=120)


class RunTests(ToolArgs):
    path: str = "tests"
    timeout: float = Field(default=60, gt=0, le=180)


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    ok: bool
    elapsed_seconds: float = 0


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    args_type: type[ToolArgs]
    handler: Callable[[ToolArgs], ToolOutcome]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.args_type.model_json_schema(),
        }}


class ToolRegistry:
    def __init__(self, root: Path, executor: Executor) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError("Workspace must exist")
        self.executor = executor
        self.definitions = {
            item.name: item for item in [
                ToolDefinition("read_file", "Read a UTF-8 file in the workspace", ReadFile, self._read),
                ToolDefinition("edit_file", "Replace exact text or create a new file", EditFile, self._edit),
                ToolDefinition("search_code", "Search workspace source for a literal string", SearchCode, self._search),
                ToolDefinition("bash", "Run a bounded command in the configured executor", Bash, self._bash),
                ToolDefinition("run_tests", "Run pytest and report its actual exit status", RunTests, self._test),
            ]
        }

    def schemas(self) -> list[dict[str, Any]]:
        return [item.schema() for item in self.definitions.values()]

    def _path(self, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or PROTECTED_NAMES.intersection(relative.parts):
            raise ValueError("Path must stay inside the workspace")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path must stay inside the workspace")
        return path

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        started = time.monotonic()
        try:
            definition = self.definitions[name]
            args = definition.args_type.model_validate(arguments)
            outcome = definition.handler(args)
        except (KeyError, ValidationError, ValueError, OSError, RuntimeError) as exc:
            outcome = ToolOutcome(f"Tool error: {exc}", False)
        content = outcome.content
        if len(content) > MAX_TOOL_OUTPUT:
            content = content[:MAX_TOOL_OUTPUT] + "\n[output truncated]"
        return ToolOutcome(content, outcome.ok, time.monotonic() - started)

    def _read(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, ReadFile)
        path = self._path(args.path)
        if path.stat().st_size > 128_000:
            raise ValueError("File exceeds read limit")
        return ToolOutcome(path.read_text(encoding="utf-8"), True)

    def _edit(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, EditFile)
        path = self._path(args.path)
        if path.exists():
            if not path.is_file() or path.stat().st_size > 1_000_000 or not args.old_text:
                raise ValueError("Existing files require a nonempty exact replacement")
            original = path.read_text(encoding="utf-8")
            if original.count(args.old_text) != 1:
                raise ValueError("old_text must occur exactly once")
            updated = original.replace(args.old_text, args.new_text, 1)
        else:
            if args.old_text:
                raise ValueError("New files require empty old_text")
            path.parent.mkdir(parents=True, exist_ok=True)
            updated = args.new_text
        if len(updated.encode("utf-8")) > 1_000_000:
            raise ValueError("Edited file exceeds size limit")
        path.write_text(updated, encoding="utf-8")
        return ToolOutcome(f"Edited {path.relative_to(self.root)}", True)

    def _search(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, SearchCode)
        if not args.pattern or len(args.pattern) > 200 or len(args.glob) > 100:
            raise ValueError("Search pattern or glob is invalid")
        base = self.root if args.path == "." else self._path(args.path)
        if not base.is_dir():
            raise ValueError("Search path must be a directory")
        hits: list[str] = []
        visited = 0
        for directory, folders, files in os.walk(base):
            folders[:] = [name for name in folders if name not in PROTECTED_NAMES]
            for filename in files:
                if filename in PROTECTED_NAMES:
                    continue
                if not fnmatch.fnmatch(filename, args.glob):
                    continue
                visited += 1
                if visited > 2_000 or len(hits) >= 80:
                    break
                path = Path(directory) / filename
                if not path.is_file() or not path.resolve().is_relative_to(self.root) or path.stat().st_size > 128_000:
                    continue
                try:
                    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if args.pattern in line:
                            hits.append(f"{path.relative_to(self.root)}:{number}: {line[:200]}")
                            if len(hits) >= 80:
                                break
                except UnicodeError:
                    continue
            if visited > 2_000 or len(hits) >= 80:
                break
        return ToolOutcome("\n".join(hits) or "No matches", True)

    def _bash(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, Bash)
        if not args.command.strip() or len(args.command) > 2_000:
            raise ValueError("Command is empty or too long")
        result = self.executor.run(args.command, args.timeout)
        return ToolOutcome(f"exit={result.returncode} timeout={result.timed_out}\n{result.output}",
                           result.returncode == 0 and not result.timed_out)

    def _test(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, RunTests)
        path = self._path(args.path)
        if not path.exists():
            raise ValueError("Test path does not exist")
        relative = path.relative_to(self.root)
        command = "python -m pytest " + shlex.quote(str(relative)) + " -q"
        result = self.executor.run(command, args.timeout)
        return ToolOutcome(f"exit={result.returncode} timeout={result.timed_out}\n{result.output}",
                           result.returncode == 0 and not result.timed_out)
