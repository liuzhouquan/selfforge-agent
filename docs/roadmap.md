# Roadmap

1. **PR review baseline — implemented.** Local and GitHub PR context, hunk line mapping, deterministic heuristic findings, and a web diff workbench.
2. **External PR workflow — partial.** Import any accessible PR and clone its head into an editable branch. Publishing edits, opening a follow-up PR, and submitting review comments are pending; source-branch permissions must be respected.
3. **LLM review — partial.** Optional DeepSeek review of a bounded set of added lines. Output is validated against actual diff locations; richer cross-file context, prompt-injection defenses, and evaluation are pending.
4. **Test execution — partial.** Explicitly trusted local repositories can run their existing pytest suite, with timeout, process evidence, and JUnit counts. A container sandbox is pending.
5. **Test-driven repair — partial.** A bounded model-backed loop edits changed Python source in temporary Git worktrees and emits a patch if the existing suite regresses on head and passes after repair. The web UI can run this only with operator and per-request trust.
6. **Risk-driven test generation — pending.** Generate and validate targeted tests for particular findings, then compare base and head.
7. **Objective verification — partial.** Existing suite health is compared across base, head, and repaired trees. Per-finding causality and richer failure classification remain pending.
8. **Benchmark and tracing — pending.** Measure false positives, verification success, time, and model cost.
