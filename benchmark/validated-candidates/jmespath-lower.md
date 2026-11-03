# jmespath lower function qualification

This candidate task qualifies a built-in function extension for `jmespath` at pinned revision `2812594e69d43098ef60f81f4efc404c071b0418` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `jmespath_lower_function` | Explicit built-in function registration: `lower(string)` registered in `jmespath.functions.Functions` | `jmespath` `2812594e69d43098ef60f81f4efc404c071b0418`, `jmespath/functions.py:245`: existing string functions include `join`, `starts_with`, `ends_with`, and `reverse`. Calling `lower(...)` in baseline raises `UnknownFunctionError`. Registered function requires 1 string argument, converts characters to lowercase via `.lower()`, preserves existing arity checks (`ArityError`) and type checks (`JMESPathTypeError`), and leaves existing functions unchanged |

Documented semantics define `lower` as taking 1 string and returning the lowercase representation.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-jmespath-lower.json \
  --output .local/benchmark/jmespath-lower-preflight.json
```

