# boltons remap mapping types support qualification

This project-authored candidate task qualifies multi-file cross-module support for `boltons.dictutils` mapping types (`FrozenDict` and `OrderedMultiDict`) inside `boltons.iterutils.remap` at pinned revision `d31d821d56c8f7756af9c30a864077f2e5855321` (BSD license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `boltons_remap_mappings` | Multi-file cross-module extension: `remap` support for `FrozenDict` and `OrderedMultiDict` | `boltons` `d31d821d56c8f7756af9c30a864077f2e5855321`. In `boltons.iterutils.remap`, remapping a `FrozenDict` crashes in `default_exit` with `TypeError: FrozenDict object is immutable` because in-place `.update()` fails; in `default_exit`, catch `(AttributeError, TypeError)` on `Mapping` and construct `new_parent.__class__(new_items)`. Provide `FrozenDict.copy()` returning `self`. In `default_enter`, traverse `value.items(multi=True)` when `value` has multi-item support (`OrderedMultiDict`), and in `default_exit` call `update_extend(new_items)` to preserve duplicate keys and their values. |

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-boltons-remap-mappings.json \
  --output .local/benchmark/boltons-remap-mappings-preflight.json
```
