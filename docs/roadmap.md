# Roadmap

| Priority | Status | Work |
| --- | --- | --- |
| 1 | Implemented | General native tool-calling Runtime, validated file/search/bash/test tools, model fallback, step and time limits |
| 1 | Implemented | Project-local resumable JSONL sessions, metadata traces, output caps, approximate context compaction |
| 1 | Implemented | Objective two-case coding evaluation and deterministic offline end-to-end demo |
| 2 | Implemented | Docker command executor and Docker-backed JUnit TestRunner; live daemon Base/Head/Repaired integration test |
| 2 | Implemented | PR Repair shares the Docker executor and JSONL session trace while preserving Base/Head/Repaired evidence |
| 2 | In Progress | Route PR Repair's proposer/edit flow through the general Tool Registry and common execution controls |
| 3 | Planned | Targeted test generation that can objectively reproduce a PR finding on Head but not Base |
| 3 | Planned | Risk-driven PR test selection, broader multi-repository evaluation, repeated-run success rates and cost/latency tracking |
| 4 | Planned | More precise tokenization, richer compaction, interactive approval and a general Coding UI |

The current general agent edits its selected workspace directly. A disposable Git worktree remains the recommended input for risky tasks until an automatic workspace lifecycle is implemented. Docker isolation applies to commands, while file tools are constrained to the selected workspace. Web PR repair now defaults to Docker; host testing requires an explicit operator setting and per-request trust.
