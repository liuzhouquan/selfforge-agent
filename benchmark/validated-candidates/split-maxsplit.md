# more-itertools negative split maxsplit qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `more-itertools` at pinned revision `4aeb4338ae1407d8da9723e86da99efeb446f719` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `more_itertools_negative_split_maxsplit` | Explicit contract tightening: `maxsplit < -1` rejected with `ValueError` mentioning `maxsplit` across delimiter splitters | `more-itertools` `4aeb4338ae1407d8da9723e86da99efeb446f719`, `more_itertools/more.py:1569,1613,1650,1690`: docstrings state that `maxsplit=-1` means unlimited splits and positive values limit splits. Values below -1 are currently accepted without error and behave as unlimited. Test `maxsplit < -1` (-2, -5, -10) on `split_at`, `split_before`, `split_after`, and `split_when`; preserve -1, 0, and positive split behaviors |

Documented semantics define `-1` as the unlimited split sentinel. Passing invalid negative counts below `-1` silently degrades into unlimited splitting instead of detecting configuration mistakes.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-split-maxsplit.json \
  --output .local/benchmark/split-maxsplit-preflight.json
```
