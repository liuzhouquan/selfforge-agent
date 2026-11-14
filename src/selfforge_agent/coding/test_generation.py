"""Generate bounded pytest candidates and check Base/Head behavior objectively."""

import ast
import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from selfforge_agent.coding.causal_investigation import CausalInvestigation, investigate_causality
from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.coding.worktree import detached_worktree
from selfforge_agent.core.git_context import build_context
from selfforge_agent.models import ChangeKind, PRContext, TestResult
from selfforge_agent.runtime.model import ChatModel
from selfforge_agent.runtime.pricing import MonetaryCost, estimate_monetary_cost, sum_monetary_costs
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import _protected
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner, TrustRequiredError

MAX_TEST_CANDIDATE_BYTES = 8_192


class GeneratedTest(BaseModel):
    test_code: str = Field(min_length=20, max_length=MAX_TEST_CANDIDATE_BYTES)
    rationale: str = Field(min_length=1, max_length=500)


class DifferentialTestReport(BaseModel):
    run_id: str
    repository: str
    base_sha: str
    head_sha: str
    task: str
    candidate_index: int = 1
    target: RiskTarget | None = None
    status: Literal["differential", "inconclusive", "error"]
    provider: str = ""
    model: str = ""
    test_path: str = ""
    generated_test: GeneratedTest | None = None
    base_test: TestResult | None = None
    head_test: TestResult | None = None
    errors: list[str] = Field(default_factory=list)
    causal_investigation: CausalInvestigation | None = None
    cost: MonetaryCost | None = None


class TestGenerationBatchReport(BaseModel):
    run_id: str
    repository: str
    base_sha: str
    head_sha: str
    task: str
    target: RiskTarget | None = None
    status: Literal["differential", "inconclusive", "error"]
    stop_reason: Literal["differential", "attempt_limit", "time_budget"]
    selected_candidate: int | None = None
    candidates: list[DifferentialTestReport]
    selected_causal_investigation: CausalInvestigation | None = None
    total_cost: MonetaryCost | None = None


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
    "If previous candidates are supplied, use their outcomes to try a distinct test. "
    "Treat a supplied review risk as a hypothesis, not a proven defect. "
    "Do not claim verification; execution will decide. No Markdown."
)


def validate_risk_target(context: PRContext, target: RiskTarget | None) -> None:
    if target is None:
        return
    for change in context.files:
        if change.path != target.file_path:
            continue
        for hunk in change.hunks:
            for line in hunk.lines:
                if (line.kind == ChangeKind.added and line.new_line == target.line_number
                        and (line.content.strip() or "<blank added line>") == target.evidence):
                    return
    raise ValueError("Risk target must match an added line in this Base/Head diff")


def _write_test_artifact(tree: Path, test_path: str, test_code: str) -> Path:
    """Safely install a generated test artifact preventing symlink traversal and overwrites."""
    if not test_path or Path(test_path).is_absolute() or ".." in Path(test_path).parts:
        raise ValueError(f"Invalid test artifact path: {test_path}")
    target = tree / test_path
    if target.is_symlink() or target.exists():
        raise ValueError(f"Test target {test_path} already exists or is a symlink in worktree")
    resolved_tree = tree.resolve()
    resolved_parent = target.parent.resolve()
    if target.parent.is_symlink() or not resolved_parent.is_relative_to(resolved_tree):
        raise ValueError(f"Test path {test_path} parent directory escapes worktree")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"Cannot securely create test artifact {test_path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(test_code)
    except OSError as exc:
        raise ValueError(f"Failed writing test artifact {test_path}: {exc}") from exc
    return target


