"""Command line interface for local reviews."""

import argparse
import os
import sys
from pathlib import Path

from verified_pr_agent.coding.agent import RepairAgent
from verified_pr_agent.coding.model_client import OpenAICompatibleCodingModel
from verified_pr_agent.coding.worktree import WorktreeError
from verified_pr_agent.core.git_context import GitContextError
from verified_pr_agent.service import ReviewService
from verified_pr_agent.verification.runner import TrustRequiredError


def main() -> int:
    parser = argparse.ArgumentParser(prog="verified-pr-agent")
    subcommands = parser.add_subparsers(dest="command", required=True)
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
    repair.add_argument("--trust-local-tests", action="store_true", required=True)
    repair.add_argument("--test-path", default="tests")
    repair.add_argument("--timeout", type=float, default=60)
    repair.add_argument("--max-attempts", type=int, default=2)
    repair.add_argument("--model", default=os.environ.get("VPA_MODEL"))
    repair.add_argument(
        "--base-url", default=os.environ.get("VPA_API_BASE_URL", "https://api.openai.com/v1")
    )
    repair.add_argument("--output", type=Path, help="Write JSON evidence to this file")
    repair.add_argument("--patch-output", type=Path, help="Write a verified patch to this file")
    args = parser.parse_args()
    if args.command == "repair":
        if not args.model:
            parser.error("repair requires --model or VPA_MODEL")
        try:
            model = OpenAICompatibleCodingModel(
                model=args.model,
                base_url=args.base_url,
                api_key=os.environ.get("VPA_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            )
            result = RepairAgent(model).run(
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
    for finding in report.findings:
        print(f"  {finding.file_path}:{finding.line_number} [{finding.severity}] {finding.description}")
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
