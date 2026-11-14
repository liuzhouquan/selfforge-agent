"""Command line interface for coding tasks and specialized workflows."""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from selfforge_agent.coding.agent import RepairAgent
from selfforge_agent.coding.model_client import FallbackCodingModel, OpenAICompatibleCodingModel
from selfforge_agent.coding.regression_validation import (
    DEFAULT_REGRESSION_TIMEOUT,
    RegressionValidationError,
    validate_workspace_regression,
)
from selfforge_agent.coding.risk_prioritization import prioritize_risks
from selfforge_agent.coding.test_generation import DifferentialTestGenerator, RiskTarget
from selfforge_agent.coding.worktree import WorktreeError
from selfforge_agent.core.git_context import GitContextError, build_context
from selfforge_agent.providers import _DEFAULTS, ProviderConfig, route_providers
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.benchmark import evaluate_suite, validate_suite
from selfforge_agent.runtime.benchmark_diff import (
    diff_eval_reports,
    format_eval_diff,
    read_eval_report,
)
from selfforge_agent.runtime.delivery import (
    DeliveryError,
    commit_workspace,
    create_pull_request,
    revert_workspace,
)
from selfforge_agent.runtime.environment import (
    audit_wheelhouse,
    check_dependency_freshness,
    detect_repository_environment,
    export_hash_locked_requirements,
    plan_environment_preparation,
)
from selfforge_agent.runtime.eval_policy import (
    enforce_policy_cli_args,
    make_policy_provenance,
    verify_eval_policy_bundle,
)
from selfforge_agent.runtime.evaluation import OfflineFixtureModel, evaluate
from selfforge_agent.runtime.execution import DockerExecutor, HostExecutor
from selfforge_agent.runtime.images import build_dependency_image
from selfforge_agent.runtime.model import OpenAIChatAdapter
from selfforge_agent.runtime.pricing import format_monetary_cost
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.runtime.workspace import CodingWorkspaceManager
from selfforge_agent.runtime.workspace_diff import workspace_diff
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
        elif (args.workspace == "direct" and not args.resume
              and not getattr(args, "allow_dirty", False) and manager.is_dirty(root)):
            dirty_sample = manager.dirty_files(root, limit=5)
            sample_str = f" ({', '.join(dirty_sample)})" if dirty_sample else ""
            parser.error(
                f"Direct workspace has uncommitted changes or untracked files{sample_str}; "
                "commit or stash them before running in direct mode, or pass --allow-dirty"
            )
        working_root = workspace.path if workspace else root
        executor = (HostExecutor(working_root, trusted=True) if args.sandbox == "host"
                    else DockerExecutor(working_root, image=args.image))
        approval_hook = None
        if getattr(args, "interactive", False):
            def cli_approval_hook(name: str, arguments: dict) -> bool:
                print(f"\n[APPROVAL REQUIRED] Tool: {name}")
                for k, v in arguments.items():
                    val_str = str(v)
                    if len(val_str) > 120:
                        val_str = val_str[:117] + "..."
                    print(f"  {k}: {val_str}")
                try:
                    choice = input(f"Execute {name}? [y/N]: ").strip().lower()
                    return choice in {"y", "yes"}
                except (EOFError, KeyboardInterrupt):
                    print("\nAction denied by operator.")
                    return False
            approval_hook = cli_approval_hook

        agent = CodingAgent(model, ToolRegistry(working_root, executor),
                            session, max_steps=args.max_steps,
                            time_budget=args.time_budget, context_budget=args.context_tokens,
                            approval_hook=approval_hook)
        if workspace:
            print(f"Workspace: {workspace.path} (branch {workspace.branch}, "
                  f"base {workspace.base_sha[:12]})")
        with manager.guard(working_root, message=f"Workspace {working_root} is locked by another running task or process"):
            result = agent.run(args.task or "", resume=bool(args.resume))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    cost_str = f", {format_monetary_cost(result.monetary_cost)}" if result.monetary_cost else ""
    token_str = f", {result.total_tokens} tokens" if result.total_tokens else ""
    print(f"Session {result.session_id}: {result.status} ({result.steps} steps, "
          f"{result.tools_used} tools{token_str}{cost_str})")
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


def _validate_cli_output_target(path: Path) -> Path:
    """Validate that an export path is not a symlink, non-regular file, or missing parent."""
    target = path.expanduser()
    if target.is_symlink():
        raise ValueError(f"Refusing to write to symlink output path: {path}")
    parent = target.parent
    if not parent.is_dir():
        raise ValueError(f"Output parent directory does not exist: {parent}")
    if os.path.lexists(target):
        info = os.lstat(target)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Output path is not a regular file: {path}")
        if info.st_nlink != 1:
            raise ValueError(f"Output path has multiple hard links: {path}")
    return target


