# selfForge Agent product scope and acceptance

## Target

The product target is multi-language Git repositories and a standalone test harness that needs no model. The current verified execution path remains Python/pytest; JS/TS, Go and Java adapters are planned, not implemented. A user submits a development task through the local Web workbench or CLI. One Agent uses constrained tools in an independent worktree, runs tests in an isolated environment, and delivers a reviewable diff, trace, test evidence, and failure reason. Sessions can resume and workspaces can be inspected and cleaned up. PR import, review, differential testing, and repair are specialized workflows.

The service remains implemented in Python. Framework adapters reuse the existing executors and evidence gate rather than creating another Agent Runtime.

Multi-agent orchestration, a general MCP platform, extra RAG, a cross-language IDE, and a large frontend are outside the current scope. Currently, `completed` requires nonempty JUnit success, matching pre/post-test and current workspace fingerprints, and a nonempty final answer. The target adapters may validate JUnit or native event evidence but must require actual nonempty test passes, no failures/errors, and plan/environment/workspace binding; it does not prove that every task requirement was verified.

## Acceptance gates

| Area | Checkable condition | Current status |
| --- | --- | --- |
| Coding Runtime | A live model autonomously reads, edits, and tests real repositories; tool errors return to the model; step/time limits and reviewable workspaces remain enforced | Core implementation exists; reliability on real repositories still needs evaluation |
| Multi-language test harness (target) | Standalone CLI/API without a model; separately qualified pytest, Vitest, Go test, Maven Surefire and Gradle Test backends; all required monorepo jobs must pass | Not implemented; first unify Python and JS/TS, then Go and Java. The [SDD](./SDD.md#多语言通用测试器目标契约待实现) owns contracts and tasks |
| Testing Harness (current) | Docker is the default; project dependencies are reproducible; independent evidence and failure categories; generated tests pass Base/Head checks | Coding/Repair share a nonempty JUnit gate; Docker, differential checks, explicit offline dependency images and session environment binding exist; read-only layout/dependency detection exists; automatic environment installation and real-project repair reliability need work |
| Job reliability | Web jobs can be queried and resumed after a restart; multiple processes cannot modify one workspace concurrently; cancellation status is explicit | Durable job records, explicit resume, cross-process file locks, and cancellation markers exist; jobs do not auto-resume, and the active count is per process |
| Historical Python evaluation baseline | At least 20 pinned tasks from at least 3 real Python repositories, each run at least 3 times with live models and independent visible and unseen tests; publish success rates, failure reasons, duration, and token usage | Repeated evaluation, pinned images, independent visible tests and reference preflight exist; [v2 has twenty tasks in five repositories with Docker reference qualification and a frozen policy](../benchmark/validated-candidates/twenty-tasks-v2.md). A separate three-task DeepSeek run scored 9/9 with three repeats each. Historical runs stay separate; the first v2 measurement scored 53/60, with incomplete historical provenance; see [evaluation notes](./evaluation.md) |
| Delivery and regression | Web/CLI exposes diffs and evidence; explicit commit and PR creation/recovery; CI runs static checks, unit and Docker integration tests | Delivery entry points, protected paths, fixed-commit evidence and retry recovery exist. [PR #34](https://github.com/liuzhouquan/selfforge-agent/pull/34) records a real Git/gh backend creation/retry smoke; CLI/Web have regression coverage. No automatic force push or merge; consult CI for the delivered commit |

These numbers are project acceptance targets, not achieved results. A useful model success threshold should be set after the first real baseline. Synthetic demonstration fixtures must stay separate from published real-task results.

## Near-term order

The [unified SDD](./SDD.md) owns current milestones, Ready specifications, acceptance and handoff. The first twenty-case measurement is complete, not a task to repeat implicitly. The next stage follows SF-HARNESS-01..09: shared contracts, explicit environments/profiles, framework adapters, shared CLI/API/Agent execution, independent regression checks and pinned real-repository reference qualification. Each initial language needs at least one pinned real-repository reference task, with both Java backends independently exercised. New support and model scores are reported separately from historical Python results. Automatic online installation, universal out-of-the-box framework support and generation/Repair for every language are not implemented promises.
