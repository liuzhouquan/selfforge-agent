# Verified PR Agent

**Evidence-Driven Code Review, Testing & Repair**

The project reviews a comparison between two commits in a local Git repository. It
records parsed diff hunks, clearly labeled heuristic risk findings, and optionally the
result of running that repository's existing pytest suite. A bounded coding agent can
then propose a repair in a temporary Git worktree when the existing suite passes on
Base and fails on Head. A failing existing test does not prove that a particular
finding is true; findings stay `suspected` until targeted verification connects the
failure to the finding.

## Current features

- Validate a repository and hexadecimal commit IDs; collect changed files, line numbers,
  and diff hunks, including rename metadata.
- Apply four deterministic patterns (`eval`, `exec`, `shell=True`, `verify=False`) to
  added lines. These are heuristic suspicions, not autonomous LLM review.
- Run existing pytest tests only when the CLI user explicitly selects
  `--trust-local-tests`. Capture the real exit code, duration, environment, logs and
  JUnit XML counts, with a timeout.
- Write a structured JSON evidence report and a short terminal summary.
- Optionally ask an OpenAI-compatible model for one source edit at a time, with a hard
  attempt limit. Compare pytest on Base, Head, and Repaired worktrees, then emit a
  reviewable patch only if the repaired suite passes. The agent cannot edit tests.
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

If your shell points `UV_INDEX_URL` at an unavailable mirror (this workspace's
configured mirror returned HTTP 403), use the official index for these commands:

```sh
UV_INDEX_URL=https://pypi.org/simple uv sync
UV_INDEX_URL=https://pypi.org/simple uv run pytest
UV_INDEX_URL=https://pypi.org/simple uv run ruff check .
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

Run the offline coding-loop demonstration, which uses a fixed proposer only for the
known demo fixture and requires no API key:

```sh
uv run python examples/repair_demo.py
```

For a real model-backed repair, configure an OpenAI-compatible Chat Completions endpoint
and explicitly trust the local repository. Source snippets and test output are sent to
that endpoint. The command never changes your checkout; a verified patch and JSON report
are written to the paths you choose:

```sh
export VPA_MODEL='your-model-name'
export VPA_API_KEY='your-api-key'
export VPA_API_BASE_URL='https://api.openai.com/v1'
uv run verified-pr-agent repair --repo "$DEMO" --base "$BASE" --head "$HEAD" \
  --task 'Restore the add function behavior' --trust-local-tests \
  --output "$DEMO/repair-report.json" --patch-output "$DEMO/verified.patch"
```

Use a local `http://localhost:.../v1` endpoint if the model server needs no API key.
The adapter requests JSON output and validates each `CodeEdit`. The status `verified`
means the **existing test suite** passed on the repaired tree after passing on Base and
failing on Head. It does not verify a specific heuristic review finding.

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
generation, per-finding verification, GitHub integration, and LLM-based PR review are
future work. Review and the offline repair demo need no API key; real model-backed repair
does.
