# PR #37–#41 review — 2026-11-04

Baseline: `7eabefc67439432001921e5dbe81aad50b2bfc18`. Preserve original feature heads `388a553`, `cfa017b`, `5f36d1a`, `6090dcc` and `6131899`; review corrections are additional commits. The integration order is #37 → #38 → #39 → #40 → #41. Each branch includes the reviewed predecessors to permit exact-head combined CI before merging.

## Standards

Six concrete findings in the environment branches: malformed sections were silently dropped or raised AttributeError; markers became unconditional pins; accepted uppercase hashes failed the shared plan parser; discovery stopped after the first128KiB; filename substrings misidentified flat lock profiles; format sniffing read symlinks before validation. Finite structured parsing now shares package/version/hash normalization and rendering in `runtime/lockfiles.py`, validates structures, rejects unresolved conditions, and uses bounded regular-file reads before content detection. Existing local/VCS exclusions and their assertions are preserved and explicitly documented as incomplete environment profiles.

Follow-up review reproduced deeply nested TOML causing a real API500. Both JSON and TOML recursion failures map to ValueError/API400. Pipfile additionally rejects duplicate keys and nonexact version syntax. These are input contracts, not formatting preferences.

## Spec

The private model report independently reconciles to60 unique attempts (20×3),53 successes,4 max_steps/1timeout/2holdout failures,2595928 provider-reported tokens and1692.396 seconds. All attempts report DeepSeek deepseek-flash; frozen image/reference/holdout digests match. [Public whitelisted attempt metadata](../benchmark/results/twenty-v2-model-repeat3.summary.json) carries every attempt and raw-report hash. Original report provenance is incomplete: no Python/Docker version or embedded application SHA/invocation; the historical operator record supplies application SHA/budgets. Those limits are disclosed without reconstructing missing history or claiming full repeatability.

Three task cards used nonexistent full SHAs; corrected to real baselines. #38 tool-count claims and causal wording did not match the raw report: replaced with actual counts and a behavior hypothesis. The change adjusts guidance and retains existing execution guards; no new hard guard or measured success-rate improvement is claimed. #39 counts returned model turns in the current run, including a current tool-phase exception, while an interrupted in-flight model call adds no turn. Original53/60 results and telemetry remain historical.

## Actual verification

- Locked dependency sync succeeded, Python3.12.13; local Docker29.8.0 and sandbox available.
- Before environment fixes: **6/6 Poetry regressions failed**, **7/7 Pipfile regressions failed**, and deep-TOML API regression observed **500 instead of400**.
- After fixes: budget selection10passed; interruption selection7passed; overlapping environment/builder selection79passed; deep-TOML plus Poetry selection7passed. These overlapping counts are not additive.
- Ruff and repair whitespace checks passed. Full combined pytest and exact-head PR/main CI outcomes are recorded in the PR delivery evidence after completion.
- No paid model calls were made during review. JSON/report reconciliation is not a model rerun. Frozen manifest/reference/holdout bytes are preserved.

## Remaining boundaries and handoff

Structured export supports a finite union of locked package pins. It does not solve Python/platform/groups/extras/index selection or install excluded local/VCS/file sources. Nonempty package markers and version forks require target-aware handling and are rejected. Hash readiness proves available bounded inputs, not installability; use the isolated builder for actual validation.

After merging, the original53/60 measurement predates Runtime budget/telemetry changes. Next bounded task SF-EVAL-04 is automatic run-provenance capture, with application SHA, toolchain versions and effective budgets/configuration. Any future model comparison needs a separately recorded run; do not combine scores or mutate frozen input/history. Exact delivered SHAs, counts and CI URLs belong in the final PR evidence.