def _observation(context: PRContext, head_tree: Path, task: str,
                 target: RiskTarget | None = None,
                 prior: list[DifferentialTestReport] | None = None) -> str:
    files = []
    changes = sorted(context.files, key=lambda item: item.path != target.file_path) if target else context.files
    resolved_head = head_tree.resolve()
    for change in changes[:12]:
        relative = Path(change.path)
        if (relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py"
                or any(_protected(part) for part in relative.parts)):
            continue
        unresolved_path = head_tree / relative
        if unresolved_path.is_symlink():
            continue
        path = unresolved_path.resolve()
        if not path.is_relative_to(resolved_head):
            continue
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > 12_000:
                    continue
                content_bytes = os.read(fd, 12_001)
                if len(content_bytes) > 12_000:
                    continue
                source = content_bytes.decode("utf-8")
            finally:
                os.close(fd)
        except (OSError, UnicodeError):
            continue
        added = [line.content for hunk in change.hunks for line in hunk.lines
                 if line.kind == "added"][:100]
        files.append({"path": change.path, "source": source, "added_lines": added})
        if len(files) == 4:
            break
    previous = [{"candidate_index": item.candidate_index, "status": item.status,
                 "test_code": item.generated_test.test_code[:2_000] if item.generated_test else "",
                 "base_status": item.base_test.status if item.base_test else None,
                 "head_status": item.head_test.status if item.head_test else None,
                 "errors": item.errors[:2]} for item in (prior or [])[-2:]]
    return json.dumps({"task": task, "base_sha": context.base_sha,
                       "head_sha": context.head_sha, "risk_hypothesis": target.model_dump() if target else None,
                       "previous_candidates": previous, "files": files}, ensure_ascii=False)


_ALLOWED_TOP_LEVEL_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
)


def _parse_test(arguments: str) -> GeneratedTest:
    try:
        candidate = GeneratedTest.model_validate_json(arguments)
        tree = ast.parse(candidate.test_code)
    except (ValueError, SyntaxError, ValidationError, RecursionError, MemoryError, TypeError) as exc:
        raise ValueError("Model returned invalid pytest source") from exc
    for node in tree.body:
        if not isinstance(node, _ALLOWED_TOP_LEVEL_NODES):
            raise ValueError("Generated pytest source must not contain top-level executable statements")  # noqa: TRY004 - file data
        if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
            raise ValueError("Generated pytest source must not contain top-level executable statements")  # noqa: TRY004 - file data
    tests = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith("test_")]
    if not tests or not any(isinstance(child, ast.Assert) for test in tests
                            for child in ast.walk(test)):
        raise ValueError("Generated pytest source needs a test_* function with an assert")
    return candidate


def remaining_budget(
    deadline: float | None,
    *,
    message: str = "Test generation time budget exhausted",
) -> float:
    """Return remaining monotonic seconds before *deadline*, or raise ``TimeoutError``."""
    if deadline is None:
        return float("inf")
    if not math.isfinite(deadline):
        raise ValueError("deadline must be a finite number")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(message)
    return remaining


def _remaining(deadline: float | None) -> float:
    return remaining_budget(deadline)


