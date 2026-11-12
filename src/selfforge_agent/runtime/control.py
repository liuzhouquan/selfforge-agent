"""Transient budget feedback and tool loop detection for the shared Coding loop."""

import json
from pathlib import Path
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


def _find_tool_content(
    call: dict,
    messages: list[dict],
    msg_idx: int,
    call_idx: int,
    tool_responses_by_id: dict[str, str],
) -> str:
    call_id = call.get("id")
    if call_id and call_id in tool_responses_by_id:
        return tool_responses_by_id[call_id]
    offset = 0
    for i in range(msg_idx + 1, len(messages)):
        m = messages[i]
        if m.get("role") == "tool":
            if offset == call_idx:
                return str(m.get("content") or "")
            offset += 1
        elif m.get("role") in {"assistant", "user"}:
            break
    return ""


def detect_edit_failure_loop(
    messages: list[dict],
    min_failures: int = 2,
) -> dict[str, Any] | None:
    """Detect repeated failed edit attempts on the same file path.

    Tracks consecutive failed `edit_file` invocations per file path.
    A path's failure count resets to 0 when:
      1. An edit to that path succeeds.
      2. A read_file for that path succeeds (inspection).
      3. A new user message arrives.
    Returns a dict with 'path' and 'count' if the most recent turn's failed edits
    contain a path whose consecutive failure count has reached min_failures.
    """
    if not messages:
        return None

    tool_responses_by_id: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool":
            cid = msg.get("tool_call_id")
            if cid:
                tool_responses_by_id[cid] = str(msg.get("content") or "")

    failure_counts: dict[str, int] = {}
    last_turn_failed: list[str] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "user":
            failure_counts.clear()
            last_turn_failed = []
            continue
        if role != "assistant":
            continue

        calls = msg.get("tool_calls") or []
        if not calls:
            last_turn_failed = []
            continue

        current_turn_failed: list[str] = []
        for call_idx, call in enumerate(calls):
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            _, parsed_args = _canonical_args(raw_args)
            raw_path = parsed_args.get("path")
            if not raw_path or not isinstance(raw_path, str):
                continue
            norm_path = Path(raw_path).as_posix()

            content = _find_tool_content(call, messages, idx, call_idx, tool_responses_by_id)
            is_error = content.startswith(("Tool error", "Action rejected", "Error:"))

            if name == "edit_file":
                if is_error:
                    failure_counts[norm_path] = failure_counts.get(norm_path, 0) + 1
                    current_turn_failed.append(norm_path)
                else:
                    failure_counts[norm_path] = 0
            elif name == "read_file" and not is_error:
                failure_counts[norm_path] = 0

        last_turn_failed = current_turn_failed

    if not last_turn_failed:
        return None

    for path in last_turn_failed:
        count = failure_counts.get(path, 0)
        if count >= min_failures:
            return {
                "path": path,
                "count": count,
            }

    return None


def evaluate_context_pressure(
    estimated_tokens: int,
    token_budget: int,
    *,
    was_compacted: bool = False,
    high_watermark: float = 0.75,
) -> dict[str, Any] | None:
    """Evaluate context token usage against budget and compaction status.

    Returns a dict with context pressure metrics if usage exceeds high_watermark
    or if compaction was active, else None.
    """
    if token_budget <= 0:
        return None
    ratio = estimated_tokens / token_budget
    if ratio >= high_watermark or was_compacted:
        return {
            "estimated_tokens": estimated_tokens,
            "token_budget": token_budget,
            "ratio": round(ratio, 3),
            "percent": int(ratio * 100),
            "was_compacted": was_compacted,
            "high_pressure": ratio >= high_watermark,
        }
    return None


def budget_notice(
    *,
    step: int,
    max_steps: int,
    remaining_seconds: float,
    tests_passed: bool,
    tool_loop: dict | None = None,
    edit_failure: dict | None = None,
    context_info: dict | None = None,
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
    if edit_failure and not (remaining_turns == 1 and tests_passed):
        path = edit_failure.get("path", "")
        count = edit_failure.get("count", 2)
        content += (
            f"Consecutive edit failures on '{path}' ({count} times): old_text did not match. "
            f"Use read_file to inspect the exact lines and indentation of '{path}' before attempting further edits.\n"
        )
    if context_info:
        est = context_info.get("estimated_tokens", 0)
        budget = context_info.get("token_budget", 0)
        pct = context_info.get("percent", int((est / budget * 100) if budget else 0))
        was_compacted = context_info.get("was_compacted", False)
        high_pressure = context_info.get("high_pressure", False)
        if was_compacted and high_pressure:
            content += (
                f"Context compaction active: earlier conversation history has been summarized "
                f"to stay within the {budget}-token limit. Context usage remains high "
                f"({est}/{budget} tokens, {pct}%). Use targeted edits and avoid large outputs "
                "to prevent loss of recent turn details.\n"
            )
        elif was_compacted:
            content += (
                f"Context compaction active: earlier conversation history has been summarized "
                f"to stay within the {budget}-token limit ({est} tokens currently used). "
                "Keep responses focused on required file edits and run_tests verification.\n"
            )
        elif high_pressure:
            content += (
                f"High context window usage: conversation history is consuming approximately "
                f"{est}/{budget} tokens ({pct}%). Avoid dumping full files or large command "
                "outputs; use targeted searches and concise edits to prevent history compaction.\n"
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
