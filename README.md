# Verified PR Agent

A local web workbench for reviewing GitHub pull requests, inspecting diffs, checking out editable branches, and running a bounded coding and test loop. It accepts any public PR or PR accessible to the signed-in `gh` account, including other people's PRs. A CLI remains available for local repositories and automation.

## Start the web workbench

Install Python 3.11+, [uv](https://docs.astral.sh/uv/), Git, and [GitHub CLI](https://cli.github.com/). Sign in with `gh auth login` to access private PRs. From this directory:

```sh
uv sync
uv run uvicorn verified_pr_agent.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, paste a GitHub PR URL, and click **导入并审查**. The browser shows changed files, diff hunks, and suspected findings from deterministic rules. Importing a PR reads GitHub metadata and patches; it does not execute the PR's code. GitHub omits text patches for some files, such as binary files and large diffs; these are marked in the UI. Reviews are limited to 300 changed files, and model input is further capped.

To enable model review, open **模型供应商设置** in the workbench, enter a DeepSeek or OpenRouter API key, and click **保存并同步模型**. Both providers have their official base URLs filled in. The model field offers IDs fetched from each provider's `/models` endpoint; you can type a custom ID if discovery fails. Configured providers sync their lists automatically when the page opens. The key stays on the local server in `.local/providers.json` (file mode `0600`, ignored by Git) and is never returned by the configuration API. The web server should be bound to localhost.

Environment variables remain supported instead of saving a key in the workbench. Export one or both provider keys in the **server process environment** and restart the server:

```sh
export DEEPSEEK_API_KEY='your-key'
export OPENROUTER_API_KEY='your-other-key'
uv run uvicorn verified_pr_agent.api.app:app --host 127.0.0.1 --port 8000
```

The key is never returned to the browser after saving. Review defaults to **rules only**; selecting a model sends a bounded list of added lines to that provider. The page also offers **Auto**, **DeepSeek**, and **OpenRouter**, plus a fallback switch. Auto tries configured providers in order: DeepSeek, then OpenRouter. A failed or unavailable primary can switch to the other configured provider; the report records each attempt. The default models are `deepseek-flash` and `openai/gpt-4.1-mini` through OpenRouter. Override them in the workbench or with `VPA_DEEPSEEK_MODEL` and `VPA_OPENROUTER_MODEL`. Local workbench settings take precedence over environment variables. Findings must point to an actual added line and remain `suspected`; model output alone does not verify a defect. If both providers fail, rule-based review remains visible.

Click **检查密钥与额度** to query the providers' read-only status endpoints. DeepSeek reports whether its balance supports API calls; OpenRouter reports the current key's remaining credits and usage. A successful model review also shows the actual prompt, completion, and total token counts returned by that provider. These are **observed usage**, not a preflight token estimate. A configured key alone does not prove a provider is reachable; use the status button to check. If your key is set only in `.zshrc`, start the server from a shell that has loaded it, or export it before starting the server.

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

The web workbench has a **隔离修复尝试** form. To use it, set `VPA_ENABLE_HOST_TESTS=1`, configure at least one provider key, restart the server, enter a repair goal, and check the per-request trust box. The coding model has the same provider selection and fallback controls. This runs the checked-out PR's pytest suite on your host, so use it only for code you trust. The server listens on localhost by default, and the endpoint rejects non-local clients.

The agent compares tests on the PR merge base and head, proposes at most two edits to changed Python source files, runs tests on each repair attempt in disposable Git worktrees, and offers a patch only when the existing suite passed on base, failed on head, and passed after repair. Tests cannot be edited by the agent. The source checkout is retained for your own work; the agent's attempts do not modify it. This proves the existing suite's regression was repaired, not that a particular review finding was objectively verified. It requires an existing pytest suite that reproduces the regression; targeted test generation and sandboxed execution are still planned.

The HTTP API also exposes `/health`, `GET /api/config`, `POST /api/providers/config`, `GET /api/providers/{provider}/models`, `POST /api/providers/check`, `POST /api/github/review`, `POST /api/github/checkout`, and `POST /api/github/repair`; interactive schemas are at `/docs`. `POST /api/github/review` defaults to rules only unless a provider is explicitly selected. The older `POST /review` endpoint can inspect a local repository configured with `VERIFIED_PR_AGENT_REPO_ROOT` and does not run tests.

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

The offline repair fixture is available with `uv run python examples/repair_demo.py`. For a live CLI repair, use `uv run verified-pr-agent repair --repo "$DEMO" --base "$BASE" --head "$HEAD" --task 'Restore the add function' --trust-local-tests --output "$DEMO/repair.json" --patch-output "$DEMO/repair.patch"` with either provider key set. CLI repair defaults to automatic fallback and accepts `--provider openrouter` or `--no-fallback`. Explicit `--model` or `--base-url` (or `VPA_MODEL`/`VPA_API_BASE_URL`) selects a single custom endpoint instead of the fallback route.

Run the checks with:

```sh
uv run pytest
uv run ruff check .
```

If the configured package mirror fails, prefix these commands with `UV_INDEX_URL=https://pypi.org/simple`.

See [architecture](docs/architecture.md) and [roadmap](docs/roadmap.md) for implementation boundaries and remaining work.
