"""Validated coding tools confined to one workspace."""

import fnmatch
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from selfforge_agent.runtime.execution import Executor

MAX_TOOL_OUTPUT = 6_000
MAX_WHOLE_FILE_BYTES = 128_000
MAX_RANGED_FILE_BYTES = 2_000_000
MAX_READ_LINES = 200
MAX_SEARCH_BYTES = 8_000_000
PROTECTED_NAMES = {
    ".git", ".venv", ".env", ".local", ".selfforge", ".workspaces", ".aws", ".ssh",
    ".npmrc", ".pypirc", ".netrc",
}


def _protected(name: str) -> bool:
    return name in PROTECTED_NAMES or name.startswith(".env.")


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadFile(ToolArgs):
    path: str
    start_line: int | None = Field(default=None, ge=1, description="First line, inclusive")
    end_line: int | None = Field(default=None, ge=1, description="Last line, inclusive")


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
    keyword: str | None = Field(default=None, min_length=1, max_length=200,
                                description="Optional pytest -k expression")
    timeout: float = Field(default=60, gt=0, le=180)


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    ok: bool
    elapsed_seconds: float = 0


@dataclass(frozen=True)
class EditPolicy:
    allowed_paths: frozenset[str] | None = None
    max_file_bytes: int = 1_000_000
    allow_create: bool = True

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0:
            raise ValueError("Edit size limit must be positive")


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
    def __init__(self, root: Path, executor: Executor | None = None,
                 *, edit_policy: EditPolicy | None = None) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError("Workspace must exist")
        self.executor = executor
        self.edit_policy = edit_policy or EditPolicy()
        definitions = [
            ToolDefinition("read_file", "Read a UTF-8 file or up to 200 selected lines in the workspace",
                           ReadFile, self._read),
            ToolDefinition("edit_file", "Replace exact text or create a new file", EditFile, self._edit),
            ToolDefinition("search_code", "Search workspace source for a literal string", SearchCode, self._search),
        ]
        if executor is not None:
            definitions.extend([
                ToolDefinition("bash", "Run a bounded command in the configured executor", Bash, self._bash),
                ToolDefinition("run_tests", "Run pytest and report its actual exit status", RunTests, self._test),
            ])
        self.definitions = {
            item.name: item for item in definitions
        }

    def schemas(self) -> list[dict[str, Any]]:
        return [item.schema() for item in self.definitions.values()]

    def _path(self, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or any(
            _protected(part) for part in relative.parts
        ):
            raise ValueError("Path must stay inside the workspace")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Path must stay inside the workspace")
        if any(_protected(part) for part in path.relative_to(self.root).parts):
            raise ValueError("Path is protected")
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
        size = path.stat().st_size
        if args.start_line is None and args.end_line is None:
            if size > MAX_WHOLE_FILE_BYTES:
                raise ValueError("File exceeds whole-file read limit; use start_line and end_line")
            return ToolOutcome(path.read_text(encoding="utf-8"), True)
        if args.start_line is None or args.end_line is None:
            raise ValueError("Specify both start_line and end_line")
        if args.end_line < args.start_line or args.end_line - args.start_line + 1 > MAX_READ_LINES:
            raise ValueError("Read range must contain 1-200 ordered lines")
        if size > MAX_RANGED_FILE_BYTES:
            raise ValueError("File exceeds ranged read limit")
        with path.open(encoding="utf-8") as handle:
            content = "".join(islice(handle, args.start_line - 1, args.end_line))
        if not content:
            raise ValueError("Read range begins beyond end of file")
        return ToolOutcome(content, True)

    def _edit(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, EditFile)
        policy = self.edit_policy
        if policy.allowed_paths is not None and args.path not in policy.allowed_paths:
            raise ValueError("Edit path is not an allowed changed source file")
        path = self._path(args.path)
        candidate = self.root
        for part in Path(args.path).parts:
            candidate /= part
            if candidate.is_symlink():
                raise ValueError("Edit path contains a symlink")
        if path.exists():
            if not path.is_file() or path.stat().st_size > policy.max_file_bytes or not args.old_text:
                raise ValueError("Existing files require a nonempty exact replacement")
            original = path.read_text(encoding="utf-8")
            if original.count(args.old_text) != 1:
                raise ValueError("old_text must occur exactly once")
            updated = original.replace(args.old_text, args.new_text, 1)
            if updated == original:
                raise ValueError("Edit must change the file")
        else:
            if not policy.allow_create:
                raise ValueError("Creating files is disabled for this workflow")
            if args.old_text:
                raise ValueError("New files require empty old_text")
            path.parent.mkdir(parents=True, exist_ok=True)
            updated = args.new_text
        if len(updated.encode("utf-8")) > policy.max_file_bytes:
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
        scanned_bytes = 0
        for directory, folders, files in os.walk(base):
            folders[:] = [name for name in folders if not _protected(name)]
            for filename in files:
                if _protected(filename):
                    continue
                if not fnmatch.fnmatch(filename, args.glob):
                    continue
                visited += 1
                if visited > 2_000 or len(hits) >= 80:
                    break
                path = Path(directory) / filename
                resolved = path.resolve()
                if (not path.is_file() or not resolved.is_relative_to(self.root)
                        or any(_protected(part) for part in resolved.relative_to(self.root).parts)
                        or path.stat().st_size > MAX_RANGED_FILE_BYTES):
                    continue
                size = path.stat().st_size
                if scanned_bytes + size > MAX_SEARCH_BYTES:
                    continue
                scanned_bytes += size
                try:
                    with path.open(encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
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
        assert self.executor is not None
        if not args.command.strip() or len(args.command) > 2_000:
            raise ValueError("Command is empty or too long")
        result = self.executor.run(args.command, args.timeout)
        return ToolOutcome(f"exit={result.returncode} timeout={result.timed_out}\n{result.output}",
                           result.returncode == 0 and not result.timed_out)

    def _test(self, args: ToolArgs) -> ToolOutcome:
        assert isinstance(args, RunTests)
        assert self.executor is not None
        path = self._path(args.path)
        if not path.exists():
            raise ValueError("Test path does not exist")
        relative = path.relative_to(self.root)
        command = "python -m pytest " + shlex.quote(str(relative)) + " -q"
        if args.keyword is not None:
            if not args.keyword.strip():
                raise ValueError("Test keyword must not be blank")
            command += " -k " + shlex.quote(args.keyword)
        result = self.executor.run(command, args.timeout)
        return ToolOutcome(f"exit={result.returncode} timeout={result.timed_out}\n{result.output}",
                           result.returncode == 0 and not result.timed_out)
