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

Current scope excludes GitHub authentication, LLM tools, generated tests, repair, and
benchmarking. A future LLM reviewer can implement `ReviewAgent` while returning the same
validated `Finding` model and using bounded read-only repository tools.
