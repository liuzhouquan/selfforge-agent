# Roadmap

| Priority | Status | Work |
| --- | --- | --- |
| 1 | Implemented | General native tool-calling Runtime, validated file/search/bash/test tools, model fallback, step and time limits |
| 1 | Implemented | Project-local resumable JSONL sessions, metadata traces, output caps, approximate context compaction |
| 1 | Implemented | Objective two-case coding evaluation and deterministic offline end-to-end demo |
| 2 | In Progress | Docker command executor: constrained flags and timeout cleanup implemented; real daemon integration not yet validated |
| 2 | In Progress | Unify PR Repair with the general Runtime's tools, session trace, and Docker-backed TestRunner while preserving Base/Head/Repaired evidence |
| 3 | Planned | Targeted test generation that can objectively reproduce a PR finding on Head but not Base |
| 3 | Planned | Risk-driven PR test selection, broader multi-repository evaluation, repeated-run success rates and cost/latency tracking |
| 4 | Planned | More precise tokenization, richer compaction, interactive approval and a general Coding UI |

The current general agent edits its selected workspace directly. A disposable Git worktree remains the recommended input for risky tasks until an automatic workspace lifecycle is implemented. Docker isolation applies to commands, while file tools are constrained to the selected workspace. The existing web repair workflow still runs trusted host tests and must not be used on untrusted code.
