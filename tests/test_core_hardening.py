"""Tests for process execution deadlock defense, graceful termination, env sanitization, and git SHA hardening (SF-CORE-02)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from selfforge_agent.core import git_context
from selfforge_agent.core.git_context import (
    GitContextError,
    build_context,
)
from selfforge_agent.core.process import (
    MAX_ENV_BYTES,
    MAX_ENV_COUNT,
    BoundedProcessResult,
    _validate_env,
    run_bounded,
)


class TestProcessDeadlockAndStreamHardening:
    """SF-CORE-02-A1: Pipe buffer deadlock defense & non-blocking stream pumping."""

    def test_run_bounded_massive_bidirectional_output_no_deadlock(self):
        """Pumping 200 KiB stdout and 200 KiB stderr simultaneously must not deadlock pipe buffers."""
        code = (
            "import sys\n"
            "stdout_data = 'A' * 200000\n"
            "stderr_data = 'B' * 200000\n"
            "# Interleave writes to stress pipe buffering\n"
            "for i in range(20):\n"
            "    sys.stdout.write(stdout_data[i*10000:(i+1)*10000])\n"
            "    sys.stdout.flush()\n"
            "    sys.stderr.write(stderr_data[i*10000:(i+1)*10000])\n"
            "    sys.stderr.flush()\n"
        )
        res = run_bounded(
            [sys.executable, "-c", code],
            timeout=10.0,
            max_bytes=300000,
            stderr_bytes=300000,
        )
        assert res.returncode == 0
        assert len(res.stdout) == 200000
        assert len(res.stderr) == 200000
        assert res.stdout_limited is False
        assert res.stderr_limited is False

    def test_run_bounded_stdin_devnull_prevents_interactive_hang(self):
        """Processes reading stdin must immediately receive EOF and exit, never blocking."""
        code = (
            "import sys\n"
            "try:\n"
            "    data = sys.stdin.read()\n"
            "    print(f'read:{len(data)}')\n"
            "except Exception as e:\n"
            "    print(f'err:{e}')\n"
        )
        res = run_bounded([sys.executable, "-c", code], timeout=3.0, max_bytes=1024)
        assert res.returncode == 0
        assert b"read:0" in res.stdout

    def test_run_bounded_drains_pipes_when_process_exits_early(self):
        """Output generated immediately before child exit must be fully captured."""
        code = "import sys; sys.stdout.write('immediate_output'); sys.exit(0)"
        res = run_bounded([sys.executable, "-c", code], timeout=3.0, max_bytes=1024)
        assert res.returncode == 0
        assert res.stdout == b"immediate_output"


class TestProcessGracefulTerminationHardening:
    """SF-CORE-02-A2: Phased process group termination (SIGTERM -> grace period -> SIGKILL)."""

    def test_process_group_sigterm_before_sigkill_graceful_cleanup(self, tmp_path):
        """Timeout teardown must send SIGTERM first, allowing cooperative cleanup handler to run."""
        marker = tmp_path / "sigterm_received.txt"
        code = (
            "import signal, sys, time, pathlib\n"
            f"marker = pathlib.Path({str(marker)!r})\n"
            "def handle_term(signum, frame):\n"
            "    marker.write_text('term_handled')\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, handle_term)\n"
            "# Signal parent readiness by flushing stdout\n"
            "sys.stdout.write('ready\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(10)\n"
        )
        with pytest.raises(TimeoutError, match="timed out"):
            run_bounded([sys.executable, "-c", code], timeout=0.3, max_bytes=1024)

        time.sleep(0.3)
        assert marker.exists()
        assert marker.read_text() == "term_handled"

    def test_process_group_sigkill_escalation_when_sigterm_ignored(self, tmp_path):
        """If a process stubbornly ignores SIGTERM, run_bounded must escalate to SIGKILL."""
        marker = tmp_path / "should_not_finish.txt"
        code = (
            "import signal, time, pathlib\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(10)\n"
            f"pathlib.Path({str(marker)!r}).write_text('lingered')\n"
        )
        t0 = time.monotonic()
        with pytest.raises(TimeoutError, match="timed out"):
            run_bounded([sys.executable, "-c", code], timeout=0.3, max_bytes=1024)
        elapsed = time.monotonic() - t0

        # Entire timeout + escalation grace period should take < 2.0s
        assert elapsed < 2.0
        time.sleep(0.3)
        assert not marker.exists()


class TestProcessEnvironmentIsolationHardening:
    """SF-CORE-02-A3: Environment variable boundaries, key validation, and credential isolation."""

    def test_validate_env_enforces_max_count(self):
        """Reject environment exceeding MAX_ENV_COUNT (500 variables)."""
        valid_env = {f"KEY_{i}": f"val_{i}" for i in range(MAX_ENV_COUNT)}
        assert len(_validate_env(valid_env)) == MAX_ENV_COUNT

        oversized_env = {f"KEY_{i}": f"val_{i}" for i in range(MAX_ENV_COUNT + 1)}
        with pytest.raises(ValueError, match="too many"):
            _validate_env(oversized_env)

    def test_validate_env_enforces_max_total_bytes(self):
        """Reject environment whose total encoded byte size exceeds MAX_ENV_BYTES (64 KiB)."""
        large_val = "x" * (MAX_ENV_BYTES + 10)
        with pytest.raises(ValueError, match="(?i)total.*size|maximum limit"):
            _validate_env({"HUGE": large_val})

    def test_validate_env_rejects_empty_keys_equals_and_newlines(self):
        """Reject keys that are empty, contain '=', or contain newline characters."""
        with pytest.raises(ValueError, match="empty"):
            _validate_env({"": "value"})

        with pytest.raises(ValueError, match="="):
            _validate_env({"KEY=FOO": "value"})

        with pytest.raises(ValueError, match="newline"):
            _validate_env({"KEY\nBAR": "value"})

        with pytest.raises(ValueError, match="newline"):
            _validate_env({"KEY\rBAR": "value"})

    def test_run_bounded_scrubs_sensitive_credentials_when_requested(self, monkeypatch):
        """When scrub_credentials=True, sensitive API keys/tokens are removed from subprocess."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_super_secret_token_12345")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-deepseek-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-key")
        monkeypatch.setenv("NORMAL_VAR", "safe_value")

        code = (
            "import os, json\n"
            "keys = ['GITHUB_TOKEN', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'NORMAL_VAR']\n"
            "found = {k: os.environ.get(k) for k in keys if k in os.environ}\n"
            "print(json.dumps(found))\n"
        )
        res = run_bounded(
            [sys.executable, "-c", code],
            max_bytes=2048,
            scrub_credentials=True,
        )
        assert res.returncode == 0
        import json
        found = json.loads(res.stdout.decode())
        assert "GITHUB_TOKEN" not in found
        assert "DEEPSEEK_API_KEY" not in found
        assert "ANTHROPIC_API_KEY" not in found
        assert found.get("NORMAL_VAR") == "safe_value"

    def test_run_bounded_allow_credentials_whitelisting(self, monkeypatch):
        """When allow_credentials is provided with scrub_credentials, specified keys are kept."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_allowlisted_token")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-disallowed-token")

        code = (
            "import os, json\n"
            "keys = ['GITHUB_TOKEN', 'OPENAI_API_KEY']\n"
            "found = {k: os.environ.get(k) for k in keys if k in os.environ}\n"
            "print(json.dumps(found))\n"
        )
        res = run_bounded(
            [sys.executable, "-c", code],
            max_bytes=2048,
            scrub_credentials=True,
            allow_credentials={"GITHUB_TOKEN"},
        )
        assert res.returncode == 0
        import json
        found = json.loads(res.stdout.decode())
        assert found.get("GITHUB_TOKEN") == "ghp_allowlisted_token"
        assert "OPENAI_API_KEY" not in found


class TestGitContextSHAHardening:
    """SF-CORE-02-A4: Git Context commit SHA regex strictness and --end-of-options protection."""

    def test_build_context_rejects_malformed_shas(self, demo_repo: tuple[Path, str, str]):
        repo, _base, head = demo_repo

        # Reject non-hex characters
        with pytest.raises(GitContextError, match="hexadecimal SHA"):
            build_context(repo, "123456g890abcdef1234567890abcdef12345678", head)

        # Reject short SHA less than 7 chars
        with pytest.raises(GitContextError, match="hexadecimal SHA"):
            build_context(repo, "abcdef", head)

        # Reject option-like strings
        with pytest.raises(GitContextError, match="hexadecimal SHA"):
            build_context(repo, "--end-of-options", head)

        with pytest.raises(GitContextError, match="hexadecimal SHA"):
            build_context(repo, "-p", head)

        # Reject whitespace inside or empty
        with pytest.raises(GitContextError, match="hexadecimal SHA|empty"):
            build_context(repo, "", head)

        with pytest.raises(GitContextError, match="hexadecimal SHA|whitespace"):
            build_context(repo, "   ", head)

    def test_git_context_passes_end_of_options_to_git(
        self, demo_repo: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
    ):
        """Underlying git calls for diff and rev-parse must pass --end-of-options before revisions."""
        repo, base, head = demo_repo
        recorded_calls: list[list[str]] = []
        orig_run_bounded = git_context.run_bounded

        def spy_run_bounded(command: list[str], **kwargs) -> BoundedProcessResult:
            recorded_calls.append(list(command))
            return orig_run_bounded(command, **kwargs)

        monkeypatch.setattr(git_context, "run_bounded", spy_run_bounded)
        context = build_context(repo, base, head)
        assert context.base_sha == base
        assert context.head_sha == head

        # Check rev-parse call for base and head includes --end-of-options
        rev_parse_calls = [c for c in recorded_calls if "rev-parse" in c and "--verify" in c]
        assert len(rev_parse_calls) >= 2
        for call in rev_parse_calls:
            assert "--end-of-options" in call
            # --end-of-options must precede revision
            eoo_idx = call.index("--end-of-options")
            rev_idx = next(i for i, arg in enumerate(call) if "^{commit}" in arg)
            assert eoo_idx < rev_idx

        # Check diff calls include --end-of-options
        diff_calls = [c for c in recorded_calls if "diff" in c]
        assert len(diff_calls) >= 1
        for call in diff_calls:
            assert "--end-of-options" in call
            eoo_idx = call.index("--end-of-options")
            base_idx = call.index(base)
            assert eoo_idx < base_idx


def test_timeout_grace_period_reaches_children_after_parent_exit(tmp_path):
    import os

    if os.name != 'posix':
        pytest.skip('POSIX process group contract')
    ready = tmp_path / 'ready'
    cleaned = tmp_path / 'cleaned'
    program = '''
import os, signal, sys, time
from pathlib import Path
if os.fork() == 0:
    def cleanup(signum, frame):
        time.sleep(0.05)
        Path(sys.argv[2]).write_text('cleaned')
        os._exit(0)
    signal.signal(signal.SIGTERM, cleanup)
    Path(sys.argv[1]).write_text('ready')
while True:
    time.sleep(1)
'''
    with pytest.raises(TimeoutError):
        run_bounded([sys.executable, '-c', program, str(ready), str(cleaned)], timeout=0.8, max_bytes=1024)
    assert ready.exists()
    assert cleaned.read_text() == 'cleaned'
