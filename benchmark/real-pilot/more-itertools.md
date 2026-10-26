# more-itertools pilot

This case uses public [more-itertools](https://github.com/more-itertools/more-itertools) at commit `4aeb4338ae1407d8da9723e86da99efeb446f719` (MIT license). The source stays in ignored `.local/benchmark/repos/`; no upstream code is vendored in selfForge Agent.

The task asks the Agent to reject non-positive `grouper` group sizes in all three incomplete modes, preserve positive-size behavior, add a visible regression test, and run `tests/test_recipes.py`. The external holdout has six invalid-size assertions that fail on the pinned baseline and one positive-size control that passes. It is copied only into an independent verification checkout after the Agent stops. The full `tests/test_recipes.py` file passed in the default offline Docker image before the Agent run: 145 tests and 9,876 subtests.

From the selfForge Agent repository root:

```sh
mkdir -p .local/benchmark/repos
git clone https://github.com/more-itertools/more-itertools.git .local/benchmark/repos/more-itertools
git -C .local/benchmark/repos/more-itertools checkout --detach 4aeb4338ae1407d8da9723e86da99efeb446f719
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent eval-suite --manifest benchmark/real-pilot/more-itertools.json \
  --repeats 3 --max-steps 18 --provider deepseek --no-fallback \
  --output .local/benchmark/more-itertools-report.json
```

The first exploratory DeepSeek-only run scored **1/1** complete Agent successes: both allowed files changed, the Agent ran the visible test file, and all seven independent holdout checks passed. It took 35.295 seconds, 10 model steps, 12 tool calls, and 54,591 provider-reported tokens. This is a project-authored task and holdout, not a general success-rate estimate.
