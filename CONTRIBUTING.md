# Contributing to selfForge Agent

Start with [docs/SDD.md](./docs/SDD.md). It contains product scope, current Ready tasks, concrete acceptance criteria, source entry points, verification and handoff in one place. The same specification applies to humans and coding agents.

[AGENTS.md](./AGENTS.md) is the automatically loaded rule entry. Consult the [development guide](./docs/development.md), [architecture](./docs/architecture.md) and relevant [engineering invariants](./docs/engineering-invariants.md) when the selected task needs implementation detail.

Claim one task in the SDD after checking open PRs and workspace ownership. Record its acceptance IDs, exact test nodes/commands, tested SHA and CI; update its status and handoff there. Do not create another live Backlog or duplicate task specification. Old Backlog/task cards are historical evidence.

Preserve unknown work and original commits. Bugs need meaningful failing regressions. Source changes need the combined pytest/Ruff gates and relevant Docker/API/process verification; documentation-only changes need links, anchors, references and whitespace checks. Never weaken assertions, permissions or evidence to obtain green checks.

Use current session authorization for external actions and integration. The SDD does not grant permissions or add an approval layer.
