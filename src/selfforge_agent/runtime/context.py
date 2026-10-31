"""Bound model context with heuristic token estimates and structured history compaction."""

import json
import re

_SUMMARY_PREFIX = "Earlier steps (compressed; preserve original task constraints):\n"

_TOKEN_PATTERN = re.compile(
    r"\s*(?:==|!=|<=|>=|->|::|\+\+|--|\+=|-=|\*=|/=|&&|\|\||[a-zA-Z0-9_]+|[^\x00-\x7F]|[^\s\w])",
)


def estimate_tokens(text: str) -> int:
    """Estimate token count without claiming model-specific tokenizer accuracy."""
    if not text:
        return 0
    tokens = 0
    last_end = 0
    for match in _TOKEN_PATTERN.finditer(text):
        tokens += (match.start() - last_end + 3) // 4
        last_end = match.end()
        token_str = match.group(0)
        # Check for multi-space indentation
        leading_spaces = len(token_str) - len(token_str.lstrip())
        if leading_spaces >= 4:
            tokens += leading_spaces // 4
        stripped = token_str.strip()
        length = len(stripped)
        if not stripped:
            if leading_spaces < 4:
                tokens += 1
            continue
        if any(ord(char) > 127 for char in stripped):
            # CJK / non-ASCII character sequences
            tokens += max(1, int(length * 1.2))
        elif length > 7 and stripped.isascii() and re.fullmatch(r"[A-Za-z0-9_]+", stripped):
            # Long words / compound identifiers: ~4 chars per token
            tokens += max(1, (length + 3) // 4)
        else:
            tokens += 1
    return tokens + (len(text) - last_end + 3) // 4


def _recent_interactions(messages: list[dict], limit: int) -> list[dict]:
    """Select complete assistant/tool blocks after the latest user task."""
    last_user = max(index for index, item in enumerate(messages) if item.get("role") == "user")
    starts = [index for index in range(last_user + 1, len(messages))
              if messages[index].get("role") == "assistant"]
    if not starts or limit <= 0:
        return []
    recent = messages[starts[-min(limit, len(starts))]:]
    pending: set[str] = set()
    for item in recent:
        if item.get("role") == "assistant":
            if pending:
                return []
            calls = item.get("tool_calls") or []
            pending = {call.get("id") for call in calls}
            if len(pending) != len(calls) or any(not isinstance(value, str) for value in pending):
                return []
        elif item.get("role") == "tool":
            call_id = item.get("tool_call_id")
            if call_id not in pending:
                return []
            pending.remove(call_id)
        else:
            return []
    return [] if pending else recent


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens for a message envelope, role, content, and tool calls."""
    # Message formatting envelope overhead (<|start|>role\n...<|end|>)
    overhead = 4
    role = str(message.get("role") or "")
    overhead += estimate_tokens(role)

    content = message.get("content")
    if isinstance(content, str):
        overhead += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            overhead += estimate_tokens(str(part))

    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        overhead += estimate_tokens(reasoning) + 2

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            overhead += 4  # tool call envelope
            func = call.get("function") or {}
            overhead += estimate_tokens(str(func.get("name") or ""))
            overhead += estimate_tokens(str(func.get("arguments") or ""))

    if message.get("tool_call_id"):
        overhead += 3

    return overhead


class ContextManager:
    def __init__(self, token_budget: int = 8_000) -> None:
        if token_budget < 500:
            raise ValueError("Context budget must be at least 500 tokens")
        self.token_budget = token_budget

    @staticmethod
    def estimate(messages: list[dict]) -> int:
        """Estimate total tokens across messages with framing overhead."""
        return sum(estimate_message_tokens(item) for item in messages) + 3

    def compact(
        self,
        messages: list[dict],
        *,
        reserve_tokens: int = 0,
        preserve_recent_turns: int = 0,
    ) -> tuple[list[dict], bool]:
        """Compact conversation history while preserving system instructions, user tasks,

        and recent interaction turns when budget permits.
        """
        if not 0 <= reserve_tokens < self.token_budget:
            raise ValueError("Reserved tokens must leave a positive context budget")
        budget = self.token_budget - reserve_tokens
        if self.estimate(messages) <= budget:
            return messages, False

        system = next((item for item in messages if item.get("role") == "system"), None)
        users = [item for item in messages if item.get("role") == "user"]
        if system is None or not users:
            raise ValueError("Session lacks a system prompt or task")
        anchors = [system, *users]
        if self.estimate(anchors) >= budget:
            raise ValueError("Task and constraints exceed context budget")

        intermediates = [item for item in messages if not any(item is anchor for anchor in anchors)]

        recent_preserved: list[dict] = []
        older_to_summarize = intermediates

        if preserve_recent_turns > 0:
            turn_slice = _recent_interactions(messages, preserve_recent_turns)
            test_compacted = [anchors[0], {"role": "system", "content": _SUMMARY_PREFIX}, *anchors[1:], *turn_slice]
            if self.estimate(test_compacted) + 50 <= budget:
                recent_preserved = turn_slice
                older_to_summarize = [item for item in intermediates
                                      if not any(item is kept for kept in recent_preserved)]

        digest: list[str] = []
        for item in older_to_summarize:
            role = item.get("role")
            if role == "system" and str(item.get("content", "")).startswith(_SUMMARY_PREFIX):
                digest.append(str(item["content"])[len(_SUMMARY_PREFIX):])
            elif role == "assistant" and item.get("tool_calls"):
                actions = []
                for call in item["tool_calls"]:
                    func = call.get("function") or {}
                    name = func.get("name", "?")
                    args_raw = func.get("arguments") or ""
                    try:
                        args = json.loads(args_raw) if args_raw else {}
                    except (ValueError, TypeError):
                        args = {}
                    if name == "edit_file" and isinstance(args, dict) and "path" in args:
                        actions.append(f"Edited file: {args['path']}")
                    elif name == "read_file" and isinstance(args, dict) and "path" in args:
                        actions.append(f"Read file: {args['path']}")
                    elif name == "search_code" and isinstance(args, dict) and "pattern" in args:
                        actions.append(f"Searched code for: {args['pattern']}")
                    elif name == "run_tests":
                        actions.append("Executed test runner")
                    else:
                        actions.append(f"Called: {name}")
                digest.append("; ".join(actions))
            elif role == "tool":
                content = str(item.get("content", ""))
                first_line = content.splitlines()[0] if content.splitlines() else ""
                if "passed" in content or "failed" in content or "error" in content:
                    digest.append(f"Test result: {first_line[:200]}")
                else:
                    digest.append("Tool result: " + content[:300])
            elif role == "assistant":
                content = str(item.get("content", ""))
                if content:
                    digest.append("Assistant: " + content[:200])

        summary_body = "\n".join(digest)
        base_overhead = self.estimate([anchors[0], *anchors[1:], *recent_preserved])
        allowed = max(100, (budget - base_overhead) * 3)

        def bounded_summary(limit: int) -> str:
            body_limit = max(0, limit - len(_SUMMARY_PREFIX))
            return _SUMMARY_PREFIX + (summary_body[-body_limit:] if body_limit else "")

        compacted = [
            anchors[0],
            {"role": "system", "content": bounded_summary(allowed)},
            *anchors[1:],
            *recent_preserved,
        ]

        while self.estimate(compacted) > budget and allowed > 50:
            allowed //= 2
            compacted[1]["content"] = bounded_summary(allowed)

        # If still exceeding budget, drop recent_preserved and retry
        if self.estimate(compacted) > budget and recent_preserved:
            return self.compact(messages, reserve_tokens=reserve_tokens, preserve_recent_turns=0)

        if self.estimate(compacted) > budget:
            raise ValueError("Task and compressed history exceed context budget")
        return compacted, True
