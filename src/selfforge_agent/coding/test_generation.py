"""Generate one pytest candidate and check Base/Head behavior objectively."""

import ast
import json
import tempfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from selfforge_agent.coding.worktree import detached_worktree
from selfforge_agent.core.git_context import build_context
from selfforge_agent.models import PRContext, TestResult
from selfforge_agent.runtime.model import ChatModel
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import _protected
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner, TrustRequiredError


class GeneratedTest(BaseModel):
    test_code: str = Field(min_length=20, max_length=8_192)
    rationale: str = Field(min_length=1, max_length=500)


class DifferentialTestReport(BaseModel):
    run_id: str
    repository: str
    base_sha: str
    head_sha: str
    task: str
    status: Literal["differential", "inconclusive", "error"]
    provider: str = ""
    model: str = ""
    test_path: str = ""
    generated_test: GeneratedTest | None = None
    base_test: TestResult | None = None
    head_test: TestResult | None = None
    errors: list[str] = Field(default_factory=list)


_SUBMIT_TEST = [{"type": "function", "function": {
    "name": "submit_test",
    "description": "Submit one pytest test file that checks the requested behavior",
    "parameters": GeneratedTest.model_json_schema(),
}}]
_SYSTEM = (
    "You are generating one targeted pytest regression test for a Python PR. "
    "Inspect the supplied changed source and diff. Call submit_test exactly once with "
    "test_code and rationale. The code must contain a test_* function and a real assertion. "
    "It should pass on Base and fail on Head if the requested regression exists. "
    "Do not claim verification; execution will decide. No Markdown."
)


def _observation(context: PRContext, head_tree: Path, task: str) -> str:
    files = []
    for change in context.files[:12]:
        relative = Path(change.path)
        if (relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py"
                or any(_protected(part) for part in relative.parts)):
            continue
        path = (head_tree / relative).resolve()
        if not path.is_relative_to(head_tree) or not path.is_file() or path.stat().st_size > 12_000:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        added = [line.content for hunk in change.hunks for line in hunk.lines
                 if line.kind == "added"][:100]
        files.append({"path": change.path, "source": source, "added_lines": added})
        if len(files) == 4:
            break
    return json.dumps({"task": task, "base_sha": context.base_sha,
                       "head_sha": context.head_sha, "files": files}, ensure_ascii=False)


def _parse_test(arguments: str) -> GeneratedTest:
    try:
        candidate = GeneratedTest.model_validate_json(arguments)
        tree = ast.parse(candidate.test_code)
    except (ValueError, SyntaxError, ValidationError) as exc:
        raise ValueError("Model returned invalid pytest source") from exc
    tests = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name.startswith("test_")]
    if not tests or not any(isinstance(child, ast.Assert) for test in tests
                            for child in ast.walk(test)):
        raise ValueError("Generated pytest source needs a test_* function with an assert")
    return candidate


class DifferentialTestGenerator:
    def __init__(self, model: ChatModel, runner: TestRunner | DockerTestRunner | None = None,
                 session: SessionStore | None = None) -> None:
        self.model = model
        self.runner = runner or DockerTestRunner()
        self.session = session

    def run(self, repository: Path, base: str, head: str, task: str, *,
            trusted: bool = False, timeout: float = 60) -> DifferentialTestReport:
        if not task.strip():
            raise ValueError("A test generation task is required")
        if len(task) > 8_000:
            raise ValueError("Test generation task exceeds 8000 characters")
        if timeout <= 0:
            raise ValueError("Test timeout must be positive")
        if not trusted and getattr(self.runner, "requires_trust", True):
            raise TrustRequiredError("Host test generation requires explicit trust")
        context = build_context(repository, base, head)
        run_id = self.session.session_id if self.session else uuid4().hex[:12]
        report = DifferentialTestReport(
            run_id=run_id, repository=context.repository,
            base_sha=context.base_sha, head_sha=context.head_sha,
            task=task, status="error",
        )
        if self.session:
            self.session.record_status("running")
        try:
            with tempfile.TemporaryDirectory(prefix="selfforge-testgen-") as temp:
                root = Path(temp)
                with (
                    detached_worktree(Path(context.repository), context.base_sha, root / "base") as base_tree,
                    detached_worktree(Path(context.repository), context.head_sha, root / "head") as head_tree,
                ):
                    turn = self.model.complete(
                        [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": _observation(context, head_tree, task)}],
                        _SUBMIT_TEST, 45,
                    )
                    report.provider, report.model = turn.provider, turn.model
                    if self.session:
                        self.session.trace("testgen_model", provider=turn.provider, model=turn.model,
                                           usage=turn.usage.model_dump() if turn.usage else None)
                    calls = turn.message.get("tool_calls") or []
                    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "submit_test":
                        raise ValueError("Model must call submit_test exactly once")
                    generated = _parse_test(calls[0]["function"].get("arguments", ""))
                    report.generated_test = generated
                    report.test_path = f"test_selfforge_{run_id}.py"
                    if self.session:
                        self.session.trace("generated_test", bytes=len(generated.test_code.encode("utf-8")))
                    for tree in (base_tree, head_tree):
                        (tree / report.test_path).write_text(generated.test_code, encoding="utf-8")
                    report.base_test = self.runner.run(
                        base_tree, trusted=trusted, timeout=timeout, test_path=report.test_path)
                    report.head_test = self.runner.run(
                        head_tree, trusted=trusted, timeout=timeout, test_path=report.test_path)
                    for stage, result in (("base", report.base_test), ("head", report.head_test)):
                        if self.session:
                            self.session.trace("differential_test", stage=stage,
                                               status=result.status, passed=result.passed,
                                               failed=result.failed, errors=result.errors,
                                               exit_code=result.exit_code)
                    if any(result.status in {"error", "timeout"}
                           for result in (report.base_test, report.head_test)):
                        report.errors.append("Generated test could not run cleanly on Base and Head")
                    elif (report.base_test.status == "passed" and report.base_test.passed > 0
                          and report.head_test.status == "failed" and report.head_test.failed > 0
                          and report.head_test.errors == 0):
                        report.status = "differential"
                    else:
                        report.status = "inconclusive"
                        report.errors.append("Base/Head results did not show a clean assertion regression")
        except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
            report.errors.append(str(exc))
        finally:
            if self.session:
                self.session.record_status(report.status)
        return report
