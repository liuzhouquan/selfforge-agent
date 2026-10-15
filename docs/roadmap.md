# Roadmap

1. **PR Review baseline — implemented.** Local Git context, hunk line mapping, and
   deterministic heuristic findings.
2. **Test Execution — implemented for explicitly trusted local repositories.** Existing
   pytest suite, timeout, process evidence, JUnit counts. Docker isolation is pending.
3. **Risk-driven Test Generation — pending.** Generate a targeted test from a finding,
   validate that the test itself is sound, and run it on base and head.
4. **Test-driven Repair — partial.** A bounded model-backed loop edits changed Python
   source in a temporary Git worktree and emits a patch after existing pytest tests
   pass. It does not yet use generated, targeted tests.
5. **Objective Verification Loop — partial.** Compare existing pytest suites on base,
   head, and repaired trees. Per-finding causality and richer failure classification
   remain pending.
6. **GitHub integration — pending.** Fetch PRs, trigger reviews, and gate publishing of
   comments behind human approval.
7. **Benchmark / Tracing — pending.** Measure precision, recall, false positives,
   verification success, cost, and trace each evidence transition.

An optional bounded LLM reviewer and OpenHands SDK runtime investigation remain pending.
The new coding adapter is OpenAI-compatible but is not a PR reviewer.
