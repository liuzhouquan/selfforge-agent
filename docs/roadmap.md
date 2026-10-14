# Roadmap

1. **PR Review baseline — implemented.** Local Git context, hunk line mapping, and
   deterministic heuristic findings.
2. **Test Execution — implemented for explicitly trusted local repositories.** Existing
   pytest suite, timeout, process evidence, JUnit counts. Docker isolation is pending.
3. **Risk-driven Test Generation — pending.** Generate a targeted test from a finding,
   validate that the test itself is sound, and run it on base and head.
4. **Test-driven Repair — pending.** Create bounded patches in isolated Git worktrees;
   reject deletion or weakening of tests.
5. **Objective Verification Loop — pending.** Compare base, head, repaired behavior and
   full regressions; classify business bugs, broken tests, and environment failures.
6. **GitHub integration — pending.** Fetch PRs, trigger reviews, and gate publishing of
   comments behind human approval.
7. **Benchmark / Tracing — pending.** Measure precision, recall, false positives,
   verification success, cost, and trace each evidence transition.

An optional bounded LLM reviewer and OpenHands SDK runtime investigation may be added
after the local baseline. Neither is part of this MVP.
