# itsdangerous negative max_age validation qualification

This project-authored candidate task qualifies a parameter validation contract tightening for `itsdangerous` at pinned revision `672971d66a2ef9f85151e53283113f33d642dabd` (BSD license).

The repository runs in the locked container environment `selfforge-itsdangerous:locked`.

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `itsdangerous_negative_max_age` | Explicit contract tightening: negative `max_age < 0` rejected with `ValueError('max_age must be >= 0')` at call time | `itsdangerous` `672971d66a2ef9f85151e53283113f33d642dabd`, `src/itsdangerous/timed.py:73`: `TimestampSigner.unsign` accepts `max_age` without verifying it is non-negative. With `max_age < 0`, fresh tokens raise `SignatureExpired` while invalid tokens raise `BadSignature` or return `False` in `validate()`. Reject `max_age < 0` immediately with `ValueError('max_age must be >= 0')` before signature verification, propagating through `unsign`, `validate`, and `loads`; preserve behavior for `max_age >= 0` and `max_age=None`. |

Documented semantics define `max_age` as the maximum allowed age of a signature in seconds. A negative maximum duration is invalid argument configuration and should raise `ValueError` directly.

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-itsdangerous-max-age.json \
  --output .local/benchmark/itsdangerous-max-age-preflight.json
```
