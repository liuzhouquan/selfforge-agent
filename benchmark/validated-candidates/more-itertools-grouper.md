# more-itertools grouper size validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `more-itertools` at pinned revision `4aeb4338ae1407d8da9723e86da99efeb446f719` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `more_itertools_grouper_size` | Explicit contract tightening: `n <= 0` rejected with `ValueError` at call time across all incomplete modes (`fill`, `ignore`, `strict`) | `more-itertools` `4aeb4338ae1407d8da9723e86da99efeb446f719`, `more_itertools/recipes.py:350`: `grouper(iterable, n, ...)` multiplies iterators by `n`. When `n <= 0`, `[iter(iterable)] * n` evaluates to an empty list `[]`, silently yielding an empty iterator for non-positive sizes in all modes instead of detecting configuration errors. At call time, reject `n <= 0` (0, -1, -2, -5) with `ValueError`; preserve positive-size behavior (1, 2, 3) across all modes and invalid-mode error |

Documented semantics define grouping into fixed-length groups of length `n`. Passing non-positive group sizes silently yields empty iterators rather than catching invalid sizes at call time.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-more-itertools-grouper.json \
  --output .local/benchmark/more-itertools-grouper-preflight.json
```
