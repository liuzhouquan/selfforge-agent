"""Tests for evaluation benchmark harness fail-closed resilience, nonblocking safe reading, and unbound session protection."""
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from selfforge_agent.runtime.benchmark import (
    BenchmarkCase,
    PreparedCase,
    _install_holdout,
    _run_once,
    _verification_copy,
    load_suite,
)


def test_run_once_handles_checkout_failure_without_unbound_session(tmp_path: Path) -> None:
    case_spec = BenchmarkCase(
        id="sample_case",
        repository="repo",
        revision="a" * 40,
        task="Test task",
        allowed_changes=["file.py"],
        holdout_test="test_holdout.py",
    )
    case = PreparedCase(
        spec=case_spec,
        repository=tmp_path / "nonexistent_repo",
        holdout_bytes=b"def test_pass(): pass\n",
        holdout_sha256="b" * 64,
    )

    # Calling _run_once with nonexistent repository will cause _checkout to fail.
    # It must NOT raise UnboundLocalError for 'session'; it must return a valid BenchmarkOutcome
    # with reason indicating harness_error.
    outcome = _run_once(
        case,
        repeat=1,
        model_factory=lambda _c: MagicMock(),
        sandbox="host",
        trusted_host=True,
        image="",
        max_steps=5,
        time_budget=30,
        test_timeout=10,
    )
    assert not outcome.success
    assert outcome.reason.startswith("harness_error")
    assert outcome.cost_usd is None


def test_load_suite_rejects_fifo_manifest(tmp_path: Path) -> None:
    fifo_path = tmp_path / "manifest.fifo"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, OSError):
        pytest.skip("mkfifo not supported")

    with pytest.raises(ValueError, match="regular file"):
        load_suite(fifo_path)


def test_load_suite_enforces_manifest_size_limit(tmp_path: Path) -> None:
    large_manifest = tmp_path / "suite.json"
    large_manifest.write_bytes(b" " * (2 * 1024 * 1024 + 10))

    with pytest.raises(ValueError, match="exceeds size limit"):
        load_suite(large_manifest)


def test_verification_copy_safely_skips_fifos(tmp_path: Path) -> None:
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "normal.py").write_text("print('hello')", encoding="utf-8")

    fifo_path = src / "test.fifo"
    try:
        os.mkfifo(fifo_path)
    except (AttributeError, OSError):
        pytest.skip("mkfifo not supported")

    dst = tmp_path / "dst"
    # _verification_copy must not hang or fail on FIFO; it should copy normal files and ignore FIFO
    _verification_copy(src, dst)
    assert (dst / "normal.py").exists()
    assert not (dst / "test.fifo").exists()


def test_install_holdout_rejects_symlink_target(tmp_path: Path) -> None:
    dst = tmp_path / "dst"
    dst.mkdir()
    tests_dir = dst / "tests"
    tests_dir.mkdir()

    case_spec = BenchmarkCase(
        id="case_sym",
        repository="repo",
        revision="a" * 40,
        task="Test task",
        allowed_changes=["file.py"],
        holdout_test="test_holdout.py",
    )
    case = PreparedCase(
        spec=case_spec,
        repository=tmp_path / "repo",
        holdout_bytes=b"def test_h(): pass\n",
        holdout_sha256="c" * 64,
    )

    # Symlink at the holdout destination pointing outside
    outside_file = tmp_path / "outside.py"
    target_symlink = tests_dir / "test_selfforge_holdout_case_sym.py"
    target_symlink.symlink_to(outside_file)

    with pytest.raises(ValueError, match="reserved holdout test path|symlink"):
        _install_holdout(dst, case)


def test_load_suite_rejects_symlink_manifest(tmp_path: Path) -> None:
    real_file = tmp_path / "real_suite.json"
    real_file.write_text('{"version": 1, "cases": []}', encoding="utf-8")
    symlink_file = tmp_path / "symlink_suite.json"
    symlink_file.symlink_to(real_file)

    with pytest.raises(ValueError, match="regular file"):
        load_suite(symlink_file)


