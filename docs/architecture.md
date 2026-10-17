# selfForge Agent architecture

## General Coding Runtime

`cli code` resolves a provider (DeepSeek, OpenRouter, or a custom OpenAI-compatible endpoint), selects a command executor, creates a project-local `SessionStore`, and starts `CodingAgent`.

```text
Task → CodingAgent → ContextManager → OpenAIChatAdapter
                    ↑                  ↓ native tool calls
                 SessionStore ← ToolRegistry → file/search tools
                    ↓                       → DockerExecutor / trusted HostExecutor → pytest
                JSONL trace
```

The loop asks the model for native function calls, validates arguments with Pydantic JSON Schema, executes one tool at a time, and returns every tool result to the model. It stops on a final answer, step/time limit, cancellation, or unrecoverable model failure. Tool errors are returned as tool observations so the model can recover. `completed` requires a successful `run_tests` after the last `edit_file`; otherwise the final state is `unverified`. A model statement alone is never test evidence.

`ToolRegistry` confines paths to the workspace, rejects traversal and symlink escapes, caps file sizes, search results, command timeouts, and tool output. `SessionStore` appends messages and status to mode-0600 JSONL, and a separate metadata-only JSONL trace records model calls, tool durations, token usage, errors, and status transitions. Resume reconstructs messages and fills any interrupted tool response. `ContextManager` estimates tokens by size, truncates tool output, and summarizes earlier actions when a budget is exceeded while retaining the system prompt and original/latest user task. This is a deterministic approximate budget, not tokenizer-exact accounting.

Docker command execution stages a temporary workspace copy that excludes `.env`, `.local`, `.selfforge`, `.git`, and `.venv`; container writes are discarded. It uses `--network none`, read-only container root, a single staged bind mount, non-root UID, dropped capabilities, no-new-privileges, CPU/memory/PID limits, and a wall timeout. The image is built from `docker/Dockerfile`. Host execution requires `--sandbox host --trust-local`. Docker arguments have unit coverage, but this development environment has no Docker daemon; an actual container integration test is still required. The general agent's `edit_file` writes to the requested workspace directly. For untrusted or important repositories, use a disposable Git worktree as `--repo` so edits are isolated and reviewable.

## Existing specialized PR workflows

The FastAPI app serves a PR workbench. `integrations.github_pr` reads PR metadata and patches with `gh api`, creates `PRContext`, and can clone the head into an editable checkout. `ReviewService` runs deterministic baseline findings. `ProviderReviewer` optionally calls configured models and validates findings against actual added lines; findings remain `suspected`. PR import/review never executes PR code.

`RepairAgent` retains its bounded Base/Head/Repaired process in three detached Git worktrees. It only edits changed Python source and emits a patch when Base pytest passes, Head has an assertion failure, and Repaired pytest passes. `TestRunner` records JUnit counts and process exit status. Web repair currently executes trusted host pytest behind localhost, operator, and per-request trust gates. It is not yet routed through the new general Docker executor or session loop. This integration is the next architectural step; the old repair logic remains the authoritative specialized implementation in the meantime.

Provider settings and HTTP transport are shared across general coding and PR workflows. Keys stay in the server environment or ignored `.local/providers.json`; traces do not include credentials. CLI evaluation uses generated fixtures and independently reruns tests to prevent model self-assessment from determining success.
