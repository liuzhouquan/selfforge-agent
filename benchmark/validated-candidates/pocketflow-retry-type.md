# PocketFlow retry-count type validation qualification

This project-authored candidate task qualifies a type validation contract tightening for `PocketFlow` at pinned revision `f74d023f93607b8c3268133339a5e532a949898c` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `pocketflow_retry_count_type` | Explicit contract tightening: `max_retries` must be an integer, rejecting `bool` and non-integers at construction with `TypeError` mentioning `max_retries` | `PocketFlow` `f74d023f93607b8c3268133339a5e532a949898c`, `pocketflow/__init__.py:26`: `Node.__init__` accepts any object for `max_retries`. Non-integers fail during iteration or downstream execution, while `bool` is silently coerced into an integer (0 or 1). At construction, reject `bool` and non-integers with `TypeError` mentioning `max_retries`; preserve valid positive integers (1, 3, 10). Test all node types (`Node`, `AsyncNode`, `BatchNode`, `AsyncBatchNode`, `AsyncParallelBatchNode`) |

Documented semantics define retry count as an integer number of attempts. Accepting floats, strings, None, or booleans leads to latent runtime failures.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-pocketflow-retry-type.json \
  --output .local/benchmark/pocketflow-retry-type-preflight.json
```
