# Incoming branches integration review — 2026-11-04

Baseline: `7e3ea6b283af390351f6cff55886ef7efe9049dc`. Scope: the 18 user-listed local branches (workspace coordination, dependency planning, 15 individual benchmark contracts, and the twenty-task freeze). Separate worktrees preserve the original checkout's unknown uncommitted test edit. Original feature commits remain ancestors; integration fixes use additional commits. No model/provider calls were needed for this review.

## Standards

The Standards review found five concrete P1 defects: custom manager roots split lock ownership; plan parsing accepted unsupported/unhashed requirements and ignored unlocked wheels; uv export existed only in memory while generated commands consumed the TOML file; unquoted shell parameters; and unbounded/symlink dependency reads. Fixes share the builder's strict parser and bounded wheel reader, preserve input types, render commands with `shlex.join`, emit an explicit export step, validate tags and use a common lock namespace independent of storage roots. A follow-up caught explicit requirements files with a `.toml`/`uv.lock` name; generation now follows the chosen parser, not the filename.

The incoming CLI lock tests passed invalid `--session-id` arguments, so they only proved argparse rejection. Corrected positional arguments and independent CLI subprocess tests assert the actual lock error and unchanged files/Git state for remove/revert/commit/PR. Eight original regression cases failed against incoming code before fixes. Resource, API symlink and explicit format controls were then added.

## Spec

The Spec review found two P1 issues and two P2 documentation/reproducibility issues: the formal policy omitted provider/model/fallback; the wait reference overflowed on a valid huge Python integer; documented failure names differed from emitted categories; and locally pinned images lacked a fresh-machine qualification procedure. The [v2 policy](../benchmark/validated-candidates/twenty-tasks-v2.md) pins model/configuration, uses actual report reasons, documents rebuilding/requalification, and freezes both manifest and reference/holdout hashes. Original assets remain historical. V2 changes the wait reference and adds a `10**400` positive control (five node types).

These are project-authored contract tightenings/features on real upstream source, mostly small validation cases, not a broad difficulty benchmark or upstream-confirmed bug collection. 20 qualified cases are not 60 successful model attempts.

## Actual verification

Locked `uv sync --locked --dev --default-index https://pypi.org/simple` succeeded, Python 3.12.13. Docker daemon 29.8.0 was available with the historical sandbox/dependency image IDs recorded in v2. No runtime logs, source caches or secrets are committed.

- Before fixes: targeted regression **8 failed, 9 passed**.
- After runtime fixes: environment-plan/concurrency selection **26 passed**; earlier wider consumer selection **71 passed** (overlapping, not additive).
- V2 `eval-validate --test-timeout 30`: **20/20 reference-qualified in real Docker**, every baseline fails assertions; all reference visible/holdout selections pass nonempty with zero failures/errors/skips. No model was instantiated.
- Private report SHA-256: `efd2aa4ce1aa4ddce583d939b3084c93e21442c200ccc42332028d1eebe43e03`; manifest SHA-256: `3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6`.
- Local full combined `uv run --no-sync pytest -q`: **382 passed, no skips**, 307.51s, including all 11 Docker integration tests; one existing Starlette/httpx deprecation warning. One additional independent CLI Coding contention case was added afterward; the final concurrency selection passed **10/10**. These overlapping runs are not additive. Final CI collects 383 tests and its actual result is recorded in the PR evidence.
- Ruff, repair-patch whitespace, 184 final local Markdown path links and common real-key/private-key pattern checks passed. The complete imported diff retains 16 blank-at-EOF whitespace notices in frozen assets and archived task records; these are disclosed rather than changing historical manifest/holdout bytes for formatting. All 18 original branch heads are preserved as ancestors. No runtime source changed after the full combined regression started.

| Case | Baseline assertion failures | Reference visible passes | Reference holdout passes |
| --- | ---: | ---: | ---: |
| `pocketflow_async_batch_empty` | 2 | 56 | 10 |
| `boltons_unicode_punctuation` | 6 | 33 | 11 |
| `boltons_depth_pruning` | 4 | 13 | 8 |
| `more_itertools_zero_chunk_size` | 8 | 21 | 14 |
| `more_itertools_replace_limit` | 7 | 8 | 10 |
| `pocketflow_invalid_retries` | 15 | 56 | 31 |
| `pocketflow_wait_validation` | 45 | 56 | 70 |
| `pocketflow_retry_count_type` | 30 | 56 | 45 |
| `pocketflow_transition_validation` | 12 | 56 | 13 |
| `boltons_negative_max_depth` | 3 | 13 | 5 |
| `boltons_windowed_size` | 8 | 58 | 9 |
| `boltons_chunked_count` | 10 | 58 | 11 |
| `more_itertools_negative_split_maxsplit` | 12 | 60 | 13 |
| `more_itertools_grouper_size` | 12 | 32 | 14 |
| `more_itertools_intersperse_n` | 8 | 6 | 11 |
| `itsdangerous_empty_secret_keys` | 18 | 17 | 21 |
| `itsdangerous_negative_max_age` | 12 | 2 | 13 |
| `jmespath_split_function` | 18 | 5 | 19 |
| `jmespath_replace_function` | 19 | 5 | 20 |
| `jmespath_lower_function` | 19 | 6 | 20 |

## Remaining boundaries

Stop old Web/CLI workers before upgrading: old versions use the previous `jobs/locks` namespace and cannot coordinate with the new one. New CLI Coding/delivery/removal and Web jobs share advisory flock ownership within the same user/XDG data home. Different OS users, different XDG homes, external Git/editors, and direct calls to low-level APIs remain outside this cooperative protocol. `workspace_guard` does not make fingerprinting an atomic snapshot.

A plan is read-only and never downloads/installs/builds. `ready_to_build` means bounded hash-allowed inputs are present; it does not prove wheel format/platform compatibility or successful dependency resolution. Follow the explicit export step before download/build when present; Docker build remains the final installability check. The existing limits and explicit network/Host trust gates remain in force.

Formal 20×3 live-model acceptance is pending. Historical small model runs stay separate. New machines must create a newly named manifest with actual image IDs and requalify; a rebuild does not reproduce old immutable IDs by promise.

## Handoff

The reviewed runtime-fix commit is `503eb96`; evaluation fixes are `29f08d4`, integrated at `521ef59`. Subsequent changes add one Coding contention case and documentation. Integration status and the exact final CI-tested/delivered SHA belong in the PR's evidence. Next independent development unit: formal model evaluation under the frozen v2 policy after verifying effective provider/model and required local images; retain all failures and incomplete attempts. Do not treat reference qualification as a model score.