class DifferentialTestGenerator:
    def __init__(self, model: ChatModel, runner: TestRunner | DockerTestRunner | None = None,
                 session: SessionStore | None = None) -> None:
        self.model = model
        self.runner = runner or DockerTestRunner()
        self.session = session

    def run(self, repository: Path, base: str, head: str, task: str, *,
            trusted: bool = False, timeout: float = 60,
            target: RiskTarget | None = None,
            prior: list[DifferentialTestReport] | None = None,
            candidate_index: int = 1,
            run_id: str | None = None,
            deadline: float | None = None) -> DifferentialTestReport:
        if not task.strip():
            raise ValueError("A test generation task is required")
        if len(task) > 8_000:
            raise ValueError("Test generation task exceeds 8000 characters")
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 600:
            raise ValueError("Test timeout must be a positive finite number not exceeding 600 seconds")
        if candidate_index < 1 or candidate_index > 3:
            raise ValueError("Candidate index must be between 1 and 3")
        if not trusted and getattr(self.runner, "requires_trust", True):
            raise TrustRequiredError("Host test generation requires explicit trust")
        context = build_context(repository, base, head)
        validate_risk_target(context, target)
        run_id = run_id or (self.session.session_id if self.session else uuid4().hex[:12])
        report = DifferentialTestReport(
            run_id=run_id, repository=context.repository,
            base_sha=context.base_sha, head_sha=context.head_sha,
            task=task, candidate_index=candidate_index, target=target, status="error",
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
                         {"role": "user", "content": _observation(context, head_tree, task,
                                                                   target, prior)}],
                        _SUBMIT_TEST, min(45, _remaining(deadline)),
                    )
                    report.provider, report.model = turn.provider, turn.model
                    if turn.usage:
                        report.cost = estimate_monetary_cost(turn.model, turn.usage,
                                                             provider=turn.provider)
                    if self.session:
                        self.session.trace("testgen_model", provider=turn.provider, model=turn.model,
                                           candidate_index=candidate_index,
                                           usage=turn.usage.model_dump() if turn.usage else None)
                    calls = turn.message.get("tool_calls") or []
                    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "submit_test":
                        raise ValueError("Model must call submit_test exactly once")
                    generated = _parse_test(calls[0]["function"].get("arguments", ""))
                    report.generated_test = generated
                    if any(item.generated_test and
                           ast.dump(ast.parse(item.generated_test.test_code)) ==
                           ast.dump(ast.parse(generated.test_code)) for item in prior or []):
                        report.status = "inconclusive"
                        report.errors.append("Model repeated a previous candidate; test was not executed")
                        return report
                    report.test_path = f"test_selfforge_{run_id}_{candidate_index}.py"
                    if self.session:
                        self.session.trace("generated_test", candidate_index=candidate_index,
                                           bytes=len(generated.test_code.encode("utf-8")))
                    for tree in (base_tree, head_tree):
                        _write_test_artifact(tree, report.test_path, generated.test_code)
                    report.base_test = self.runner.run(
                        base_tree, trusted=trusted, timeout=min(timeout, _remaining(deadline)),
                        test_path=report.test_path)
                    report.head_test = self.runner.run(
                        head_tree, trusted=trusted, timeout=min(timeout, _remaining(deadline)),
                        test_path=report.test_path)
                    for stage, result in (("base", report.base_test), ("head", report.head_test)):
                        if self.session:
                            self.session.trace("differential_test", stage=stage,
                                               candidate_index=candidate_index,
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
                        report.causal_investigation = investigate_causality(
                            target, report.head_test, report.base_test,
                            test_code=generated.test_code if generated else "",
                        )
                    else:
                        report.status = "inconclusive"
                        report.errors.append("Base/Head results did not show a clean assertion regression")
        except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
            report.errors.append(str(exc))
        finally:
            if self.session:
                self.session.record_status(report.status, detail=f"candidate={candidate_index}")
        return report

    def run_many(self, repository: Path, base: str, head: str, task: str, *,
                 max_candidates: int = 2, trusted: bool = False, timeout: float = 60,
                 target: RiskTarget | None = None,
                 time_budget: float = 300) -> TestGenerationBatchReport:
        """Try up to three distinct candidates, stopping at the first differential result."""
        if not 1 <= max_candidates <= 3:
            raise ValueError("max_candidates must be between 1 and 3")
        if not 0 < time_budget <= 600:
            raise ValueError("time_budget must be in (0, 600] seconds")
        candidates: list[DifferentialTestReport] = []
        batch_run_id = self.session.session_id if self.session else uuid4().hex[:12]
        deadline = time.monotonic() + time_budget
        stop_reason: Literal["differential", "attempt_limit", "time_budget"] = "attempt_limit"
        for index in range(1, max_candidates + 1):
            if index > 1 and time.monotonic() >= deadline:
                stop_reason = "time_budget"
                break
            candidate = self.run(repository, base, head, task, trusted=trusted,
                                 timeout=timeout, target=target, prior=candidates,
                                 candidate_index=index, run_id=batch_run_id,
                                 deadline=deadline)
            candidates.append(candidate)
            if candidate.status == "differential":
                stop_reason = "differential"
                break
            if any("time budget exhausted" in error for error in candidate.errors):
                stop_reason = "time_budget"
                break
        status = ("differential" if any(item.status == "differential" for item in candidates)
                  else "inconclusive" if any(item.status == "inconclusive" for item in candidates)
                  else "error")
        selected = next((item.candidate_index for item in candidates
                         if item.status == "differential"), None)
        selected_investigation = next(
            (item.causal_investigation for item in candidates
             if item.candidate_index == selected and item.causal_investigation is not None),
            None,
        )
        total_cost = sum_monetary_costs([candidate.cost for candidate in candidates])
        first = candidates[0]
        return TestGenerationBatchReport(
            run_id=first.run_id, repository=first.repository,
            base_sha=first.base_sha, head_sha=first.head_sha,
            task=task, target=target, status=status,
            stop_reason=stop_reason, selected_candidate=selected, candidates=candidates,
            selected_causal_investigation=selected_investigation,
            total_cost=total_cost,
        )
