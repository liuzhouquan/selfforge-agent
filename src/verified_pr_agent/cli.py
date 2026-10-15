"""Command line interface for local reviews."""

import argparse
import sys
from pathlib import Path

from verified_pr_agent.core.git_context import GitContextError
from verified_pr_agent.service import ReviewService


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
    args = parser.parse_args()
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
