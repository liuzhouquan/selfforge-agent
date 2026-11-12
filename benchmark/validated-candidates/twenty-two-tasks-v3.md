# Expanded twenty-two-case suite and execution specification — policy v3

This specification freezes the next-round evaluation inputs, Docker environment policy, failure taxonomy, and live-model execution ceilings for `SF-EVAL-08` across **22 distinct tasks** and **5 public repositories** (`pocketflow`, `boltons`, `more-itertools`, `itsdangerous`, `jmespath`).

> [!IMPORTANT]
> Historical frozen manifests and scores remain completely untouched:
> - `suite-twenty-tasks.json` (`SHA-256: 0a5bd935e7d0aa4b6b9a93ad101686a50c57af2cd1b4143accd5a3bc4e327fd8`)
> - `suite-twenty-tasks-v2.json` (`SHA-256: 3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6`)
> - `suite-twenty-tasks-v2.assets.json` (`SHA-256: 89bd73aaa068193257a7e5392632f2eedd831e3c9723cdf8ca5bd8fee001795d`)
> - Historical v2 result (**53/60**) belongs solely to its recorded runtime and frozen v2 inputs.
>
> **Execution Status (`SF-EVAL-08-A3`)**: No live model benchmark (`eval-suite`) has been executed under policy v3 (`model_benchmark_executed: false`, `completed_runs: 0`, `success_rate: null`).

---

## 1. Frozen Inputs and Asset Digests

- **Manifest**: [`suite-twenty-two-tasks-v3.json`](./suite-twenty-two-tasks-v3.json)
  - `SHA-256`: `b8351e6210f89d29563a01b48b84490a53e111c02b6a87ec828e464631c48f19`
- **Holdout & Reference Inventory**: [`suite-twenty-two-tasks-v3.assets.json`](./suite-twenty-two-tasks-v3.assets.json)
  - `SHA-256`: `21a11779c924341ebce86a81c4e12567fbf0fe759663d8d14504d4f159490a35`
- **Machine-Verifiable Policy Bundle**: [`suite-twenty-two-tasks-v3.policy.json`](./suite-twenty-two-tasks-v3.policy.json)
  - `SHA-256`: `c7f207570a32896e4e408fb9f4febaba6ef4176a014735d195be0f1bad722b89`
- **Dependency Lockfile (`itsdangerous`)**: [`docker/profiles/itsdangerous.lock`](../../docker/profiles/itsdangerous.lock)
  - `SHA-256`: `884dc01acbd6ed8ae06a14bc5069afe5bb861cf5194d67ac1bd824d59b5c40fc`

### Task Set Composition (22 Cases / 5 Repositories)

- Cases `1–20`: All 20 tasks preserved from v2 across `pocketflow` (5), `boltons` (5), `more-itertools` (5), `itsdangerous` (2), and `jmespath` (3).
- Case `21`: `jmespath_unknown_function_suggestions` ([contract](./jmespath-unknown-function.md), `SF-EVAL-06`) — multi-file extension touching `jmespath/exceptions.py`, `jmespath/functions.py`, `jmespath/visitor.py`, `tests/test_functions.py`.
- Case `22`: `boltons_remap_mappings` ([contract](./boltons-remap-mappings.md), `SF-EVAL-07`) — multi-file extension touching `boltons/dictutils.py`, `boltons/iterutils.py`, `tests/test_dictutils.py`, `tests/test_iterutils.py`.

### Pinned Source Repositories

| Repository | Pinned Git URL | Pinned Commit SHA | Cases |
| --- | --- | --- | ---: |
| `pocketflow` | `https://github.com/The-Pocket/PocketFlow.git` | `f74d023f93607b8c3268133339a5e532a949898c` | 5 |
| `boltons` | `https://github.com/mahmoud/boltons.git` | `d31d821d56c8f7756af9c30a864077f2e5855321` | 6 |
| `more-itertools` | `https://github.com/more-itertools/more-itertools.git` | `4aeb4338ae1407d8da9723e86da99efeb446f719` | 5 |
| `itsdangerous` | `https://github.com/pallets/itsdangerous.git` | `672971d66a2ef9f85151e53283113f33d642dabd` | 2 |
| `jmespath` | `https://github.com/jmespath/jmespath.py.git` | `2812594e69d43098ef60f81f4efc404c071b0418` | 4 |

---

## 2. Docker Environment & Model-Free Reference Preflight (`SF-EVAL-08-A2`)

Policy v3 does **not** reuse or alias historical v2 Docker image IDs (`sha256:abad14d3...`, `sha256:205fa9f6...`). Operators and CI build fresh images from the checked-in `docker/Dockerfile` and hash-locked `docker/profiles/itsdangerous.lock`, and `eval-validate` / `eval-suite` resolve each image tag once to its immutable local `sha256:...` digest before executing cases:

