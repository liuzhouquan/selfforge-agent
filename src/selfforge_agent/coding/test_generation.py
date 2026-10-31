"""Generate bounded pytest candidates and check Base/Head behavior objectively."""

import ast
import json
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
from selfforge_agent.runtime.pricing import MonetaryCost, estimate_monetary_cost
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


def _observation(context: PRContext, head_tree: Path, task: str,
                 target: RiskTarget | None = None,
                 prior: list[DifferentialTestReport] | None = None) -> str:
    files = []
    changes = sorted(context.files, key=lambda item: item.path != target.file_path) if target else context.files
    for change in changes[:12]:
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
    previous = [{"candidate_index": item.candidate_index, "status": item.status,
                 "test_code": item.generated_test.test_code[:2_000] if item.generated_test else "",
                 "base_status": item.base_test.status if item.base_test else None,
                 "head_status": item.head_test.status if item.head_test else None,
                 "errors": item.errors[:2]} for item in (prior or [])[-2:]]
    return json.dumps({"task": task, "base_sha": context.base_sha,
                       "head_sha": context.head_sha, "risk_hypothesis": target.model_dump() if target else None,
                       "previous_candidates": previous, "files": files}, ensure_ascii=False)


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


def _remaining(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Test generation time budget exhausted")
    return remaining


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
        if timeout <= 0:
            raise ValueError("Test timeout must be positive")
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
                        report.cost = estimate_monetary_cost(turn.model, turn.usage)
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
                        (tree / report.test_path).write_text(generated.test_code, encoding="utf-8")
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
        valid_costs = [c.cost for c in candidates if c.cost is not None]
        if valid_costs:
            total_cost = MonetaryCost(
                currency="USD",
                prompt_cost_usd=round(sum(c.prompt_cost_usd for c in valid_costs), 6),
                completion_cost_usd=round(sum(c.completion_cost_usd for c in valid_costs), 6),
                total_cost_usd=round(sum(c.total_cost_usd for c in valid_costs), 6),
                calibrated=True,
            )
        else:
            total_cost = None
        first = candidates[0]
        return TestGenerationBatchReport(
            run_id=first.run_id, repository=first.repository,
            base_sha=first.base_sha, head_sha=first.head_sha,
            task=task, target=target, status=status,
            stop_reason=stop_reason, selected_candidate=selected, candidates=candidates,
            selected_causal_investigation=selected_investigation,
            total_cost=total_cost,
        )
