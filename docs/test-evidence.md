# Test evidence contract

General Coding, PR Repair, differential test generation, and benchmark verification reuse the JUnit-backed runners. A model answer or successful command stdout is not test evidence.

## Successful run

`status=passed` requires all of the following:

- pytest exited with code 0 and did not time out;
- a parseable JUnit report has nonnegative, consistent suite counts;
- at least one test passed;
- no test failures or collection/setup errors were reported.

Passing tests mixed with skips are allowed. All-skipped and empty selections are not successful verification. Missing or malformed reports return `error`. Reported failures/errors return `failed` even when a plugin forces exit code 0; a nonzero exit code also prevents success. Timeouts take precedence over any available report.

`run_tests` places the status, exit code, counts, and error detail before bounded stdout/stderr in the tool response. General Coding only reaches `completed` with valid post-edit evidence and a nonempty final answer. Independent benchmark runs additionally verify allowed file changes and their own visible/holdout selections.

Failure feedback also prioritizes bounded JUnit labels/messages and first-failure
detail before stdout. Host and Docker parse counts and diagnostics together;
contradictory evidence cannot establish success. Optional private evaluation
snapshots retain independent diagnostics while reports stay metadata-only.
See [test diagnostics](./test-diagnostics.md) for bounds, the live recovery demo
and the Host bytecode-cache fix.

## Execution and recovery

- Docker is the default. Tests use `run_with_artifact` and fetch JUnit before container cleanup. The supplied executor retains its image and cancellation marker; a workspace/image mismatch is rejected. The container remains isolated, and source modifications are discarded.
- Host tests require an explicitly trusted `HostExecutor` and run under the Agent's Python interpreter. The runner kills and reaps the test process group on timeout or Ctrl-C. Install project dependencies in the selected environment or use a prepared Docker image.
- Executors without the supported evidence path cannot silently run tests using exit codes alone.
- Verification events now include `evidence=junit-v1`. Old session messages stay readable, but old success booleans without this policy marker cannot restore completion. Resume and run tests again. Existing historical job records retain the status originally recorded; they are not retroactively rewritten.

This is an evidence-quality gate, not proof of adequate test coverage or a defense against malicious test code forging reports. Workspace evidence is not yet bound to a content fingerprint. Interruption ordering is addressed separately by PR #25.

## Reproduce and regress

In a disposable trusted repository, create this sole test:

```python
import pytest

def test_optional():
    pytest.skip("environment not configured")
```

pytest itself exits 0. The runner must return `status=error`, `passed=0`, `skipped=1`, with a `no passing tests` reason. A Coding task that then ends is `unverified`, including after resume. Replace it with a real passing test and rerun; fresh evidence can establish `completed`.

```sh
uv run --no-sync pytest -q tests/test_coding_evidence.py tests/test_repair_agent.py
uv run --no-sync pytest -q
uv run --no-sync ruff check .
```

The marked Docker test requires a running daemon and `selfforge-agent-sandbox:latest`; absent prerequisites are reported as skips. The tests cover Host and real Docker all-skipped behavior, success controls, resume/migration, empty selections, timeout, corrupt/missing reports, inconsistent counts, cancellation propagation, interruption cleanup, and rejection of an all-skipped Repair baseline. Scripted models make these execution checks deterministic; they do not measure model ability.
