"""Bounded native tool-calling loop shared by coding workflows."""

import json
import threading
import time
from dataclasses import dataclass
from typing import Literal

from selfforge_agent.runtime.context import ContextManager
from selfforge_agent.runtime.model import ChatModel
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry

Status = Literal["completed", "unverified", "failed", "max_steps", "timed_out", "cancelled"]

SYSTEM_PROMPT = """You are selfForge Agent, a Python coding agent.
Inspect the workspace with search_code and read_file, then make small exact edits with edit_file.
Run run_tests after your final edit and use its exit status as evidence. Do not claim tests passed
unless the tool reported exit=0. Commands execute in a configured sandbox or explicitly trusted host.
Do not access paths outside the workspace. When finished, explain edits and verification briefly.
If a tool fails, inspect its result and recover or report the failure. Never invent tool results."""


@dataclass(frozen=True)
class AgentResult:
    session_id: str
    status: Status
    answer: str
    steps: int
    tools_used: int
    error: str = ""


class CodingAgent:
    def __init__(self, model: ChatModel, tools: ToolRegistry, session: SessionStore,
                 *, max_steps: int = 12, time_budget: float = 300,
                 context_budget: int = 8_000, cancel: threading.Event | None = None) -> None:
        if not 1 <= max_steps <= 100 or time_budget <= 0:
            raise ValueError("Positive time budget and 1-100 steps are required")
        self.model = model
        self.tools = tools
        self.session = session
        self.max_steps = max_steps
        self.time_budget = time_budget
        self.context = ContextManager(context_budget)
        self.cancel = cancel or threading.Event()

    def _append(self, messages: list[dict], message: dict) -> None:
        messages.append(message)
        self.session.record_message(message)

    def _finish(self, status: Status, answer: str, steps: int, tools_used: int,
                error: str = "") -> AgentResult:
        self.session.record_status(status, detail=error)
        return AgentResult(self.session.session_id, status, answer, steps, tools_used, error)

    def _resume_pending(self, messages: list[dict]) -> None:
        last_assistant = next((index for index in range(len(messages) - 1, -1, -1)
                               if messages[index].get("role") == "assistant"), None)
        if last_assistant is None:
            return
        calls = messages[last_assistant].get("tool_calls") or []
        existing = {item.get("tool_call_id") for item in messages[last_assistant + 1:]
                    if item.get("role") == "tool"}
        for call in calls:
            if call.get("id") not in existing:
                self._append(messages, {"role": "tool", "tool_call_id": call.get("id"),
                                        "content": "Previous run was interrupted before this tool completed"})

    def run(self, task: str = "", *, resume: bool = False) -> AgentResult:
        if resume:
            messages, _ = self.session.load()
            self._resume_pending(messages)
            if task.strip():
                self._append(messages, {"role": "user", "content": task.strip()})
        else:
            if not task.strip() or self.session.path.exists():
                raise ValueError("New sessions need a task and unused session ID")
            messages = []
            self._append(messages, {"role": "system", "content": SYSTEM_PROMPT})
            self._append(messages, {"role": "user", "content": task.strip()})
        self.session.record_status("running")
        deadline = time.monotonic() + self.time_budget
        tools_used = 0
        tests_passed = False
        try:
            for step in range(1, self.max_steps + 1):
                if self.cancel.is_set():
                    return self._finish("cancelled", "", step - 1, tools_used)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._finish("timed_out", "", step - 1, tools_used)
                messages, compacted = self.context.compact(messages)
                if compacted:
                    self.session.record_compaction(messages)
                started = time.monotonic()
                turn = self.model.complete(messages, self.tools.schemas(), min(45, remaining))
                self.session.trace("model_call", provider=turn.provider, model=turn.model,
                                   duration_seconds=round(time.monotonic() - started, 3),
                                   usage=turn.usage.model_dump() if turn.usage else None,
                                   finish_reason=turn.finish_reason)
                self._append(messages, turn.message)
                calls = turn.message.get("tool_calls") or []
                if not calls:
                    if turn.finish_reason not in {"stop", "end_turn"}:
                        return self._finish("failed", "", step, tools_used,
                                            f"Model stopped with {turn.finish_reason}")
                    status: Status = "completed" if tests_passed else "unverified"
                    return self._finish(status, str(turn.message.get("content") or ""),
                                        step, tools_used)
                for call in calls:
                    if self.cancel.is_set():
                        return self._finish("cancelled", "", step, tools_used)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._finish("timed_out", "", step, tools_used)
                    function = call.get("function") or {}
                    name = function.get("name", "")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise TypeError("Tool arguments must be an object")
                        if name in {"bash", "run_tests"}:
                            arguments["timeout"] = min(float(arguments.get("timeout", 20)), remaining)
                        outcome = self.tools.execute(name, arguments)
                    except (ValueError, TypeError) as exc:
                        from selfforge_agent.runtime.tools import ToolOutcome
                        outcome = ToolOutcome(f"Tool arguments error: {exc}", False)
                    tools_used += 1
                    if name == "edit_file" and outcome.ok:
                        tests_passed = False
                    elif name == "run_tests" and outcome.ok:
                        tests_passed = True
                    self.session.trace("tool", name=name, ok=outcome.ok,
                                       duration_seconds=round(outcome.elapsed_seconds, 3))
                    self._append(messages, {"role": "tool", "tool_call_id": call.get("id"),
                                            "content": outcome.content})
            return self._finish("max_steps", "", self.max_steps, tools_used)
        except KeyboardInterrupt:
            return self._finish("cancelled", "", 0, tools_used)
        except (RuntimeError, ValueError, OSError) as exc:
            self.session.trace("error", error_type=type(exc).__name__, message=str(exc)[:300])
            return self._finish("failed", "", 0, tools_used, str(exc))
