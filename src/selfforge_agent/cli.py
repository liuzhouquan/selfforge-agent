"""Command line interface for coding tasks and specialized workflows."""

import argparse
import json
import os
import sys
from pathlib import Path

from selfforge_agent.coding.agent import RepairAgent
from selfforge_agent.coding.model_client import FallbackCodingModel, OpenAICompatibleCodingModel
from selfforge_agent.coding.test_generation import DifferentialTestGenerator, RiskTarget
from selfforge_agent.coding.worktree import WorktreeError
from selfforge_agent.core.git_context import GitContextError, build_context
from selfforge_agent.providers import ProviderConfig, route_providers
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.benchmark import evaluate_suite, validate_suite
from selfforge_agent.runtime.evaluation import OfflineFixtureModel, evaluate
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.images import build_dependency_image
from selfforge_agent.runtime.model import OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.service import ReviewService
from selfforge_agent.verification.feedback import failure_feedback
from selfforge_agent.verification.runner import DockerTestRunner, TestRunner, TrustRequiredError


def _run_code(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not args.task and not args.resume:
        parser.error("code requires --task or --resume")
    root = args.repo.resolve()
    if not root.is_dir():
        parser.error("Repository directory does not exist")
    try:
        if args.sandbox == "host" and not args.trust_local:
            parser.error("Host execution requires --trust-local")
        if args.model or args.base_url:
            if not args.model or not args.base_url:
                parser.error("Custom model requires both --model and --base-url")
            candidates = [ProviderConfig(
                name="custom", label="Custom", model=args.model, base_url=args.base_url,
                key=os.environ.get("SELFFORGE_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
                status_url="",
            )]
        else:
            candidates = route_providers(args.provider, not args.no_fallback)
        model = OpenAIChatAdapter(candidates)
        manager = CodingWorkspaceManager()
        session = SessionStore(manager.session_root(root, args.resume, legacy_roots=(root,)),
                               args.resume)
        workspace = None
        if args.workspace == "worktree":
            workspace = (manager.get(root, session.session_id) if args.resume
                         else manager.create(root, session.session_id))
        working_root = workspace.path if workspace else root
        executor = (HostExecutor(working_root, trusted=True) if args.sandbox == "host"
                    else DockerExecutor(working_root, image=args.image))
        agent = CodingAgent(model, ToolRegistry(working_root, executor),
                            session, max_steps=args.max_steps,
                            time_budget=args.time_budget, context_budget=args.context_tokens)
        if workspace:
            print(f"Workspace: {workspace.path} (branch {workspace.branch}, "
                  f"base {workspace.base_sha[:12]})")
        result = agent.run(args.task or "", resume=bool(args.resume))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    print(f"Session {result.session_id}: {result.status} ({result.steps} steps, "
          f"{result.tools_used} tools)")
    if result.answer:
        print(result.answer)
    if result.error:
        print(result.error, file=sys.stderr)
    return 0 if result.status == "completed" else 1


def _run_test(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    root = args.repo.resolve()
    try:
        if args.sandbox == "host":
            report = TestRunner().run(root, trusted=args.trust_local,
                                      timeout=args.timeout, test_path=args.path)
            print(f"pytest: {report.status}; passed={report.passed}; failed={report.failed}; "
                  f"errors={report.errors}; exit={report.exit_code}")
            if report.error_message:
                print(report.error_message, file=sys.stderr)
            diagnostics = failure_feedback(report)
            if diagnostics:
                print(diagnostics)
            return 0 if report.status == "passed" else 1
        outcome = ToolRegistry(root, DockerExecutor(root, image=args.image)).execute(
            "run_tests", {"path": args.path, "timeout": args.timeout})
        print(outcome.content)
        return 0 if outcome.ok else 1
    except (ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(prog="selfforge-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    sandbox = subcommands.add_parser("sandbox", help="Prepare an offline dependency image")
    sandbox.add_argument("action", choices=["build"])
    sandbox.add_argument("--requirements", type=Path, required=True)
    sandbox.add_argument("--wheelhouse", type=Path, required=True)
    sandbox.add_argument("--tag", required=True)
    sandbox.add_argument("--base-image", default="selfforge-agent-sandbox:latest")
    sandbox.add_argument("--timeout", type=float, default=300)
    code = subcommands.add_parser("code", help="Run a general autonomous coding task")
    code.add_argument("--repo", type=Path, default=Path.cwd())
    code.add_argument("--task", help="Natural-language development task")
    code.add_argument("--resume", help="Resume a prior session ID")
    code.add_argument("--workspace", choices=["worktree", "direct"], default="worktree",
                      help="Persistent isolated worktree (default) or explicit direct edits")
    code.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    code.add_argument("--trust-local", action="store_true", help="Required for host execution")
    code.add_argument("--image", help="New Docker image (default sandbox); resume reuses saved ID")
    code.add_argument("--provider", choices=["auto", "deepseek", "openrouter"], default="auto")
    code.add_argument("--no-fallback", action="store_true")
    code.add_argument("--model")
    code.add_argument("--base-url")
    code.add_argument("--max-steps", type=int, default=12)
    code.add_argument("--time-budget", type=float, default=300)
    code.add_argument("--context-tokens", type=int, default=8_000)
    testing = subcommands.add_parser("test", help="Run pytest with Docker or explicit host trust")
    testing.add_argument("--repo", type=Path, default=Path.cwd())
    testing.add_argument("--path", default="tests")
    testing.add_argument("--timeout", type=float, default=60)
    testing.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    testing.add_argument("--trust-local", action="store_true")
    testing.add_argument("--image", default="selfforge-agent-sandbox:latest")
    trace = subcommands.add_parser("trace", help="Show a session's structured trace")
    trace.add_argument("--repo", type=Path, default=Path.cwd())
    trace.add_argument("session_id")
    workspaces = subcommands.add_parser("workspaces", help="Inspect or remove Coding worktrees")
    workspaces.add_argument("action", choices=["list", "remove"])
    workspaces.add_argument("session_id", nargs="?")
    workspaces.add_argument("--repo", type=Path, default=Path.cwd())
    evaluation = subcommands.add_parser("eval", help="Run objective generated coding fixtures")
    evaluation.add_argument("--offline", action="store_true", help="Exercise the tool protocol without an LLM")
    evaluation.add_argument("--provider", choices=["auto", "deepseek", "openrouter"], default="auto")
    evaluation.add_argument("--max-steps", type=int, default=10)
    evaluation.add_argument("--time-budget", type=float, default=180)
    suite = subcommands.add_parser("eval-suite", help="Repeat pinned Git tasks with unseen tests")
    suite.add_argument("--manifest", type=Path, required=True)
    suite.add_argument("--repeats", type=int, default=1)
    suite.add_argument("--provider", choices=["auto", "deepseek", "openrouter"], default="auto")
    suite.add_argument("--no-fallback", action="store_true")
    suite.add_argument("--model")
    suite.add_argument("--base-url")
    suite.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    suite.add_argument("--trust-local", action="store_true", help="Required for host execution")
    suite.add_argument("--image", default="selfforge-agent-sandbox:latest")
    suite.add_argument("--max-steps", type=int, default=12)
    suite.add_argument("--time-budget", type=float, default=300)
    suite.add_argument("--test-timeout", type=float, default=60)
    suite.add_argument("--output", type=Path, help="Write aggregate JSON report")
    suite.add_argument("--failure-artifacts", type=Path,
                       help="Opt in to private local test-failure snapshots outside evaluated repos")
    validation = subcommands.add_parser("eval-validate", help="Validate task reference fixes without a model")
    validation.add_argument("--manifest", type=Path, required=True)
    validation.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    validation.add_argument("--trust-local", action="store_true")
    validation.add_argument("--image", default="selfforge-agent-sandbox:latest")
    validation.add_argument("--test-timeout", type=float, default=60)
    validation.add_argument("--output", type=Path)
    review = subcommands.add_parser("review", help="Review a local Git comparison")
    review.add_argument("--repo", type=Path, required=True)
    review.add_argument("--base", required=True)
    review.add_argument("--head", required=True)
    review.add_argument("--trust-local-tests", action="store_true")
    review.add_argument("--test-path", default="tests")
    review.add_argument("--timeout", type=float, default=60)
    review.add_argument("--output", type=Path, help="Write full JSON evidence to this file")
    repair = subcommands.add_parser("repair", help="Repair a verified local regression")
    repair.add_argument("--repo", type=Path, required=True)
    repair.add_argument("--base", required=True)
    repair.add_argument("--head", required=True)
    repair.add_argument("--task", required=True)
    repair.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    repair.add_argument("--trust-local-tests", action="store_true",
                        help="Required only for host pytest execution")
    repair.add_argument("--image", default="selfforge-agent-sandbox:latest")
    repair.add_argument("--test-path", default="tests")
    repair.add_argument("--timeout", type=float, default=60)
    repair.add_argument("--max-attempts", type=int, default=2)
    repair.add_argument("--provider", choices=["auto", "deepseek", "openrouter"], default="auto")
    repair.add_argument("--no-fallback", action="store_true")
    repair.add_argument("--model", default=os.environ.get("SELFFORGE_MODEL"))
    repair.add_argument("--base-url", default=os.environ.get("SELFFORGE_API_BASE_URL"))
    repair.add_argument("--output", type=Path, help="Write JSON evidence to this file")
    repair.add_argument("--patch-output", type=Path, help="Write a verified patch to this file")
    generate = subcommands.add_parser("generate-test", help="Generate and verify a Base/Head regression test")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--base", required=True)
    generate.add_argument("--head", required=True)
    generate.add_argument("--task", required=True)
    generate.add_argument("--provider", choices=["auto", "deepseek", "openrouter"], default="auto")
    generate.add_argument("--no-fallback", action="store_true")
    generate.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    generate.add_argument("--trust-local-tests", action="store_true")
    generate.add_argument("--image", default="selfforge-agent-sandbox:latest")
    generate.add_argument("--timeout", type=float, default=60)
    generate.add_argument("--time-budget", type=float, default=300,
                          help="Overall budget for multi-candidate generation (max 600 seconds)")
    generate.add_argument("--max-candidates", type=int, default=1, choices=[1, 2, 3],
                          help="Try up to this many distinct tests; stop at the first differential result")
    generate.add_argument("--finding-index", type=int,
                          help="Zero-based index from local rule review of this Base/Head diff")
    generate.add_argument("--output", type=Path, help="Write JSON evidence to this file")
    generate.add_argument("--test-output", type=Path, help="Write a differential test to this file")
    args = parser.parse_args()
    if args.command == "sandbox":
        try:
            report = build_dependency_image(args.requirements, args.wheelhouse, tag=args.tag,
                                            base_image=args.base_image, timeout=args.timeout)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "eval-validate":
        try:
            report = validate_suite(args.manifest, sandbox=args.sandbox,
                                    trusted_host=args.trust_local, image=args.image,
                                    test_timeout=args.test_timeout)
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    if args.command == "code":
        return _run_code(args, parser)
    if args.command == "test":
        return _run_test(args, parser)
    if args.command == "trace":
        try:
            manager = CodingWorkspaceManager()
            root = manager.session_root(args.repo, args.session_id, legacy_roots=(args.repo,))
            events = SessionStore(root, args.session_id).trace_events()
        except ValueError as exc:
            parser.error(str(exc))
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return 0
    if args.command == "workspaces":
        manager = CodingWorkspaceManager()
        try:
            if args.action == "list":
                if args.session_id:
                    parser.error("workspaces list does not take a session ID")
                for workspace in manager.list(args.repo):
                    print(f"{workspace.session_id}  {workspace.branch}  {workspace.path}")
            else:
                if not args.session_id:
                    parser.error("workspaces remove requires a session ID")
                workspace = manager.remove(args.repo, args.session_id)
                print(f"Removed worktree {workspace.path}; branch {workspace.branch} remains")
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))
        return 0
    if args.command == "eval":
        try:
            if args.offline:
                factory = OfflineFixtureModel
            else:
                providers = route_providers(args.provider, False)
                factory = lambda _case: OpenAIChatAdapter(providers)
            report = evaluate(factory, max_steps=args.max_steps,
                              time_budget=args.time_budget)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["successes"] == report["tasks"] else 1
    if args.command == "eval-suite":
        try:
            if args.model or args.base_url:
                if not args.model or not args.base_url:
                    parser.error("Custom model requires both --model and --base-url")
                providers = [ProviderConfig(
                    name="custom", label="Custom", model=args.model, base_url=args.base_url,
                    key=os.environ.get("SELFFORGE_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
                    status_url="",
                )]
            else:
                providers = route_providers(args.provider, not args.no_fallback)
            report = evaluate_suite(
                args.manifest, lambda _case: OpenAIChatAdapter(providers),
                repeats=args.repeats, sandbox=args.sandbox, trusted_host=args.trust_local,
                image=args.image, max_steps=args.max_steps, time_budget=args.time_budget,
                test_timeout=args.test_timeout, failure_artifacts=args.failure_artifacts,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0 if report["successes"] == report["runs"] else 1
    if args.command == "generate-test":
        try:
            candidates = route_providers(args.provider, not args.no_fallback)
            if not candidates:
                parser.error("A configured model provider is required")
            runner = TestRunner() if args.sandbox == "host" else DockerTestRunner(args.image)
            session_root = CodingWorkspaceManager().session_root(args.repo.resolve())
            generator = DifferentialTestGenerator(
                OpenAIChatAdapter(candidates), runner=runner,
                session=SessionStore(session_root),
            )
            target = None
            if args.finding_index is not None:
                findings = ReviewService().review_context(
                    build_context(args.repo, args.base, args.head)).findings
                if not findings:
                    parser.error("No rule findings exist for this Base/Head diff")
                if not 0 <= args.finding_index < len(findings):
                    parser.error(f"finding index must be in 0..{len(findings) - 1}")
                finding = findings[args.finding_index]
                target = {
                    "file_path": finding.file_path, "line_number": finding.line_number,
                    "description": finding.description, "evidence": finding.evidence,
                    "category": finding.category,
                }
            if args.max_candidates > 1 or target is not None:
                batch = generator.run_many(
                    args.repo.resolve(), args.base, args.head, args.task,
                    max_candidates=args.max_candidates,
                    target=RiskTarget.model_validate(target) if target else None,
                    trusted=args.trust_local_tests if args.sandbox == "host" else False,
                    timeout=args.timeout, time_budget=args.time_budget,
                )
                result = next((item for item in batch.candidates
                               if item.candidate_index == batch.selected_candidate),
                              batch.candidates[-1])
            else:
                batch = None
                result = generator.run(
                    args.repo.resolve(), args.base, args.head, args.task,
                    trusted=args.trust_local_tests if args.sandbox == "host" else False,
                    timeout=args.timeout,
                )
        except (GitContextError, TrustRequiredError, WorktreeError, ValueError, OSError) as exc:
            parser.error(str(exc))
        if args.output:
            args.output.write_text((batch or result).model_dump_json(indent=2) + "\n",
                                   encoding="utf-8")
        if result.status == "differential" and args.test_output and result.generated_test:
            args.test_output.write_text(result.generated_test.test_code, encoding="utf-8")
        print(f"Run {result.run_id}: {batch.status if batch else result.status} "
              f"({len(batch.candidates) if batch else 1} candidate(s))")
        if batch:
            for item in batch.candidates:
                print(f"  Candidate {item.candidate_index}: {item.status} "
                      f"({item.provider}/{item.model})")
        if result.base_test and result.head_test:
            print(f"Base: {result.base_test.status} ({result.base_test.passed} passed); "
                  f"Head: {result.head_test.status} ({result.head_test.failed} failed)")
        for error in result.errors:
            print(error, file=sys.stderr)
        return 0 if result.status == "differential" else 1
    if args.command == "repair":
        try:
            candidates = route_providers(args.provider, not args.no_fallback)
            if args.model or args.base_url or os.environ.get("SELFFORGE_API_KEY"):
                first = candidates[0] if candidates else None
                model = OpenAICompatibleCodingModel(
                    model=args.model or (first.model if first else ""),
                    base_url=args.base_url or (first.base_url if first else "https://api.openai.com/v1"),
                    api_key=(os.environ.get("SELFFORGE_API_KEY") or (first.key if first else "")
                             or os.environ.get("OPENAI_API_KEY", "")),
                )
            else:
                model = FallbackCodingModel(candidates)
            runner = TestRunner() if args.sandbox == "host" else DockerTestRunner(args.image)
            result = RepairAgent(model, runner=runner, session=SessionStore(args.repo)).run(
                args.repo,
                args.base,
                args.head,
                args.task,
                trusted=args.trust_local_tests,
                test_path=args.test_path,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
            )
        except (GitContextError, TrustRequiredError, WorktreeError, ValueError) as exc:
            parser.error(str(exc))
        if args.output:
            args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        if result.status == "verified" and args.patch_output:
            args.patch_output.write_text(result.verified_patch, encoding="utf-8")
        print(f"Run {result.run_id}: {result.status}")
        if result.base_test and result.head_test:
            print(f"Base tests: {result.base_test.status}; Head tests: {result.head_test.status}")
        for attempt in result.attempts:
            outcome = attempt.test_result.status if attempt.test_result else attempt.error
            print(f"Attempt {attempt.number}: {outcome}")
        if result.errors:
            print("Errors: " + "; ".join(result.errors), file=sys.stderr)
        return 0 if result.status == "verified" else 1
    try:
        report = ReviewService().review(
            args.repo,
            args.base,
            args.head,
            trusted_tests=args.trust_local_tests,
            test_path=args.test_path,
            timeout=args.timeout,
        )
    except (GitContextError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Run {report.run_id}: {report.execution_status}")
    print(f"{report.base_sha[:12]}..{report.head_sha[:12]} | {len(report.context.files)} files")
    print(f"Findings: {len(report.findings)} heuristic suspected")
    for index, finding in enumerate(report.findings):
        print(f"  [{index}] {finding.file_path}:{finding.line_number} "
              f"[{finding.severity}] {finding.description}")
    for result in report.test_results:
        print(
            f"Tests: {result.status} (exit={result.exit_code}, "
            f"passed={result.passed}, failed={result.failed}, errors={result.errors})"
        )
    if report.errors:
        print("Errors: " + "; ".join(report.errors), file=sys.stderr)
    return 2 if report.execution_status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
