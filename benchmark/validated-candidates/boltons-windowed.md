# boltons windowed size validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `boltons` at pinned revision `d31d821d56c8f7756af9c30a864077f2e5855321` (BSD license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `boltons_windowed_size` | Explicit contract tightening: `size <= 0` rejected with `ValueError` mentioning `window size` at call time across `windowed` and `windowed_iter` | `boltons` `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/iterutils.py:504`: `windowed` and `windowed_iter` accept `size <= 0`. With `size=0`, it silently returns an empty list/iterator without error. With negative sizes, it fails during `itertools.tee` with generic error. Use `_validate_positive_int(size, 'window size')` to reject non-positive sizes (0, -1, -2, -5) with `ValueError(match="window size")` at call time, consistent with `chunked`; preserve positive size behavior and fill options |

Documented semantics define `windowed` as returning tuples with length `size`. Passing non-positive window sizes silently produces empty outputs or fails downstream in standard library internals.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-boltons-windowed.json \
  --output .local/benchmark/boltons-windowed-preflight.json
```
