"""Shared result evaluator and success threshold enforcer."""

import math
import uuid
import xml.etree.ElementTree as ET

from selfforge_agent.verification.harness.contracts import (
    HarnessReasonCode,
    HarnessStatus,
    TestCaseResult,
    TestPlan,
    TestRunResult,
)
from selfforge_agent.verification.junit import parse_junit


def evaluate_harness_result(
    plan: TestPlan,
    exit_code: int | None,
    timed_out: bool,
    cancelled: bool,
    duration_seconds: float,
    stdout: str,
    stderr: str,
    xml_bytes: bytes | None,
    run_id: str | None = None,
) -> TestRunResult:
    """Evaluate raw execution outcomes against shared language-agnostic success threshold."""
    if run_id is None:
        run_id = f"run-{uuid.uuid4().hex[:16]}"

    duration = max(0.0, duration_seconds) if math.isfinite(duration_seconds) else 0.0

    # 1. Timeout failure path
    if timed_out:
        return TestRunResult(
            run_id=run_id,
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            plan_digest=plan.plan_digest,
            status=HarnessStatus.TIMEOUT,
            reason_code=HarnessReasonCode.TIMEOUT,
            exit_code=None,
            duration_seconds=duration,
            stdout=stdout[-50000:],
            stderr=stderr[-50000:],
            error_message=f"{plan.adapter_id} exceeded {plan.timeout:g} seconds",
            environment=plan.environment,
            command=plan.command,
        )

    # 2. Cancelled failure path
    if cancelled:
        return TestRunResult(
            run_id=run_id,
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            plan_digest=plan.plan_digest,
            status=HarnessStatus.CANCELLED,
            reason_code=HarnessReasonCode.CANCELLED,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout[-50000:],
            stderr=stderr[-50000:],
            error_message="Test execution was cancelled",
            environment=plan.environment,
            command=plan.command,
        )

    # 3. Missing report artifact path
    if xml_bytes is None:
        is_build_error = exit_code not in (0, None)
        return TestRunResult(
            run_id=run_id,
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            plan_digest=plan.plan_digest,
            status=HarnessStatus.ERROR,
            reason_code=HarnessReasonCode.BUILD_ERROR if is_build_error else HarnessReasonCode.INVALID_EVIDENCE,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout[-50000:],
            stderr=stderr[-50000:],
            error_message=(
                f"{plan.adapter_id} exited with code {exit_code} (build or collection error)"
                if is_build_error
                else f"{plan.adapter_id} did not produce JUnit XML"
            ),
            environment=plan.environment,
            command=plan.command,
        )

    # 4. Parse JUnit XML artifact
    evidence = parse_junit(xml_bytes, timed_out)
    if evidence.error is not None:
        is_build_error = (exit_code == 2) or (
            exit_code is not None
            and exit_code != 0
            and any(
                marker in (stdout + stderr)
                for marker in ("SyntaxError", "Cannot find", "Failed to parse", "Parse error")
            )
        )
        if is_build_error:
            return TestRunResult(
                run_id=run_id,
                adapter_id=plan.adapter_id,
                adapter_version=plan.adapter_version,
                plan_digest=plan.plan_digest,
                status=HarnessStatus.ERROR,
                reason_code=HarnessReasonCode.BUILD_ERROR,
                exit_code=exit_code,
                duration_seconds=duration,
                stdout=stdout[-50000:],
                stderr=stderr[-50000:],
                error_message=f"{plan.adapter_id} exited with code {exit_code} (build/collection error): {evidence.error}",
                environment=plan.environment,
                command=plan.command,
            )
        return TestRunResult(
            run_id=run_id,
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            plan_digest=plan.plan_digest,
            status=HarnessStatus.ERROR,
            reason_code=HarnessReasonCode.INVALID_EVIDENCE,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout[-50000:],
            stderr=stderr[-50000:],
            error_message=evidence.error,
            environment=plan.environment,
            command=plan.command,
        )

    # 5. Extract testcases and check count integrity
    test_cases: list[TestCaseResult] = []
    try:
        root = ET.fromstring(xml_bytes)
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        for s in suites:
            suite_total = int(s.get("tests", "0"))
            suite_cases = s.findall("testcase")
            if suite_cases and len(suite_cases) != suite_total:
                return TestRunResult(
                    run_id=run_id,
                    adapter_id=plan.adapter_id,
                    adapter_version=plan.adapter_version,
                    plan_digest=plan.plan_digest,
                    status=HarnessStatus.ERROR,
                    reason_code=HarnessReasonCode.COUNT_MISMATCH,
                    exit_code=exit_code,
                    duration_seconds=duration,
                    passed=evidence.counts["passed"],
                    failed=evidence.counts["failed"],
                    errors=evidence.counts["errors"],
                    skipped=evidence.counts["skipped"],
                    total=sum(evidence.counts.values()),
                    stdout=stdout[-50000:],
                    stderr=stderr[-50000:],
                    error_message=(
                        f"Inconsistent testcase count ({len(suite_cases)}) "
                        f"vs testsuite total ({suite_total})"
                    ),
                    environment=plan.environment,
                    command=plan.command,
                )

        for case in root.iter("testcase"):
            name = (case.get("name") or "unknown")[:512]
            classname = (case.get("classname") or None)
            if classname:
                classname = classname[:512]
            raw_time = case.get("time")
            try:
                c_time = float(raw_time) if raw_time is not None else 0.0
                if not math.isfinite(c_time) or c_time < 0:
                    c_time = 0.0
            except (ValueError, TypeError):
                c_time = 0.0

            case_status = "passed"
            msg = None
            detail = None
            fail_el = case.find("failure")
            err_el = case.find("error")
            skip_el = case.find("skipped")
            if fail_el is not None:
                case_status = "failed"
                msg = fail_el.get("message")
                detail = "".join(fail_el.itertext())
            elif err_el is not None:
                case_status = "error"
                msg = err_el.get("message")
                detail = "".join(err_el.itertext())
            elif skip_el is not None:
                case_status = "skipped"
                msg = skip_el.get("message")
                detail = "".join(skip_el.itertext())

            test_cases.append(TestCaseResult(
                name=name,
                classname=classname,
                status=case_status,
                duration_seconds=c_time,
                message=msg[:1024] if msg else None,
                detail=detail[:4096] if detail else None,
            ))
    except (ET.ParseError, ValueError, TypeError, KeyError) as exc:
        return TestRunResult(
            run_id=run_id,
            adapter_id=plan.adapter_id,
            adapter_version=plan.adapter_version,
            plan_digest=plan.plan_digest,
            status=HarnessStatus.ERROR,
            reason_code=HarnessReasonCode.INVALID_EVIDENCE,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=stdout[-50000:],
            stderr=stderr[-50000:],
            error_message=f"Failed to extract testcases from JUnit: {exc}",
            environment=plan.environment,
            command=plan.command,
        )

    passed = evidence.counts["passed"]
    failed = evidence.counts["failed"]
    errors = evidence.counts["errors"]
    skipped = evidence.counts["skipped"]
    total = passed + failed + errors + skipped

    # 6. Evaluate final status and reason code based on shared threshold
    has_test_failures = failed > 0 or any(f.kind == "failure" for f in evidence.failures)

    if failed > 0 or errors > 0:
        if has_test_failures:
            status = HarnessStatus.FAILED
            reason_code = HarnessReasonCode.TEST_FAILURE
            error_message = None
        else:
            status = HarnessStatus.ERROR
            reason_code = HarnessReasonCode.BUILD_ERROR
            error_message = f"{plan.adapter_id} exited with errors (build, transpile, or collection failure)"
    elif exit_code != 0:
        status = HarnessStatus.ERROR
        reason_code = HarnessReasonCode.BUILD_ERROR
        error_message = f"{plan.adapter_id} exited with code {exit_code}"
    else:
        # Exit code == 0
        if total == 0:
            status = HarnessStatus.ERROR
            reason_code = HarnessReasonCode.EMPTY_SUITE
            error_message = f"{plan.adapter_id} produced empty testsuite (0 tests run)"
        elif passed == 0 and skipped > 0:
            status = HarnessStatus.ERROR
            reason_code = HarnessReasonCode.ALL_SKIPPED
            error_message = f"{plan.adapter_id} produced no passing tests (all skipped)"
        elif passed > 0:
            status = HarnessStatus.PASSED
            reason_code = None
            error_message = None
        else:
            status = HarnessStatus.ERROR
            reason_code = HarnessReasonCode.INVALID_EVIDENCE
            error_message = f"{plan.adapter_id} produced no passing tests"

    return TestRunResult(
        run_id=run_id,
        adapter_id=plan.adapter_id,
        adapter_version=plan.adapter_version,
        plan_digest=plan.plan_digest,
        status=status,
        reason_code=reason_code,
        exit_code=exit_code,
        duration_seconds=duration,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        total=total,
        test_cases=test_cases,
        failure_details=evidence.failures,
        failure_details_truncated=evidence.truncated,
        stdout=stdout[-50000:],
        stderr=stderr[-50000:],
        error_message=error_message,
        environment=plan.environment,
        command=plan.command,
    )
