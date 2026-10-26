# Real-repository pilots

## PocketFlow

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

On 2026-10-26, after the Runtime preserved DeepSeek thinking-mode reasoning in tool-call history and requested a final answer on the last step when tests had passed, a DeepSeek-only three-repeat run on the same manifest and image ID scored **3/3**. Each run passed all six hidden checks. The earlier run allowed OpenRouter fallback, so this is an observation under different provider settings, not a controlled comparison. Reports remain in ignored `.local/benchmark/`.

A second public repository, [boltons](./boltons.md), has its own pinned task and external holdout. The combined [two-repository manifest](./suite-two-repos.json) keeps the original one-case manifests intact for reproduction.

## Two-repository run, 2026-10-27

After cloning and pinning both repositories as described here and in the [boltons instructions](./boltons.md), run:

```sh
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/suite-two-repos.json \
  --repeats 3 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/two-repos-repeat3.json
```

One local DeepSeek-only run of this frozen manifest scored **6/6** complete Agent successes: PocketFlow **3/3** with six holdout checks each, and boltons **3/3** with five holdout checks each. The manifest SHA-256 was `fe93f742e9e8cc15a112942e854da693055f81049fbe57179c7ab105105af09a` and both cases used Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. The report totals 211.380 seconds and 420,283 provider-reported tokens. Individual outcomes and step/tool counts are recorded in the [evaluation notes](../../docs/evaluation.md).

This is an exploratory pilot. Both tasks and holdouts were authored by this project, and the 18-step budget was selected after a 12-step boltons attempt hit the limit. It is not the planned three-repository, twenty-task acceptance set or an unbiased measure of general model ability.
