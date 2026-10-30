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

## Three-repository run, 2026-10-27

The third public repository is [more-itertools](./more-itertools.md), pinned to `4aeb4338ae1407d8da9723e86da99efeb446f719`. Clone it as described there, then run the frozen [three-repository manifest](./suite-three-repos.json):

```sh
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/suite-three-repos.json \
  --repeats 3 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/three-repos-repeat3.json
```

One local DeepSeek-only run scored **8/9** complete successes: PocketFlow **2/3**, boltons **3/3**, and more-itertools **3/3**. PocketFlow's third attempt changed only allowed files and passed all six independent holdout checks, but reached the 18-step cap, so its result is `agent_max_steps`. All other attempts reached `completed` and passed their holdouts. The manifest SHA-256 was `2a0e193b3d990793bb79d30de0e6df9e6174e01c73a2207dfb150f0e8e9ae62d`; all cases used Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. The report totals 321.037 seconds and 594,856 provider-reported tokens. Individual outcomes are in the [evaluation record](../../docs/evaluation.md).

This is still an exploratory pilot with three project-authored tasks and holdouts. It reaches the repository count but remains 17 tasks short of the planned acceptance set. The step budget was chosen after earlier trials, and the more-itertools case had a successful exploratory run before this combined run. Do not read 8/9 as a general Coding Agent success rate.

## Four-task run with independent visible tests, 2026-10-27

A second [more-itertools task](./more-itertools-chunks.md) exercises the larger `more_itertools/more.py` file. The evaluator now independently reruns each case's specified visible tests before installing its unseen holdout. The fourth task uses `agent_test_keyword` to select the 21 existing `ChunkedTests` and `SlicedTests` checks; the broader file contains an unrelated test that exceeds the sandbox's process limit.

```sh
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/suite-four-tasks.json \
  --repeats 1 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/four-tasks-once.json
```

One run of this fixed four-task manifest scored **4/4**. The evaluator's visible-test counts were 63, 35, 10,036, and 29 passed respectively; hidden-test counts were 6, 5, 7, and 5 passed, with no failures or errors. The manifest SHA-256 was `834c0e46e3e187923e229e2450a3c76609fe50b691308eda03e5d11c0063904e`, and the Docker image ID was `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. Total duration was 143.255 seconds; provider-reported tokens totaled 226,096. This new gate and single-repeat sample are separate from the earlier 8/9 three-task result. The four tasks are project-authored, and 16 tasks remain before the planned acceptance set.

## Nine-task candidate baseline, 2026-10-29

The [nine-task candidate manifest](./suite-nine-tasks.json) retains those four pilot cases and adds five project-authored boundary tasks: empty async batches, retry-count types, retry-wait validation, negative file-search depth, and split `maxsplit` validation. It uses the same three pinned repositories and the existing network-isolated Docker image. The manifest SHA-256 is `bb6606594f9995da94e697e669859428ffee8599c5af3f789fe55e62c6188f60`; the image ID is `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`.

Before any model run, all nine holdouts were checked on their pinned baseline. Each produced assertion failures with no collection errors. The visible selections also passed:

| Added candidate | Visible tests passed | Baseline holdout assertions failed |
| --- | ---: | ---: |
| PocketFlow empty async batches | 56 | 2 |
| PocketFlow retry-count types | 56 | 8 |
| PocketFlow retry-wait validation | 56 | 14 |
| boltons negative `max_depth` | 13 | 1 |
| more-itertools negative `maxsplit` | 60 | 8 |

These checks establish that the five tasks are runnable against the current image; they are not live Agent results. The nine-task candidate is still 11 tasks short of the 20-task acceptance target, and no live-model runs have been made on these five additions. Do not report the earlier four-task 4/4 pilot as a result for this larger manifest.
