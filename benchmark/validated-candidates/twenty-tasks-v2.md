# Reviewed twenty-case suite — policy v2

This is a project-authored set of 20 cases across five real repositories, mostly constructor/input-validation contracts and small string/iterator extensions. These are not 20 upstream-confirmed bugs, a broad difficulty benchmark, or a measured Agent success rate. Overlapping earlier pilots count once. At policy freeze, formal 60-run model evaluation had not been performed. The subsequent 2026-11-04 measurement scored **53/60**, with missing historical provenance fields disclosed in [the evaluation record](../../docs/evaluation.md); it does not establish performance for later Runtime changes.

## Frozen inputs

- Manifest: [suite-twenty-tasks-v2.json](./suite-twenty-tasks-v2.json), SHA-256 `3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6`.
- Reference/holdout inventory: [suite-twenty-tasks-v2.assets.json](./suite-twenty-tasks-v2.assets.json), SHA-256 `89bd73aaa068193257a7e5392632f2eedd831e3c9723cdf8ca5bd8fee001795d`. Structural tests check every asset digest, case ID, revision, allowed path and image ID.
- Original [v1 manifest](./suite-twenty-tasks.json) stays byte-for-byte frozen. V2 fixes wait validation so finite arbitrarily large Python integers are accepted, adds the corresponding positive control to the independent holdout, and pins ItsDangerous's image to the previously recorded ID.
- Source revisions, source URLs and notices are in the manifest, individual contract documents and [licenses](./LICENSES.md). Check out sources under `.local/benchmark/repos/` at the full specified SHAs; no source caches or wheel binaries are committed.

## Environment and reproducibility

Default cases require local image `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`. ItsDangerous requires `sha256:205fa9f663b395ace68468c42cfdfdb6c0b180593504c67d913634f962860da6`. Both must exist before evaluation; the harness resolves/validates them before cases. No pull/network execution fallback is allowed.

These historical local images are not distributed artifacts. A fresh Dockerfile build is **not** guaranteed to reproduce their IDs. To run elsewhere, build the base image with `docker/Dockerfile`, follow [dependency-image preparation](../../docs/dependency-images.md) for the checked-in ItsDangerous lock, and record returned immutable IDs. Create a separately named manifest replacing only image IDs, create its new asset/manifest hash record, then repeat **all** reference preflight controls. Such results belong to the new manifest/policy/environment and cannot inherit historical qualification or scores. Do not overwrite v1/v2 to match new tags.

With the recorded images and pinned source caches available:

```sh
uv sync --locked --dev
mkdir -p .local/benchmark
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-twenty-tasks-v2.json \
  --test-timeout 30 --output .local/benchmark/twenty-v2-preflight.json
```

## Formal model policy

- Provider: **DeepSeek**, exact model ID **`deepseek-flash`**, endpoint `https://api.deepseek.com`; **no fallback**. Configure that exact model/base URL in provider settings (settings take precedence over environment variables). Before starting, check the effective configuration without printing credentials. Unavailable or changed model IDs require a new recorded policy, not silent substitution.
- 20 tasks × 3 repeats = 60 independent attempts, fresh pinned checkout each time.
- Docker, no network in test/command containers, existing filesystem/resource limits; independent visible/holdout snapshots created before either verification selection runs.
- 18 Agent steps, 180-second Agent budget, 30-second per-test timeout. Common Runtime context budget: 8,000 heuristic tokens; estimates are not exact tokenization. Runner/evaluator phases have their own bounded timeouts, so the 180-second Agent budget is not an end-to-end suite deadline.
- No reference/holdout content in the Agent workspace or conversation. No manual intervention, modified constraints, test-selection changes or discarded failed attempts.
- Run the same reviewed application commit and locked environment for all attempts; record that SHA, Python/Docker versions, image IDs, manifest/asset hashes and effective provider/model with the report. A later Runtime requires a new result, even with the same cases.

After confirming the above effective model configuration and a successful preflight, the explicit model command is:

```sh
uv run --no-sync selfforge-agent eval-suite \
  --manifest benchmark/validated-candidates/suite-twenty-tasks-v2.json \
  --provider deepseek --no-fallback --repeats 3 --max-steps 18 \
  --time-budget 180 --test-timeout 30 \
  --output .local/benchmark/twenty-v2-model-repeat3.json
```

The evaluator repeats baseline/reference preflight before constructing models. Inspect the actual provider/model fields of **every attempt**; configuration or identity mismatches invalidate comparison and must remain disclosed. Provider failures remain failed attempts; never change providers or replace failures with reruns. Preserve all attempted-run evidence. An interrupted sweep is incomplete, not a 60-run score. No automatic reruns to replace failures.

## Outcomes and accounting

Success requires `completed`, unchanged pinned Git HEAD, a nonempty allowed change set, independent nonempty visible passes and independent nonempty holdout passes (zero errors/failures). Step/time limits remain failures even if a later test passes.

Use actual report reasons from `runtime/benchmark.py`, including `agent_max_steps`, `agent_timed_out`, `agent_failed`, `agent_cancelled`, `no_code_change`, `unexpected_file_changes`, `revision_changed`, `visible_test_missing`, `visible_test_timeout`, `visible_test_collection_error`, `visible_test_assertion_failed`, `visible_test_environment_error`, `holdout_timeout`, `holdout_collection_error`, `holdout_assertion_failed`, `holdout_environment_error`, and `harness_error`. Preserve any other actual reason instead of dropping it. Provider HTTP categories such as `provider_http_429` belong to sanitized `agent_error_code`, not the top-level success reason.

Report successes/failures, latency, and provider-reported token usage for all attempts. Missing usage stays incomplete; configured exact-model rates can yield estimates, never actual billing. Retain raw sessions, failure snapshots and reports privately; publish sanitized aggregate evidence. No model acceptance threshold is claimed before a real baseline exists.

## Review verification

See [integration review](../../docs/incoming-branches-review.md) for actual commands and outcomes. Reference qualification verifies task solvability and negative/positive controls, not Agent/model performance. The candidate pool's limited task diversity remains an explicit limitation.
