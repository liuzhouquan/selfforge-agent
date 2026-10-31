# Working on selfForge Agent

selfForge Agent is a Python Coding Agent with a testing harness. General Coding is the primary workflow; PR Review, differential tests and Repair are specialized workflows. The repository/package name is `selfforge-agent` / `selfforge_agent`; the existing checkout directory may still be named `verified-pr-agent`. Do not switch to a sibling project because of IDE tabs.

Current non-goals: product Multi-Agent orchestration, a general MCP platform, extra RAG, a large frontend or another Runtime, unless the user explicitly changes the scope.

## Start here

1. Read [development guide](docs/development.md) for setup, module entry points and document authority.
2. Read [agent workflow](docs/agent-workflow.md). Confirm checkout ownership, clean/dirty state, base SHA and open PR dependencies before editing.
3. Use the current user task as the scope. For “continue development”, select **one** ready item from [backlog](docs/backlog.md); Roadmap entries are not an instruction to implement every planned feature.
4. For behavior, permission or cross-module changes, fill the [task brief](docs/task-template.md) before implementation. A small documentation fix can put the same essentials in its PR description.
5. Read the relevant sections of [engineering invariants](docs/engineering-invariants.md) and linked contracts. Do not rely on this short entry file alone when changing Runtime/Harness behavior.

## Work rules

- One task should deliver one independently checkable behavior. Split unrelated capabilities into separate tasks/PRs. Necessary shared prerequisites are allowed when explained in the task brief; do not create another Runtime or speculative framework.
- Reuse existing modules and good open-source code after checking licenses/notices. Do not modify sibling repositories or publish private course source.
- Each simultaneous developer/agent needs an owned branch and checkout/worktree. Do not switch a shared checkout, overwrite another agent's changes, or stage unknown files. Parallel agent work still depends on the current user's/tool's authorization; this document does not automatically request it.
- For a bug, establish a concrete failing case before the fix. Test the public boundary and meaningful error paths, not just a private helper. Never delete tests, weaken assertions, or adapt production code to a test double merely to get green checks.
- Preserve protected paths, default Docker isolation, explicit Host trust, bounded execution, cancellation and write-ahead evidence invalidation. A passing test boolean is not proof about a later workspace or commit. Read the owning invariants before changing these paths.
- Keep native tool messages and all their tool replies together; preserve user constraints on compaction. Keep one general `CodingAgent`, shared tools and JUnit runners. Repair's separate proposer loop is a documented boundary.
- Do not call heuristic token estimates exact, stack matches causal proof, synthetic fixtures real-repository benchmarks, or configured-rate estimates actual billing. Describe implemented, partial and unverified behavior separately.
- Keep keys, `.env`, provider settings, private sessions/traces, generated logs/reports and external course source out of commits and public PRs. Publish sanitized evidence only.

## Verification and delivery

- Source changes: locked dependencies, relevant regression, complete combined pytest and Ruff; exercise Docker/API/process boundaries when affected. Commands and skip reporting: [development guide](docs/development.md#verification-gates).
- Documentation-only changes: check local links, referenced code/test paths and `git diff --check`; do not rerun Python tests for prose alone. Hosted CI still runs its configured checks.
- Record commands, actual outcomes, environment limitations and the tested SHA. Historical branch counts are not additive. Mocks do not prove live provider, Docker or GitHub execution.
- Follow current user authorization and repository protections for push/merge. These docs neither grant remote actions nor add an extra approval layer. When integration is authorized, merge only the reviewed commit after its CI passes, preserve history, and verify the resulting main tree/status.
- Leave the [handoff section](docs/task-template.md#handoff) with exact HEAD, completed/pending work, test evidence and the next executable step. Do not claim “done” while required task criteria remain.
