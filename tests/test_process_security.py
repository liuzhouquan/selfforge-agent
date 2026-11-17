"""Security, boundary validation, and lifecycle tests for run_bounded process runner (SF-PROC-01)."""

import sys
import time
from pathlib import Path

import pytest

from selfforge_agent.core.process import BoundedProcessResult, run_bounded


class TestProcessCommandGatingSecurity:
    """SF-PROC-01-A1: Command and argument gating."""

    def test_run_bounded_rejects_non_list_command(self):
        for invalid in ["echo hello", 12345, None, {"cmd": "ls"}, ("echo", "hello")]:
            with pytest.raises(TypeError, match="[Cc]ommand must be a list"):
                run_bounded(invalid, max_bytes=1024)

    def test_run_bounded_rejects_empty_command_or_empty_binary(self):
        with pytest.raises(ValueError, match="[Cc]ommand"):
            run_bounded([], max_bytes=1024)

        with pytest.raises(ValueError, match="[Cc]ommand"):
            run_bounded([""], max_bytes=1024)

        with pytest.raises(ValueError, match="[Cc]ommand"):
            run_bounded(["   "], max_bytes=1024)

    def test_run_bounded_rejects_non_string_or_null_byte_arguments(self):
        for invalid_arg in [123, None, Path("/tmp"), ["sub"]]:
            with pytest.raises(TypeError, match="[Aa]rgument"):
                run_bounded(["echo", invalid_arg], max_bytes=1024)

        with pytest.raises(ValueError, match="[Nn]ull byte"):
            run_bounded(["echo", "hello\0world"], max_bytes=1024)

    def test_run_bounded_rejects_excessive_arguments(self):
        excessive = ["echo"] + ["arg"] * 1001
        with pytest.raises(ValueError, match="too many arguments|[Ll]imit"):
            run_bounded(excessive, max_bytes=1024)

    def test_run_bounded_rejects_oversized_single_argument(self):
        huge_arg = "a" * (200 * 1024)
        with pytest.raises(ValueError, match="too long|[Ll]imit"):
            run_bounded(["echo", huge_arg], max_bytes=1024)


class TestProcessWorkingDirAndEnvironmentSecurity:
    """SF-PROC-01-A2: Working directory and environment safety."""

    def test_run_bounded_rejects_invalid_cwd_types(self):
        for invalid in [12345, ["/tmp"], {"cwd": "/tmp"}]:
            with pytest.raises(TypeError, match="[Cc]wd"):
                run_bounded([sys.executable, "-c", "print(1)"], cwd=invalid, max_bytes=1024)

    def test_run_bounded_rejects_null_bytes_in_cwd(self):
        with pytest.raises(ValueError, match="[Nn]ull byte"):
            run_bounded([sys.executable, "-c", "print(1)"], cwd="/tmp/\0evil", max_bytes=1024)

    def test_run_bounded_rejects_non_existent_cwd(self, tmp_path):
        non_existent = tmp_path / "does_not_exist_dir"
        with pytest.raises((ValueError, FileNotFoundError)):
            run_bounded([sys.executable, "-c", "print(1)"], cwd=non_existent, max_bytes=1024)

    def test_run_bounded_rejects_file_as_cwd(self, tmp_path):
        a_file = tmp_path / "regular.txt"
        a_file.write_text("not a dir")
        with pytest.raises(ValueError, match="directory"):
            run_bounded([sys.executable, "-c", "print(1)"], cwd=a_file, max_bytes=1024)

    def test_run_bounded_rejects_symlinked_cwd_or_parent(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        sym_dir = tmp_path / "sym_dir"
        sym_dir.symlink_to(real_dir, target_is_directory=True)

        with pytest.raises(ValueError, match="symlink"):
            run_bounded([sys.executable, "-c", "print(1)"], cwd=sym_dir, max_bytes=1024)

    def test_run_bounded_accepts_and_validates_env(self):
        res = run_bounded(
            [sys.executable, "-c", "import os; print(os.environ.get('SF_TEST_VAR', ''))"],
            env={"SF_TEST_VAR": "bounded_val"},
            max_bytes=1024,
        )
        assert res.returncode == 0
        assert b"bounded_val" in res.stdout

        for invalid_env in ["not_a_dict", [("A", "B")], {123: "val"}, {"KEY": 123}]:
            with pytest.raises(TypeError, match="[Ee]nv"):
                run_bounded([sys.executable, "-c", "print(1)"], env=invalid_env, max_bytes=1024)


class TestProcessResourceBoundariesAndTimeoutSecurity:
    """SF-PROC-01-A3: Resource boundaries and timeout guarding."""

    def test_run_bounded_timeout_validation(self):
        for invalid in [0, -5, "30", float("nan"), float("inf"), 4000, None, True]:
            with pytest.raises(ValueError, match="[Tt]imeout"):
                run_bounded([sys.executable, "-c", "print(1)"], timeout=invalid, max_bytes=1024)

    def test_run_bounded_output_limit_validation(self):
        for invalid in [0, -100, 60 * 1024 * 1024, "1024", 3.14, None, True]:
            with pytest.raises(ValueError, match="[Oo]utput limit"):
                run_bounded([sys.executable, "-c", "print(1)"], max_bytes=invalid)

        for invalid in [0, -100, 60 * 1024 * 1024, "1024", 3.14, True]:
            with pytest.raises(ValueError, match="[Oo]utput limit"):
                run_bounded([sys.executable, "-c", "print(1)"], max_bytes=1024, stderr_bytes=invalid)

    def test_run_bounded_truncates_large_stdout(self):
        res = run_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('A' * 20000); sys.stdout.flush()"],
            max_bytes=1000,
        )
        assert len(res.stdout) == 1000
        assert res.stdout_limited is True
        assert res.stderr_limited is False

    def test_run_bounded_truncates_large_stderr(self):
        res = run_bounded(
            [sys.executable, "-c", "import sys; sys.stderr.write('E' * 20000); sys.stderr.flush()"],
            max_bytes=10000,
            stderr_bytes=500,
        )
        assert len(res.stderr) == 500
        assert res.stdout_limited is False
        assert res.stderr_limited is True


