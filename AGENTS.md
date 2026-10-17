# Engineering notes for future sessions

- This repository is independent from sibling projects. Existing source may be reused
  where it helps, after checking its license and preserving required notices and
  attribution. Do not modify sibling repositories as part of this project.
- Python 3.11+, uv, FastAPI, Pydantic v2, pytest, Git CLI. Run `uv run pytest` and
  `uv run ruff check .` before committing.
- `ReviewService` is the shared CLI/API workflow. `ReviewAgent` is a Protocol; the
  current `BaselineReviewer` is deterministic and must always label findings heuristic.
- A finding is `suspected` until a targeted test objectively reproduces it. Existing
  suite failure alone does not prove a finding. Preserve exit codes and report errors.
- Web review reads any accessible GitHub PR via `gh`, without executing code. The older
  local `/review` API accepts commit IDs in `VERIFIED_PR_AGENT_REPO_ROOT`.
- Model keys remain on the local server, either in its environment or the ignored
  `.local/providers.json` file (mode 0600). Never return or log secrets. DeepSeek and
  OpenRouter are configured independently; log provider attempts and actual usage. Provider balance
  checks run only on explicit user request. Review API calls default to rules only.
- Host test execution requires `trusted=True`; web repair also requires localhost,
  `VPA_ENABLE_HOST_TESTS=1`, and explicit per-request trust. It has no sandbox.
- `repair` uses three temporary detached worktrees (base, head, repair). It edits only
  changed Python source files, preserves tests, and emits a patch only after the full
  existing suite passes on repair while base passes and head fails. It needs explicit
  local trust and a configured model. Web repair may invoke it only behind the trust gates.
- Never add `shell=True`, arbitrary HTTP test commands, or unbounded agent loops. A
  future sandbox backend should replace `TestRunner`, not silently weaken its trust gate.
- Use the generated local fixture for demos. Keep secrets, `.env`, virtual environments,
  external course code, and generated reports out of commits.
