# selfForge Agent product scope and acceptance

## Target

selfForge Agent serves Python Git repositories. A user submits a development task through the local Web workbench or CLI. One Agent uses constrained tools in an independent worktree, runs tests in an isolated environment, and delivers a reviewable diff, trace, test evidence, and failure reason. Sessions can resume and workspaces can be inspected and cleaned up. PR import, review, differential testing, and repair are specialized workflows.

Multi-agent orchestration, a general MCP platform, extra RAG, a cross-language IDE, and a large frontend are outside the current scope. `completed` requires nonempty JUnit success, matching pre/post-test and current workspace fingerprints, and a nonempty final answer; it does not prove that every task requirement was verified.

## Acceptance gates

| Area | Checkable condition | Current status |
| --- | --- | --- |
| Coding Runtime | A live model autonomously reads, edits, and tests real repositories; tool errors return to the model; step/time limits and reviewable workspaces remain enforced | Core implementation exists; reliability on real repositories still needs evaluation |
| Testing Harness | Docker is the default; project dependencies are reproducible; independent evidence and failure categories; generated tests pass Base/Head checks | Coding/Repair share a nonempty JUnit gate; Docker, differential checks, explicit offline dependency images and session environment binding exist; read-only layout/dependency detection exists; automatic environment installation and real-project repair reliability need work |
| Job reliability | Web jobs can be queried and resumed after a restart; multiple processes cannot modify one workspace concurrently; cancellation status is explicit | Durable job records, explicit resume, cross-process file locks, and cancellation markers exist; jobs do not auto-resume, and the active count is per process |
| Objective evaluation | At least 20 pinned tasks from at least 3 real Python repositories, each run at least 3 times with live models and independent visible and unseen tests; publish success rates, failure reasons, duration, and token usage | Repeated evaluation, pinned images, independent visible tests and reference preflight exist; five tasks in three repositories passed reference qualification. A separate three-task DeepSeek run scored 9/9 with three repeats each. Historical runs stay separate; formal twenty-task acceptance remains open; see [evaluation notes](./evaluation.md) |
| Delivery and regression | Web/CLI exposes diffs and evidence; explicit commit and PR creation/recovery; CI runs static checks, unit and Docker integration tests | Delivery entry points, protected paths, fixed-commit evidence and retry recovery exist. [PR #34](https://github.com/liuzhouquan/selfforge-agent/pull/34) records a real Git/gh backend creation/retry smoke; CLI/Web have regression coverage. No automatic force push or merge; consult CI for the delivered commit |

These numbers are project acceptance targets, not achieved results. A useful model success threshold should be set after the first real baseline. Synthetic demonstration fixtures must stay separate from published real-task results.

## Near-term order

1. Expand and freeze at least 20 distinct tasks from 3 or more real repositories, each with a behavior contract and reference fix; run each at least three times under a fixed provider policy and report failure categories.
2. Use the [backlog](./backlog.md) for small work units. Reproduced reliability problems take priority, followed by formal evaluation. Do not reimplement delivery, read-only environment discovery or Web recovery. Commands and acceptance process are in the [development guide](./development.md); the [roadmap](./roadmap.md) records capability direction.
