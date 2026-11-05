# Review of PR #42–#47 — 2026-11-05

Baseline: `602df51ccaf7e81a9eb97651a23c891516e73afc`. Original feature commits remain ancestors; review fixes are additional commits. Integration order: provenance → flat locks → wheel audit → loop guidance → report diff → workspace pruning. The original checkout contains pre-existing uncommitted PR47-related work and was left untouched; review uses a separate worktree.

## Standards

Six reproduced issues: provider URLs exposed userinfo/query/fragment; application identity could use a target repository's cwd or be captured after runs; audit accepted nonfinite deadlines and exceeded the final-read timeout/failed-read byte budget; custom cleanup locks split from Web locks; ignored user data was deleted; merged-only pruning trusted an obsolete audit. Fixes capture run-start provenance, sanitize endpoints, label declared metadata, share bounded wheel scanning and lock configuration, and revalidate deletion state under the workspace lock.

Ignored user files are protected. Only real __pycache__, .pytest_cache and .ruff_cache directories are exempt as derived caches. Existing Git non-force guards and branch history remain. Audit is diagnostic, and external editors/Git do not participate in advisory locking. Batches are not filesystem transactions.

## Spec

Six reproduced issues: flat-lock normalization worked in export but not planning; valid epoch versions were rejected; real reports lost duration/token metrics; rounding hid small regressions; empty/malformed reports could pass or return500; old loop hints survived verification/new tasks. Fixes share profile loading, provide canonical export steps when needed, retain strict hashes/valid versions, validate real report schemas and compare count fractions before formatting. Legacy metric aliases remain compatible. Report output cannot overwrite input files. Synthetic fixtures now use full hashes/reconciled counts; original assertions remain.

Loop hints remain advisory, transient and metadata-only, and are omitted on final-answer-only turns. Report differences are descriptive rather than causal/statistical improvement proof. Manifest mismatch blocks automated regression gating; missing provenance remains unknown. Original53/60 results and frozen benchmark inputs were not changed.

## Actual verification

- Locked uv dependencies installed; Ruff and repair whitespace checks passed.
- Incoming implementations failed two provenance regressions, five flat-profile cases, four audit budget cases, one stale-loop case, two report/API cases and three prune cases. Independent probes also reproduced the specific URL/privacy, ignored-file deletion and minute-regression failures.
- Relevant post-fix selections: provenance7; flat profiles53; wheel audit/builder54; loop/budget18; report/CLI/API10; workspace/concurrency/provenance63 (one integration deselected). Counts overlap and are not additive.
- The strengthened removal guard initially blocked the existing cache-bearing CLI removal regression. Explicitly limiting the exemption to three actual cache directories restored that workflow while retaining ignored-user-file protection; no test was deleted or assertion weakened.
- Final combined test collection:482. Full local pytest and exact-head PR/final-main CI outcomes are recorded in the PR delivery evidence after execution. No paid model calls were made.

## Boundaries and handoff

Provenance is run-start evidence, not a filesystem snapshot or a certificate that an environment-declared SHA was built from that source. Nonstandard provider URL paths are omitted for privacy; Docker version evidence can be engine or client. Historical reports are not backfilled.

Flat/structured profiles remain finite hashed inputs. Hash-ready wheels are not proof of valid archives, target compatibility or installability. Scans enforce200wheels/1000directory entries/64MiB perwheel/128MiB input including locks, plus a finite audit deadline checked after reads and before return. Read failures abort rather than resetting allowance.

After integration, record exact head/merge SHAs and CI URLs in the PR evidence. Next bounded task SF-EVAL-06: add one independently contracted multi-file real-repository task with negative/reference controls in a new manifest. Do not reuse the old53/60 score for the changed Runtime or silently combine policies.