class TestProcessLifecycleAndContractSecurity:
    """SF-PROC-01-A4: Lifecycle and output contract."""

    def test_bounded_process_result_contract(self):
        res = run_bounded([sys.executable, "-c", "import sys; print('ok'); sys.exit(0)"], max_bytes=1024)
        assert isinstance(res, BoundedProcessResult)
        assert res.returncode == 0
        assert b"ok" in res.stdout
        assert res.stderr == b""
        assert res.stdout_limited is False
        assert res.stderr_limited is False

    def test_non_zero_exit_code_captured_cleanly(self):
        res = run_bounded([sys.executable, "-c", "import sys; sys.exit(42)"], max_bytes=1024)
        assert res.returncode == 42
        assert res.stdout == b""
        assert res.stdout_limited is False

    def test_process_group_killed_on_timeout(self, tmp_path):
        marker = tmp_path / "late_child_marker"
        child_code = f"import time, pathlib; time.sleep(0.5); pathlib.Path({str(marker)!r}).write_text('failed')"
        parent_code = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(10)"

        with pytest.raises(TimeoutError, match="timed out"):
            run_bounded([sys.executable, "-c", parent_code], timeout=0.2, max_bytes=1024)

        time.sleep(0.6)
        assert not marker.exists()


def test_pipe_read_error_cannot_return_successful_incomplete_output(tmp_path, monkeypatch):
    import errno
    import os

    real_read = os.read
    pid_file = tmp_path / 'producer.pid'

    def fail_pipe_read(fd, length):
        if length <= 8192:
            raise OSError(errno.EIO, 'fixture pipe read failure')
        return real_read(fd, length)

    monkeypatch.setattr(os, 'read', fail_pipe_read)
    code = f'import os,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); print("important evidence")'
    with pytest.raises(OSError) as error:
        run_bounded([sys.executable, '-c', code], timeout=2, max_bytes=1024)
    assert error.value.errno == errno.EIO
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize('argument', ['界'*50000, 'x'*(128*1024)], ids=['utf8', 'terminator'])
def test_argument_budget_counts_encoded_bytes_and_terminator(argument):
    with pytest.raises(ValueError):
        run_bounded([sys.executable, '-c', 'pass', argument], timeout=2, max_bytes=1024)


def test_huge_integer_timeout_is_controlled_before_launch():
    with pytest.raises(ValueError):
        run_bounded([sys.executable, '-c', 'pass'], timeout=10**1000, max_bytes=1024)
