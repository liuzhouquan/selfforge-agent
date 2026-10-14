# Verified PR Agent

**Evidence-Driven Code Review, Testing & Repair**

This first MVP reviews a comparison between two commits in a local Git repository. It
records parsed diff hunks, clearly labeled heuristic risk findings, and optionally the
result of running that repository's existing pytest suite. A failing existing test does
not prove that a particular finding is true; findings stay `suspected` until future
targeted verification connects the failure to the finding.

## Current features

- Validate a repository and hexadecimal commit IDs; collect changed files, line numbers,
  and diff hunks, including rename metadata.
- Apply four deterministic patterns (`eval`, `exec`, `shell=True`, `verify=False`) to
  added lines. These are heuristic suspicions, not autonomous LLM review.
- Run existing pytest tests only when the CLI user explicitly selects
  `--trust-local-tests`. Capture the real exit code, duration, environment, logs and
  JUnit XML counts, with a timeout.
- Write a structured JSON evidence report and a short terminal summary.
- Expose `GET /health` and `POST /review`. The HTTP endpoint uses one server-configured
  repository and never runs tests.

The stack is Python 3.11+, FastAPI, Pydantic v2, pytest, Git CLI, and uv. The local test
runner executes trusted code on the host; it is not a Docker sandbox.

## Install and run

Install [uv](https://docs.astral.sh/uv/) and Git, then from this directory:

```sh
uv sync
uv run pytest
uv run ruff check .
```

Create a disposable known-code demo repository and review the regression:

```sh
DEMO="/tmp/verified-pr-demo-$$"
uv run python examples/create_demo.py "$DEMO"
BASE="$(git -C "$DEMO" rev-list --max-parents=0 HEAD)"
HEAD="$(git -C "$DEMO" rev-parse HEAD)"
uv run verified-pr-agent review --repo "$DEMO" --base "$BASE" --head "$HEAD" \
  --trust-local-tests --output "$DEMO/report.json"
```

The terminal should show one suspected `eval` finding and one failing test. The JSON
report includes the exact SHAs, hunk lines, test command, exit code, JUnit counts,
timestamps, and error messages. For a repository that you have not explicitly trusted,
omit `--trust-local-tests`; the review and JSON report still work.

Example report excerpt (IDs, paths, SHAs, and timestamps vary):

```json
{
  "execution_status": "completed",
  "findings": [{"category": "dynamic-code", "verification_status": "suspected", "source": "heuristic"}],
  "test_results": [{"status": "failed", "exit_code": 1, "passed": 0, "failed": 1}]
}
```

`execution_status=completed` means the requested review steps ran. The failing test
remains visible in `test_results`; it does not change the finding's verification status.

To serve the HTTP API, choose one repository on the server:

```sh
export VERIFIED_PR_AGENT_REPO_ROOT="$DEMO"
uv run uvicorn verified_pr_agent.api.app:app --host 127.0.0.1 --port 8000
```

Then send `POST /review` with `{"base":"<hex SHA>","head":"<hex SHA>"}`. The API
does not accept a repository path or test command from callers. Do not expose this local
service publicly without authentication and deployment controls.

## Roadmap

See [architecture](docs/architecture.md) and [roadmap](docs/roadmap.md). Targeted test
generation, repair, objective verification, GitHub integration, and LLM review are future
work. No API key is needed for the current version.
