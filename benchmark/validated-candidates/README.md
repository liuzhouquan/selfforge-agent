# Reference-qualified real-repository candidates

This small set adds positive reference controls to two project-authored tasks. It does not increase the formal acceptance count: the async empty-batch mechanism overlaps a candidate in PR #24, and the Unicode feature appeared in earlier pilots. The goal here is stronger task qualification and public-lifecycle coverage. The twenty-task, three-repository acceptance set is still incomplete.

**Current expansion:** the separate [iterator/traversal set](./iterator-traversal.md)
qualifies five distinct tasks across three repositories. It adds depth pruning,
zero-chunk validation (overlapping an earlier pilot) and replacement callback
short-circuiting. [suite-five-tasks.json](./suite-five-tasks.json) is the cumulative
qualification manifest; [suite-iterator-traversal.json](./suite-iterator-traversal.json)
contains only the three new reference controls for live evaluation. The original
two-case manifest and results below remain historical, frozen inputs.

## Contracts and classification

| Case | Classification and contract | Fixed source evidence |
| --- | --- | --- |
| `pocketflow_async_batch_empty` | Project-authored consistency fix: default prep_async returning None acts like an empty sync batch; preserve post callbacks, sequence/generator order and execution errors | PocketFlow `f74d023f93607b8c3268133339a5e532a949898c`, `pocketflow/__init__.py`: AsyncNode prep, BatchNode empty-input handling, public run_async lifecycle |
| `boltons_unicode_punctuation` | Explicit feature extension: Unicode category P* separates words; preserve letters, combining marks, symbols and existing ASCII behavior | boltons `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/strutils.py`: split_punct_ws ASCII regex and slugify delegation |

These are not claims that upstream maintainers confirmed bugs. Reference patches stay outside Agent repositories. Patch context licenses are retained in [LICENSES.md](./LICENSES.md). Holdouts are original project-authored checks; upstream source is not vendored.

## Reproduce

Place pinned clones at `.local/benchmark/repos/pocketflow` and `.local/benchmark/repos/boltons` (see [pilot setup](../real-pilot/README.md) and [boltons setup](../real-pilot/boltons.md)). From the selfForge repository root:

```sh
uv run selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite.json \
  --output .local/benchmark/validated-candidates-preflight.json
```

Every case needs an external reference patch. The tool freezes and hashes manifest, holdouts and patches; checks the failing baseline without collection errors; applies only an allowed text patch in a disposable checkout; and requires nonempty visible/holdout passes on separate copies. It does not construct a model. Invalid references stop evaluation before model calls. This gate proves that the reference satisfies these tests, not that coverage completely specifies the task.

## Docker qualification — 2026-10-30

| Case | Baseline holdout failures | Reference visible passes | Reference holdout passes |
| --- | ---: | ---: | ---: |
| PocketFlow async empty batch | 2 | 56 | 10 |
| boltons Unicode punctuation | 6 | 33 | 11 |

References had zero failures/errors/skips and exit 0. Image: `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. Manifest SHA-256: `d5e0480e6c2b2c2e7de0cfccceda66569ef975fb43fd22fb7284c0ec0b7ef5e5`. Per-reference and holdout hashes are in the local report.

## Live model run policy

Fixed before execution: DeepSeek without fallback, three repeats per case, 18 turns, 180 seconds per task, 30 seconds per test. The step cap comes from earlier pilots and is not untuned. The model sees only the task and pinned repository.

```sh
uv run selfforge-agent eval-suite --manifest benchmark/validated-candidates/suite.json \
  --provider deepseek --no-fallback --repeats 3 --max-steps 18 \
  --time-budget 180 --test-timeout 30 \
  --output .local/benchmark/validated-candidates-repeat3.json
```

## Actual run — 2026-10-30

The run used model ID `deepseek-flash` and scored **5/6** complete Agent successes: PocketFlow **3/3**, boltons **2/3**. Every independent visible selection and holdout passed. boltons repeat 2 hit the 18-turn cap, so it remains `agent_max_steps` despite its passing tests. No provider error was reported.

| Case / repeat | Model turns | Tool calls | Visible passes | Holdout passes | Final reason |
| --- | ---: | ---: | ---: | ---: | --- |
| PocketFlow 1 | 11 | 14 | 70 | 10 | passed |
| PocketFlow 2 | 8 | 8 | 75 | 10 | passed |
| PocketFlow 3 | 18 | 26 | 81 | 10 | passed |
| boltons 1 | 17 | 21 | 36 | 11 | passed |
| boltons 2 | 18 | 21 | 35 | 11 | agent_max_steps |
| boltons 3 | 14 | 20 | 38 | 11 | passed |

Visible counts differ from reference controls because the Agent added regression tests. The run used the manifest/image IDs above, took 285.961 seconds across attempts and used 479,247 provider-reported tokens. These two tasks and the step budget have prior pilot exposure; this is a small exploratory result, separate from older 4/4 or 8/9 results and from the planned acceptance set. Sessions, provider messages, test output and generated reports remain ignored local artifacts.

Historical reference-validation branch: `uv run --no-sync pytest -q` produced **145 passed**, no skips, with one existing Starlette/httpx warning; Ruff and Git whitespace checks passed. It was based on main `278ff34`, excluding the then-open #24–#27 tests. Current combined checks are in the [integration record](../../docs/runtime-integration.md). Patch-file attributes preserve unified diff's required blank-context markers; source/test whitespace checks stay enabled.
