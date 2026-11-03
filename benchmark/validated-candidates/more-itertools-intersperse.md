# more-itertools intersperse parameter validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `more-itertools` at pinned revision `4aeb4338ae1407d8da9723e86da99efeb446f719` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `more_itertools_intersperse_n` | Explicit contract tightening: non-positive `n <= 0` consistently rejected with `ValueError('n must be > 0')` across `intersperse` at call time | `more-itertools` `4aeb4338ae1407d8da9723e86da99efeb446f719`, `more_itertools/more.py:979`: `intersperse` checks `if n == 0: raise ValueError('n must be > 0')`, but negative `n` bypasses this check and falls through to `chunked(iterable, n)`, which raises `ValueError('n must be at least 0')`. Consistently reject all non-positive integers `n <= 0` with `ValueError('n must be > 0')` at call time; preserve positive separation behavior, empty iterables, and fill values. |

Documented semantics define `intersperse` as leaving *n* items between each filler element. A non-positive spacing makes no physical sense and should consistently raise `ValueError('n must be > 0')`.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-more-itertools-intersperse.json \
  --output .local/benchmark/more-itertools-intersperse-preflight.json
```

