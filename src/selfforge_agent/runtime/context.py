"""Bound model context while keeping the task and important constraints."""

import json

_SUMMARY_PREFIX = "Earlier steps (compressed; preserve original task constraints):\n"


class ContextManager:
    def __init__(self, token_budget: int = 8_000) -> None:
        if token_budget < 500:
            raise ValueError("Context budget must be at least 500 tokens")
        self.token_budget = token_budget

    @staticmethod
    def estimate(messages: list[dict]) -> int:
        return sum((len(json.dumps(item, ensure_ascii=False)) + 3) // 4 for item in messages)

    def compact(self, messages: list[dict]) -> tuple[list[dict], bool]:
        if self.estimate(messages) <= self.token_budget:
            return messages, False
        system = next((item for item in messages if item.get("role") == "system"), None)
        users = [item for item in messages if item.get("role") == "user"]
        if system is None or not users:
            raise ValueError("Session lacks a system prompt or task")
        anchors = [system, *users]
        if self.estimate(anchors) >= self.token_budget:
            raise ValueError("Task and constraints exceed context budget")
        digest = []
        for item in messages:
            if any(item is anchor for anchor in anchors):
                continue
            role = item.get("role")
            if role == "system" and str(item.get("content", "")).startswith(_SUMMARY_PREFIX):
                digest.append(str(item["content"])[len(_SUMMARY_PREFIX):])
            elif role == "assistant" and item.get("tool_calls"):
                names = [call.get("function", {}).get("name", "?")
                         for call in item["tool_calls"]]
                digest.append("Called: " + ", ".join(names))
            elif role == "tool":
                digest.append("Tool result: " + str(item.get("content", ""))[:450])
            elif role == "assistant":
                digest.append("Assistant: " + str(item.get("content", ""))[:300])
        summary_body = "\n".join(digest)
        allowed = max(200, (self.token_budget - self.estimate(anchors)) * 3)
        def bounded_summary(limit: int) -> str:
            body_limit = max(0, limit - len(_SUMMARY_PREFIX))
            return _SUMMARY_PREFIX + (summary_body[-body_limit:] if body_limit else "")

        compacted = [anchors[0], {"role": "system", "content": bounded_summary(allowed)},
                     *anchors[1:]]
        while self.estimate(compacted) > self.token_budget and allowed > 100:
            allowed //= 2
            compacted[1]["content"] = bounded_summary(allowed)
        if self.estimate(compacted) > self.token_budget:
            raise ValueError("Task and compressed history exceed context budget")
        return compacted, True
