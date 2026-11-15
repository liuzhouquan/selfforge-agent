"""Bound model context with heuristic token estimates and structured history compaction."""

import json
import re

_SUMMARY_PREFIX = "Earlier steps (compressed; preserve original task constraints):\n"

_TOKEN_PATTERN = re.compile(
    r"\s*(?:==|!=|<=|>=|->|::|\+\+|--|\+=|-=|\*=|/=|&&|\|\||[a-zA-Z0-9_]+|[^\x00-\x7F]|[^\s\w])",
)


def estimate_tokens(text: str) -> int:
    """Estimate token count without claiming model-specific tokenizer accuracy."""
    if text is None:
        return 0
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
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
    if not isinstance(messages, list) or type(limit) is not int or limit <= 0:
        return []
    user_indices = [
        index for index, item in enumerate(messages)
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    if not user_indices:
        return []
    last_user = max(user_indices)
    starts = [
        index for index in range(last_user + 1, len(messages))
        if isinstance(messages[index], dict) and messages[index].get("role") == "assistant"
    ]
    if not starts:
        return []
    recent = messages[starts[-min(limit, len(starts))]:]
    pending: set[str] = set()
    for item in recent:
        if not isinstance(item, dict):
            return []
        role = item.get("role")
        if role == "assistant":
            if pending:
                return []
            calls = item.get("tool_calls")
            if calls is None:
                continue
            if not isinstance(calls, list):
                return []
            pending = set()
            for call in calls:
                if not isinstance(call, dict):
                    return []
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    return []
                pending.add(call_id)
            if len(pending) != len(calls):
                return []
        elif role == "tool":
            call_id = item.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                return []
            pending.remove(call_id)
        else:
            return []
    return [] if pending else recent


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens for a message envelope, role, content, and tool calls."""
    if not isinstance(message, dict):
        raise TypeError(f"message must be a dict, got {type(message).__name__}")

    # Message formatting envelope overhead (<|start|>role\n...<|end|>)
    overhead = 4
    role = str(message.get("role") or "")
    overhead += estimate_tokens(role)

    content = message.get("content")
    if isinstance(content, str):
        overhead += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                part_text = part.get("text")
                if isinstance(part_text, str):
                    overhead += estimate_tokens(part_text)
                else:
                    overhead += estimate_tokens(str(part))
            else:
                overhead += estimate_tokens(str(part))
    elif content is not None:
        overhead += estimate_tokens(str(content))

    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        overhead += estimate_tokens(reasoning) + 2
    elif reasoning is not None:
        overhead += estimate_tokens(str(reasoning)) + 2

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            overhead += 4  # tool call envelope
            if not isinstance(call, dict):
                continue
            func = call.get("function")
            if isinstance(func, dict):
                name = func.get("name")
                if name is not None:
                    overhead += estimate_tokens(str(name))
                args = func.get("arguments")
                if args is not None:
                    overhead += estimate_tokens(str(args))
            elif func is not None:
                overhead += estimate_tokens(str(func))

    if message.get("tool_call_id"):
        overhead += 3

    return overhead


class ContextManager:
    def __init__(self, token_budget: int = 8_000) -> None:
        if type(token_budget) is not int or token_budget < 500:
            raise ValueError("Context budget must be an integer of at least 500 tokens")
        self.token_budget = token_budget

    @staticmethod
    def estimate(messages: list[dict]) -> int:
        """Estimate total tokens across messages with framing overhead."""
        if not isinstance(messages, list):
            raise TypeError(f"messages must be a list, got {type(messages).__name__}")
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
        if not isinstance(messages, list):
            raise TypeError(f"messages must be a list, got {type(messages).__name__}")
        for i, item in enumerate(messages):
            if not isinstance(item, dict):
                raise TypeError(f"message at index {i} must be a dict, got {type(item).__name__}")

        if type(reserve_tokens) is not int or not (0 <= reserve_tokens < self.token_budget):
            raise ValueError("Reserved tokens must leave a positive context budget")

        if type(preserve_recent_turns) is not int or preserve_recent_turns < 0:
            raise ValueError("preserve_recent_turns must be a non-negative integer")

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
            if turn_slice:
                test_compacted = [anchors[0], {"role": "system", "content": _SUMMARY_PREFIX}, *anchors[1:], *turn_slice]
                if self.estimate(test_compacted) + 50 <= budget:
                    recent_preserved = turn_slice
                    older_to_summarize = [item for item in intermediates
                                          if not any(item is kept for kept in recent_preserved)]

        digest: list[str] = []
        for item in older_to_summarize:
            role = item.get("role")
            if role == "system" and str(item.get("content", "")).startswith(_SUMMARY_PREFIX):
                content_str = str(item.get("content", ""))
                digest.append(content_str[len(_SUMMARY_PREFIX):])
            elif role == "assistant" and item.get("tool_calls"):
                actions = []
                calls = item.get("tool_calls")
                if isinstance(calls, list):
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        func = call.get("function")
                        if not isinstance(func, dict):
                            func = {}
                        name = str(func.get("name") or "?")[:100]
                        args_raw = func.get("arguments")
                        args = {}
                        if isinstance(args_raw, str) and args_raw:
                            try:
                                parsed = json.loads(args_raw)
                                if isinstance(parsed, dict):
                                    args = parsed
                            except (ValueError, TypeError):
                                args = {}
                        elif isinstance(args_raw, dict):
                            args = args_raw

                        if name == "edit_file" and "path" in args:
                            actions.append(f"Edited file: {str(args['path'])[:200]}")
                        elif name == "read_file" and "path" in args:
                            actions.append(f"Read file: {str(args['path'])[:200]}")
                        elif name == "search_code" and "pattern" in args:
                            actions.append(f"Searched code for: {str(args['pattern'])[:200]}")
                        elif name == "run_tests":
                            actions.append("Executed test runner")
                        else:
                            actions.append(f"Called: {name}")
                if actions:
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

        summary_body = "\n".join(d for d in digest if d)
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

        while self.estimate(compacted) > budget and allowed > 0:
            allowed //= 2
            compacted[1]["content"] = bounded_summary(allowed)

        # If still exceeding budget, drop recent_preserved and retry
        if self.estimate(compacted) > budget and recent_preserved:
            return self.compact(messages, reserve_tokens=reserve_tokens, preserve_recent_turns=0)

        if self.estimate(compacted) > budget:
            raise ValueError("Task and compressed history exceed context budget")
        return compacted, True
