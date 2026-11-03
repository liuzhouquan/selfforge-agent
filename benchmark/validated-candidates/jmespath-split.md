# JMESPath split function qualification

This task extends the reference-qualified benchmark set to six tasks across four distinct Python repositories, adding `jmespath` (AWS/boto3 core dependency). It fulfills the `SF-EVAL-01` backlog unit by delivering a distinct mechanism with a pinned revision, clear behavioral contract, failing baseline negative control, and verified reference patch.

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `jmespath_split_function` | Feature extension: built-in `split(search, separator)` string function with Unicode character-split on empty separator | `jmespath` revision `2812594e69d43098ef60f81f4efc404c071b0418` (MIT License). `jmespath/functions.py`: existing `@signature` function registry (`starts_with`, `ends_with`, `join`). Test multi-char separators, missing separators, consecutive/edge delimiters, empty string separator yielding Unicode characters, piped expressions, `JMESPathTypeError` for non-string types, and `ArityError` for incorrect argument counts. |

### Behavioral specification

1. Function Registration:
   - Register `split` in `jmespath.functions.Functions` decorated with `@signature({'types': ['string']}, {'types': ['string']})`.
   - First parameter is the string to search/split; second parameter is the separator string.
2. Separation Rules:
   - Non-empty `separator`: split `search` on `separator` into list of substrings (e.g., `split('a,b,c', ',')` -> `['a', 'b', 'c']`).
   - Empty `separator` (`""`): split `search` into individual Unicode characters (e.g., `split('hello', '')` -> `['h', 'e', 'l', 'l', 'o']`, `split('', '')` -> `[]`, `split('你好世界', '')` -> `['你', '好', '世', '界']`).
3. Type and Arity Invariants:
   - Reject non-string arguments with `JMESPathTypeError`.
   - Reject incorrect argument count with `ArityError`.
   - Keep existing built-in functions (`join`, `starts_with`, `ends_with`, `reverse`, `length`, etc.) intact.
4. Regression and Allowed Changes:
   - Allowed change paths: `jmespath/functions.py` and `tests/test_functions.py`.
   - Visible test target: `tests/test_functions.py`.

## Validation results

Host qualification command:
```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-jmespath-split.json \
  --sandbox host --trust-local
```

### Preflight summary

| Case | Baseline holdout assertion failures | Reference visible passes | Reference holdout passes |
| --- | ---: | ---: | ---: |
| `jmespath_split_function` | 18 | 5 | 19 |

- Baseline status: `failed` with 18 assertion failures, 0 errors, 0 skips, exit code 1.
- Reference patch: applies cleanly via `git apply`, modifies only `jmespath/functions.py`, passes visible selection (`5` passed, 0 failures, 0 errors, exit 0) and holdout (`19` passed, 0 failures, 0 errors, exit 0).
- Manifest SHA-256 (`suite-jmespath-split.json`): `c54f8cecfe2a4a34496918bcbab4d291b24f0ffcc1b85d0a08f308f72b44a365`
- Cumulative 6-task manifest SHA-256 (`suite-six-tasks.json`): `103b48f972d92ada778d9aa858be1b9b95e18b6efca45be678239a17c8025f16`
- Holdout SHA-256: `462b0fa0f9b11f3957786f8b15b54d9ce0883fde9b854a45c230430365a708d0`
- Reference patch SHA-256: `17786d9567256073d224b8ab09eea7b05f9c26ec77e91ba5fcc416eb5a15b19d`

