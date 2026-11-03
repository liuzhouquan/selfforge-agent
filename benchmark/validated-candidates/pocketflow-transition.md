# PocketFlow transition and action validation qualification

This project-authored candidate task qualifies parameter validation tightening for `PocketFlow` at pinned revision `f74d023f93607b8c3268133339a5e532a949898c` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `pocketflow_transition_validation` | Explicit contract tightening: `action` must be a string and `node` must be a `BaseNode` in `BaseNode.next()` | `pocketflow` `f74d023f93607b8c3268133339a5e532a949898c`, `pocketflow/__init__.py:6`: `BaseNode.__sub__` requires action to be `str` (`TypeError("Action must be a string")`), but `BaseNode.next()` accepts any object for `action` or `node`. Require `action` to be `str` and `node` to be `BaseNode` at call time in `BaseNode.next()`, protecting `node.next()`, `node >> other`, and `(node - action) >> other`. |

Documented semantics define transitions between nodes identified by action strings. Passing invalid types silently corrupts the successors mapping or crashes later during flow orchestration.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-pocketflow-transition.json \
  --output .local/benchmark/pocketflow-transition-preflight.json
```
