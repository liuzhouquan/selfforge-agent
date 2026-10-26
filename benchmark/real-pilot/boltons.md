# Real-repository pilot: boltons

This task uses public [boltons](https://github.com/mahmoud/boltons) at commit `d31d821d56c8f7756af9c30a864077f2e5855321` (BSD licensed). The requested enhancement makes `split_punct_ws` and `slugify` treat Unicode punctuation as separators while preserving Unicode letters and existing ASCII behavior. The task is authored for this project; it is not a claim that upstream considers the baseline behavior a bug.

The Agent sees the repository's `tests/test_strutils.py` and is asked to add regression cases there. The five independent checks in `holdouts/test_boltons_unicode_punctuation.py` are kept outside the repository and inserted only after the Agent finishes. Four checks fail by assertion on the pinned baseline; the ASCII control passes. The existing 33 tests in `tests/test_strutils.py` run in the default offline Docker image.

From the selfForge Agent repository root:

```sh
mkdir -p .local/benchmark/repos
git clone https://github.com/mahmoud/boltons.git .local/benchmark/repos/boltons
git -C .local/benchmark/repos/boltons checkout --detach d31d821d56c8f7756af9c30a864077f2e5855321
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/boltons.json \
  --repeats 3 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/boltons-report.json
```

The [two-repository manifest](./suite-two-repos.json) combines this case with PocketFlow under the same step, provider, and Docker policy. Neither task belongs to the planned 20-task acceptance set until that larger set and its methodology are frozen.
