"""Reproduction and boundary tests for SessionStore hardening (SF-SESS-02)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfforge_agent.runtime.session import (
    SessionStore,
)


class TestSessionAtomicSnapshotAndExport:
    """SF-SESS-02-A1: Atomic snapshot export via tempfile and replace."""

    def test_export_snapshot_creates_atomic_file(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessatomic01")
        store.record_message({"role": "user", "content": "hello world"})
        store.record_status("completed", detail="all tests passed")

        export_target = tmp_path / "exports" / "snapshot.json"
        result_path = store.export_snapshot(export_target)
        assert result_path == export_target
        assert export_target.is_file()

        data = json.loads(export_target.read_text(encoding="utf-8"))
        assert data["session_id"] == "sessatomic01"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["content"] == "hello world"
        assert data["status"] == "completed"

    def test_export_snapshot_rejects_symlink_target_and_parents(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessatomic02")
        store.record_message({"role": "user", "content": "test"})

        # Target file is a symlink
        real_file = tmp_path / "real.json"
        real_file.write_text("{}")
        symlink_target = tmp_path / "symlink.json"
        symlink_target.symlink_to(real_file)

        with pytest.raises(ValueError, match="(?i)symlink"):
            store.export_snapshot(symlink_target)

    def test_export_trace_creates_atomic_file(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessatomic03")
        store.trace("step_1", detail="init")
        store.trace("step_2", detail="done")

        trace_target = tmp_path / "exports" / "trace.json"
        result_path = store.export_trace(trace_target)
        assert result_path == trace_target
        assert trace_target.is_file()

        lines = [json.loads(line) for line in trace_target.read_text().splitlines() if line.strip()]
        assert len(lines) == 2
        assert lines[0]["event"] == "step_1"


class TestTraceAndSessionRedaction:
    """SF-SESS-02-A2: Automatic redaction of sensitive credentials, tokens, and local paths."""

    def test_redact_sensitive_text_masks_credentials(self, tmp_path: Path):
        from selfforge_agent.runtime.session import redact_sensitive_text

        raw_text = (
            "API key sk-abcdef1234567890abcdef1234567890 and "
            "GitHub token ghp_1234567890abcdef1234567890 and "
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz and "
            "clone url https://username:supersecretpassword@github.com/repo.git"
        )
        redacted = redact_sensitive_text(raw_text)
        assert "sk-abcdef1234567890abcdef1234567890" not in redacted
        assert "ghp_1234567890abcdef1234567890" not in redacted
        assert "supersecretpassword" not in redacted
        assert "sk-***" in redacted or "***" in redacted

    def test_redact_sensitive_text_masks_local_root_path(self, tmp_path: Path):
        from selfforge_agent.runtime.session import redact_sensitive_text

        local_path = str(tmp_path / "subfolder" / "secret.py")
        redacted = redact_sensitive_text(f"File at {local_path}", root=tmp_path)
        assert str(tmp_path) not in redacted
        assert "[ROOT]/subfolder/secret.py" in redacted or "~/subfolder/secret.py" in redacted

    def test_trace_automatically_masks_sensitive_fields(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sesstrace01")
        store.trace(
            "model_call",
            api_key="sk-secret1234567890abcdef1234567890",
            token="ghp_token1234567890abcdef1234567890",
            safe_param="value_123",
        )
        events = store.trace_events()
        assert len(events) == 1
        ev = events[0]
        assert ev.get("safe_param") == "value_123"
        assert ev.get("api_key") != "sk-secret1234567890abcdef1234567890"
        assert ev.get("token") != "ghp_token1234567890abcdef1234567890"

    def test_export_snapshot_redacts_credentials_by_default(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessredact01")
        store.record_message({
            "role": "user",
            "content": "My secret key is sk-live99999999999999999999999999",
        })
        target = tmp_path / "redacted_snapshot.json"
        store.export_snapshot(target, redact=True)

        data = json.loads(target.read_text())
        assert "sk-live99999999999999999999999999" not in data["messages"][0]["content"]


class TestSessionDiskQuotaAndEventBounds:
    """SF-SESS-02-A3: Disk quota defense and session event limit bounding."""

    def test_append_enforces_max_session_events(self, tmp_path: Path, monkeypatch):
        import selfforge_agent.runtime.session as session_mod
        monkeypatch.setattr(session_mod, "MAX_SESSION_EVENTS", 3)

        store = SessionStore(tmp_path, "sessevents01")
        store.record_message({"role": "user", "content": "1"})
        store.record_message({"role": "user", "content": "2"})
        store.record_message({"role": "user", "content": "3"})

        with pytest.raises(ValueError, match="(?i)maximum event limit"):
            store.record_message({"role": "user", "content": "4"})

    def test_trace_enforces_max_trace_bytes_quota(self, tmp_path: Path, monkeypatch):
        import selfforge_agent.runtime.session as session_mod
        monkeypatch.setattr(session_mod, "MAX_TRACE_BYTES", 200)

        store = SessionStore(tmp_path, "sesstracequota01")
        # Write small trace events until max is reached
        for i in range(10):
            store.trace("evt", msg=f"data_{i}")

        # Size should not exceed MAX_TRACE_BYTES
        assert store.trace_path.stat().st_size <= 200

    def test_trace_events_enforces_max_query_limit(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sesstracelimit01")
        with pytest.raises(ValueError, match="(?i)limit exceeds maximum"):
            store.trace_events(limit=20_000)


class TestCorruptedSessionDegradationAndRecovery:
    """SF-SESS-02-A4: Graceful degradation and recovery on corrupted session events."""

    def test_load_with_recover_true_skips_malformed_json_lines(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessrecover01")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(
            b'{"type": "message", "message": {"role": "user", "content": "valid 1"}}\n'
            b'{malformed-json-broken-record\n'
            b'{"type": "message", "message": {"role": "assistant", "content": "valid 2"}}\n'
            b'{"type": "status", "status": "finished"}\n'
        )

        # recover=False fails closed (SF-SESS-01 invariant preserved)
        with pytest.raises(json.JSONDecodeError):
            store.load(recover=False)

        # recover=True recovers valid events and latest status
        messages, status = store.load(recover=True)
        assert len(messages) == 2
        assert messages[0]["content"] == "valid 1"
        assert messages[1]["content"] == "valid 2"
        assert status == "finished"

    def test_recover_session_returns_diagnostics(self, tmp_path: Path):
        store = SessionStore(tmp_path, "sessrecover02")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(
            b'{"type": "message", "message": {"role": "user", "content": "msg"}}\n'
            b'bad-corrupt-line-1\n'
            b'bad-corrupt-line-2\n'
            b'{"type": "status", "status": "recovered_status"}\n'
        )
        messages, status, corrupt_count = store.recover_session()
        assert len(messages) == 1
        assert status == "recovered_status"
        assert corrupt_count == 2

    def test_session_corrupt_error_type(self):
        from selfforge_agent.runtime.session import SessionCorruptError
        assert issubclass(SessionCorruptError, ValueError)
