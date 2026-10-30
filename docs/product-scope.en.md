# selfForge Agent product scope and acceptance

## Target

selfForge Agent serves Python Git repositories. A user submits a development task through the local Web workbench or CLI. One Agent uses constrained tools in an independent worktree, runs tests in an isolated environment, and delivers a reviewable diff, trace, test evidence, and failure reason. Sessions can resume and workspaces can be inspected and cleaned up. PR import, review, differential testing, and repair are specialized workflows.

Multi-agent orchestration, a general MCP platform, a cross-language IDE, and a large frontend are outside the current scope. `completed` means pytest succeeded after the Agent's last edit; it does not prove that every task requirement was verified.

## Acceptance gates

| Area | Checkable condition | Current status |
| --- | --- | --- |
| Coding Runtime | A live model autonomously reads, edits, and tests real repositories; tool errors return to the model; step/time limits and reviewable workspaces remain enforced | Core implementation exists; reliability on real repositories still needs evaluation |
| Testing Harness | Docker is the default; project dependencies are reproducible; independent evidence and failure categories; generated tests pass Base/Head checks | Coding/Repair share a nonempty JUnit gate; Docker, differential checks, explicit offline dependency images and session environment binding exist; automatic environment detection and real-project repair reliability need work |
| Job reliability | Web jobs can be queried and resumed after a restart; multiple processes cannot modify one workspace concurrently; cancellation status is explicit | Durable job records, explicit resume, cross-process file locks, and cancellation markers exist; jobs do not auto-resume, and the active count is per process |
| Objective evaluation | At least 20 pinned tasks from at least 3 real Python repositories, each run at least 3 times with live models and independent visible and unseen tests; publish success rates, failure reasons, duration, and token usage | Repeated evaluation, pinned images, independent visible tests and external reference-fix qualification exist; four project-authored tasks in three repositories scored 4/4 with one repeat each under the new gate. Three tasks scored 8/9 across three repeats each under the older gate; nine candidates passed baseline checks, with five added cases still lack live-model results under that manifest; formal 20-task acceptance remains open |
| Delivery and regression | Web/CLI exposes diffs and test evidence; CI runs static checks, unit tests, and Docker integration when available | Local diff and checks exist; the first GitHub Actions run passed Ruff, 90 unit tests, and 5 Docker integration tests; complete PR submission remains open |

These numbers are project acceptance targets, not achieved results. A useful model success threshold should be set after the first real baseline. Synthetic demonstration fixtures must stay separate from published real-task results.

## Near-term order

1. Prioritize reproduced reliability problems. After interruption recovery, align general Coding with independent evaluation requirements for nonempty JUnit-backed test evidence.
2. Add reproducible project dependencies, test selection, and the repair loop. Review candidate task contracts and reference fixes before freezing 20 tasks with three repeats each and recording failure modes.
3. Maintain existing CI, complete delivery workflows, and keep testing Web recovery in real deployments. See the [development guide (Chinese)](./development.md) for concrete acceptance checks.
