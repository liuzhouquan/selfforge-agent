# Architecture

`core.git_context` validates a working tree and two commit SHAs, then parses file
changes and hunk lines. `review.baseline` inspects added lines through the `ReviewAgent`
interface. `verification.runner` executes the existing pytest suite in a trusted local
repository and reads JUnit XML. `ReviewService` combines these into an `EvidenceReport`.
The CLI and FastAPI app both call that service.

The CLI trust flag is the host execution boundary. The HTTP API accepts no path and no
test request; its repository is fixed by an operator environment variable. The test
runner captures actual process results and kills its process group on timeout on POSIX.
It does not isolate filesystem or network access. Docker sandboxing is a future backend.

Review findings are hypotheses. The current test result records suite health, without
claiming causality between a finding and a test. The `VerificationStatus` enum reserves
`reproduced`, `refuted`, `unverified`, and `error` for a later targeted verification loop.
The current baseline emits only `suspected`.

Current review scope excludes GitHub authentication, LLM-based review, generated tests,
and benchmarking. A future LLM reviewer can implement `ReviewAgent` while returning the
same validated `Finding` model and using bounded read-only repository tools.

## Bounded coding loop

`coding.agent.RepairAgent` compares pytest on detached base and head worktrees before
asking a `CodingModel` for an exact source replacement. A third worktree holds the
candidate repair. Each attempt records the validated edit, patch, and real test result;
at most three attempts are allowed. The agent accepts a repair only when base passes,
head has an assertion failure, repaired tests pass, and the tracked diff contains only
the allowed changed Python source files. Temporary worktrees are removed afterward.
The output patch is reviewable; it is never applied to the user's working tree.

`OpenAICompatibleCodingModel` is an optional chat completion adapter. It sends the task,
small changed source files, heuristic PR findings, and capped test output to the
configured endpoint, then
validates a `CodeEdit` object. The API itself does not decide whether a repair worked;
the pytest process does. No model service is called in the offline test suite.
