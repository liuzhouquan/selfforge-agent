# Iterator and traversal qualification

These three project-authored tasks extend the reference-qualified set to five
distinct tasks across three repositories. The zero-chunk contract overlaps an
earlier pilot; it is not an additional unique task. The original two-case
manifest and all historical results remain unchanged. These are small candidate
tasks, not the twenty-task product acceptance set or confirmed upstream issues.

## Contracts and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `boltons_depth_pruning` | Traversal efficiency fix: `max_depth` bounds directory scanning, preserving yielded paths and boundary directory matches | boltons `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/fileutils.py:492`: documented traversal depth; existing loop filters deep roots without pruning `os.walk`. Observe `os.scandir` over a real temporary tree; test depth 0/1, trailing separators, path-like roots, include_dirs, patterns/ignored names and unlimited traversal |
| `more_itertools_zero_chunk_size` | Explicit contract tightening: zero size raises at call time in both strict modes | more-itertools `4aeb4338ae1407d8da9723e86da99efeb446f719`, `more_itertools/more.py:215,1532`: existing negative guards and zero-size empty iterators. Test empty/nonempty inputs, lazy single-use sources, positive/negative/None sizes, slice types and strict partial-tail errors |
| `more_itertools_replace_limit` | Explicit callback-efficiency extension: stop predicate calls after the replacement count is exhausted, including count 0 | Same more-itertools SHA, `more_itertools/more.py:3446`: predicate is currently evaluated before the count check. Test callback counts, overlapping windows and short tails, unlimited mode, single-use substitutes, invalid windows and exception propagation |

The replacement task intentionally changes callback semantics. Upstream docs
limit replacement count but do not promise predicate short-circuiting. No
assumption about negative counts or eager substitutes is added. File-search
ignored patterns filter results, not traversal; boundary child-directory entries
must remain visible with `include_dirs=True`. These compatibility controls stop
a superficially plausible optimization from silently changing unrelated results.

Patches contain only minimal source changes. Holdouts are original checks and
never import Agent-generated regression tests. References, holdouts, and license
notices stay outside evaluated repositories; see [LICENSES.md](./LICENSES.md).
No external repository is changed by qualification or evaluation.

## Reproduce and fixed live policy

Prepare the three pinned clones using the [pilot instructions](../real-pilot/README.md),
including [more-itertools](../real-pilot/more-itertools.md). The image ID in both
new manifests is `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`.
That exact image must be available locally; a newly built image with a different
ID requires a separately recorded manifest change and new qualification.

```sh
# All five tasks, no model or provider key required.
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-five-tasks.json \
  --output .local/benchmark/five-tasks-preflight.json

# Only the three newly qualified cases, three fresh attempts each.
uv run --no-sync selfforge-agent eval-suite \
  --manifest benchmark/validated-candidates/suite-iterator-traversal.json \
  --provider deepseek --no-fallback --repeats 3 --max-steps 18 \
  --time-budget 180 --test-timeout 30 \
  --failure-artifacts .local/benchmark/iterator-traversal-failures \
  --output .local/benchmark/iterator-traversal-repeat3.json
```

Before the first model attempt, fix DeepSeek without fallback, three repeats,
18 turns, 180 seconds per task, 30 seconds per test, and the manifests/holdouts/
reference bytes. This budget comes from previous pilots. Do not increase it or
change tests and silently replace an unsuccessful result. Record the returned
model ID, hashes, failure categories and independent visible/holdout counts.
Private bounded failure snapshots are enabled for diagnosis; holdout diagnostics
are never fed back to the evaluated Agent. No raw diagnostics or sessions are
published.

Visible selections are the complete `tests/test_fileutils.py` for boltons;
`ChunkedTests or SlicedTests` and `ReplaceTests` in `tests/test_more.py` for the
two more-itertools tasks. The broader more-itertools file has an unrelated
100-thread test beyond the current sandbox PID limit. Narrowed selections are
disclosed in the task prompt and also used by independent verification.

