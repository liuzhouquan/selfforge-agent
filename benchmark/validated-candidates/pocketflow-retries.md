# PocketFlow invalid retries qualification

This project-authored candidate task qualifies a constructor validation contract tightening for `PocketFlow` at pinned revision `f74d023f93607b8c3268133339a5e532a949898c` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `pocketflow_invalid_retries` | Explicit constructor validation: non-positive `max_retries` rejected at initialization with `ValueError` mentioning `max_retries` | `pocketflow` `f74d023f93607b8c3268133339a5e532a949898c`, `pocketflow/__init__.py:27`: `Node(max_retries=0)` causes `range(0)` in `_exec()`, silently skipping execution and returning `None`. Test non-positive counts (0, -1, -5) across all node subclasses (`Node`, `AsyncNode`, `BatchNode`, `AsyncBatchNode`, `AsyncParallelBatchNode`); preserve positive integers and default execution behavior |

Passing non-positive retry counts silently breaks the execution lifecycle without error. Eager validation during construction prevents silent non-execution bugs.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-pocketflow-retries.json \
  --output .local/benchmark/pocketflow-retries-preflight.json
```

