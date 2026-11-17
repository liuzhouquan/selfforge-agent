"""Security and lifecycle hardening reproduction tests for SessionStore (SF-SESS-01)."""

import json
import os
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.runtime.session import SessionStore


class TestSessionStoreInitAndGating:
    """SF-SESS-01-A1: Constructor and session identifier security gating."""

    def test_init_rejects_non_path_root(self):
        with pytest.raises((TypeError, ValueError)):
            SessionStore(123)

        with pytest.raises((TypeError, ValueError)):
            SessionStore(None)

    def test_init_rejects_file_as_root(self, tmp_path):
        file_root = tmp_path / "not_a_dir.txt"
        file_root.write_text("hello")
        with pytest.raises(ValueError):
            SessionStore(file_root)

    def test_init_rejects_non_string_session_id(self, tmp_path):
        with pytest.raises((TypeError, ValueError)):
            SessionStore(tmp_path, 12345)

        with pytest.raises((TypeError, ValueError)):
            SessionStore(tmp_path, ["invalid"])

    def test_init_rejects_empty_or_whitespace_session_id(self, tmp_path):
        with pytest.raises(ValueError):
            SessionStore(tmp_path, "")

        with pytest.raises(ValueError):
            SessionStore(tmp_path, "   ")

    def test_init_rejects_non_alphanumeric_or_traversal_session_id(self, tmp_path):
        for invalid in ["../escape", "sess/sub", "sess\0id", "sess id", "sess-id!", ""]:
            with pytest.raises(ValueError):
                SessionStore(tmp_path, invalid)

    def test_init_rejects_oversized_session_id(self, tmp_path):
        with pytest.raises(ValueError, match="[Ll]ength|[Ss]ession"):
            SessionStore(tmp_path, "a" * 128)


class TestAppendAndLockSafety:
    """SF-SESS-01-A2: Event appending, file locking, symlink rejection, and serialization defense."""

    def test_append_rejects_non_dict_event(self, tmp_path):
        store = SessionStore(tmp_path, "sessappend01")
        with pytest.raises(TypeError):
            store._append(store.path, [1, 2, 3])

        with pytest.raises(TypeError):
            store._append(store.path, "string event")

    def test_append_rejects_unserializable_event(self, tmp_path):
        store = SessionStore(tmp_path, "sessappend02")
        circular = {}
        circular["self"] = circular
        with pytest.raises(ValueError):
            store._append(store.path, circular)

        class Unserializable:
            pass

        with pytest.raises(ValueError):
            store._append(store.path, {"bad": Unserializable()})

    def test_append_rejects_oversized_payload(self, tmp_path, monkeypatch):
        store = SessionStore(tmp_path, "sessappend03")
        import selfforge_agent.runtime.session as session_mod
        monkeypatch.setattr(session_mod, "MAX_SESSION_LINE_BYTES", 64)
        with pytest.raises(ValueError, match="exceeds maximum line bytes"):
            store._append(store.path, {"type": "message", "message": {"text": "x" * 200}})

    def test_append_rejects_symlinked_path(self, tmp_path):
        store = SessionStore(tmp_path, "sessappend04")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "target.txt"
        target.write_text("initial")
        store.path.symlink_to(target)

        with pytest.raises(ValueError, match="symlink"):
            store._append(store.path, {"type": "message", "message": {"text": "hello"}})

    def test_append_rejects_fifo(self, tmp_path):
        store = SessionStore(tmp_path, "sessappend05")
        fifo_path = tmp_path / "test.fifo"
        try:
            os.mkfifo(fifo_path)
        except (AttributeError, OSError):
            pytest.skip("FIFO not supported on this OS")

        with pytest.raises((OSError, ValueError)):
            store._append(fifo_path, {"type": "message", "message": {"text": "test"}})

    def test_record_status_rejects_non_string_or_empty_status(self, tmp_path):
        store = SessionStore(tmp_path, "sessstat01")
        with pytest.raises((TypeError, ValueError)):
            store.record_status(123)

        with pytest.raises((TypeError, ValueError)):
            store.record_status("")

        with pytest.raises((TypeError, ValueError)):
            store.record_status("   ")

    def test_record_verification_rejects_non_bool_passed(self, tmp_path):
        store = SessionStore(tmp_path, "sessverif01")
        with pytest.raises((TypeError, ValueError)):
            store.record_verification(1, workspace_sha256="a" * 64)

        with pytest.raises((TypeError, ValueError)):
            store.record_verification("true", workspace_sha256="a" * 64)

    def test_record_compaction_rejects_non_list_messages(self, tmp_path):
        store = SessionStore(tmp_path, "sesscompact01")
        with pytest.raises((TypeError, ValueError)):
            store.record_compaction("not-a-list")

        with pytest.raises((TypeError, ValueError)):
            store.record_compaction([123, "not-a-dict"])


