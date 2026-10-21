# Engineering notes for future sessions

- This repository is selfForge Agent, a Python Coding Agent. General `code` tasks are the primary workflow; PR Review and Base/Head/Repaired repair remain specialized workflows. Preserve Git history and existing behavior.
- The repository is independent from sibling projects. Study MiniCode, mini-SWE-agent, and OpenHands SDK locally; reuse source only after checking its license and preserving required notices. Do not modify sibling repositories.
- Python 3.11+, uv, FastAPI, Pydantic v2, pytest, Git CLI. Run `uv run pytest` and `uv run ruff check .` before committing. Keep docs truthful about implemented and untested capabilities.
- `runtime.agent.CodingAgent` is the general native tool-calling loop. `runtime.tools.ToolRegistry` validates schemas, paths, timeouts, and output size. Keep tool results in the model conversation and session log. Bound steps and wall time. Do not add `shell=True`.
- General command execution defaults to Docker. Host execution needs explicit trust. File tools stay inside the selected workspace. Docker integration was not live-tested in the original development environment; do not claim otherwise until a daemon test passes.
- A general task is `completed` only after `run_tests` succeeds after the last edit; without such evidence use `unverified`. Evaluation must inspect actual files and run pytest independently, not trust the model's answer.
- `.selfforge/` stores mode-0600 sessions and traces; `.local/providers.json` may contain a key. Both are ignored by Git. Never log or commit keys, generated reports, `.env`, virtual environments, or external course source.
- `ReviewService` findings are heuristic and `suspected`. Existing suite failure alone does not prove a particular finding. Preserve subprocess exit codes and error statuses.
- PR import/review through `gh` never runs code. Web repair requires localhost, `SELFFORGE_ENABLE_HOST_TESTS=1`, and per-request trust. Its existing `TestRunner` uses host pytest and has no sandbox.
- `RepairAgent` keeps three temporary detached worktrees and only emits a patch when Base passes, Head has assertion failure, and Repaired passes. It edits changed Python source, not tests. Integrate it with the general Runtime incrementally without weakening evidence or trust gates.
