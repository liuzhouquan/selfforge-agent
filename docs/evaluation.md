# Evaluation record

Date: 2026-10-17 (Asia/Shanghai)

The command `uv run selfforge-agent eval --provider deepseek` creates two temporary Python projects. Each has a source defect and a pytest assertion. The Agent is asked to edit code and run tests. After the Agent finishes, the evaluator independently checks that `app.py` changed and reruns `python -m pytest tests -q`. Success requires both checks and an Agent `completed` status. Model text alone cannot pass a task.

| Mode | Tasks | Successes | Failures |
| --- | ---: | ---: | --- |
| Offline scripted protocol fixture | 2 | 2 | 0 |
| DeepSeek live native tool calling, one run | 2 | 2 | 0 |

Cases: `addition` fixes `add(2,3)`; `normalization` strips and lowercases a name. Each live case finished in 5 model turns using search, read, edit, and test tools. No benchmark claim follows from two small synthetic tasks or one run. The offline fixture checks the control flow and tool protocol, not language-model ability. A negative control where the model merely claims success scores 0/2 because the source is unchanged and pytest still fails.

An additional live `selfforge-agent code --sandbox host --trust-local --provider deepseek` run on a fresh generated addition fixture ended `completed` in 6 model turns and 7 tool calls. Its trace records a successful `run_tests`, and an independent `selfforge-agent test` returned 1 passed, exit 0. This is a CLI smoke test, not an extra benchmark task.

Reproduce with:

```sh
uv run selfforge-agent eval --offline
DEEPSEEK_API_KEY=... uv run selfforge-agent eval --provider deepseek
uv run pytest tests/test_runtime.py
```

The two-case coding evaluation uses generated code on the explicitly trusted local host. No external repository code is executed. The temporary sessions are removed after each case. For a stronger benchmark, add varied tasks and repositories, repeated runs, Docker execution, token/cost/latency summaries, and saved failure traces with secrets scrubbed.

On 2026-10-21, `uv run --no-sync pytest -q` passed **45 tests**, including one live Docker integration fixture. In that fixture, Base passed, Head had a JUnit assertion failure, and Repaired passed; the verified patch changed `calculator.py` while the original checkout stayed unchanged. This is a single generated smoke test of the sandbox and repair evidence, not a model benchmark. The test skips when the local daemon or `selfforge-agent-sandbox:latest` image is unavailable. Reproduce it with `docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .` and `uv run --no-sync pytest -q tests/test_docker_repair.py`.

On 2026-10-22, `uv run pytest -q` passed **69 tests**, including a live Docker differential test generation fixture. A separate disposable Git fixture was run once with `selfforge-agent generate-test --provider deepseek --no-fallback`: DeepSeek `deepseek-flash` generated one test; Docker pytest passed 1 test on Base and failed 1 assertion on Head, so the command returned `differential`. The fixture changes `add(a, b)` from addition to subtraction. This validates the provider call, tool response, temporary worktrees, and Base/Head JUnit gate on one synthetic case. It is not a success-rate benchmark for test generation or a causal attribution to a review finding. The automated fixture skips the Docker portion if the daemon or image is unavailable.

Later on 2026-10-22, `uv run pytest -q` passed **76 tests**, including a live Docker integration fixture in which the first generated candidate passed on both versions and the second passed on Base but failed an assertion on Head. A separate disposable Git fixture used real DeepSeek `deepseek-flash` with `--finding-index 0 --max-candidates 2`; the selected rule finding pointed to an added `eval` line. The model generated a differential test on its first attempt, so this live run checked risk anchoring and the provider/Docker path, while the automated Docker fixture checked actual multi-attempt feedback. Neither small synthetic run establishes a success rate, nor does its differential result prove that the selected risk caused the behavior change. Both automated Docker fixtures skip if the daemon or sandbox image is unavailable.

On 2026-10-25, the new manifest-driven `eval-suite` harness was checked with two generated Git repositories and two repeats each: the scripted protocol model passed **4/4** independent holdouts. Negative controls confirmed that a model merely claiming success and a model changing only visible tests both scored zero. Another control rejected a holdout that already passed on the pinned baseline. A live Docker integration test ran the Agent, baseline control, and unseen JUnit-backed holdout. `uv run --no-sync pytest -q` passed **81 tests** (one third-party deprecation warning), and `uv run --no-sync ruff check .` passed. The `--no-sync` flag used existing installed dependencies because this machine's configured PyPI mirror returned HTTP 403 during dependency resolution.

The synthetic `examples/benchmark_demo.py` protocol demonstration passed **2/2** repeated runs on one generated task. A separate `examples/benchmark_demo.py --live` run used DeepSeek `deepseek-flash` and Docker on the generated addition task: **1/1** holdout passed after the baseline failed; the report recorded 7.722 seconds and 7,438 provider-reported tokens. These are smoke tests of the new evaluation path. No repeated live-model result on multiple real repositories has been measured. The next evaluation milestone is the [real-task acceptance set](./product-scope.md), using the [benchmark manifest format](./benchmark.md).

## Real-repository pilot, 2026-10-25

The [PocketFlow pilot](../benchmark/real-pilot/README.md) uses upstream commit `f74d023f93607b8c3268133339a5e532a949898c`. Its external holdout has four assertions that fail on the baseline; two positive-count controls pass. One DeepSeek `deepseek-flash` run on the first task wording changed the implementation and added `tests/test_max_retries_validation.py`. All six holdout checks then passed, but the report scored **0/1** because the manifest allowed only the implementation path (`unexpected_file_changes`; 24.824 seconds, 35,792 provider-reported tokens). The task wording and allowlist were then revised to request and permit that regression-test file.

Two exploratory runs with the revised manifest also scored **0/1** each. In both, the changed files were within the allowlist and all six independent holdout checks passed, but the Agent returned `failed` during finalization. The second of these runs recorded the sanitized cause `provider_http_400` (26.788 seconds, 69,919 provider-reported tokens). Only DeepSeek was configured locally, so no alternate provider was available. The preceding run recorded 24.518 seconds and 55,267 tokens; its report predated the new error-code field. The raw provider response was not retained, so the precise HTTP 400 cause remains unknown.

These runs are not independent repeats of one frozen task: the manifest changed after the first run, and the holdout file had a style-only edit before the last run. They demonstrate that passing hidden tests alone does not satisfy the Agent completion and allowed-path gates. No model success rate should be inferred from this pilot. The next investigation is the HTTP 400 request shape at finalization, followed by a frozen multi-repository task set.

After freezing the revised manifest and holdout, one more single run succeeded: **1/1**, with the same two allowed files changed and all six holdout checks passing. Both provider keys were available to this run, but the report names DeepSeek as the final provider; it does not demonstrate a fallback. A subsequent `--repeats 3` run on that same manifest and Docker image ID produced **1/3** complete successes. The other two runs reached the configured 12-step limit (`agent_max_steps`) even though their changed files were allowed and all six holdout checks passed. The three runs took 41.251, 26.499, and 36.159 seconds; the report totals 163,551 provider-reported tokens. The successful run ended on DeepSeek; the two step-limited runs ended on OpenRouter, indicating that the configured fallback was exercised at some point in those runs, without identifying why it switched. This is a one-case observation, not a multi-repository benchmark result or a provider comparison. The report stays in ignored `.local/benchmark/pocketflow-report-repeat3.json`; reproduce it using the pilot instructions with both providers configured.

The next Runtime investigation should examine why successful tests are followed by more tool turns until the step cap, while preserving the rule that a completed Agent needs a coherent final result. The DeepSeek HTTP 400 from the exploratory run also remains unresolved.
