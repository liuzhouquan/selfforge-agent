# Test failure diagnosis and recovery

`run_tests` uses the shared JUnit-backed runners for general Coding, including
Web tasks. Structured failure evidence precedes stdout/stderr, so noisy logs
cannot hide it behind the tool output cap. The existing loop can inspect the
test/source, edit and retest; there is no second repair runtime.

## Evidence and bounds

`TestResult` adds backward-compatible `failure_details` and
`failure_details_truncated` fields. Each entry has a JUnit class/name label,
`failure` or `error` kind, message and stack detail. The label is not assumed to
be a runnable pytest node ID; use it to find the test in the selected path.

- At most 5 entries: IDs/messages at most 256 characters each, detail at most the
  first 1,200 characters. Counts still cover all failures.
- Model feedback is at most 4,000 characters: all retained labels/messages
  first, then the first failure's detail when space permits. Truncation is
  disclosed; process output retains its normal cap.
- Host and Docker parse counts and diagnostics from the same XML document.
  Reports are limited to 8 MiB. Invalid XML, NUL/DTD/entity declarations, nested
  aggregate suites and failure tags contradicting zero failure/error counters
  fail explicitly and cannot establish successful verification.
- Collection/setup errors retain `error`; assertion failures retain `failure`.
  These are reported outcomes, not claims about cause or blame.
- Test data is untrusted. It cannot authorize ignoring user instructions or
  weakening assertions merely to obtain a pass. Existing timeout, nonempty-pass
  and post-edit evidence rules remain.

Host CLI `test` prints the same failure summary. General Coding sessions retain
tool feedback privately. Traces and public benchmark reports do not gain test
IDs, failure text, stack traces or model reasoning.

## Optional local benchmark snapshots

```sh
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/suite-four-tasks.json \
  --provider deepseek --no-fallback --repeats 3 --max-steps 18 \
  --failure-artifacts .local/benchmark/failures \
  --output .local/benchmark/diagnostic-report.json
```

Snapshots are off by default. This command is a reproduction example, not a
claim that this branch ran that repeated benchmark. The directory must be
outside every evaluated repository. Each invocation creates a unique mode-0700
run directory; only failed attempts write mode-0600 JSON, at most 128 KiB/file.
Files contain the primary reason and bounded independent visible/holdout test
diagnostics, counts and status. They exclude stdout/stderr, command paths, model
conversations/reasoning and workspace/source archives.

The aggregate report records only `failure_artifact_run` and each failed run's
`failure_artifact_id`. Open `<directory>/<run>/<id>` locally. Snapshots may
contain test-emitted values and source fragments; keep them out of commits and
published reports. Holdout diagnostics are never fed back to the evaluated
Agent. A write failure sets `failure_artifact_error=artifact_write_failed`
without replacing the primary Agent/test result. The snapshot helps diagnose an
assertion but does not preserve enough state to replay the whole attempt.

## Host bytecode correctness

A rapid edit can preserve file size and integer modification time while Python
still has a valid old `.pyc`. Reusing that cache made a second Host pytest run
test old source. A regression pins size/time and precompiles the old file to
reproduce this deterministically. Each Host test process now uses a fresh
temporary `-X pycache_prefix` and `-B`, ignoring repository bytecode and avoiding
new cache writes. Existing caches are not deleted. Interpreter startup does
more work; this favors correct retesting over cache reuse. Docker staging
already excludes `__pycache__`.

## Live failure-recovery demo

With a configured provider and the sandbox image:

```sh
uv run python examples/test_recovery_demo.py --provider deepseek \
  --output .local/demo/test-recovery-deepseek.json
```

The disposable generated fixture has a broken order-preserving deduplication
function and four fixed tests. Success requires a failed initial `run_tests`, a
source change, unchanged test bytes, Agent `completed`, and all four independent
Docker checks passing. It uses the actual configured model, without fallback,
under a 12-turn/180-second cap.

Actual run on 2026-10-30: success, **5 model turns / 5 tools**: failed `run_tests`
→ two file reads → source edit → passed `run_tests`. Independent Docker checks:
**4 passed, 0 failures/errors**, original tests unchanged. This one generated
fixture does not measure real-repository coverage, a general success rate, or
additional acceptance tasks. The twenty-task acceptance target remains open.

Local verification: `uv run --no-sync pytest -q` **175 passed**, no skips,
including live Docker integration tests, with one existing Starlette/httpx
deprecation warning. Ruff passed. This branch builds on PR #26; separate
interruption, offline-image, reference-validation and budget-feedback branches
are not included in that count. Integrate them independently and rerun the
combined regression suite.
