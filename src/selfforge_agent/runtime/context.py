"""Bound model context with heuristic token estimates and structured history compaction."""

import json
import re
from typing import Any

from pydantic import BaseModel, Field

MAX_CONTEXT_BUDGET = 2_000_000
MAX_ESTIMATE_CHARS = 4 * 1024 * 1024
MAX_RECURSION_DEPTH = 5
_SUMMARY_PREFIX = "Earlier steps (compressed; preserve original task constraints):\n"

_TOKEN_PATTERN = re.compile(
    r"\s*(?:==|!=|<=|>=|->|::|\+\+|--|\+=|-=|\*=|/=|&&|\|\||[a-zA-Z0-9_]+|[^\x00-\x7F]|[^\s\w])",
)
_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class ContextMetrics(BaseModel):
    """Metrics evaluating current context token usage against budget."""

    total_messages: int = Field(..., ge=0)
    estimated_tokens: int = Field(..., ge=0)
    token_budget: int = Field(..., ge=500, le=MAX_CONTEXT_BUDGET)
    reserve_tokens: int = Field(default=0, ge=0)
    utilization: float = Field(..., ge=0.0)
    is_under_pressure: bool = False
    was_compacted: bool = False


class CompactionReport(BaseModel):
    """Formal outcome and accounting of a conversation compaction operation."""

    was_compacted: bool
    original_messages_count: int = Field(..., ge=0)
    compacted_messages_count: int = Field(..., ge=0)
    original_tokens: int = Field(..., ge=0)
    compacted_tokens: int = Field(..., ge=0)
    tokens_saved: int = Field(..., ge=0)
    preserved_turns: int = Field(default=0, ge=0)
    summary_tokens: int = Field(default=0, ge=0)


