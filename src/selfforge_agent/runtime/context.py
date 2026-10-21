"""Bound model context while keeping the task and important constraints."""

import json


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
        anchors = [system, users[0]]
        if users[-1] is not users[0]:
            anchors.append(users[-1])
        if self.estimate(anchors) >= self.token_budget:
            raise ValueError("Task and constraints exceed context budget")
        digest = []
        for item in messages:
            if item in anchors:
                continue
            role = item.get("role")
            if role == "assistant" and item.get("tool_calls"):
                names = [call.get("function", {}).get("name", "?")
                         for call in item["tool_calls"]]
                digest.append("Called: " + ", ".join(names))
            elif role == "tool":
                digest.append("Tool result: " + str(item.get("content", ""))[:450])
            elif role == "assistant":
                digest.append("Assistant: " + str(item.get("content", ""))[:300])
        summary = "Earlier steps (compressed; preserve original task constraints):\n" + "\n".join(digest)
        allowed = max(200, (self.token_budget - self.estimate(anchors)) * 3)
        compacted = [anchors[0], anchors[1], {"role": "assistant", "content": summary[-allowed:]}]
        if len(anchors) == 3:
            compacted.append(anchors[2])
        while self.estimate(compacted) > self.token_budget and allowed > 100:
            allowed //= 2
            compacted[2]["content"] = summary[-allowed:]
        if self.estimate(compacted) > self.token_budget:
            raise ValueError("Task and compressed history exceed context budget")
        return compacted, True
