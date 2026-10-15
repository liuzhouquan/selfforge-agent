# Engineering notes for future sessions

- This repository is independent from sibling projects. Do not copy or publish paid
  DevFlow AI or Testing Automation source. Reference projects are read only.
- Python 3.11+, uv, FastAPI, Pydantic v2, pytest, Git CLI. Run `uv run pytest` and
  `uv run ruff check .` before committing.
- `ReviewService` is the shared CLI/API workflow. `ReviewAgent` is a Protocol; the
  current `BaselineReviewer` is deterministic and must always label findings heuristic.
- A finding is `suspected` until a targeted test objectively reproduces it. Existing
  suite failure alone does not prove a finding. Preserve exit codes and report errors.
- Host test execution requires `trusted=True` and is offered only by the CLI. The API
  accepts commit IDs only and uses `VERIFIED_PR_AGENT_REPO_ROOT` as its one allowed repo.
- Never add `shell=True`, arbitrary HTTP test commands, or unbounded agent loops. A
  future sandbox backend should replace `TestRunner`, not silently weaken its trust gate.
- Use the generated local fixture for demos. Keep secrets, `.env`, virtual environments,
  external course code, and generated reports out of commits.
