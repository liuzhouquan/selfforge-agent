# Roadmap

Development order and acceptance checks: [development guide](./development.md). The iterator/traversal delivery passed 259 local tests, including two new Host verification-snapshot regressions. The earlier [combined #24–#30 delivery](./runtime-integration.md) passed 257; branch counts are historical and not additive. Confirmed reliability fixes and evidence consistency precede expansion of acceptance task count.

| Priority | Status | Work |
| --- | --- | --- |
| 1 | Implemented | General Coding reuses JUnit runners with a nonempty-pass gate, preserves Docker cancellation, and requires retesting of legacy exit-code-only session evidence |
| 1 | Implemented | Write-ahead verification invalidation for edits, persistent commands, test attempts and new tasks; conservative recovery of legacy pending calls, with abrupt-process-exit regression coverage |
| 1 | Implemented | Bounded workspace content fingerprints bind nonempty JUnit evidence before/after tests, before reuse and final completion; external edits revoke evidence, unbound legacy sessions require retesting. Local regression: 274 passed / 11 Docker-dependent skips; remote integration status belongs to the delivery PR |
| 1 | Implemented | General native tool-calling Runtime, validated file/search/bash/test tools, model fallback, step and time limits |
| 1 | Implemented | Shared bounded JUnit failure feedback, opt-in private evaluation diagnostics, live-model failure/edit/retest demo, and fresh Host bytecode cache namespace for correct rapid retesting |
| 1 | Implemented | Per-turn remaining-budget and verification feedback, near-cap verification guidance, context space reserved for transient controls, and metadata budget traces; no extra calls or relaxed completion gate |
| 1 | Implemented | Resumable JSONL sessions in user data storage, metadata traces, output caps, approximate context compaction |
| 1 | Implemented | Objective two-case coding evaluation and deterministic offline end-to-end demo |
| 1 | Implemented | Provider-free task reference qualification: frozen external patches, allowed-path checks, baseline failure and independent visible/holdout positive controls before model runs |
| 1 | Implemented | Manifest-driven repeated Coding evaluation over pinned local Git commits, unseen pytest holdouts, baseline negative controls, per-case Docker images pinned to IDs, and aggregate reason/token/latency reports with sanitized tool-step diagnostics |
| 2 | Implemented | Explicit offline dependency-image builder with hash-locked wheels, frozen base/result IDs, src-layout support and Web Coding image selection; pinned ItsDangerous suite passed 297 tests |
| 2 | Implemented | Coding sessions persist execution mode and immutable Docker image ID; CLI/Web resume reuses it, rejects environment changes and exposes it in durable history |
| 2 | Planned | Broader dependency profiles and automatic environment detection; source builds and OS dependencies remain operator-managed |
| 2 | Implemented | Docker command executor and Docker-backed JUnit TestRunner; live daemon Base/Head/Repaired integration test |
| 2 | Implemented | PR Repair shares the Docker executor and JSONL session trace while preserving Base/Head/Repaired evidence |
| 2 | Implemented | PR Repair exact edits use the shared Tool Registry with a changed-source allowlist and size limit; model proposal remains specialized |
| 2 | Implemented | Local Web Coding tasks use the common Runtime with status, recent traces, cancellation, and session resume |
| 2 | Implemented | General Coding defaults to persistent reviewable Git worktrees, resumes in place, and provides safe cleanup; direct edits require explicit selection |
| 2 | Implemented | Web Coding displays a bounded, read-only diff against the worktree base commit |
| 2 | Implemented | Durable Web job records and history, explicit resume after server interruption, cross-process workspace locks and cancellation markers; active-job limit remains per process |
| 3 | Implemented | Up to three distinct pytest candidates with feedback, early stop on objective Base-pass/Head-assertion-fail evidence, and an optional review risk anchored to an added line; CLI and Web entry points |
| 3 | In Progress | Five distinct tasks across three repositories passed Docker reference qualification. A separate three-task iterator/traversal DeepSeek run scored 9/9 (three repeats each); prior 5/6, 4/4 and old-gate 8/9 results remain separate. Expand diverse contract-reviewed tasks before formal twenty-task acceptance; do not combine scores or count overlaps |
| 3 | Implemented | GitHub Actions runs locked dependency installation, Ruff, unit tests, and Docker integration tests on pushes to main and pull requests; first remote run passed |
| 3 | Planned | Automatic PR risk prioritization and risk-to-test causal investigation; calibrated monetary cost reporting when model prices are available |
| 4 | Planned | More precise tokenization, richer compaction, interactive approval, and richer Coding UI controls |

General Coding now creates a worktree from committed HEAD by default and edits there; source-checkout changes are excluded. Worktrees persist until the user commits or reverts edits and explicitly removes the worktree. The branch remains after removal. Docker isolation applies to commands, while file tools are constrained to the selected workspace. Web Coding and PR repair default to Docker; host execution requires an explicit operator setting and per-request trust.
