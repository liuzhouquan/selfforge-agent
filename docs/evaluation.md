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
