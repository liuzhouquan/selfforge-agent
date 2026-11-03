# Frozen Twenty-Task Acceptance Suite and Execution Policy

This document defines and freezes the formal 20-task reference-qualified benchmark suite (`suite-twenty-tasks.json`) and execution policy for `SF-EVAL-02`.

- **Suite Manifest**: `benchmark/validated-candidates/suite-twenty-tasks.json`
- **Frozen Manifest SHA-256**: `0a5bd935e7d0aa4b6b9a93ad101686a50c57af2cd1b4143accd5a3bc4e327fd8`
- **Repository Coverage**: 5 public repositories (`pocketflow`, `boltons`, `more-itertools`, `itsdangerous`, `jmespath`), satisfying the minimum 3 repository diversity requirement.
- **Task Count**: 20 distinct, reference-qualified real-repository tasks.
- **Preflight Status**: 20/20 validated with Docker live daemon; zero collection errors, baseline negative assertions confirmed, and reference positive visible and holdout passes verified.

---

## 1. Pinned Tasks and Qualification Evidence

| # | Task ID | Repository & Pinned Revision | Baseline Holdout Failures | Reference Visible Passes | Reference Holdout Passes | Docker Image |
|---|---|---|---|---|---|---|
| 1 | `pocketflow_async_batch_empty` | `pocketflow` @ `f74d023f` | 2 | 56 | 10 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 2 | `boltons_unicode_punctuation` | `boltons` @ `d31d821d` | 6 | 33 | 11 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 3 | `boltons_depth_pruning` | `boltons` @ `d31d821d` | 4 | 13 | 8 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 4 | `more_itertools_zero_chunk_size` | `more-itertools` @ `4aeb4338` | 8 | 21 | 14 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 5 | `more_itertools_replace_limit` | `more-itertools` @ `4aeb4338` | 7 | 8 | 10 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 6 | `pocketflow_invalid_retries` | `pocketflow` @ `f74d023f` | 15 | 56 | 31 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 7 | `pocketflow_wait_validation` | `pocketflow` @ `f74d023f` | 45 | 56 | 65 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 8 | `pocketflow_retry_count_type` | `pocketflow` @ `f74d023f` | 30 | 56 | 45 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 9 | `pocketflow_transition_validation` | `pocketflow` @ `f74d023f` | 12 | 56 | 13 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 10 | `boltons_negative_max_depth` | `boltons` @ `d31d821d` | 3 | 13 | 5 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 11 | `boltons_windowed_size` | `boltons` @ `d31d821d` | 8 | 58 | 9 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 12 | `boltons_chunked_count` | `boltons` @ `d31d821d` | 10 | 58 | 11 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 13 | `more_itertools_negative_split_maxsplit` | `more-itertools` @ `4aeb4338` | 12 | 60 | 13 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 14 | `more_itertools_grouper_size` | `more-itertools` @ `4aeb4338` | 12 | 32 | 14 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 15 | `more_itertools_intersperse_n` | `more-itertools` @ `4aeb4338` | 8 | 6 | 11 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 16 | `itsdangerous_empty_secret_keys` | `itsdangerous` @ `672971d6` | 18 | 17 | 21 | `selfforge-itsdangerous:locked` |
| 17 | `itsdangerous_negative_max_age` | `itsdangerous` @ `672971d6` | 12 | 2 | 13 | `selfforge-itsdangerous:locked` |
| 18 | `jmespath_split_function` | `jmespath` @ `2812594e` | 18 | 5 | 19 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 19 | `jmespath_replace_function` | `jmespath` @ `2812594e` | 19 | 5 | 20 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |
| 20 | `jmespath_lower_function` | `jmespath` @ `2812594e` | 19 | 6 | 20 | `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20` |

---

## 2. Formal Execution Policy

### 2.1 Execution Protocol and Repeats
- **Total Runs**: 20 tasks × 3 repeats = 60 independent runs.
- **Clean Checkout**: Each run executes from an independent, freshly checked out copy of the repository at the pinned revision.
- **Zero Information Leakage**: Reference patches and holdout test files are kept completely external to the Agent's working copy and conversation history. The agent only receives the natural-language task prompt and repository workspace.
- **Holdout Execution**: Holdout tests execute in a separate, isolated verification copy created only after the Agent finishes its run.

### 2.2 Budgets and Deadlines
- **Maximum Agent Steps**: 18 steps per run (`--max-steps 18`).
- **Time Budget**: 180 seconds per run (`--time-budget 180`).
- **Test Execution Timeout**: 30 seconds per test command invocation (`--test-timeout 30`).

### 2.3 Environment and Sandbox Isolation
- **Sandbox Mode**: Docker sandbox (`--sandbox docker`) with `--network=none` network isolation.
- **Image Resolution**:
  - Tasks 1–15 and 18–20: `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).
  - Tasks 16–17 (`itsdangerous`): `selfforge-itsdangerous:locked` (`sha256:205fa9f663b395ace68468c42cfdfdb6c0b180593504c67d913634f962860da6`).
- **Image Pinning**: Image references are resolved to immutable local image IDs prior to baseline verification. Rebuilding or retagging during runs cannot alter the runtime environment.

### 2.4 Success Criteria and Quality Gates
A run is classified as a complete success (`success: true`) if and only if all of the following conditions are met:
1. Agent status is `completed`.
2. Repository HEAD matches the pinned revision (no git branch/commit corruption).
3. At least one file has been modified.
4. All modified files are strictly within `allowed_changes`.
5. The independent visible test selection runs with `exit_code == 0`, passing at least 1 test with 0 failures and 0 errors.
6. The independent holdout test selection runs with `exit_code == 0`, passing at least 1 test with 0 failures and 0 errors.

### 2.5 Failure Classification Taxonomy
All non-successful runs are categorized under standardized, sanitized failure reasons:
- `agent_max_steps`: Agent reached step limit (18 steps) without reaching completion.
- `agent_timeout`: Agent exceeded execution wall-clock time limit (180s).
- `agent_error`: Agent failed with an internal tool error or execution failure.
- `holdout_failed`: Holdout pytest assertions failed on the agent's changes.
- `visible_failed`: Repository visible tests failed or regressed.
- `allowed_changes_violation`: Agent modified files outside the declared `allowed_changes`.
- `revision_changed`: Agent altered the underlying Git branch revision.
- `harness_error`: Infrastructure failure during sandbox staging or test execution.
- `provider_http_<code\>`: Sanitized LLM provider API status errors (e.g. 400, 429, 500).

### 2.6 Accounting and Reporting
- Token metrics: Provider-reported `prompt_tokens`, `completion_tokens`, and `total_tokens` are recorded without extrapolation.
- Costs: Token costs are only computed when explicit pricing models are configured; no pricing estimates are fabricated.
- Duration: Per-task wall-clock duration is recorded to 3 decimal places.
- Reproducibility: Benchmark outcomes are written to machine-readable JSON reports preserving manifest SHA, image IDs, and step traces.
