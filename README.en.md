# selfForge Agent

**A Python Coding Agent with Reliable Execution and Self-Testing**

A Python coding agent with a complete foundational harness, focused on autonomous coding and test-backed verification.

[简体中文](./README.md) · [English](./README.en.md) · [Architecture](./docs/architecture.md) · [Roadmap](./docs/roadmap.md)

```text
User task → native tool calling → search/read code → exact edits → pytest → exit-code evidence and trace
```

Run an end-to-end demo without an API key:

```sh
uv sync
uv run python examples/coding_demo.py
# Two generated fixtures: search_code → read_file → edit_file → run_tests → objective assertions
```

This demo checks the protocol and execution path; live model results are below. `selfforge-agent code` runs general coding tasks. PR review and Base/Head/Repaired repair remain specialized workflows.

## Capabilities and status

| Capability | Status | Current boundary |
| --- | --- | --- |
| Coding Agent Runtime | Implemented | OpenAI-compatible native tool calling, multiple turns, step/time limits, provider fallback |
| Tools and sessions | Implemented | read/edit/search/bash/run_tests; JSON Schema, workspace path checks, resumable JSONL sessions and traces |
| Context management | Implemented | Truncated tool output, approximate token budget, deterministic summary retaining task constraints |
| Docker executor | Implemented | Verified with a live Docker container; no network, resource limits, temporary workspace copied into tmpfs, separate artifact retrieval |
| Self-testing | Implemented | Uses actual pytest exit status; tasks without test evidence are `unverified` |
| Web Coding workbench | Implemented | Local Git repository tasks, live status and traces, cancellation, and session resume; background jobs live only in the server process |
| Coding workspaces | Implemented | New tasks default to a persistent branch and worktree from committed HEAD; resume, inspect a bounded diff in the browser, and explicitly clean up |
| PR Review / Repair | Implemented | Web review and checkout; repair edits use the constrained shared Tool Registry, Docker execution, JUnit evidence, and JSONL traces, while its proposal loop remains separate |
| Targeted test generation | Implemented (one candidate) | Generate one pytest file and run it in isolated Base/Head worktrees; differential evidence requires Base to pass and Head to fail an assertion |