1. Build the base sandbox and locked `itsdangerous` dependency image:
   ```sh
   docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
   mkdir -p .local/wheelhouse/itsdangerous
   uv tool run --from pip pip download --index-url https://pypi.org/simple \
     --only-binary=:all: --require-hashes \
     --dest .local/wheelhouse/itsdangerous -r docker/profiles/itsdangerous.lock
   uv run --no-sync selfforge-agent sandbox build \
     --requirements docker/profiles/itsdangerous.lock \
     --wheelhouse .local/wheelhouse/itsdangerous \
     --base-image selfforge-agent-sandbox:latest \
     --tag selfforge-itsdangerous:locked > .local/itsdangerous-image.json
   ```
2. Provision all 5 pinned repositories using the shared preparation script:
   ```sh
   uv run --no-sync python scripts/prepare_eval_sources.py --profile v3
   ```
3. Run model-free qualification (`eval-validate` constructs no provider/model and makes zero LLM API calls):
   ```sh
   uv run --no-sync selfforge-agent eval-validate \
     --manifest benchmark/validated-candidates/suite-twenty-two-tasks-v3.json \
     --test-timeout 30 \
     --output .local/benchmark/twenty-two-v3-preflight.json
   ```

Every case must fail on its pinned baseline holdout (`status == "failed"`, `failed >= 1`, `errors == 0`) and pass both its independent visible test selection and external holdout test with its external `reference_patch` (`status == "passed"`, `passed >= 1`, `failed == 0`, `errors == 0`, only `allowed_changes` touched). The report records exact resolved `sha256:` image IDs, source revision SHAs, holdout/patch SHA-256 digests, and manifest SHA-256.

---

## 3. Separate Live-Model Execution Plan & Ceilings (`SF-EVAL-08-A3`)

> [!WARNING]
> **Do NOT run `eval-suite` as part of `SF-EVAL-08`.** This section defines the frozen plan for a **separate** future live evaluation run only after preflight succeeds and effective provider credentials/limits are explicitly verified.

### Provider, Model & Fallback Policy

- **Provider**: `deepseek`
- **Model ID**: `deepseek-flash`
- **Base URL**: `https://api.deepseek.com`
- **Fallback**: Disabled (`--no-fallback`)
- **Pre-run config gate**: Verify effective configured provider/model/base URL without printing credentials (`provider_info()` / CLI check). No silent substitution on unavailable models or quota errors.

### Budgets, Repeats & Execution Ceilings

- **Repeats**: `3` independent fresh checkouts per task (`22 tasks × 3 repeats = 66 attempts`)
- **Per-attempt Turn Budget**: `18` steps (`--max-steps 18`)
- **Per-attempt Time Budget**: `180` seconds (`--time-budget 180`)
- **Per-test Command Timeout**: `30` seconds (`--test-timeout 30`)
- **Runtime Context Budget**: `8,000` heuristic tokens (with dynamic transient budget-notice reservation; not exact tokenization)
- **Strict Operational Caps**:
  - `max_case_attempts`: `66` (zero automatic retries or failure replacements)
  - `max_model_calls_upper_bound`: `1,188` (`66 × 18`)
  - `max_wall_clock_minutes`: `90` (stop the sweep if operator deadline expires; report partial runs as incomplete rather than a 66-run score)
  - `max_spend_usd`: `$25.00` hard operator spend ceiling (verified via provider account balance / explicit `SELFFORGE_PRICING_JSON` `ModelRates`; spend remains `uncalibrated` if rate cards are omitted — never infer USD cost from token counts without rate cards)

### Canonical Failure Taxonomy

All outcomes use the exact classifications in [`selfforge_agent.runtime.benchmark`](../../src/selfforge_agent/runtime/benchmark.py):

- **Success (`success: true`)**: `"passed"`
- **Failure `reason` categories (`17`)**:
  - Agent status: `agent_max_steps`, `agent_timed_out`, `agent_failed`, `agent_cancelled`
  - Workspace / Git integrity: `no_code_change`, `unexpected_file_changes`, `revision_changed`
  - Independent visible test gate: `visible_test_missing`, `visible_test_timeout`, `visible_test_collection_error`, `visible_test_assertion_failed`, `visible_test_environment_error`
  - Independent unseen holdout gate: `holdout_timeout`, `holdout_collection_error`, `holdout_assertion_failed`, `holdout_environment_error`
  - Evaluator infrastructure: `harness_error` (plus bounded exception-class suffix `harness_error:<ExceptionType>` if raised)
- **Sanitized `agent_error_code` categories**: `provider_http_<status>`, `provider_unavailable`, `model_finish_reason`, `runtime_error`, `unknown` (zero raw response bodies persisted).

### Explicit Future Live Command (To Run Separately)

```sh
uv run --no-sync selfforge-agent eval-suite \
  --manifest benchmark/validated-candidates/suite-twenty-two-tasks-v3.json \
  --provider deepseek \
  --no-fallback \
  --repeats 3 \
  --max-steps 18 \
  --time-budget 180 \
  --test-timeout 30 \
  --output .local/benchmark/twenty-two-v3-model-repeat3.json
```