def estimate_tokens(text: str) -> int:
    """Estimate token count without claiming model-specific tokenizer accuracy."""
    if text is None:
        return 0
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")
    if "\0" in text:
        raise ValueError("text must not contain null bytes")
    if len(text) > MAX_ESTIMATE_CHARS:
        raise ValueError(
            f"text exceeds maximum token estimation limit of {MAX_ESTIMATE_CHARS} characters"
        )
    if not text:
        return 0
    tokens = 0
    last_end = 0
    for match in _TOKEN_PATTERN.finditer(text):
        tokens += (match.start() - last_end + 3) // 4
        last_end = match.end()
        token_str = match.group(0)
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
            tokens += max(1, int(length * 1.2))
        elif length > 7 and stripped.isascii() and re.fullmatch(r"[A-Za-z0-9_]+", stripped):
            tokens += max(1, (length + 3) // 4)
        else:
            tokens += 1
    return max(0, tokens + (len(text) - last_end + 3) // 4)


def _estimate_content_tokens(content: Any, depth: int = 0) -> int:
    if depth > MAX_RECURSION_DEPTH:
        return estimate_tokens(str(content)[:1000])
    if isinstance(content, str):
        return estimate_tokens(content)
    elif isinstance(content, list):
        return sum(_estimate_content_tokens(part, depth + 1) for part in content)
    elif isinstance(content, dict):
        total = 0
        part_text = content.get("text")
        if isinstance(part_text, str):
            total += estimate_tokens(part_text)
        else:
            for k, v in content.items():
                total += estimate_tokens(str(k)[:100]) + _estimate_content_tokens(v, depth + 1)
        return total
    elif content is not None:
        return estimate_tokens(str(content))
    return 0


def _recent_interactions(messages: list[dict], limit: int) -> list[dict]:
    """Select complete assistant/tool blocks after the latest user task."""
    if not isinstance(messages, list) or type(limit) is not int or limit <= 0:
        return []
    effective_limit = min(limit, 1000)
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
    recent = messages[starts[-min(effective_limit, len(starts))]:]
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
            seen_calls_in_msg = set()
            for call in calls:
                if not isinstance(call, dict):
                    return []
                function = call.get("function")
                if (
                    call.get("type", "function") != "function"
                    or not isinstance(function, dict)
                    or not isinstance(function.get("name"), str)
                    or not function["name"]
                    or not isinstance(function.get("arguments"), str)
                ):
                    return []
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id:
                    return []
                if call_id in seen_calls_in_msg:
                    return []
                seen_calls_in_msg.add(call_id)
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

    role_raw = message.get("role")
    if role_raw is not None and not isinstance(role_raw, str):
        raise TypeError(f"role must be a string, got {type(role_raw).__name__}")
    if isinstance(role_raw, str) and (not role_raw.strip() or "\0" in role_raw or len(role_raw) > 64):
        raise ValueError(f"Invalid role in message: {role_raw!r}")

    role = str(role_raw or "")
    overhead = 4 + estimate_tokens(role)

    content = message.get("content")
    overhead += _estimate_content_tokens(content)

    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        if "\0" in reasoning:
            raise ValueError("reasoning_content must not contain null bytes")
        overhead += estimate_tokens(reasoning) + 2
    elif reasoning is not None:
        overhead += estimate_tokens(str(reasoning)[:MAX_ESTIMATE_CHARS]) + 2

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            overhead += 4
            if not isinstance(call, dict):
                continue
            func = call.get("function")
            if isinstance(func, dict):
                name = func.get("name")
                if name is not None:
                    overhead += estimate_tokens(str(name)[:256])
                args = func.get("arguments")
                if args is not None:
                    overhead += estimate_tokens(str(args)[:MAX_ESTIMATE_CHARS])
            elif func is not None:
                overhead += estimate_tokens(str(func)[:MAX_ESTIMATE_CHARS])

    if message.get("tool_call_id"):
        overhead += 3

    return max(0, overhead)


class ContextManager:
    """Bounded conversation context manager with token budgeting and compaction."""

    def __init__(self, token_budget: int = 8_000) -> None:
        if (
            type(token_budget) is not int
            or token_budget < 500
            or token_budget > MAX_CONTEXT_BUDGET
        ):
            raise ValueError(
                f"Context budget must be an integer of at least 500 (between 500 and {MAX_CONTEXT_BUDGET} tokens)"
            )
        self.token_budget = token_budget

    @staticmethod
    def estimate(messages: list[dict]) -> int:
        """Estimate total tokens across messages with framing overhead."""
        if not isinstance(messages, list):
            raise TypeError(f"messages must be a list, got {type(messages).__name__}")
        return sum(estimate_message_tokens(item) for item in messages) + 3

    def get_metrics(self, messages: list[dict], reserve_tokens: int = 0) -> ContextMetrics:
        """Calculate current context metrics and pressure against budget."""
        if not isinstance(messages, list):
            raise TypeError(f"messages must be a list, got {type(messages).__name__}")
        if type(reserve_tokens) is not int or not (0 <= reserve_tokens < self.token_budget):
            raise ValueError("Reserved tokens must leave a positive context budget")

        est = self.estimate(messages)
        utilization = round(est / self.token_budget, 4)
        was_compacted = any(
            isinstance(m, dict)
            and m.get("role") == "system"
            and str(m.get("content", "")).startswith(_SUMMARY_PREFIX)
            for m in messages
        )
        is_under_pressure = (est + reserve_tokens) >= int(self.token_budget * 0.75) or was_compacted
        return ContextMetrics(
            total_messages=len(messages),
            estimated_tokens=est,
            token_budget=self.token_budget,
            reserve_tokens=reserve_tokens,
            utilization=utilization,
            is_under_pressure=is_under_pressure,
            was_compacted=was_compacted,
        )

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
        compacted, report = self.compact_with_report(
            messages,
            reserve_tokens=reserve_tokens,
            preserve_recent_turns=preserve_recent_turns,
        )
        return compacted, report.was_compacted

    def compact_with_report(
        self,
        messages: list[dict],
        *,
        reserve_tokens: int = 0,
        preserve_recent_turns: int = 0,
    ) -> tuple[list[dict], CompactionReport]:
        """Compact context and return structured CompactionReport."""
        if not isinstance(messages, list):
            raise TypeError(f"messages must be a list, got {type(messages).__name__}")
        for i, item in enumerate(messages):
            if not isinstance(item, dict):
                raise TypeError(f"message at index {i} must be a dict, got {type(item).__name__}")

        if type(reserve_tokens) is not int or not (0 <= reserve_tokens < self.token_budget):
            raise ValueError("Reserved tokens must leave a positive context budget")

        if type(preserve_recent_turns) is not int or preserve_recent_turns < 0:
            raise ValueError("preserve_recent_turns must be a non-negative integer")

        orig_tokens = self.estimate(messages)
        budget = self.token_budget - reserve_tokens
        if orig_tokens <= budget:
            report = CompactionReport(
                was_compacted=False,
                original_messages_count=len(messages),
                compacted_messages_count=len(messages),
                original_tokens=orig_tokens,
                compacted_tokens=orig_tokens,
                tokens_saved=0,
                preserved_turns=preserve_recent_turns,
                summary_tokens=0,
            )
            return messages, report

        systems = [
            item for item in messages
            if item.get("role") == "system"
            and not str(item.get("content", "")).startswith(_SUMMARY_PREFIX)
        ]
        users = [item for item in messages if item.get("role") == "user"]
        if not systems or not users:
            raise ValueError("Session lacks a system prompt or task")

        anchors = [*systems, *users]
        if self.estimate(anchors) >= budget:
            raise ValueError("Task and constraints exceed context budget")

        intermediates = [item for item in messages if not any(item is anchor for anchor in anchors)]

        recent_preserved: list[dict] = []
        older_to_summarize = intermediates

        if preserve_recent_turns > 0:
            turn_slice = _recent_interactions(messages, preserve_recent_turns)
            if turn_slice:
                test_compacted = [*systems, {"role": "system", "content": _SUMMARY_PREFIX}, *users, *turn_slice]
                if self.estimate(test_compacted) + 50 <= budget:
                    recent_preserved = turn_slice
                    older_to_summarize = [
                        item for item in intermediates
                        if not any(item is kept for kept in recent_preserved)
                    ]

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
                        elif name == "write_file" and "path" in args:
                            actions.append(f"Wrote file: {str(args['path'])[:200]}")
                        elif name == "read_file" and "path" in args:
                            actions.append(f"Read file: {str(args['path'])[:200]}")
                        elif name == "search_code" and "pattern" in args:
                            actions.append(f"Searched code for: {str(args['pattern'])[:200]}")
                        elif name == "run_command" and "command" in args:
                            actions.append(f"Ran command: {str(args['command'])[:200]}")
                        elif name == "run_tests":
                            actions.append("Executed test runner")
                        else:
                            actions.append(f"Called: {name}")
                if actions:
                    digest.append("; ".join(actions))
            elif role == "tool":
                content = str(item.get("content", ""))
                clean_content = _ANSI_PATTERN.sub("", content)
                first_line = clean_content.splitlines()[0] if clean_content.splitlines() else ""
                if any(k in clean_content for k in ("passed", "failed", "error", "FAILED", "ERROR")):
                    digest.append(f"Test result: {first_line[:200]}")
                else:
                    digest.append("Tool result: " + clean_content[:300])
            elif role == "assistant":
                content = str(item.get("content", ""))
                clean_content = _ANSI_PATTERN.sub("", content)
                if clean_content:
                    digest.append("Assistant: " + clean_content[:200])

        summary_body = "\n".join(d for d in digest if d)
        base_overhead = self.estimate([*systems, *users, *recent_preserved])
        allowed = max(100, (budget - base_overhead) * 3)

        def bounded_summary(limit: int) -> str:
            body_limit = max(0, limit - len(_SUMMARY_PREFIX))
            return _SUMMARY_PREFIX + (summary_body[-body_limit:] if body_limit else "")

        summary_msg = {"role": "system", "content": bounded_summary(allowed)}
        compacted = [*systems, summary_msg, *users, *recent_preserved]

        summary_idx = len(systems)
        while self.estimate(compacted) > budget and allowed > 0:
            allowed //= 2
            compacted[summary_idx]["content"] = bounded_summary(allowed)

        if self.estimate(compacted) > budget and recent_preserved:
            return self.compact_with_report(messages, reserve_tokens=reserve_tokens, preserve_recent_turns=0)

        if self.estimate(compacted) > budget:
            raise ValueError("Task and compressed history exceed context budget")

        compacted_tokens = self.estimate(compacted)
        summary_tokens = estimate_message_tokens(compacted[summary_idx])
        tokens_saved = max(0, orig_tokens - compacted_tokens)
        report = CompactionReport(
            was_compacted=True,
            original_messages_count=len(messages),
            compacted_messages_count=len(compacted),
            original_tokens=orig_tokens,
            compacted_tokens=compacted_tokens,
            tokens_saved=tokens_saved,
            preserved_turns=preserve_recent_turns if recent_preserved else 0,
            summary_tokens=summary_tokens,
        )
        return compacted, report


__all__ = [
    "MAX_CONTEXT_BUDGET",
    "MAX_ESTIMATE_CHARS",
    "CompactionReport",
    "ContextManager",
    "ContextMetrics",
    "estimate_message_tokens",
    "estimate_tokens",
]