class TestEventReadingAndSecurity:
    """SF-SESS-01-A3: Safe reading, fail-closed corruption defense, and torn write recovery."""

    def test_complete_events_fails_closed_on_malformed_json(self, tmp_path):
        store = SessionStore(tmp_path, "sessread01")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b'{"type": "message"\n')
        with pytest.raises(json.JSONDecodeError):
            list(store._complete_events(store.path))

    def test_complete_events_fails_closed_on_non_dict_event(self, tmp_path):
        store = SessionStore(tmp_path, "sessread02")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b'[1, 2, 3]\n')
        with pytest.raises((TypeError, ValueError), match="JSON object"):
            list(store._complete_events(store.path))

    def test_complete_events_rejects_symlink(self, tmp_path):
        store = SessionStore(tmp_path, "sessread03")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        sensitive = tmp_path / "sensitive.txt"
        sensitive.write_text('{"type": "secret"}\n')
        store.path.symlink_to(sensitive)

        with pytest.raises(ValueError, match="symlink"):
            list(store._complete_events(store.path))

    def test_complete_events_rejects_fifo(self, tmp_path):
        fifo_path = tmp_path / "read_fifo.jsonl"
        try:
            os.mkfifo(fifo_path)
        except (AttributeError, OSError):
            pytest.skip("FIFO not supported on this OS")

        with pytest.raises(ValueError, match="regular file"):
            list(SessionStore._complete_events(fifo_path))

    def test_complete_events_rejects_oversized_file(self, tmp_path, monkeypatch):
        store = SessionStore(tmp_path, "sessread04")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b'{"type": "msg"}\n')
        import selfforge_agent.runtime.session as session_mod
        monkeypatch.setattr(session_mod, "MAX_SESSION_BYTES", 5)
        with pytest.raises(ValueError, match="exceeds maximum size"):
            list(store._complete_events(store.path))

    def test_complete_events_rejects_oversized_line(self, tmp_path, monkeypatch):
        store = SessionStore(tmp_path, "sessread05")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(b'{"type": "msg"}\n')
        import selfforge_agent.runtime.session as session_mod
        monkeypatch.setattr(session_mod, "MAX_SESSION_LINE_BYTES", 5)
        with pytest.raises(ValueError, match="exceeds maximum size"):
            list(store._complete_events(store.path))

    def test_complete_events_recovers_torn_write_without_raising(self, tmp_path):
        store = SessionStore(tmp_path, "sessread06")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(
            b'{"type": "message", "message": {"text": "first"}}\n'
            b'{"type": "message", "mess'
        )
        events = list(store._complete_events(store.path))
        assert len(events) == 1
        assert events[0]["message"]["text"] == "first"

    def test_load_fails_closed_on_corrupted_session_event(self, tmp_path):
        store = SessionStore(tmp_path, "sessload01")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(
            b'{"type": "message", "message": {"role": "user", "content": "hi"}}\n'
            b'{malformed json line\n'
        )
        with pytest.raises(json.JSONDecodeError):
            store.load()

    def test_load_recovers_after_torn_write_and_sanitizes_entries(self, tmp_path):
        store = SessionStore(tmp_path, "sessload02")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_bytes(
            b'{"type": "message", "message": {"role": "user", "content": "hi"}}\n'
            b'{"type": "compaction", "messages": [{"role": "assistant", "content": "res"}, 123]}\n'
            b'{"type": "status", "status": "running"}\n'
            b'{"incomplete": "torn"'
        )
        messages, status = store.load()
        assert messages == [{"role": "assistant", "content": "res"}]
        assert status == "running"

    def test_trace_events_rejects_invalid_limit(self, tmp_path):
        store = SessionStore(tmp_path, "sesstrace01")
        for invalid in [True, False, 0, -5, "10", 1.5]:
            with pytest.raises((ValueError, TypeError)):
                store.trace_events(limit=invalid)


