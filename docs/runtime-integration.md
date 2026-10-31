# Runtime / Harness integration — 2026-10-30

PR #30 is the single delivery entry for the following previously separate
changes. Integration preserves their original commits and adds merge-resolution
and composition tests. Merging this delivery includes the prerequisite branches;
it does not require applying the seven PR diffs individually.

This is a historical record of the PR #30 integration point, not the current
open-work list. Follow-up PR #32 implemented workspace-bound test evidence and
is merged; see [workspace evidence](./workspace-evidence.md) and the current
[development guide](./development.md). The PR #30 regression counts below are
kept to identify exactly what was verified at that earlier point.

| PR | Included capability |
| --- | --- |
| #24 | Nine pinned candidate tasks and Docker baseline checks; candidates are not formal acceptance results |
| #25 | Write-ahead evidence invalidation and conservative interrupted-call recovery |
| #26 | Shared nonempty JUnit pass gate and legacy evidence migration |
| #27 | Explicit offline dependency images and immutable session environment binding |
| #28 | Frozen external reference-patch qualification before model construction |
| #29 | Per-turn budget/verification guidance with reserved context space |
| #30 | Bounded JUnit failure feedback, optional private snapshots and fresh Host bytecode namespace |

## Composition decisions

- Keep one native Coding loop, ToolRegistry, SessionStore and test-runner path.
  Bind/validate the session execution environment before model calls, within the
  existing deadline. Web workspace locking still precedes this binding.
- Invalidate evidence before a new task, interruptible write or test attempt.
  Resume resolves pending tool replies conservatively, retains the JUnit policy
  marker, and regenerates the budget notice from current evidence.
- Reserve context for the transient notice; never retain stale budget controls
  in conversation summaries. A notice cannot grant successful verification or
  add model turns.
- Both `eval-validate` and `eval-suite` use shared reference preflight. Keep
  failure snapshots after qualification and outside all evaluated repositories.
  Invalid references stop before model calls or snapshot creation. Public
  reports remain metadata-only; holdout diagnostics never reach the Agent.
- Keep both CLI dispatch paths: explicit `sandbox build` and provider-free
  `eval-validate`, alongside `eval-suite --failure-artifacts`.
- The live recovery demo independently verifies with the Agent's resolved image
  ID, so retagging cannot silently change its verification environment.

## Regression evidence

Before the final whole-suite run, targeted combinations passed:

| Selection | Result |
| --- | ---: |
| Runtime recovery + JUnit evidence + original Runtime | 67 passed |
| Session environment + dependency images + Web Coding + recovery | 59 passed |
| Reference validation + budget feedback + benchmark | 38 passed |
| New interrupted-write/resume and reference/snapshot composition cases | 3 passed |
| Nine-task manifest structure | 1 passed |

These selections overlap; do not sum them into a unique test count.

| Final local gate | Actual result |
| --- | --- |
| `uv run --no-sync pytest -q` | **257 passed**, no skips, 231.79 seconds; one existing Starlette/httpx deprecation warning |
| `uv run --no-sync ruff check .` | Passed |
| `uv sync --locked --dev --default-index https://pypi.org/simple` | Passed; default local Tsinghua mirror returned 403. Lock/dependency files unchanged; an obsolete `verified-pr-agent==0.1.0` install was removed |
| Actual DeepSeek recovery demo, no fallback | **5 turns / 5 tools**; initial failure observed, source changed, original tests unchanged, final `completed`, independent Docker **4 passed / 0 failures/errors** |

The combined demo used the same immutable image for Agent and independent
verification: `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`.
Its ignored summary is `.local/demo/test-recovery-integrated.json`. This remains
one generated fixture, not formal acceptance or an improved benchmark score.
The [PR #30 checks](https://github.com/liuzhouquan/selfforge-agent/pull/30/checks)
record remote locked-install, Ruff and unit/Docker regression results.
Historical 145/155/175-test records describe their original branches.

## What remained open after PR #30

This integration does not create twenty qualified acceptance tasks or improve
previous model scores by reclassifying failures. The nine-task candidate set,
two overlapping reference-qualified tasks, old 4/4, 8/9, separate 5/6 pilots and
generated-fixture demo remain distinct. Missing work includes broader task
qualification and live repeats, automatic dependency detection, a full product
PR-submission workflow, workspace-bound evidence fingerprints (completed in PR
#32) and stronger crash durability. Source builds and OS dependencies remain
operator-managed. The product PR-submission flow and stronger crash durability
remain open today; workspace-bound evidence does not.

Reproduce local gates with locked dependencies, Ruff and full pytest as in the
[development guide](./development.md). Docker tests need a daemon and the sandbox
image; a skip is not isolation verification. Use the real-provider
[recovery demo](./test-diagnostics.md) separately from formal evaluation.
