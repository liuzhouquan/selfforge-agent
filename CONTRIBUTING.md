# Contributing to selfForge Agent

Start with [AGENTS.md](./AGENTS.md), then the [development guide](./docs/development.md). The same workflow applies to human contributors and coding agents.

| Need | Read |
| --- | --- |
| Understand the product and what remains open | [Product scope](./docs/product-scope.md) |
| Pick a small, bounded next task | [Backlog](./docs/backlog.md) |
| Set up, implement, verify and hand off | [Agent workflow](./docs/agent-workflow.md) |
| Copy a task brief and completion record | [Task template](./docs/task-template.md) |
| Change Runtime, state, permissions or evidence | Relevant sections of [engineering invariants](./docs/engineering-invariants.md) |
| Find source/test entry points | [Development guide](./docs/development.md#module-entry-points), [architecture](./docs/architecture.md) |

Deliver one coherent behavior per task. Planned features in the Roadmap are not a request to implement all of them in one branch. Use a separate owned checkout/worktree for simultaneous work, and preserve other contributors' changes and commits.

Bug fixes need a reproducible failure and a meaningful regression. Source changes need the combined test/lint gates; documentation changes need link/path and whitespace checks. Record real outcomes and missing environment requirements. The PR template makes these boundaries reviewable.

Use current session authorization for external services and repository integration. No new permissions, keys, or services are required merely to read these guides.
