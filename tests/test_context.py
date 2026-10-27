from pathlib import Path

import pytest

from selfforge_agent.runtime.context import ContextManager
from selfforge_agent.runtime.session import SessionStore


def test_compaction_keeps_every_user_instruction_across_resume(tmp_path: Path) -> None:
    manager = ContextManager(500)
    users = [
        {"role": "user", "content": "Fix the parser"},
        {"role": "user", "content": "Do not change the public API"},
        {"role": "user", "content": "Add a regression test"},
    ]
    messages = [
        {"role": "system", "content": "You are a coding agent"},
        users[0],
        {"role": "assistant", "content": "Investigating " + "x" * 2_000},
        users[1],
        {"role": "tool", "content": "y" * 3_000},
        users[2],
    ]

    compacted, changed = manager.compact(messages)
    assert changed
    assert [item for item in compacted if item["role"] == "user"] == users
    assert manager.estimate(compacted) <= manager.token_budget

    session = SessionStore(tmp_path)
    session.record_compaction(compacted)
    restored, _ = SessionStore(tmp_path, session.session_id).load()
    again, changed = manager.compact(restored + [
        {"role": "assistant", "content": "More investigation " + "z" * 3_000},
    ])
    assert changed
    session.record_compaction(again)
    persisted, _ = SessionStore(tmp_path, session.session_id).load()
    assert [item for item in persisted if item["role"] == "user"] == users
    assert manager.estimate(persisted) <= manager.token_budget


def test_compaction_fails_when_user_instructions_exceed_budget() -> None:
    messages = [
        {"role": "system", "content": "You are a coding agent"},
        {"role": "user", "content": "Fix the parser"},
        {"role": "assistant", "content": "x" * 3_000},
        {"role": "user", "content": "Do not change " + "public API " * 250},
        {"role": "user", "content": "Add a test"},
    ]
    with pytest.raises(ValueError, match="Task and constraints exceed context budget"):
        ContextManager(500).compact(messages)
