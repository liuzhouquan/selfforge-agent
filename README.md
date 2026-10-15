# Verified PR Agent

A local web workbench for reviewing GitHub pull requests, inspecting diffs, checking out editable branches, and running a bounded coding and test loop. It accepts any public PR or PR accessible to the signed-in `gh` account, including other people's PRs. A CLI remains available for local repositories and automation.

## Start the web workbench

Install Python 3.11+, [uv](https://docs.astral.sh/uv/), Git, and [GitHub CLI](https://cli.github.com/). Sign in with `gh auth login` to access private PRs. From this directory:

```sh
uv sync
uv run uvicorn verified_pr_agent.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, paste a GitHub PR URL, and click **导入并审查**. The browser shows changed files, diff hunks, and suspected findings from deterministic rules. Importing a PR reads GitHub metadata and patches; it does not execute the PR's code. GitHub omits text patches for some files, such as binary files and large diffs; these are marked in the UI. Reviews are limited to 300 changed files, and model input is further capped.

To enable optional DeepSeek review, provide your key in the server environment and restart the server:

```sh
export DEEPSEEK_API_KEY='your-key'
uv run uvicorn verified_pr_agent.api.app:app --host 127.0.0.1 --port 8000
```

The key is never sent to the browser. DeepSeek receives a bounded list of added lines and returns proposed findings. Findings must point to an actual added line and remain `suspected`; model output alone does not verify a defect. If the model request fails, the rule-based review remains visible with an error message. The default model is `deepseek-flash` at `https://api.deepseek.com`.

Click **下载可编辑工作区** to clone the repository and check out the PR head into a new branch under `.workspaces/`. The app creates a new directory for each checkout and does not overwrite your own checkout. You can open that path in your IDE, edit files, commit, and use Git normally. A minimal manual equivalent is:

```sh
gh repo clone OWNER/REPO my-review
cd my-review
gh pr checkout 123 --branch my-pr-123
# edit and test
git add .
git commit -m 'Fix issue found in review'
```

Pushing back to the same PR requires write access to its source branch or an eligible fork PR with **Allow edits from maintainers** enabled. If you do not have that permission, push your change to your own fork and open a new PR. The workbench currently prepares a local branch and a patch; it does not push commits, post review comments, or create a GitHub PR on your behalf.

After committing, inspect `git remote -v`. If you have write access to the PR's source branch, push the local checkout to that branch with `git push <source-remote> HEAD:<source-branch>`; GitHub updates the existing PR automatically. If the push is denied, fork the repository with `gh repo fork OWNER/REPO --remote`, inspect the newly added fork remote, push your branch to it, and open a follow-up PR with `gh pr create --repo OWNER/REPO --head YOUR_USER:YOUR_BRANCH --base BASE_BRANCH`. Review the resulting diff before creating the PR.

## Coding and test loop

The web workbench has a **隔离修复尝试** form. To use it, set `VPA_ENABLE_HOST_TESTS=1`, configure `DEEPSEEK_API_KEY`, restart the server, enter a repair goal, and check the per-request trust box. This runs the checked-out PR's pytest suite on your host, so use it only for code you trust. The server listens on localhost by default, and the endpoint rejects non-local clients.

The agent compares tests on the PR merge base and head, proposes at most two edits to changed Python source files, runs tests on each repair attempt in disposable Git worktrees, and offers a patch only when the existing suite passed on base, failed on head, and passed after repair. Tests cannot be edited by the agent. The source checkout is retained for your own work; the agent's attempts do not modify it. This proves the existing suite's regression was repaired, not that a particular review finding was objectively verified. It requires an existing pytest suite that reproduces the regression; targeted test generation and sandboxed execution are still planned.

The HTTP API also exposes `/health`, `POST /api/github/review`, `POST /api/github/checkout`, and `POST /api/github/repair`; interactive schemas are at `/docs`. The older `POST /review` endpoint can inspect a local repository configured with `VERIFIED_PR_AGENT_REPO_ROOT` and does not run tests.

## Local CLI and development

Create a disposable example repository and review it:

```sh
DEMO="/tmp/verified-pr-demo-$$"
uv run python examples/create_demo.py "$DEMO"
BASE="$(git -C "$DEMO" rev-list --max-parents=0 HEAD)"
HEAD="$(git -C "$DEMO" rev-parse HEAD)"
uv run verified-pr-agent review --repo "$DEMO" --base "$BASE" --head "$HEAD" \
  --trust-local-tests --output "$DEMO/report.json"
```

The offline repair fixture is available with `uv run python examples/repair_demo.py`. For a live CLI repair, use `uv run verified-pr-agent repair --repo "$DEMO" --base "$BASE" --head "$HEAD" --task 'Restore the add function' --trust-local-tests --output "$DEMO/repair.json" --patch-output "$DEMO/repair.patch"` with `DEEPSEEK_API_KEY` set. You can override the model with `VPA_MODEL` and the endpoint with `VPA_API_BASE_URL` for the CLI and coding adapter.

Run the checks with:

```sh
uv run pytest
uv run ruff check .
```

If the configured package mirror fails, prefix these commands with `UV_INDEX_URL=https://pypi.org/simple`.

See [architecture](docs/architecture.md) and [roadmap](docs/roadmap.md) for implementation boundaries and remaining work.
