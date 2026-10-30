# Runtime budget feedback

The shared Coding loop sends the model its current turn number, remaining turns
(including the final answer), remaining wall time, and post-write `run_tests`
verification state. In the last three turns or last 45 seconds, it asks the model
to prioritize required edits, the task's actual test selection, and a final answer.
A successful `bash` command does not supply verification evidence.

This is model guidance, not an automatic completion rule. The existing cap still
counts **every** model call. The Runtime does not add an extra summary turn, run
tests on the model's behalf, or mark a step-limited run successful. On the last
turn, current passing evidence disables tools; unexpected calls are rejected.
Without that evidence tools remain available, and an unfinished attempt can
still reach `max_steps`. A final answer without evidence remains `unverified`.

## Session and context behavior

The notice is a transient system message after the original system prompt and
before task/history messages. It does not split assistant/tool turns or alter
provider reasoning. `ContextManager` reserves space for it before compacting
history; user constraints retain their existing protection. The notice is not
appended to session conversation history or repeated summaries. On resume, the
Runtime derives it from the new invocation's budget and persisted verification.

Each attempted model call has a metadata-only `turn_budget` trace event: turn,
cap, turns/time left, verification flag, and final-only flag. Trace events carry
no task, source code, tool output, or model reasoning. Cancellation and the task
deadline are checked again after compaction and before calling the model.

The context budget remains an approximate message-size estimate; it is not
tokenizer-exact and does not include function-schema token overhead.

For failure triage, compare `turn_budget` with the existing `tool` events. Missing
evidence plus only `bash` calls means the official test tool was never used;
`run_tests` on the last turn leaves no room for a final answer. Passing evidence
plus a rejected final-turn call indicates the provider ignored the no-tools
request. Keep these outcomes distinct, and record any later budget change as a
new experiment instead of replacing the failed run.

## Reproducible checks

```sh
uv run pytest -q tests/test_runtime_budget.py tests/test_context.py tests/test_runtime.py
uv run ruff check .
```

Regression cases exercise a real edit/test/final-answer flow with a protocol
fixture, evidence revocation, resume, repeated compaction, failure to call
`run_tests`, and cancellation/deadline expiry during compaction. Protocol fixtures
test runtime behavior and do not measure model ability.

## Live follow-up policy — 2026-10-30

Before execution, keep the [reference-qualified candidates](../benchmark/validated-candidates/README.md)
and all prior limits unchanged: DeepSeek without fallback, three repeats per
case, 18 model turns, 180 seconds per task, and 30 seconds per test. The only
runtime intervention is budget feedback and its context reservation. Both tasks
have prior pilot exposure, so this is an exploratory follow-up, not formal
acceptance or a controlled provider comparison. Preserve the earlier **5/6**
result, including its `agent_max_steps` failure.

```sh
uv run selfforge-agent eval-suite --manifest benchmark/validated-candidates/suite.json \
  --provider deepseek --no-fallback --repeats 3 --max-steps 18 \
  --time-budget 180 --test-timeout 30 \
  --output .local/benchmark/validated-candidates-budget-repeat3.json
```

## Actual follow-up result

DeepSeek `deepseek-flash` scored **5/6**, with PocketFlow **2/3** and boltons
**3/3**. The manifest hash and immutable Docker image IDs match the previous
run exactly. All six attempts called `run_tests`; this does not imply all tests
passed. PocketFlow repeat 1 called it three times, each unsuccessfully, and hit
the 18-turn cap. Its independent visible suite had **84 passes and 1 failure**,
while all ten holdout checks passed. It remains `agent_max_steps`, not success.
The compact report does not retain the failing assertion or final source, so the
specific cause of that visible failure is **unconfirmed**.

| Case / repeat | Model turns | Tool calls | Visible passes/failures | Holdout passes | Result | Seconds |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| PocketFlow 1 | 18 | 24 | 84 / 1 | 10 | agent_max_steps | 60.450 |
| PocketFlow 2 | 8 | 9 | 71 / 0 | 10 | passed | 28.769 |
| PocketFlow 3 | 7 | 8 | 64 / 0 | 10 | passed | 23.551 |
| boltons 1 | 14 | 16 | 37 / 0 | 11 | passed | 36.645 |
| boltons 2 | 13 | 15 | 36 / 0 | 11 | passed | 38.123 |
| boltons 3 | 9 | 10 | 36 / 0 | 11 | passed | 31.083 |

The report totals **218.621 seconds** and **396,089 provider-reported tokens**
(370,902 prompt, 25,187 completion), with no provider error category. Local
regression tests overlapped the beginning of this run. These timings are not a
controlled performance comparison; neither the unchanged 5/6 aggregate nor the
changed per-task outcomes establish a general success-rate improvement. Do not
combine this run with earlier pilots or count its overlapping tasks as new
acceptance cases.

Local verification: `uv run --no-sync pytest -q` **155 passed**, no skips,
including live Docker integration tests; one existing Starlette/httpx deprecation
warning. `uv run --no-sync ruff check .` passed. This branch builds on PR #28;
open PRs #24–#27 are not included in that count. Raw sessions and generated
reports stay in ignored local storage. Next, improve failed-test diagnosis and
recovery, then qualify more distinct tasks for the twenty-task acceptance set.