def test_tool_trace_handles_malformed_events_gracefully() -> None:
    from unittest.mock import MagicMock

    from selfforge_agent.runtime.benchmark import _tool_trace

    session = MagicMock()
    # Return malformed trace events: non-dicts, missing keys, invalid types
    session.trace_events.return_value = [
        "not-a-dict",
        12345,
        {"event": "tool", "name": "read_file", "ok": True},
        {"event": "tool", "name": None, "ok": "yes"},
        {"event": "tool", "name": "unknown_tool", "ok": False},
        {"event": "other", "data": "irrelevant"},
    ]

    trace = _tool_trace(session)
    assert len(trace) == 3
    assert trace[0] == {"name": "read_file", "ok": True}
    assert trace[1] == {"name": "unknown", "ok": False}
    assert trace[2] == {"name": "unknown", "ok": False}


def _case_for_install(tmp_path: Path) -> PreparedCase:
    return PreparedCase(
        spec=BenchmarkCase(id="short", repository="repo", revision="a" * 40, task="test",
                           allowed_changes=["app.py"], holdout_test="hidden.py"),
        repository=tmp_path / "repo",
        holdout_bytes=b"def test_contract():\n    assert True\n    assert False\n",
        holdout_sha256="b" * 64,
    )


def test_holdout_install_writes_complete_frozen_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case_for_install(tmp_path)
    original = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: original(fd, data[:35]))
    path = _install_holdout(tmp_path, case)
    assert (tmp_path / path).read_bytes() == case.holdout_bytes


def test_holdout_parent_swap_cannot_write_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    tests = destination / "tests"
    tests.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and (Path(path).name == "tests" or str(path).endswith("test_selfforge_holdout_short.py")):
            swapped = True
            tests.rename(destination / "original-tests")
            tests.symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    try:
        _install_holdout(destination, _case_for_install(tmp_path))
    except ValueError:
        pass
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("input_kind", ["manifest", "holdout"])
def test_frozen_inputs_obey_the_same_size_limits(
    tmp_path: Path, input_kind: str,
) -> None:
    from test_benchmark import _manifest

    from selfforge_agent.runtime import benchmark

    manifest, _ = _manifest(tmp_path)
    kwargs = {}
    if input_kind == "manifest":
        kwargs["_contents"] = manifest.read_bytes() + b" " * benchmark.MAX_MANIFEST_BYTES
    else:
        suite, _ = load_suite(manifest)
        kwargs["_assets"] = {case.holdout_test: b"x" * (benchmark.MAX_HOLDOUT_BYTES + 1)
                             for case in suite.cases}
    with pytest.raises(ValueError, match="size limit"):
        load_suite(manifest, **kwargs)


def test_metrics_error_after_session_creation_returns_harness_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_benchmark import _manifest

    from selfforge_agent.runtime import benchmark

    manifest, _ = _manifest(tmp_path)
    _, cases = load_suite(manifest)
    monkeypatch.setattr(benchmark.SessionStore, "token_metrics", MagicMock(side_effect=ValueError("bad metrics")))
    agent = MagicMock()
    agent.run.side_effect = ValueError("initialization failed")
    monkeypatch.setattr(benchmark, "CodingAgent", MagicMock(return_value=agent))
    outcome = _run_once(cases[0], 1, lambda _case: None, sandbox="host", trusted_host=True,
                        image="unused", max_steps=1, time_budget=10, test_timeout=10)
    assert not outcome.success
    assert outcome.reason == "harness_error:ValueError"
    assert outcome.cost_usd is None


def test_cost_is_captured_before_temporary_session_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_benchmark import ScriptedModel, _manifest

    manifest, _ = _manifest(tmp_path)
    _, cases = load_suite(manifest)
    monkeypatch.setenv("SELFFORGE_PRICING_JSON", '{"fixture":{"scripted":{'
                       '"prompt_per_million":1000000,"completion_per_million":1000000,'
                       '"source":"test fixture rates","as_of":"2026-11-14"}}}')
    outcome = _run_once(cases[0], 1, ScriptedModel, sandbox="host", trusted_host=True,
                        image="unused", max_steps=6, time_budget=30, test_timeout=10)
    assert outcome.success
    assert outcome.cost_usd == 60.0
