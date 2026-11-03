# PocketFlow retry-wait validation qualification

This project-authored candidate task qualifies a constructor validation contract tightening for `PocketFlow` at pinned revision `f74d023f93607b8c3268133339a5e532a949898c` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `pocketflow_wait_validation` | Explicit constructor validation: non-numeric `wait` raises `TypeError`, negative or non-finite `wait` raises `ValueError` | `pocketflow` `f74d023f93607b8c3268133339a5e532a949898c`, `pocketflow/__init__.py:27`: `Node(wait=...)` stores raw value and uses it in `time.sleep(self.wait)`. Non-numeric values (str, None, bool) fail late during execution with `TypeError` or wrong behavior (`bool` sleeps 1s); negative or non-finite values (inf, -inf, nan) crash or hang. Test across all 5 node types; preserve finite non-negative numbers (0, 0.01, 1, 2.5) |

Eagerly validating `wait` at construction prevents delayed execution crashes, infinite delays, or type-coercion bugs.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-pocketflow-wait.json \
  --output .local/benchmark/pocketflow-wait-preflight.json
```
