# Repeatable Coding evaluation

`eval-suite` measures the general Coding Agent on pinned local Git tasks. Each run starts from a fresh checkout. The evaluator checks the changed file list and runs a separate pytest file that the Agent did not receive. It never uses the model's final answer as a success signal.

## Manifest

Create a JSON file outside the repositories under test:

```json
{
  "version": 1,
  "cases": [
    {
      "id": "example_fix",
      "repository": "./repos/example",
      "revision": "0123456789abcdef0123456789abcdef01234567",
      "task": "Fix the parser regression and run tests",
      "allowed_changes": ["src/example/parser.py"],
      "holdout_test": "./holdouts/test_parser.py",
      "agent_test_path": "tests",
      "image": "my-project-benchmark:locked"
    }
  ]
}
```

Paths are relative to the manifest. `revision` must be a full commit SHA present in the local repository. `holdout_test` must be a Python file outside that repository; it is copied only into a separate verification checkout after the Agent stops. `allowed_changes` lists exact paths that the Agent may change, including any regression-test file the task requests. Python and pytest cache files are ignored when computing changed paths. The optional `agent_test_path` tells the Agent which committed test directory or file to run; it defaults to `tests`. The optional `image` selects a Docker image for this case, overriding the CLI `--image` fallback.

The evaluator first checks that the holdout has a failing assertion on the pinned baseline, with no collection errors. A passing or broken baseline is rejected as an invalid task. It freezes and hashes the holdout contents before any repeat starts.

## Run

```sh
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent eval-suite --manifest ./benchmark/suite.json \
  --repeats 3 --provider deepseek --output ./benchmark-report.json

# Only for repositories you explicitly trust:
uv run selfforge-agent eval-suite --manifest ./benchmark/suite.json \
  --repeats 3 --sandbox host --trust-local

# Deterministic protocol smoke test; no API key and no model benchmark claim:
uv run python examples/benchmark_demo.py
# Optional single-task live model + Docker smoke test:
uv run python examples/benchmark_demo.py --live
```

Docker is the default and does not provide network access to executed code. The default image contains Python and pytest only. Build a suitable image for each project's dependencies, set `image` per case, or use `--image` as the suite-wide fallback. Image references are resolved to immutable local image IDs before baseline checks and every repeat uses the resolved ID; rebuilds of a tag during a run cannot silently change its environment. The report includes a case-to-image reference/ID map. Host execution can read files outside the benchmark workspace; use it only with trusted local fixtures. The Agent receives the natural-language task and visible repository tests, never the holdout source. The evaluator's report records the Git revision, holdout SHA-256, image IDs, aggregate success rate, per-case results, failure categories, duration, and provider-reported token usage. Agent failures include a sanitized category such as `provider_http_400`, without raw provider response text. Prices are not inferred from token counts.

A run succeeds only if the Agent finishes `completed`, changes at least one file, changes no file outside `allowed_changes`, and the independent holdout passes at least one test with no failures or collection errors. Each repeat uses a fresh checkout and a fresh model adapter. Reports omit raw model and test output. The benchmark does not establish general model quality until it contains diverse real repositories and repeated live runs; the generated demonstration and [one-case real-repository pilot](../benchmark/real-pilot/README.md) are kept separate from that acceptance set.
