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

## Runtime reliability follow-up, 2026-10-26

The Runtime now preserves DeepSeek `reasoning_content` in private session messages and sends it back on subsequent DeepSeek tool turns, including after resume. [DeepSeek's thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode/) says omitting this field from the next request can return HTTP 400. This is a plausible explanation for the earlier sanitized `provider_http_400`, but its raw response was not retained, so the exact cause cannot be proven. The adapter removes that provider-specific field when switching to another OpenAI-compatible provider. Context compaction uses a system summary before the original user task instead of a synthetic assistant message. The Agent also requests a final answer without tool schemas on its last step when a post-edit test has passed; it still requires a nonempty final answer and successful tests for `completed`. Test evidence persists across session resume and is cleared after a new task, a successful edit, or a later failed test.

With the same pinned PocketFlow revision, frozen manifest hash `4bff2613f00d8377a3da74792fdfacb61530a20026f9e821524d08969ff01d81`, holdout hash `0388cdb9540a95a3d07edb9091bb7e7bda52fcea29aa2568b37cc1101959e18e`, and Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`, one DeepSeek-only check scored **1/1** (`completed`, hidden tests 6/6; 20.374 seconds, 37,768 provider-reported tokens). A separate DeepSeek-only `--repeats 3` run scored **3/3** (`completed` and hidden tests 6/6 each), with durations 22.292, 29.383, and 17.265 seconds and 133,355 total provider-reported tokens. No HTTP 400 occurred in these four runs. Reports are ignored at `.local/benchmark/pocketflow-after-runtime-fix.json` and `.local/benchmark/pocketflow-after-runtime-fix-repeat3.json`.

The prior 1/3 run allowed OpenRouter fallback, while these runs used `--no-fallback`; model outputs are stochastic. The observed improvement does not isolate which Runtime change mattered, and a one-task pilot cannot estimate general coding reliability. Next, assemble the three-repository, twenty-task acceptance set in [product scope](./product-scope.md) and run repeated evaluations under a fixed provider policy.

## Two-repository exploratory pilot, 2026-10-27

The [boltons case](../benchmark/real-pilot/boltons.md) adds a second public Python repository, pinned at `d31d821d56c8f7756af9c30a864077f2e5855321`. The task asks the Agent to handle Unicode punctuation in `split_punct_ws` and `slugify` and add a visible regression test. Its five project-authored external checks include four assertion failures and one passing ASCII control on the pinned baseline; the repository's existing 33 targeted string tests pass in the default offline Docker image. The upstream code is not vendored.

An exploratory DeepSeek-only run with the default 12-step limit scored **0/1** (`agent_max_steps`), although all five independent holdout checks passed and only the source file changed. A later one-run trial with an 18-step limit scored **1/1**: source and visible test changed, the Agent ran the targeted tests, and all five holdout checks passed. Its report recorded 18 model steps, 21 tool calls, 48.355 seconds, and 105,889 provider-reported tokens. These trials informed the step budget for the combined run; they are not included in its aggregate result.

The frozen [two-repository manifest](../benchmark/real-pilot/suite-two-repos.json), SHA-256 `fe93f742e9e8cc15a112942e854da693055f81049fbe57179c7ab105105af09a`, was run once with three fresh attempts per case, DeepSeek `deepseek-flash` only, 18 model steps, and Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. It scored **6/6** complete successes: PocketFlow **3/3** with six holdout checks passing each time; boltons **3/3** with five checks passing each time. Failure categories were empty. Durations were 37.070, 28.590, and 29.890 seconds for PocketFlow, then 32.588, 44.679, and 38.563 seconds for boltons. The report totals 211.380 seconds and 420,283 provider-reported tokens. Agent steps were 12, 11, 11 and 12, 16, 14 respectively. The ignored report is `.local/benchmark/two-repos-repeat3.json`.

The two tasks and holdouts are project-authored. The 18-step setting was chosen after observing the boltons failure at 12 steps, so this pilot is exploratory and tuned. It does not satisfy the planned three-repository, twenty-task acceptance set or establish a general success rate. Next, freeze a larger task set and provider/step policy before running it, and report failures as well as successes.

The first [GitHub Actions CI run](https://github.com/liuzhouquan/selfforge-agent/actions/runs/40358812798) on this milestone passed locked dependency installation, Ruff, 90 unit tests, a Docker image build, and 5 Docker integration tests. This checks the hosted workflow separately from local verification; it does not run the live-model pilot or require API keys.

## Three-repository exploratory pilot, 2026-10-27

The [more-itertools case](../benchmark/real-pilot/more-itertools.md) adds a third public Python repository at `4aeb4338ae1407d8da9723e86da99efeb446f719`. Its project-authored holdout has six assertion failures on the pinned baseline and one passing positive-size control. The repository's full `tests/test_recipes.py` file passed in the default offline Docker image (145 tests, 9,876 subtests). A first exploratory DeepSeek-only run under an 18-step cap scored **1/1** complete success in 35.295 seconds, 10 model steps, and 54,591 provider-reported tokens. That run is separate from the combined result.

The frozen [three-repository manifest](../benchmark/real-pilot/suite-three-repos.json), SHA-256 `2a0e193b3d990793bb79d30de0e6df9e6174e01c73a2207dfb150f0e8e9ae62d`, was run once with three fresh attempts per case, DeepSeek `deepseek-flash` only, 18 model steps, and Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. It scored **8/9**: PocketFlow **2/3**, boltons **3/3**, more-itertools **3/3**. The only failure category was `agent_max_steps` (one PocketFlow attempt). That attempt changed the two allowed files and passed all six external checks, but its final `run_tests` succeeded at step 18, leaving no model turn to provide the required final answer. It is correctly a failure under the complete Agent gate. All successful attempts changed only allowed source/test files, reached `completed`, and passed their independent holdouts (six, five, and seven checks per case respectively).

| Case | Result | Agent steps by repeat | Duration by repeat (seconds) |
| --- | ---: | --- | --- |
| PocketFlow | 2/3 | 8, 10, 18 | 19.519, 27.491, 36.165 |
| boltons | 3/3 | 18, 13, 11 | 50.983, 31.672, 31.996 |
| more-itertools | 3/3 | 9, 14, 9 | 33.552, 43.563, 46.096 |

The report totals 321.037 seconds and 594,856 provider-reported tokens. Its ignored local path is `.local/benchmark/three-repos-repeat3.json`. The manifest and each external holdout were frozen before this combined run. The 18-step setting was chosen from earlier exploratory trials, and the more-itertools case was tried once before inclusion; these tasks and holdouts are project-authored. The result reaches three repositories but only three of the planned twenty tasks, so it is not a general model success-rate estimate. The next work is a larger fixed task set, dependency-ready Docker images, and test selection for unrelated suite failures; record every failure in the first acceptance run without adjusting the task wording or budget afterward.

## Independent visible-test gate and fourth task, 2026-10-27

The evaluator now reruns the manifest's visible test path independently on a separate copy **before** adding the unseen holdout. Both suites need at least one passed test and no failures, errors, or timeout. The report records visible and hidden JUnit counts separately. A synthetic negative control changed the source so its holdout passed, deliberately broke the specified visible test, ran a different green test, and obtained Agent `completed`; the new gate scored it `visible_test_assertion_failed`. Optional `agent_test_keyword` selects an explicit pytest `-k` expression for both Agent and evaluator, with tests that cover its use and empty selections.

The [large-file more-itertools task](../benchmark/real-pilot/more-itertools-chunks.md) uses the same pinned upstream commit as the grouper case. The external holdout had four baseline assertion failures and one passing control. A single exploratory DeepSeek-only attempt completed in 17 model steps, changed only `more_itertools/more.py` and `tests/test_more.py`, and passed 23 independently rerun selected visible tests plus all five hidden checks. It took 40.240 seconds, 23 tool calls, and 102,596 provider-reported tokens; this trial is separate from the combined run.

The fixed [four-task manifest](../benchmark/real-pilot/suite-four-tasks.json), SHA-256 `834c0e46e3e187923e229e2450a3c76609fe50b691308eda03e5d11c0063904e`, was then run once per task with DeepSeek `deepseek-flash`, 18 model steps, and Docker image ID `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. All **4/4** attempts reached `completed`, changed only allowed files, and passed the independently rerun visible selection and external holdout:

| Task | Visible passed | Hidden passed | Steps | Seconds | Provider tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| PocketFlow retry validation | 63 | 6 | 8 | 23.830 | 38,042 |
| boltons Unicode punctuation | 35 | 5 | 18 | 46.698 | 100,430 |
| more-itertools grouper size | 10,036 | 7 | 8 | 46.442 | 28,837 |
| more-itertools zero chunk size | 29 selected | 5 | 11 | 26.285 | 58,787 |

The report totals 143.255 seconds and 226,096 provider-reported tokens; no failure categories occurred. It stays in ignored `.local/benchmark/four-tasks-once.json`. The earlier 8/9 result used three tasks, three repeats each, and an older gate, so the two aggregates are not comparable or combinable. All four tasks and holdouts are project-authored; the 18-step limit was selected after prior trials, and the new task had one successful exploratory attempt before this run. This remains 16 tasks short of the planned 20-task acceptance set.
