# PR #48–#54 integration review

Base: `944cfca` (PR #55), incoming branches originally based on `4ecd6a5`.
Scope: preserve all original feature commits, provider presets and both CLI/Web
entry points. Original checkout's uncommitted files are untouched. Integration
uses an owned worktree. No model/provider requests or paid evaluation runs.

## Conflict decisions

- Backlog rows are unioned by task ID, with merged PR #55 marked Complete; no
  incoming task replaces another task's row.
- CLI/API provider changes coexist with dirty-workspace, freshness and storage
  endpoints. Existing specialized Review/Repair paths remain.
- Runtime merges edit-failure and context-pressure parameters and trace fields
  explicitly; it does not discard either advisory workflow or add tool turns.

## Standards review

Six findings on incoming heads: undeclared production `packaging`; invalid input
silently certified fresh; incomplete storage scans appearing authoritative;
non-hermetic upstream Host tests; custom-function TypeError causing duplicate
execution in the reference patch; raw edit paths in traces. All received bounded
fixes and regression coverage. No style-only refactoring was required.

## Spec review

Six findings on incoming heads: missing optional declarations/false freshness;
incorrect shortened Poetry ranges; incomplete storage bounds; Host qualification
in default tests; stale first-compaction/resume telemetry; incorrect mismatch
diagnosis for permission failures. All addressed. The axes overlap and their
counts are not distinct defect counts.

## Fixes and evidence

- Nine new dependency/storage regressions failed before fixes. Related corrected
  checks plus existing Runtime/storage/dependency tests: 70 passed locally.
- A custom function that raises TypeError executed twice under the incoming
  reference; the added holdout failed. Execution-time catch was replaced with
  signature binding before one dispatch: all six new holdout checks passed in an
  explicit trusted Host diagnostic. This is not Docker qualification.
- Structure tests now use hermetic Git fixtures; live controls are Docker-only
  integration tests with explicitly provisioned pinned sources. Named source
  refs are created so benchmark's independent clone can obtain pinned commits.
- Storage uses no-follow descriptors, bounded entries/files/depth/time and
  explicit `complete`/`scan_error`. Incomplete byte counts are lower bounds; an
  unknown under-quota verdict is null, and CLI exits 2. The 10-second budget
  bounds scanning; existing metadata/Git operations retain separate timeouts. These are apparent bytes,
  not filesystem physical allocation or an enforced quota. Audit/prune advice
  is a non-atomic snapshot; actual prune retains its locked rechecks.
- Freshness includes optional groups and rejects unsupported declarations;
  current Host markers are not target-container resolution.
- Context metrics describe the outgoing post-compaction history, with heuristic
  estimates, bounded rebuilding, transient notices and cancellation checks.
  Trace records edit failure counts rather than model-selected paths.

## Verification status

Docker Desktop became available during review (Engine 29.8.0). The final
combination's Docker integration group passed locally: **13 passed, 555
deselected**, no skips (53.28 seconds). This includes both candidate baselines
and independent reference-visible/holdout controls, using the candidate image
`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`.
No image override was used locally. Pinned sources were read from an existing
external cache via `SELFFORGE_EVAL_SOURCE_ROOT`; the tests made fresh checkouts.

Locked dependency installation, Ruff, frontend `node --check`, and full diff
whitespace checks passed. The new direct-mode checkbox was syntax checked and
its API guard tested; no interactive browser smoke is claimed. Full unit
regression and exact-head remote CI results are recorded in the PR delivery
comments. CI uses an explicit temporary image override after building its image;
its qualification is not reported as a historical frozen-image run. Frozen v2
manifests/assets remain unchanged. Historical 53/60 predates this Runtime and
is not a score for these changes.
