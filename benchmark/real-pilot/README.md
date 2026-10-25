# Real-repository pilot: PocketFlow

This one-case pilot uses the public [PocketFlow](https://github.com/The-Pocket/PocketFlow) repository at commit `f74d023f93607b8c3268133339a5e532a949898c`. PocketFlow is MIT licensed; its source stays in the ignored `.local/benchmark/repos/` directory and is not vendored here. The task asks for input validation on retry counts. The holdout file is copied only into an independent verification checkout after the Agent finishes.

From the selfForge Agent repository root:

```sh
mkdir -p .local/benchmark/repos
git clone https://github.com/The-Pocket/PocketFlow.git .local/benchmark/repos/pocketflow
git -C .local/benchmark/repos/pocketflow checkout --detach f74d023f93607b8c3268133339a5e532a949898c
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/suite.json \
  --repeats 3 --provider deepseek --output .local/benchmark/pocketflow-report.json
```

The committed manifest refers to the ignored checkout and a holdout next to the manifest. The evaluator requires a failing assertion on the pinned baseline before any Agent run. This is one real-repository pilot case, not the planned three-repository, twenty-task acceptance set. The task and holdout are project-authored, so success on this pilot alone does not establish broader coding ability.

On 2026-10-25, a fixed-manifest three-repeat run with DeepSeek and OpenRouter configured scored **1/3** under the complete Agent gate. In all three runs the six hidden checks passed; two runs hit the 12-step cap before the Agent finalized. The [evaluation record](../../docs/evaluation.md) separates this run from earlier exploratory task edits and records the failure categories. This pilot should be expanded across repositories before drawing model-quality conclusions.
