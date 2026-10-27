# more-itertools large-file task

This case uses the same public [more-itertools](https://github.com/more-itertools/more-itertools) checkout and MIT-licensed commit `4aeb4338ae1407d8da9723e86da99efeb446f719` as the [grouper task](./more-itertools.md). Upstream source stays in ignored `.local/benchmark/repos/`; it is not vendored here.

The task asks for `chunked(..., 0)` and `sliced(..., 0)` to raise `ValueError` when called, in both strict modes. The independent holdout has four assertions failing on the pinned baseline and one positive-size control. `more_itertools/more.py` is about 175 KB and `tests/test_more.py` about 256 KB; this task exercises bounded search and line-range reads. The 21 existing `ChunkedTests` and `SlicedTests` checks pass in the default Docker image. The full `tests/test_more.py` file was also tried, but its unrelated concurrent-consumer test attempts roughly 100 threads and exceeds the current sandbox process limit. The case therefore explicitly selects the two relevant classes with `-k` and reports only that selection as its visible-test evidence.

After preparing the pinned checkout as in the [pilot guide](./README.md), run:

```sh
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/more-itertools-chunks.json \
  --repeats 1 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/more-itertools-chunks-report.json
```

The first exploratory DeepSeek-only run scored **1/1** complete success. The Agent changed the allowed source and visible test files, then the evaluator independently reran the selected visible tests (23 passed) and the external holdout (5 passed). It used 17 model steps, 23 tool calls, 40.240 seconds, and 102,596 provider-reported tokens. This is one project-authored task and one live attempt; it does not establish a general success rate.