## Docker qualification — 2026-10-30

| Case | Baseline holdout assertion failures | Reference visible passes | Reference holdout passes |
| --- | ---: | ---: | ---: |
| PocketFlow async empty batch (existing) | 2 | 56 | 10 |
| boltons Unicode punctuation (existing) | 6 | 33 | 11 |
| boltons depth pruning | 4 | 13 | 8 |
| more-itertools zero chunks | 8 | 21 | 14 |
| more-itertools replacement limit | 7 | 8 | 10 |

All positive controls had exit 0 and zero failures/errors/skips. Full five-task
manifest SHA-256: `5fbdc62e2e5be199e72a79c9b3ae23dcc325847cdb4e07bbddf20be4bfc20942`.
Three-task live manifest SHA-256: `225d271542b0387bf2c61008d7e8669fdb1a4b032b08b6e54e420c00567513e4`.
The exact image is recorded above. The local preflight report records each
holdout/reference hash and passed count. Qualification is separate from model
evaluation, and the two older cases are not rerun by the live delta manifest.

## Live results

The fixed run on 2026-10-30 used DeepSeek model ID `deepseek-flash` and completed
**9/9** attempts. Each case scored **3/3**. All independent visible selections
and holdouts passed with zero failures/errors. Every attempt changed only allowed
paths, called `run_tests`, and reached `completed` within the original budget.

| Case / repeat | Model turns | Tools | Visible passes | Holdout passes | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Depth pruning 1 | 6 | 7 | 16 | 8 | passed |
| Depth pruning 2 | 11 | 14 | 17 | 8 | passed |
| Depth pruning 3 | 15 | 17 | 19 | 8 | passed |
| Zero chunks 1 | 13 | 23 | 25 | 14 | passed |
| Zero chunks 2 | 13 | 20 | 34 | 14 | passed |
| Zero chunks 3 | 7 | 15 | 39 | 14 | passed |
| Replacement limit 1 | 6 | 8 | 16 | 10 | passed |
| Replacement limit 2 | 16 | 21 | 16 | 10 | passed |
| Replacement limit 3 | 16 | 20 | 17 | 10 | passed |

Total attempt duration: **398.216 seconds**. Provider-reported tokens:
**560,626** (504,654 prompt; 55,972 completion). No provider errors or failure
categories occurred. Private failure snapshots were enabled but none was
needed. Local aggregate report SHA-256:
`a54d66c97d5c4dbc03e8b76a7194fac20103cff2730f17662c23dd72e16d6bde`.
The report, model sessions and test output remain ignored local artifacts.

This is an exploratory result on three small project-authored tasks, not a
general success-rate estimate or proof of improved reliability. The zero-chunk
task has prior pilot exposure; two other tasks are new explicit contracts.
Two short Host regression checks overlapped the live run, so duration is
observational and unsuitable for comparative performance claims. No full
regression suite or other Docker evaluation ran concurrently.

The evaluation process started on Runtime baseline `c82ac70` plus these frozen
assets, before the Host verification-copy fix in this delivery. Docker test
commands already discard their copies, so the reproduced Host contamination
path does not affect this Docker result. The new Host regression tests cover
both false-success and false-failure cases after that fix. These nine attempts
are reported separately from both historical 5/6 runs; the cumulative five-task
manifest has reference qualification, not a newly combined live score. The
twenty-task acceptance run remains incomplete.

## Delivery regression

The combined change passed `uv run --no-sync pytest -q`: **259 passed**, no skips,
226.99 seconds, including available Docker integration tests. One existing
Starlette/httpx deprecation warning remains. Ruff and Git whitespace checks
passed. This count is the selfForge regression suite; the 32 new holdout checks
are exercised separately by actual pinned-repository qualification/evaluation,
not added to that count. Staged credential/private-artifact scanning found no
matches. Remote CI and merge status are recorded in the delivery PR.