## Quick Start

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/). PR workflows also need Git and [GitHub CLI](https://cli.github.com/). From this repository:

```sh
uv sync
export DEEPSEEK_API_KEY='your-key'  # or OPENROUTER_API_KEY
# Enable Docker first to use the default isolated executor
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent code --repo ./your-project --task 'Fix the failing test and verify it'
```

`code` now creates a `selfforge/SESSION_ID` branch and separate worktree from the repository's committed HEAD by default. Uncommitted changes in the source checkout are excluded; file tools edit only the new worktree. Worktrees live under `${XDG_DATA_HOME:-~/.local/share}/selfforge-agent/worktrees/` and remain for review and commit after the task. The repository needs an initial commit. Use `--workspace direct` only when you explicitly want source-checkout edits.

Commands and tests run in Docker by default. The container has no network and bounded CPU/memory/processes. A temporary workspace copy excluding `.env`, local keys, and sessions is transferred into container tmpfs. Container command edits do not propagate back; `edit_file` makes persistent edits. The sample image includes pytest only; extend it for project-specific dependencies. If Docker is unavailable, use `--sandbox host --trust-local` only for a trusted repository. The model receives files it reads and tool output.

DeepSeek and OpenRouter have default URLs and models. The web workbench can save a local key, URL, and automatically sync model lists. For another OpenAI-compatible endpoint, use `--model ID --base-url URL` and set `SELFFORGE_API_KEY` or `OPENAI_API_KEY`. `--provider openrouter` pins a provider; the default route falls back on request failure.

You can also start the local web workbench and enter a repository path and natural-language task in the browser:

```sh
uv run uvicorn selfforge_agent.api.app:app --host 127.0.0.1 --port 8000
# Open http://127.0.0.1:8000, select Docker, and start the Coding Agent
```

The page shows job status, the worktree path, a bounded diff against the base commit, and recent JSONL trace events, and can cancel or resume by session ID. It can remove a worktree once there are no uncommitted or untracked files; the branch remains. New sessions live under `selfforge-agent/sessions/` in the user data directory. To resume an older direct-edit session, select direct mode and enter its ID. Sessions can resume after a server restart, but the state of an active background job is process-local. Direct source-checkout edits require per-request confirmation. Web host execution also requires `SELFFORGE_ENABLE_HOST_TESTS=1` and explicit trust on each request.

## CLI

```sh
# General coding task
uv run selfforge-agent code --repo ./your-project --task 'Fix the add regression' \
  --max-steps 12 --time-budget 300

# Resume and inspect a session stored in the user data directory
uv run selfforge-agent code --repo ./your-project --resume SESSION_ID --task 'Continue and verify'
uv run selfforge-agent trace --repo ./your-project SESSION_ID

# Review a worktree's Git diff; commit before cleanup (the branch stays)
uv run selfforge-agent workspaces list --repo ./your-project
git -C /path/from/workspaces/list diff
uv run selfforge-agent workspaces remove SESSION_ID --repo ./your-project

# Explicitly edit the source checkout when needed
uv run selfforge-agent code --repo ./your-project --workspace direct --task 'Fix add'
# Also use --workspace direct to resume an older direct-edit session

# Run tests; Docker is the default
uv run selfforge-agent test --repo ./your-project --path tests

# Live Docker repair demo without an API key (build the image first)
uv run python examples/repair_demo.py

# Trusted host tests with JUnit counts
uv run selfforge-agent test --repo ./your-project --sandbox host --trust-local

# Specialized local PR comparison and Base/Head/Repaired repair
uv run selfforge-agent review --repo ./your-project --base BASE --head HEAD
uv run selfforge-agent repair --repo ./your-project --base BASE --head HEAD \
  --task 'Repair the regression shown by existing tests' --patch-output repair.patch

# Generate one candidate regression test and check its Base/Head behavior
uv run selfforge-agent generate-test --repo ./your-project --base BASE --head HEAD \
  --task 'add(2, 3) should return 5' --output test-evidence.json --test-output test_regression.py

# Explicit host execution for a trusted PR only
uv run selfforge-agent repair --repo ./your-project --base BASE --head HEAD \
  --task 'Repair the regression' --sandbox host --trust-local-tests
```

Agent statuses are `completed` (pytest succeeded after the last edit), `unverified` (finished without test evidence), `failed`, `max_steps`, `timed_out`, and `cancelled`. New sessions and traces live under `${XDG_DATA_HOME:-~/.local/share}/selfforge-agent/sessions/`, outside the target repository; older sessions remain resumable by ID. `workspaces remove` never forces deletion and refuses a dirty worktree. Ctrl-C interrupts a run; cancellation is checked between model and tool calls, while each call has its own timeout.

## Architecture

```mermaid
flowchart LR
  U[User task] --> I[CLI / Web workbench]
  I --> W[CodingWorkspaceManager / Git worktree]
  I --> A[CodingAgent]
  W --> A
  A --> C[ContextManager]
  C --> M[OpenAIChatAdapter]
  M --> T[ToolRegistry]
  T --> F[Files and search]
  T --> X[Docker or trusted host executor]
  X --> P[pytest]
  A --> S[SessionStore / JSONL trace]
  P --> A
  PR[PR Review / Repair] --> R[RepairAgent]
  R --> T
  R --> E[Git worktrees / DockerTestRunner]
  E --> X
  E --> S
```

The Runtime separates the loop, model adapter, tools, executor, context, and session modules. See the [architecture document](./docs/architecture.md). PR Repair now uses the same Tool Registry for exact edits with a stricter edit policy. Its model proposal and bounded repair loop remain separate from the general tool-calling agent.

## Verification and Evaluation

A general coding task is `completed` only when `run_tests` succeeded after the final edit. Otherwise it is `unverified`. `eval` independently reruns fixture tests and checks for a real source change; it does not trust the model's claim. PR Repair only emits a verified patch when Base tests pass, Head fails with an assertion, and Repaired passes. That establishes repair of an existing-suite regression, not causality for an individual review finding.

`generate-test` creates one pytest candidate and executes it on Base and Head. It writes `--test-output` only if Base passes and Head has an assertion failure with no collection errors. Other outcomes are `inconclusive` or `error`. The test runs only in temporary worktrees until explicitly exported. A differential result does not establish causality for an individual review finding.

```sh
uv run selfforge-agent eval --offline       # Deterministic protocol check, not model skill
uv run selfforge-agent eval --provider deepseek  # Live model on generated fixtures
uv run pytest
uv run ruff check .
```

On 2026-10-17, one local run produced **2/2** for the offline protocol fixtures and **2/2** for DeepSeek's live tool-calling run. The fixtures cover addition and name normalization; success requires a changed source file and an independent pytest exit code of zero. This tiny sample is not a general coding-agent benchmark. See [evaluation notes](./docs/evaluation.md) for reproduction and limits.

## Specialized Workflows: PR Review

The web workbench can also import your own or someone else's accessible GitHub PR, inspect diffs and heuristic/model findings, check out an editable branch, generate a differential test, and optionally run verified repair. Findings remain `suspected`; import and review never execute PR code. Web test generation and repair run in Docker by default. Explicit host execution still requires `SELFFORGE_ENABLE_HOST_TESTS=1` and per-request trust. The app does not push, comment on, or merge PRs. Repair traces are stored in the checkout's `.selfforge/sessions/`; inspect them with `selfforge-agent trace --repo CHECKOUT RUN_ID`.

## Roadmap

Next, build multiple test candidates linked to specific risks, PR risk-driven tests, cross-process job coordination, and broader objective evaluations. See the [roadmap](./docs/roadmap.md).
