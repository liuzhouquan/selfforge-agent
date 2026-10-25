# Roadmap

| Priority | Status | Work |
| --- | --- | --- |
| 1 | Implemented | General native tool-calling Runtime, validated file/search/bash/test tools, model fallback, step and time limits |
| 1 | Implemented | Resumable JSONL sessions in user data storage, metadata traces, output caps, approximate context compaction |
| 1 | Implemented | Objective two-case coding evaluation and deterministic offline end-to-end demo |
| 1 | Implemented | Manifest-driven repeated Coding evaluation over pinned local Git commits, unseen pytest holdouts, baseline negative controls, and aggregate reason/token/latency reports; Docker default |
| 2 | Implemented | Docker command executor and Docker-backed JUnit TestRunner; live daemon Base/Head/Repaired integration test |
| 2 | Implemented | PR Repair shares the Docker executor and JSONL session trace while preserving Base/Head/Repaired evidence |
| 2 | Implemented | PR Repair exact edits use the shared Tool Registry with a changed-source allowlist and size limit; model proposal remains specialized |
| 2 | Implemented | Local Web Coding tasks use the common Runtime with status, recent traces, cancellation, and session resume |
| 2 | Implemented | General Coding defaults to persistent reviewable Git worktrees, resumes in place, and provides safe cleanup; direct edits require explicit selection |
| 2 | Implemented | Web Coding displays a bounded, read-only diff against the worktree base commit |
| 2 | In Progress | Durable Web job recovery and cross-process workspace coordination |
| 3 | Implemented | Up to three distinct pytest candidates with feedback, early stop on objective Base-pass/Head-assertion-fail evidence, and an optional review risk anchored to an added line; CLI and Web entry points |
| 3 | In Progress | Build a real multi-repository task set and publish repeated live success/failure results; improve dependency-ready Docker images and test selection based on observed failures |
| 3 | Planned | Automatic PR risk prioritization and risk-to-test causal investigation; calibrated monetary cost reporting when model prices are available |
| 4 | Planned | More precise tokenization, richer compaction, interactive approval, and richer Coding UI controls |

General Coding now creates a worktree from committed HEAD by default and edits there; source-checkout changes are excluded. Worktrees persist until the user commits or reverts edits and explicitly removes the worktree. The branch remains after removal. Docker isolation applies to commands, while file tools are constrained to the selected workspace. Web Coding and PR repair default to Docker; host execution requires an explicit operator setting and per-request trust.
