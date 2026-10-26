# selfForge Agent product scope and acceptance

## Target

selfForge Agent serves Python Git repositories. A user submits a development task through the local Web workbench or CLI. One Agent uses constrained tools in an independent worktree, runs tests in an isolated environment, and delivers a reviewable diff, trace, test evidence, and failure reason. Sessions can resume and workspaces can be inspected and cleaned up. PR import, review, differential testing, and repair are specialized workflows.

Multi-agent orchestration, a general MCP platform, a cross-language IDE, and a large frontend are outside the current scope. `completed` means pytest succeeded after the Agent's last edit; it does not prove that every task requirement was verified.

## Acceptance gates

| Area | Checkable condition | Current status |
| --- | --- | --- |
| Coding Runtime | A live model autonomously reads, edits, and tests real repositories; tool errors return to the model; step/time limits and reviewable workspaces remain enforced | Core implementation exists; reliability on real repositories still needs evaluation |
| Testing Harness | Docker is the default; project dependencies are reproducible; independent test evidence and failure categories are available; generated tests pass Base/Head checks | pytest, Docker, and differential checks exist; dependency handling and the general test/repair loop need work |
| Job reliability | Web jobs can be queried and resumed after a restart; multiple processes cannot modify one workspace concurrently; cancellation status is explicit | Durable job records, explicit resume, cross-process file locks, and cancellation markers exist; jobs do not auto-resume, and the active count is per process |
| Objective evaluation | At least 20 pinned tasks from at least 3 real Python repositories, each run at least 3 times with live models and unseen tests; publish success rates, failure reasons, duration, and token usage | Repeated evaluation and per-case pinned images exist; two project-authored tasks in two real repositories scored 6/6 across three runs each with a step budget tuned after exploratory trials; the 3-repository/20-task acceptance set remains open |
| Delivery and regression | Web/CLI exposes diffs and test evidence; CI runs static checks, unit tests, and Docker integration when available | Local diff and checks exist; the first GitHub Actions run passed Ruff, 90 unit tests, and 5 Docker integration tests; complete PR submission remains open |

These numbers are project acceptance targets, not achieved results. A useful model success threshold should be set after the first real baseline. Synthetic demonstration fixtures must stay separate from published real-task results.

## Near-term order

1. Build a real Python repository task set with the [repeated-evaluation tool](./benchmark.md), then measure failure modes.
2. Improve dependency-ready execution, test selection, and repair based on those failures.
3. Finish CI and the delivery workflow, then keep testing Web recovery in real service deployments.