def _validate_distinct_cli_outputs(*paths: Path | None) -> None:
    """Reject exports that would overwrite another export from this invocation."""
    targets = [_validate_cli_output_target(path) for path in paths if path is not None]
    for index, target in enumerate(targets):
        for other in targets[:index]:
            if target.resolve() == other.resolve() or (
                target.exists() and other.exists() and target.samefile(other)
            ):
                raise ValueError("Output paths must refer to distinct files")


def _write_cli_output(path: Path, content: str) -> None:
    """Write CLI export content safely without following symlinks or special files."""
    target = _validate_cli_output_target(path)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(target, flags, 0o644)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Output path is not a regular file: {path}")
        if info.st_nlink != 1:
            raise ValueError(f"Output path has multiple hard links: {path}")
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
    finally:
        if fd >= 0:
            os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selfforge-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    sandbox = subcommands.add_parser("sandbox", help="Prepare an offline dependency image or detect environment")
    sandbox.add_argument("action", choices=["audit", "build", "check", "detect", "export-lock", "freshness", "plan"])
    sandbox.add_argument("--repo", type=Path, default=Path.cwd())
    sandbox.add_argument("--lock", type=Path, help="Path to uv.lock, poetry.lock, Pipfile.lock, or requirements.lock file for export-lock action")
    sandbox.add_argument("--requirements", type=Path)
    sandbox.add_argument("--wheelhouse", type=Path)
    sandbox.add_argument("--tag")
    sandbox.add_argument("--base-image", default="selfforge-agent-sandbox:latest")
    sandbox.add_argument("--timeout", type=float, default=300)
    sandbox.add_argument("--output", type=Path, help="Output path for export-lock, audit, freshness, plan, or detect")
    sandbox.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    code = subcommands.add_parser("code", help="Run a general autonomous coding task")
    code.add_argument("--repo", type=Path, default=Path.cwd())
    code.add_argument("--task", help="Natural-language development task")
    code.add_argument("--resume", help="Resume a prior session ID")
    code.add_argument("--workspace", choices=["worktree", "direct"], default="worktree",
                      help="Persistent isolated worktree (default) or explicit direct edits")
    code.add_argument("--allow-dirty", action="store_true",
                      help="Allow running in direct workspace mode on a repository with uncommitted changes")
    code.add_argument("--sandbox", choices=["docker", "host"], default="docker")
    code.add_argument("--trust-local", action="store_true", help="Required for host execution")
    code.add_argument("--image", help="New Docker image (default sandbox); resume reuses saved ID")
    provider_choices = ["auto", *_DEFAULTS]
    code.add_argument("--provider", choices=provider_choices, default="auto")
    code.add_argument("--no-fallback", action="store_true")
    code.add_argument("--model")
    code.add_argument("--base-url")
    code.add_argument("--max-steps", type=int, default=12)
    code.add_argument("--time-budget", type=float, default=300)
    code.add_argument("--context-tokens", type=int, default=8_000)
    code.add_argument("--interactive", action="store_true",
                      help="Prompt operator for approval before running tools")
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
    trace.add_argument("--summary", action="store_true",
                       help="Print aggregate token usage and estimated cost")
    trace.add_argument("--json", action="store_true",
                       help="Format trace or summary output as JSON")
    trace.add_argument("--output", type=Path,
                       help="Optional path to write trace or summary output")
    workspaces = subcommands.add_parser("workspaces", help="Inspect, commit, revert, or create PRs from Coding worktrees")
    workspaces.add_argument("action", choices=["audit", "commit", "diff", "list", "pr", "prune", "remove", "revert", "storage"])
    workspaces.add_argument("session_id", nargs="?")
    workspaces.add_argument("--repo", type=Path, default=Path.cwd())
    workspaces.add_argument("--message", "-m", help="Commit message for 'commit' action")
    workspaces.add_argument("--author-name", help="Author name for 'commit' action")
    workspaces.add_argument("--author-email", help="Author email for 'commit' action")
    workspaces.add_argument("--title", help="PR title for 'pr' action")
    workspaces.add_argument("--body", default="", help="PR body for 'pr' action")
    workspaces.add_argument("--base", help="Target base branch for 'pr' action")
    workspaces.add_argument("--remote", default="origin", help="Git remote for 'pr' action")
    workspaces.add_argument("--draft", action="store_true", help="Create draft PR")
    workspaces.add_argument("--dry-run", action="store_true", help="Preview worktrees to prune without removing")
    workspaces.add_argument("--include-unmerged", action="store_true", help="Include unmerged clean worktrees when pruning")
    workspaces.add_argument("--quota-bytes", type=int, help="Optional byte quota threshold for workspace storage audit")
    workspaces.add_argument("--output", type=Path, help="Optional path to write JSON storage report")
    workspaces.add_argument("--json", action="store_true", help="Format output as JSON")
    evaluation = subcommands.add_parser("eval", help="Run objective generated coding fixtures")
    evaluation.add_argument("--offline", action="store_true", help="Exercise the tool protocol without an LLM")
    evaluation.add_argument("--provider", choices=provider_choices, default="auto")
    evaluation.add_argument("--max-steps", type=int, default=10)
    evaluation.add_argument("--time-budget", type=float, default=180)
    suite = subcommands.add_parser("eval-suite", help="Repeat pinned Git tasks with unseen tests")
    suite_source = suite.add_mutually_exclusive_group(required=True)
    suite_source.add_argument("--manifest", type=Path, help="Path to evaluation suite manifest JSON")
    suite_source.add_argument("--policy", type=Path, help="Path to frozen evaluation policy JSON bundle")
    suite.add_argument("--repeats", type=int, default=None)
    suite.add_argument("--provider", choices=provider_choices, default=None)
    suite.add_argument("--no-fallback", action="store_true")
    suite.add_argument("--model")
    suite.add_argument("--base-url")
    suite.add_argument("--sandbox", choices=["docker", "host"], default=None)
    suite.add_argument("--trust-local", action="store_true", help="Required for host execution")
    suite.add_argument("--image", default=None)
    suite.add_argument("--context-tokens", type=int, default=None)
    suite.add_argument("--max-steps", type=int, default=None)
    suite.add_argument("--time-budget", type=float, default=None)
    suite.add_argument("--test-timeout", type=float, default=None)
    suite.add_argument("--output", type=Path, help="Write aggregate JSON report")
    suite.add_argument("--failure-artifacts", type=Path,
                       help="Opt in to private local test-failure snapshots outside evaluated repos")
    validation = subcommands.add_parser("eval-validate", help="Validate task reference fixes without a model")
    val_source = validation.add_mutually_exclusive_group(required=True)
    val_source.add_argument("--manifest", type=Path, help="Path to evaluation suite manifest JSON")
    val_source.add_argument("--policy", type=Path, help="Path to frozen evaluation policy JSON bundle")
    validation.add_argument("--sandbox", choices=["docker", "host"], default=None)
    validation.add_argument("--trust-local", action="store_true")
    validation.add_argument("--image", default=None)
    validation.add_argument("--test-timeout", type=float, default=None)
    validation.add_argument("--output", type=Path)
    eval_diff = subcommands.add_parser("eval-diff", help="Compare two benchmark evaluation reports and report regressions")
    eval_diff.add_argument("before", type=Path, help="Path to baseline evaluation summary JSON")
    eval_diff.add_argument("after", type=Path, help="Path to comparison evaluation summary JSON")
    eval_diff.add_argument("--format", choices=["json", "markdown", "text"], default="markdown", help="Output format")
    eval_diff.add_argument("--output", type=Path, help="Write diff report to file")
    eval_diff.add_argument("--fail-on-regression", action="store_true", help="Exit code 1 if any case regressed or success rate dropped")
    review = subcommands.add_parser("review", help="Review a local Git comparison")
    review.add_argument("--repo", type=Path, required=True)
    review.add_argument("--base", required=True)
    review.add_argument("--head", required=True)
    review.add_argument("--trust-local-tests", action="store_true")
    review.add_argument("--test-path", default="tests")
    review.add_argument("--timeout", type=float, default=60)
    review.add_argument("--output", type=Path, help="Write full JSON evidence to this file")
    review.add_argument("--json", action="store_true", help="Format review report as JSON")
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
    repair.add_argument("--provider", choices=provider_choices, default="auto")
    repair.add_argument("--no-fallback", action="store_true")
    repair.add_argument("--model", default=os.environ.get("SELFFORGE_MODEL"))
    repair.add_argument("--base-url", default=os.environ.get("SELFFORGE_API_BASE_URL"))
    repair.add_argument("--output", type=Path, help="Write JSON evidence to this file")
    repair.add_argument("--patch-output", type=Path, help="Write a verified patch to this file")
    repair.add_argument("--json", action="store_true", help="Format repair report as JSON")
    generate = subcommands.add_parser("generate-test", help="Generate and verify a Base/Head regression test")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--base", required=True)
    generate.add_argument("--head", required=True)
    generate.add_argument("--task", required=True)
    generate.add_argument("--provider", choices=provider_choices, default="auto")
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
    generate.add_argument("--auto-target", action="store_true",
                          help="Automatically prioritize risk hypotheses from the diff")
    generate.add_argument("--output", type=Path, help="Write JSON evidence to this file")
    generate.add_argument("--test-output", type=Path, help="Write a differential test to this file")
    generate.add_argument("--json", action="store_true", help="Format test generation report as JSON")
    regress = subcommands.add_parser(
        "test-regression",
        help="Validate a regression test against a Coding workspace and its base commit",
    )
    regress.add_argument("--repo", type=Path, default=Path("."), help="Repository path")
    regress.add_argument("--session", required=True, help="Bound Coding session ID")
    regress.add_argument("--test-file", required=True, help="Bounded candidate pytest file path")
    regress.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_REGRESSION_TIMEOUT,
        help="Total validation timeout budget in seconds (1-180)",
    )
    regress.add_argument("--output", type=Path, help="Optional path to write JSON report")
    args = parser.parse_args(argv)
    if args.command == "sandbox":
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if args.action == "audit":
            if not args.wheelhouse:
                parser.error("sandbox audit requires --wheelhouse")
            try:
                report = audit_wheelhouse(
                    args.repo,
                    wheelhouse=args.wheelhouse,
                    lock_file=args.lock,
                    requirements_file=args.requirements,
                    timeout=args.timeout,
                )
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                try:
                    _write_cli_output(args.output, rendered)
                except (ValueError, OSError) as exc:
                    parser.error(str(exc))
            if args.output and not args.json:
                print(f"Wheelhouse audit report written to {args.output}")
            else:
                print(rendered, end="")
            return 0 if report["ready"] else 1
        if args.action in {"check", "freshness"}:
            try:
                report = check_dependency_freshness(
                    args.repo,
                    lock_file=args.lock,
                    requirements_file=args.requirements,
                )
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                try:
                    _write_cli_output(args.output, rendered)
                except (ValueError, OSError) as exc:
                    parser.error(str(exc))
            if args.json:
                print(rendered, end="")
            elif args.output:
                print(f"Dependency freshness report written to {args.output}")
            else:
                if report["fresh"]:
                    print("Dependency lock is fresh and consistent with project declarations.")
                else:
                    print(f"Dependency lock issue detected: {report['status']}")
                    for w in report.get("warnings", []):
                        print(f"  - {w}")
                print(rendered, end="")
            return 0 if report["fresh"] else 1
        if args.action == "detect":
            try:
                env = detect_repository_environment(args.repo)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            rendered = json.dumps(env, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                try:
                    _write_cli_output(args.output, rendered)
                except (ValueError, OSError) as exc:
                    parser.error(str(exc))
            if getattr(args, "json", False):
                print(rendered, end="")
            elif not args.output:
                print(f"Repository: {env['repository']}")
                print(f"Layout: {env['layout']}")
                print(f"Test framework: {env['test_framework']} ({', '.join(env['test_paths']) or 'none found'})")
                print(f"Dependency files: {', '.join(env['dependency_files']) or 'none'}")
                print(f"Lock files: {', '.join(env['lock_files']) or 'none'} (hash-locked: {env['has_hash_lock']})")
                if env.get("python_requires"):
                    print(f"Python required: {env['python_requires']}")
                if env.get("suggested_image"):
                    print(f"Suggested profile image: {env['suggested_image']}")
                if env.get("warnings"):
                    print("Warnings:")
                    for w in env["warnings"]:
                        print(f"  - {w}")
            return 0
        if args.action == "export-lock":
            if not args.lock:
                parser.error("sandbox export-lock requires --lock")
            try:
                content = export_hash_locked_requirements(args.lock, args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            if args.json:
                print(json.dumps({"requirements": content}, ensure_ascii=False, indent=2))
            elif args.output:
                print(f"Exported hash-locked requirements to {args.output}")
            else:
                print(content, end="")
            return 0
        if args.action == "plan":
            try:
                plan = plan_environment_preparation(
                    args.repo,
                    wheelhouse=args.wheelhouse,
                    tag=args.tag,
                    lock_file=args.lock,
                    requirements_file=args.requirements,
                )
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            rendered = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                try:
                    _write_cli_output(args.output, rendered)
                except (ValueError, OSError) as exc:
                    parser.error(str(exc))
            if args.output and not args.json:
                print(f"Environment preparation plan written to {args.output}")
            else:
                print(rendered, end="")
            return 0
        if not args.requirements or not args.wheelhouse or not args.tag:
            parser.error("sandbox build requires --requirements, --wheelhouse, and --tag")
        try:
            report = build_dependency_image(args.requirements, args.wheelhouse, tag=args.tag,
                                            base_image=args.base_image, timeout=args.timeout)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "eval-validate":
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        bundle = None
        if getattr(args, "policy", None):
            try:
                bundle = verify_eval_policy_bundle(args.policy)
                enforce_policy_cli_args(bundle, args)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            manifest_path = bundle.manifest_path
            sandbox = bundle.policy.environment.sandbox if args.sandbox is None else args.sandbox
            image = args.image or "selfforge-agent-sandbox:latest"
            test_timeout = bundle.policy.budgets.test_timeout_seconds if args.test_timeout is None else args.test_timeout
        else:
            manifest_path = args.manifest
            sandbox = "docker" if args.sandbox is None else args.sandbox
            image = args.image or "selfforge-agent-sandbox:latest"
            test_timeout = 60.0 if args.test_timeout is None else args.test_timeout
        try:
            report = validate_suite(manifest_path, sandbox=sandbox,
                                    trusted_host=args.trust_local, image=image,
                                    test_timeout=test_timeout,
                                    manifest_contents=bundle.manifest_contents if bundle else None,
                                    asset_contents=bundle.asset_contents if bundle else None)
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))
        if bundle is not None:
            if report.get("manifest_sha256") != bundle.manifest_sha256:
                parser.error("Evaluation report does not match verified policy manifest")
            report["policy"] = make_policy_provenance(bundle)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            try:
                _write_cli_output(args.output, rendered)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        print(rendered, end="")
        return 0
    if args.command == "eval-diff":
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if args.output and any(args.output.resolve() == p.resolve() or (args.output.exists() and p.exists() and args.output.samefile(p)) for p in (args.before, args.after)):
            parser.error("Diff output must not overwrite an input report")
        if not args.before.is_file():
            parser.error(f"Baseline report file does not exist: {args.before}")
        if not args.after.is_file():
            parser.error(f"Comparison report file does not exist: {args.after}")
        try:
            before_data = read_eval_report(args.before)
            after_data = read_eval_report(args.after)
            diff = diff_eval_reports(before_data, after_data)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        if args.format == "json":
            rendered = json.dumps(diff, ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = format_eval_diff(diff, format=args.format)
        if args.output:
            try:
                _write_cli_output(args.output, rendered)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        else:
            print(rendered, end="")
        if args.fail_on_regression:
            if not diff["comparable"]:
                return 2
            has_regressed = len(diff.get("regressed", [])) > 0 or diff["summary"]["success_rate"]["delta"] < 0
            return 1 if has_regressed else 0
        return 0
    if args.command == "code":
        return _run_code(args, parser)
    if args.command == "test":
        return _run_test(args, parser)
    if args.command == "trace":
        try:
            if getattr(args, "output", None):
                _validate_cli_output_target(args.output)
            manager = CodingWorkspaceManager()
            root = manager.session_root(args.repo, args.session_id, legacy_roots=(args.repo,))
            store = SessionStore(root, args.session_id)
            events = store.trace_events()
            if getattr(args, "summary", False):
                metrics = store.token_metrics()
                if getattr(args, "json", False):
                    rendered = json.dumps(
                        {"session_id": args.session_id, **metrics},
                        ensure_ascii=False,
                        indent=2,
                    ) + "\n"
                else:
                    cost_str = (
                        f" · Estimated cost: ${metrics['cost']['total_cost_usd']:.4f} "
                        f"({metrics.get('model') or 'calibrated'})"
                        if metrics.get("cost")
                        else ""
                    )
                    rendered = (
                        f"Summary for session {args.session_id}:\n"
                        f"  Total tokens: {metrics['total_tokens']:,} "
                        f"(Prompt: {metrics['prompt_tokens']:,}, Completion: {metrics['completion_tokens']:,})"
                        f"{cost_str}\n"
                    )
            elif getattr(args, "json", False):
                rendered = json.dumps(
                    {
                        "session_id": args.session_id,
                        "event_count": len(events),
                        "events": events,
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n"
            else:
                rendered = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
            if getattr(args, "output", None):
                _write_cli_output(args.output, rendered)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(rendered, end="")
        return 0
    if args.command == "workspaces":
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        manager = CodingWorkspaceManager()
        try:
            if (args.output and args.action in {"remove", "revert", "commit", "pr", "prune"}
                    and (args.session_id or args.action == "prune")):
                # Keep reports outside trees the action may delete or change after evidence checks.
                affected = (manager.root if args.action == "prune" else
                            manager.get(args.repo, args.session_id).path)
                if args.output.expanduser().resolve().is_relative_to(affected.resolve()):
                    raise ValueError("Mutation reports must be written outside the affected workspace")
            if args.action == "list":
                if args.session_id:
                    parser.error("workspaces list does not take a session ID")
                workspaces = manager.list(args.repo)
                rendered_json = json.dumps(
                    [
                        {
                            "session_id": ws.session_id,
                            "branch": ws.branch,
                            "path": str(ws.path),
                            "base_sha": ws.base_sha,
                            "repository": str(ws.repository),
                        }
                        for ws in workspaces
                    ],
                    ensure_ascii=False,
                    indent=2,
                ) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspaces list written to {args.output}")
                else:
                    for workspace in workspaces:
                        print(f"{workspace.session_id}  {workspace.branch}  {workspace.path}")
            elif args.action == "remove":
                if not args.session_id:
                    parser.error("workspaces remove requires a session ID")
                workspace = manager.get(args.repo, args.session_id)
                with manager.guard(workspace.path, message=f"Cannot remove: workspace {workspace.path} is currently locked"):
                    workspace = manager.remove(args.repo, args.session_id)
                res_data = {
                    "session_id": workspace.session_id,
                    "branch": workspace.branch,
                    "path": str(workspace.path),
                    "removed": True,
                }
                rendered_json = json.dumps(res_data, ensure_ascii=False, indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspace remove report written to {args.output}")
                else:
                    print(f"Removed worktree {workspace.path}; branch {workspace.branch} remains")
            elif args.action == "diff":
                if not args.session_id:
                    parser.error("workspaces diff requires a session ID")
                workspace = manager.get(args.repo, args.session_id)
                data = workspace_diff(workspace)
                rendered_json = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspace diff written to {args.output}")
                else:
                    if not data["files"]:
                        print("No visible changes in workspace.")
                    for f in data["files"]:
                        print(f"=== {f['status']}: {f['path']} ===")
                        if f.get("patch"):
                            print(f["patch"])
            elif args.action == "commit":
                if not args.session_id:
                    parser.error("workspaces commit requires a session ID")
                if not args.message:
                    parser.error("workspaces commit requires --message")
                workspace = manager.get(args.repo, args.session_id)
                with manager.guard(workspace.path, message=f"Cannot commit: workspace {workspace.path} is currently locked"):
                    res = commit_workspace(
                        workspace,
                        args.message,
                        author_name=args.author_name,
                        author_email=args.author_email,
                    )
                rendered_json = res.model_dump_json(indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Commit report written to {args.output}")
                else:
                    print(f"Committed {res.commit_sha[:12]} on {res.branch}: {res.message}")
                    for path in res.files_committed:
                        print(f"  {path}")
            elif args.action == "pr":
                if not args.session_id:
                    parser.error("workspaces pr requires a session ID")
                if not args.title:
                    parser.error("workspaces pr requires --title")
                workspace = manager.get(args.repo, args.session_id)
                session_root = manager.session_root(args.repo, args.session_id, legacy_roots=(args.repo,))
                session = SessionStore(session_root, args.session_id)
                with manager.guard(workspace.path, message=f"Cannot create PR: workspace {workspace.path} is currently locked"):
                    pr_res = create_pull_request(
                        workspace,
                        args.title,
                        body=args.body,
                        base_branch=args.base,
                        remote=args.remote,
                        draft=args.draft,
                        session_store=session,
                    )
                rendered_json = pr_res.model_dump_json(indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Pull request report written to {args.output}")
                else:
                    print(f"Created Pull Request: {pr_res.pr_url}")
                    if pr_res.pr_number:
                        print(f"PR #{pr_res.pr_number} on {pr_res.branch} ({pr_res.commit_sha[:12]})")
            elif args.action == "revert":
                if not args.session_id:
                    parser.error("workspaces revert requires a session ID")
                workspace = manager.get(args.repo, args.session_id)
                session_root = manager.session_root(args.repo, args.session_id, legacy_roots=(args.repo,))
                session = SessionStore(session_root, args.session_id) if session_root.is_dir() else None
                with manager.guard(workspace.path, message=f"Cannot revert: workspace {workspace.path} is currently locked"):
                    result = revert_workspace(workspace, session)
                rendered_json = result.model_dump_json(indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Revert report written to {args.output}")
                else:
                    if result.reverted_files:
                        print(f"Reverted {len(result.reverted_files)} files in workspace {workspace.path}:")
                        for f in result.reverted_files:
                            print(f"  - {f}")
                    else:
                        print(f"Workspace {workspace.path} is clean; nothing to revert.")
            elif args.action == "audit":
                if args.session_id:
                    parser.error("workspaces audit does not take a session ID")
                items = manager.audit(args.repo)
                rendered_json = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspace audit report written to {args.output}")
                else:
                    if not items:
                        print("No worktrees found.")
                    else:
                        for it in items:
                            status_flags = []
                            if it["is_locked"]:
                                status_flags.append("locked")
                            if it["is_dirty"]:
                                status_flags.append("dirty")
                            if it["is_merged"]:
                                status_flags.append("merged")
                            flag_str = f" [{', '.join(status_flags)}]" if status_flags else ""
                            prune_str = " (prunable)" if it["prunable"] else ""
                            print(f"{it['session_id']}  {it['branch']}  {it['path']}{flag_str}{prune_str}")
            elif args.action == "prune":
                if args.session_id:
                    parser.error("workspaces prune does not take a session ID")
                dry_run = getattr(args, "dry_run", False)
                merged_only = not getattr(args, "include_unmerged", False)
                result = manager.prune(args.repo, dry_run=dry_run, merged_only=merged_only)
                rendered_json = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspace prune report written to {args.output}")
                else:
                    prefix = "[dry-run] " if dry_run else ""
                    if result["pruned"]:
                        print(f"{prefix}Pruned {len(result['pruned'])} workspace(s): {', '.join(result['pruned'])}")
                    else:
                        print(f"{prefix}No workspaces pruned.")
                    if result["skipped_locked"]:
                        print(f"Skipped {len(result['skipped_locked'])} locked workspace(s): {', '.join(result['skipped_locked'])}")
                    if result["skipped_dirty"]:
                        print(f"Skipped {len(result['skipped_dirty'])} dirty workspace(s): {', '.join(result['skipped_dirty'])}")
                    if result["skipped_unmerged"]:
                        print(f"Skipped {len(result['skipped_unmerged'])} unmerged workspace(s): {', '.join(result['skipped_unmerged'])}")
            elif args.action == "storage":
                if args.session_id:
                    parser.error("workspaces storage does not take a session ID")
                report = manager.audit_storage(args.repo, quota_bytes=args.quota_bytes)
                rendered_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                if getattr(args, "output", None):
                    _write_cli_output(args.output, rendered_json)
                if getattr(args, "json", False):
                    print(rendered_json, end="")
                elif getattr(args, "output", None):
                    print(f"Workspace storage audit report written to {args.output}")
                else:
                    print(f"Workspace Storage Audit ({report['repository']}):")
                    print(f"  Active Workspaces: {report['workspace_count']}")
                    print(f"  Total Space: {report['total_bytes']:,} bytes (Caches: {report['total_cache_bytes']:,} bytes)")
                    print(f"  Reclaimable Storage: {report['reclaimable_bytes']:,} bytes")
                    if report["quota_bytes"] is not None:
                        status_str = ("EXCEEDED" if report["quota_exceeded"] else
                                      ("OK" if report["complete"] else "UNKNOWN"))
                        print(f"  Quota: {report['quota_bytes']:,} bytes [{status_str}]")
                    if report["recommendations"]:
                        print("  Recommendations:")
                        for rec in report["recommendations"]:
                            print(f"    - {rec}")
                    if report["workspaces"]:
                        print("\nWorkspaces:")
                        for ws in report["workspaces"]:
                            flags = []
                            if ws["is_locked"]:
                                flags.append("locked")
                            if ws["is_dirty"]:
                                flags.append("dirty")
                            if ws["is_merged"]:
                                flags.append("merged")
                            if ws["prunable"]:
                                flags.append("prunable")
                            flag_desc = f" [{', '.join(flags)}]" if flags else ""
                            print(f"  {ws['session_id']} ({ws['branch']}){flag_desc}:")
                            print(f"    Path: {ws['path']}")
                            print(f"    Size: {ws['total_bytes']:,} bytes ({ws['file_count']} files) | Caches: {ws['cache_bytes']:,} bytes")
                return 2 if not report["complete"] else (1 if report["quota_exceeded"] else 0)
        except (ValueError, RuntimeError, OSError, DeliveryError) as exc:
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
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        bundle = None
        if getattr(args, "policy", None):
            try:
                bundle = verify_eval_policy_bundle(args.policy)
                enforce_policy_cli_args(bundle, args)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            manifest_path = bundle.manifest_path
            repeats = bundle.policy.budgets.repeats if args.repeats is None else args.repeats
            provider_name = bundle.policy.provider_policy.provider if args.provider is None else args.provider
            sandbox = bundle.policy.environment.sandbox if args.sandbox is None else args.sandbox
            max_steps = bundle.policy.budgets.max_steps if args.max_steps is None else args.max_steps
            time_budget = bundle.policy.budgets.time_budget_seconds if args.time_budget is None else args.time_budget
            test_timeout = bundle.policy.budgets.test_timeout_seconds if args.test_timeout is None else args.test_timeout
            no_fallback = True if not bundle.policy.provider_policy.fallback else args.no_fallback
            model = bundle.policy.provider_policy.model if args.model is None else args.model
            base_url = bundle.policy.provider_policy.base_url if args.base_url is None else args.base_url
        else:
            manifest_path = args.manifest
            repeats = 1 if args.repeats is None else args.repeats
            provider_name = "auto" if args.provider is None else args.provider
            sandbox = "docker" if args.sandbox is None else args.sandbox
            max_steps = 12 if args.max_steps is None else args.max_steps
            time_budget = 300.0 if args.time_budget is None else args.time_budget
            test_timeout = 60.0 if args.test_timeout is None else args.test_timeout
            no_fallback = args.no_fallback
            model = args.model
            base_url = args.base_url
        try:
            if bundle is not None:
                providers = route_providers(provider_name, not no_fallback)
                if not providers or providers[0].name != provider_name or not providers[0].key:
                    raise ValueError("Policy provider is not configured")
                first = providers[0]
                if first.base_url.rstrip("/") != base_url.rstrip("/"):
                    raise ValueError("Configured provider endpoint conflicts with policy base URL")
                providers = [ProviderConfig(
                    name=first.name, label=first.label, key=first.key, model=model,
                    base_url=first.base_url, status_url=first.status_url,
                ), *providers[1:]]
            elif model or base_url:
                if not model or not base_url:
                    parser.error("Custom model requires both --model and --base-url")
                providers = [ProviderConfig(
                    name="custom", label="Custom", model=model, base_url=base_url,
                    key=os.environ.get("SELFFORGE_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
                    status_url="",
                )]
            else:
                providers = route_providers(provider_name, not no_fallback)
            report = evaluate_suite(
                manifest_path, lambda _case: OpenAIChatAdapter(providers),
                repeats=repeats, sandbox=sandbox, trusted_host=args.trust_local,
                image=args.image or "selfforge-agent-sandbox:latest", max_steps=max_steps, time_budget=time_budget,
                test_timeout=test_timeout, failure_artifacts=args.failure_artifacts,
                providers=providers,
                context_budget=(bundle.policy.budgets.context_budget_tokens if bundle else (8000 if args.context_tokens is None else args.context_tokens)),
                manifest_contents=bundle.manifest_contents if bundle else None,
                asset_contents=bundle.asset_contents if bundle else None,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            parser.error(str(exc))
        if bundle is not None:
            if report.get("manifest_sha256") != bundle.manifest_sha256:
                parser.error("Evaluation report does not match verified policy manifest")
            report["policy"] = make_policy_provenance(bundle)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            try:
                _write_cli_output(args.output, rendered)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        print(rendered, end="")
        return 0 if report["successes"] == report["runs"] else 1
    if args.command == "test-regression":
        if getattr(args, "output", None):
            try:
                _validate_cli_output_target(args.output)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        raw_repo = args.repo
        legacy_roots = (
            (raw_repo.resolve(),)
            if (not raw_repo.is_symlink() and raw_repo.is_dir())
            else ()
        )
        try:
            report = validate_workspace_regression(
                raw_repo,
                args.session,
                args.test_file,
                timeout=args.timeout,
                workspace_manager=CodingWorkspaceManager(),
                legacy_session_roots=legacy_roots,
            )
        except (RegressionValidationError, WorktreeError, ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        rendered = report.model_dump_json(indent=2) + "\n"
        if getattr(args, "output", None):
            try:
                _write_cli_output(args.output, rendered)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        print(rendered, end="")
        if report.status == "confirmed":
            return 0
        if report.status == "inconclusive":
            return 1
        return 2
    if args.command == "generate-test":
        try:
            _validate_distinct_cli_outputs(args.output, args.test_output)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
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
            elif args.auto_target:
                context = build_context(args.repo, args.base, args.head)
                prioritized = prioritize_risks(context)
                if not prioritized:
                    parser.error("No candidate added lines found to prioritize in this Base/Head diff")
                top = prioritized[0]
                target = top.to_risk_target().model_dump()
                print(f"Auto-selected risk target (rank {top.rank}, score {top.score:.0f}): "
                      f"{top.file_path}:{top.line_number} [{top.category}]",
                      file=sys.stderr if args.json else sys.stdout)
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
        rendered_json = (batch or result).model_dump_json(indent=2) + "\n"
        if args.output:
            try:
                _write_cli_output(args.output, rendered_json)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if result.status == "differential" and args.test_output and result.generated_test:
            try:
                _write_cli_output(args.test_output, result.generated_test.test_code)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if getattr(args, "json", False):
            print(rendered_json, end="")
        else:
            print(f"Run {result.run_id}: {batch.status if batch else result.status} "
                  f"({len(batch.candidates) if batch else 1} candidate(s))")
            if batch:
                for item in batch.candidates:
                    print(f"  Candidate {item.candidate_index}: {item.status} "
                          f"({item.provider}/{item.model})")
            if result.base_test and result.head_test:
                print(f"Base: {result.base_test.status} ({result.base_test.passed} passed); "
                      f"Head: {result.head_test.status} ({result.head_test.failed} failed)")
            inv = (batch.selected_causal_investigation if batch else None) or result.causal_investigation
            if inv:
                print(f"Trace association: {inv.confidence} ({inv.explanation})")
            cost = (batch.total_cost if batch else None) or result.cost
            if cost:
                print(f"Cost: {format_monetary_cost(cost)}")
        for error in result.errors:
            print(error, file=sys.stderr)
        return 0 if result.status == "differential" else 1
    if args.command == "repair":
        try:
            _validate_distinct_cli_outputs(args.output, args.patch_output)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
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
        rendered_json = result.model_dump_json(indent=2) + "\n"
        if args.output:
            try:
                _write_cli_output(args.output, rendered_json)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if result.status == "verified" and args.patch_output:
            try:
                _write_cli_output(args.patch_output, result.verified_patch)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
        if getattr(args, "json", False):
            print(rendered_json, end="")
        else:
            cost_str = f" · {format_monetary_cost(result.monetary_cost)}" if result.monetary_cost else ""
            token_str = f" ({result.total_tokens} tokens{cost_str})" if result.total_tokens else ""
            print(f"Run {result.run_id}: {result.status}{token_str}")
            if result.base_test and result.head_test:
                print(f"Base tests: {result.base_test.status}; Head tests: {result.head_test.status}")
            for attempt in result.attempts:
                outcome = attempt.test_result.status if attempt.test_result else attempt.error
                print(f"Attempt {attempt.number}: {outcome}")
        if result.errors:
            print("Errors: " + "; ".join(result.errors), file=sys.stderr)
        return 0 if result.status == "verified" else 1
    if getattr(args, "output", None):
        try:
            _validate_cli_output_target(args.output)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
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
    rendered_json = report.model_dump_json(indent=2) + "\n"
    if args.output:
        try:
            _write_cli_output(args.output, rendered_json)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if getattr(args, "json", False):
        print(rendered_json, end="")
    else:
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
