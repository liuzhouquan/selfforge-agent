"""Transient budget feedback for the shared Coding loop."""


def budget_notice(*, step: int, max_steps: int, remaining_seconds: float,
                  tests_passed: bool) -> dict:
    """Describe actual runtime state, without adding turns or granting evidence."""
    remaining_turns = max_steps - step + 1
    content = (
        "Runtime budget (generated for this turn):\n"
        f"Turn {step}/{max_steps}; {remaining_turns} model turn(s) left including this one "
        f"and your final answer; {max(0, int(remaining_seconds))} seconds left.\n"
        "Current post-write run_tests evidence: "
        + ("passed." if tests_passed else "missing.")
        + " A successful bash command is not run_tests verification.\n"
    )
    if remaining_turns == 1:
        if tests_passed:
            content += (
                "Final-answer-only turn: tools are disabled. Give a nonempty final answer "
                "summarizing the changes, actual verification, and any remaining limitations."
            )
        else:
            content += (
                "Final turn without passing verification: calling any tool will exhaust the step "
                "budget and end with max_steps. If you cannot or do not execute further tools, "
                "give a nonempty final answer summarizing your work and report missing verification "
                "honestly (resulting in unverified status)."
            )
    elif remaining_turns == 2 and not tests_passed:
        content += (
            "Penultimate turn: this is your final opportunity to run tools and tests. Complete "
            "required edits and run_tests on this turn so the final turn can be used for your "
            "final answer. Do not use bash for testing; only run_tests establishes verification. "
            "Report missing verification honestly."
        )
    elif remaining_turns <= 3 or remaining_seconds <= 45:
        content += (
            "Budget is nearly exhausted. Avoid optional exploration. "
            "If requirements are satisfied and verification passed, give your final answer now. "
            "Otherwise prioritize the required edits and run_tests with the task's test path "
            "and selection before the final turn. Do not use bash for test verification; "
            "only run_tests establishes verification. Report missing verification honestly."
        )
    else:
        content += "Plan time for run_tests after the final write and for your final answer."
    return {"role": "system", "content": content}
