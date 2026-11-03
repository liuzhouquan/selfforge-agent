# boltons chunked count validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `boltons` at pinned revision `d31d821d56c8f7756af9c30a864077f2e5855321` (BSD license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `boltons_chunked_count` | Explicit contract tightening: `count < 0` rejected with `ValueError` mentioning `chunk count` at call time | `boltons` `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/iterutils.py:288`: `chunked` accepts `count` without validating `count >= 0`. Negative count falls through to `itertools.islice`, raising an internal standard library error. Validate `count` using `_validate_positive_int(count, 'chunk count', strictly_positive=False)` when `count is not None`, rejecting negative integers with `ValueError` mentioning `chunk count`; preserve `count=0`, positive count, and `count=None`. |

Documented semantics define `count` as the maximum number of chunks to produce. Negative counts are invalid argument specifications.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-boltons-chunked-count.json \
  --output .local/benchmark/boltons-chunked-count-preflight.json
```
