# boltons negative max_depth validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `boltons` at pinned revision `d31d821d56c8f7756af9c30a864077f2e5855321` (BSD license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `boltons_negative_max_depth` | Explicit contract tightening: `max_depth < 0` rejected with `ValueError` mentioning `max_depth` in `iter_find_files` | `boltons` `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/fileutils.py:506`: docstring states `max_depth` is `0 for the specified directory only, 1 for directory and one level of subdirectory`. Values below 0 are currently accepted without error and silently return no matches. Holdout tests negative depths (-1, -2, -10) to ensure rejection with `ValueError(match="max_depth")`; preserves `max_depth=0`, `max_depth=1`, and unlimited `max_depth=None` |

Documented semantics define `0` as search in the specified directory only. Passing negative depth values silently yields no matches instead of detecting programmer errors at construction/call time.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-boltons-max-depth.json \
  --output .local/benchmark/boltons-max-depth-preflight.json
```
