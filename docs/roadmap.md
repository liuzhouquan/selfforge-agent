# Roadmap

Development order and per-change acceptance checks: [development guide](./development.md). Confirmed reliability fixes and test-evidence consistency precede expansion of the acceptance task count.

| Priority | Status | Work |
| --- | --- | --- |
| 1 | Implemented | General Coding reuses JUnit runners with a nonempty-pass gate, preserves Docker cancellation, and requires retesting of legacy exit-code-only session evidence |
| 1 | Implemented | Write-ahead verification invalidation for edits, persistent commands, test attempts and new tasks; conservative recovery of legacy pending calls, with abrupt-process-exit regression coverage |
| 2 | Planned | Reproducible project dependency images beyond the default Python/pytest sandbox |
| 1 | Implemented | General native tool-calling Runtime, validated file/search/bash/test tools, model fallback, step and time limits |
| 1 | Implemented | Shared bounded JUnit failure feedback, opt-in private evaluation diagnostics, live-model failure/edit/retest demo, and fresh Host bytecode cache namespace for correct rapid retesting |
| 1 | Implemented | Resumable JSONL sessions in user data storage, metadata traces, output caps, approximate context compaction |
| 1 | Implemented | Objective two-case coding evaluation and deterministic offline end-to-end demo |
| 1 | Implemented | Manifest-driven repeated Coding evaluation over pinned local Git commits, unseen pytest holdouts, baseline negative controls, per-case Docker images pinned to IDs, and aggregate reason/token/latency reports with sanitized tool-step diagnostics |
| 2 | Implemented | Docker command executor and Docker-backed JUnit TestRunner; live daemon Base/Head/Repaired integration test |
| 2 | Implemented | PR Repair shares the Docker executor and JSONL session trace while preserving Base/Head/Repaired evidence |
| 2 | Implemented | PR Repair exact edits use the shared Tool Registry with a changed-source allowlist and size limit; model proposal remains specialized |
| 2 | Implemented | Local Web Coding tasks use the common Runtime with status, recent traces, cancellation, and session resume |
| 2 | Implemented | General Coding defaults to persistent reviewable Git worktrees, resumes in place, and provides safe cleanup; direct edits require explicit selection |
| 2 | Implemented | Web Coding displays a bounded, read-only diff against the worktree base commit |
| 2 | Implemented | Durable Web job records and history, explicit resume after server interruption, cross-process workspace locks and cancellation markers; active-job limit remains per process |
| 3 | Implemented | Up to three distinct pytest candidates with feedback, early stop on objective Base-pass/Head-assertion-fail evidence, and an optional review risk anchored to an added line; CLI and Web entry points |
| 3 | In Progress | A tuned three-repository, four-task DeepSeek pilot scored 4/4 with one repeat per task under the independent visible/hidden test gate. The earlier three-task, three-repeat run scored 8/9 under the older gate. Build a frozen 20-task acceptance set and dependency-ready images before claiming broader reliability |
| 3 | Implemented | GitHub Actions runs locked dependency installation, Ruff, unit tests, and Docker integration tests on pushes to main and pull requests; first remote run passed |
| 3 | Planned | Automatic PR risk prioritization and risk-to-test causal investigation; calibrated monetary cost reporting when model prices are available |
| 4 | Planned | More precise tokenization, richer compaction, interactive approval, and richer Coding UI controls |

General Coding now creates a worktree from committed HEAD by default and edits there; source-checkout changes are excluded. Worktrees persist until the user commits or reverts edits and explicitly removes the worktree. The branch remains after removal. Docker isolation applies to commands, while file tools are constrained to the selected workspace. Web Coding and PR repair default to Docker; host execution requires an explicit operator setting and per-request trust.
