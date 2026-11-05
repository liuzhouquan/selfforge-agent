"""Transient budget feedback and tool loop detection for the shared Coding loop."""

import json
from typing import Any

READ_ONLY_TOOLS = frozenset({"read_file", "search_code"})
WRITE_TOOLS = frozenset({"edit_file", "bash"})


def _canonical_args(args: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return json.dumps(parsed, sort_keys=True), parsed
        except (ValueError, TypeError):
            pass
        return args, {}
    if isinstance(args, dict):
        return json.dumps(args, sort_keys=True), args
    return str(args), {}


def detect_tool_loop(messages: list[dict], *, min_repeats: int = 2) -> dict[str, Any] | None:
    """Detect repeated identical read-only tool calls in conversation history.

    Scans the conversation history in chronological order. Whenever a write tool
    (e.g. edit_file or bash) is called, read-only tracking resets.
    If the most recent assistant tool call repeats an identical read-only invocation
    (same tool name and canonical arguments) at least `min_repeats` times in the
    current unedited phase, returns a dictionary describing the loop.
    """
    if not messages:
        return None

    counts: dict[tuple[str, str], int] = {}
    last_assistant_calls: list[tuple[str, str, dict[str, Any]]] = []

    for msg in messages:
        role = msg.get("role")
        if role == "user":
            counts.clear()
            last_assistant_calls = []
        if role != "assistant":
            continue
        calls = msg.get("tool_calls") or []
        if not calls:
            last_assistant_calls = []
            continue

        turn_calls: list[tuple[str, str, dict[str, Any]]] = []
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            canon_str, parsed_dict = _canonical_args(raw_args)

            if name in WRITE_TOOLS:
                counts.clear()
            elif name in READ_ONLY_TOOLS:
                key = (name, canon_str)
                counts[key] = counts.get(key, 0) + 1
                turn_calls.append((name, canon_str, parsed_dict))

        last_assistant_calls = turn_calls

    if not last_assistant_calls:
        return None

    for name, canon_str, parsed_dict in last_assistant_calls:
        count = counts.get((name, canon_str), 0)
        if count >= min_repeats:
            return {
                "tool": name,
                "count": count,
                "arguments": parsed_dict,
            }

    return None


def budget_notice(
    *,
    step: int,
    max_steps: int,
    remaining_seconds: float,
    tests_passed: bool,
    tool_loop: dict | None = None,
) -> dict:
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
    if tool_loop:
        tool_name = tool_loop.get("tool", "read-only")
        count = tool_loop.get("count", 2)
        content += (
            f"Repeated read-only tool call detected: you have called '{tool_name}' "
            f"with identical arguments {count} times without intervening edits. "
            "Avoid duplicate lookups and proceed with required edits (edit_file) or verification (run_tests).\n"
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
