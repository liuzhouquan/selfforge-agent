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

Evaluation uses only generated code on the explicitly trusted local host. No external repository code is executed. The temporary sessions are removed after each case. For a stronger benchmark, add varied tasks and repositories, repeated runs, Docker execution, token/cost/latency summaries, and saved failure traces with secrets scrubbed.
