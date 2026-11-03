# ItsDangerous empty secret key qualification

This project-authored candidate task qualifies an empty secret key contract tightening for `pallets/itsdangerous` at pinned revision `672971d66a2ef9f85151e53283113f33d642dabd` (BSD-3-Clause).

The repository uses source-layout (`src/itsdangerous`) and locked dependency profile `docker/profiles/itsdangerous.lock`. It runs against the prebuilt dependency sandbox `selfforge-itsdangerous:locked` (`sha256:205fa9f663b395ace68468c42cfdfdb6c0b180593504c67d913634f962860da6`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `itsdangerous_empty_secret_keys` | Explicit contract tightening: empty sequence of secret keys rejected at construction with `ValueError` mentioning `secret_key` | `itsdangerous` `672971d66a2ef9f85151e53283113f33d642dabd`, `src/itsdangerous/signer.py:73`: `_make_keys_list` accepts empty iterables without error, leading to deferred `IndexError` on `.secret_key`, `.sign()`, `.unsign()`, `.dumps()`, or `.loads()`. Test empty list `[]`, empty tuple `()`, and empty iterator across all 6 public signer/serializer classes; preserve single strings, bytes, and multi-key rotation controls |

Upstream docs state that `secret_key` supports a list of keys for rotation from oldest to newest. Passing an empty sequence silently produces an empty `secret_keys` list, postponing failure to usage time. Rejecting empty keys eagerly at initialization prevents invalid state propagation.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-itsdangerous.json \
  --output .local/benchmark/itsdangerous-preflight.json
```