class TestTelemetryAndMetricsHardening:
    """SF-SESS-01-A4: Telemetry metrics extraction resilience and error isolation."""

    def test_token_metrics_handles_invalid_usage_payloads(self, tmp_path):
        store = SessionStore(tmp_path, "sessmetrics01")
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_bytes(
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n'
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": "invalid_string"}\n'
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}\n'
        )
        metrics = store.token_metrics()
        assert metrics["total_tokens"] == 45
        assert metrics["prompt_tokens"] == 30
        assert metrics["completion_tokens"] == 15
        assert metrics["usage_complete"] is False

    def test_token_metrics_handles_pricing_estimation_failure(self, tmp_path, monkeypatch):
        store = SessionStore(tmp_path, "sessmetrics02")
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_bytes(
            b'{"event": "model_call", "model": "crashing_model", "provider": "p", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n'
        )

        def bad_estimate(*args, **kwargs):
            raise ValueError("Pricing API exploded")

        monkeypatch.setattr(
            "selfforge_agent.runtime.pricing.estimate_monetary_cost",
            bad_estimate,
        )
        metrics = store.token_metrics()
        assert metrics["total_tokens"] == 15
        assert metrics["cost"] is None

    def test_token_metrics_sanitizes_negative_tokens(self, tmp_path):
        store = SessionStore(tmp_path, "sessmetrics03")
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_bytes(
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": {"prompt_tokens": -10, "completion_tokens": 5, "total_tokens": -5}}\n'
        )
        metrics = store.token_metrics()
        assert metrics["total_tokens"] == 0
        assert metrics["usage_complete"] is False

    def test_token_metrics_handles_non_finite_tokens(self, tmp_path):
        store = SessionStore(tmp_path, "sessmetrics04")
        store.trace_path.parent.mkdir(parents=True, exist_ok=True)
        store.trace_path.write_bytes(
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n'
            b'{"event": "model_call", "model": "m", "provider": "p", "usage": {"prompt_tokens": 1e999, "completion_tokens": 5, "total_tokens": 1e999}}\n'
        )
        metrics = store.token_metrics()
        assert metrics["total_tokens"] == 15
        assert metrics["usage_complete"] is False


@pytest.mark.parametrize('location', ['root', 'ancestor', 'metadata'])
def test_store_rejects_symlink_at_every_directory_boundary(tmp_path, location):
    real = tmp_path / 'real'
    real.mkdir()
    if location == 'root':
        root = tmp_path / 'alias'
        root.symlink_to(real, target_is_directory=True)
    elif location == 'ancestor':
        alias = tmp_path / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        root = alias / 'child'
    else:
        root = tmp_path / 'root'
        root.mkdir()
        (root / '.selfforge').symlink_to(real, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        SessionStore(root, 'fixture').record_message({'role': 'user', 'content': 'private'})
    assert not list(real.rglob('*.jsonl'))


@pytest.mark.parametrize('trace', [False, True])
def test_writer_refuses_overflow_without_losing_readable_history(tmp_path, monkeypatch, trace):
    import selfforge_agent.runtime.session as module

    monkeypatch.setattr(module, 'MAX_SESSION_BYTES', 1024)
    store = SessionStore(tmp_path, 'fixture')
    path = store.trace_path if trace else store.path
    append = (lambda: store.trace('fixture', detail='x'*300)) if trace else (
        lambda: store.record_message({'role': 'user', 'content': 'x'*300}))
    append()
    append()
    before = path.read_bytes()
    if trace:
        append()  # Metadata traces are best effort; overflow must leave prior bytes intact.
    else:
        with pytest.raises(ValueError, match='size|limit|maximum'):
            append()
    assert path.read_bytes() == before
    assert list(store._complete_events(path))
