"""Tests for pinned evaluation source preparation (SF-DEV-01)."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from selfforge_agent.runtime.benchmark import load_suite

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "prepare_eval_sources.py"
CANDIDATES = ROOT / "benchmark" / "validated-candidates"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load_prepare_module():
    if not SCRIPT_PATH.is_file():
        pytest.fail(f"Missing preparation script: {SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location("prepare_eval_sources", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prepare_eval_sources"] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_remote_repo(base: Path, name: str, filename: str) -> tuple[Path, str, str]:
    remote = base / f"remote-{name}"
    remote.mkdir()
    _git(remote, "init", "-q")
    _git(remote, "config", "user.name", "Fixture")
    _git(remote, "config", "user.email", "fixture@example.invalid")
    (remote / filename).write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = remote / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", f"{name} pinned commit")
    pinned = _git(remote, "rev-parse", "HEAD")
    (remote / filename).write_text("VALUE = 2\n", encoding="utf-8")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", f"{name} later commit")
    later = _git(remote, "rev-parse", "HEAD")
    return remote, pinned, later


def _make_fake_git_dir(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(exist_ok=True)
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
    git_wrapper.chmod(git_wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def test_pinned_sources_match_candidate_manifests_and_policy() -> None:
    mod = _load_prepare_module()
    jmespath_suite = json.loads(
        (CANDIDATES / "suite-jmespath-unknown-function.json").read_text(encoding="utf-8")
    )
    boltons_suite = json.loads(
        (CANDIDATES / "suite-boltons-remap-mappings.json").read_text(encoding="utf-8")
    )

    by_name = {item.name: item for item in mod.PINNED_SOURCES}
    assert set(by_name) == {"jmespath", "boltons"}
    assert by_name["jmespath"].url == "https://github.com/jmespath/jmespath.py.git"
    assert by_name["jmespath"].revision == jmespath_suite["cases"][0]["revision"]
    assert by_name["boltons"].url == "https://github.com/mahmoud/boltons.git"
    assert by_name["boltons"].revision == boltons_suite["cases"][0]["revision"]

    parser = mod.build_parser()
    option_strings = {opt for action in parser._actions for opt in action.option_strings}
    assert {"--root", "--timeout", "--offline"}.issubset(option_strings)
    assert "--url" not in option_strings


def test_online_preparation_creates_cloneable_repos_and_ownership_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SF-DEV-01-A1: online mode fetches pinned SHAs and supports independent clone/checkout."""
    mod = _load_prepare_module()
    remote_j, sha_j, later_j = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, later_b = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )

    root = tmp_path / "empty-root"
    root.mkdir()
    rc = mod.main(["--root", str(root), "--timeout", "60"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "jmespath" in captured.out
    assert "boltons" in captured.out

    state_file = root / mod.STATE_FILENAME
    assert state_file.is_file() and not state_file.is_symlink()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["owner"] == mod.OWNER_ID
    assert state["sources"]["jmespath"]["status"] == "ready"
    assert state["sources"]["jmespath"]["owned"] is True
    assert state["sources"]["boltons"]["status"] == "ready"
    assert state["sources"]["boltons"]["owned"] is True

    for name, expected_sha, filename, forbidden_sha in (
        ("jmespath", sha_j, "pkg_j.py", later_j),
        ("boltons", sha_b, "pkg_b.py", later_b),
    ):
        cached = root / name
        assert cached.is_dir() and not cached.is_symlink()
        clone_dir = tmp_path / f"clone-{name}"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-local", "--no-checkout", str(cached), str(clone_dir)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "--quiet", "--detach", expected_sha],
            check=True,
        )
        assert _git(clone_dir, "rev-parse", "HEAD") == expected_sha
        assert (clone_dir / filename).read_text(encoding="utf-8") == "VALUE = 1\n"
        # Shallow fetch of pinned SHA must not silently pull unrelated later remote commits.
        later_check = subprocess.run(
            ["git", "-C", str(clone_dir), "cat-file", "-e", f"{forbidden_sha}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        assert later_check.returncode != 0

    # Compatible with benchmark suite loader.
    holdout = tmp_path / "test_holdout.py"
    holdout.write_text("def test_holdout():\n    assert True\n", encoding="utf-8")
    manifest = tmp_path / "suite.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "j_case",
                        "repository": str(root / "jmespath"),
                        "revision": sha_j,
                        "task": "check",
                        "allowed_changes": ["pkg_j.py"],
                        "holdout_test": str(holdout),
                    },
                    {
                        "id": "b_case",
                        "repository": str(root / "boltons"),
                        "revision": sha_b,
                        "task": "check",
                        "allowed_changes": ["pkg_b.py"],
                        "holdout_test": str(holdout),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _, prepared = load_suite(manifest)
    assert len(prepared) == 2


def test_default_root_uses_env_var_and_cli_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify --root default reads SELFFORGE_EVAL_SOURCE_ROOT and works via real CLI subprocess."""
    mod = _load_prepare_module()
    root = tmp_path / "from-env"
    root.mkdir()
    for src in mod.PINNED_SOURCES:
        repo = root / src.name
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "remote", "add", "origin", src.url)

    # Real CLI subprocess with empty/missing cache in offline mode fails cleanly.
    env = os.environ.copy()
    env["SELFFORGE_EVAL_SOURCE_ROOT"] = str(root)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--offline"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "[ready]" not in proc.stdout

    # Blank SELFFORGE_EVAL_SOURCE_ROOT must fail closed rather than silently using cwd.
    env["SELFFORGE_EVAL_SOURCE_ROOT"] = "   "
    proc_blank = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--offline"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_blank.returncode != 0

    monkeypatch.setenv("SELFFORGE_EVAL_SOURCE_ROOT", str(root))
    assert mod._resolve_root_arg(None) == root


def test_offline_mode_succeeds_without_fetch_and_preserves_user_head_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SF-DEV-01-A2: offline mode validates existing cache without fetch or mutating HEAD/files."""
    mod = _load_prepare_module()
    remote_j, sha_j, _ = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, _ = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )

    root = tmp_path / "existing-cache"
    root.mkdir()
    user_states = {}
    for name, remote, sha, filename in (
        ("jmespath", remote_j, sha_j, "pkg_j.py"),
        ("boltons", remote_b, sha_b, "pkg_b.py"),
    ):
        repo = root / name
        subprocess.run(["git", "clone", "--quiet", str(remote), str(repo)], check=True)
        _git(repo, "config", "user.name", "User")
        _git(repo, "config", "user.email", "user@example.invalid")
        _git(repo, "branch", "candidate", sha)
        _git(repo, "checkout", "-q", "-b", "user-feature")
        (repo / filename).write_text("VALUE = 999\n", encoding="utf-8")
        (repo / "local_notes.txt").write_text("keep me\n", encoding="utf-8")
        user_states[name] = {
            "head": _git(repo, "rev-parse", "HEAD"),
            "branch": _git(repo, "symbolic-ref", "HEAD"),
        }

    # Wrap git with a stand-in that blocks fetch/network/mutating operations.
    bin_dir = _make_fake_git_dir(
        tmp_path / "bin",
        """
import os, sys
args = sys.argv[1:]
blocked = {"fetch", "pull", "push", "ls-remote", "clone", "checkout", "reset", "update-ref"}
if any(tok in blocked for tok in args):
    sys.stderr.write(f"BLOCKED_GIT_CMD: {' '.join(args)}\\n")
    sys.exit(99)
real_git = "/usr/bin/git"
os.execv(real_git, [real_git, *args])
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    rc = mod.main(["--root", str(root), "--offline"])
    assert rc == 0
    assert not (root / mod.STATE_FILENAME).exists()
    for name, filename in (("jmespath", "pkg_j.py"), ("boltons", "pkg_b.py")):
        repo = root / name
        assert _git(repo, "rev-parse", "HEAD") == user_states[name]["head"]
        assert _git(repo, "symbolic-ref", "HEAD") == user_states[name]["branch"]
        assert (repo / filename).read_text(encoding="utf-8") == "VALUE = 999\n"
        assert (repo / "local_notes.txt").read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize("bad_timeout", ["0", "-1", "301", "nan", "inf", "abc"])
def test_invalid_timeout_cli_rejected(tmp_path: Path, bad_timeout: str) -> None:
    _load_prepare_module()
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--root",
            str(tmp_path / "r"),
            "--timeout",
            bad_timeout,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_timeout_kills_slow_git_and_leaves_diagnosable_unready_state(tmp_path: Path) -> None:
    """SF-DEV-01-A3: slow subprocess respects total deadline, exits non-zero, preserves files."""
    _load_prepare_module()
    root = tmp_path / "timeout-root"
    root.mkdir()
    sentinel = root / "user-keep.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")

    bin_dir = _make_fake_git_dir(
        tmp_path / "slow-bin",
        """
import os, sys, time
args = sys.argv[1:]
if "fetch" in args:
    sys.stderr.write("fetching slowly...\\n")
    sys.stderr.flush()
    time.sleep(10)
    sys.exit(0)
real_git = "/usr/bin/git"
os.execv(real_git, [real_git, *args])
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(root), "--timeout", "1"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - start
    assert proc.returncode != 0
    assert elapsed < 5.0
    assert "[ready]" not in proc.stdout
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"

    state = json.loads((root / ".selfforge-eval-sources.json").read_text(encoding="utf-8"))
    assert state["sources"]["jmespath"]["status"] == "failed"
    assert "Timed out" in state["sources"]["jmespath"]["error"]

    # Offline mode must refuse the incomplete/failed cache.
    proc_offline = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(root), "--offline"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_offline.returncode != 0


def test_online_bad_remote_and_missing_commit_fail_and_recover_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SF-DEV-01-A3: bad source or missing commit fails, marks failed, and retries cleanly."""
    mod = _load_prepare_module()
    remote_j, sha_j, _ = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, _ = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    missing_sha = "0" * 40

    # Bad remote URL (nonexistent repo path) fails and marks failed.
    bad_url = str(tmp_path / "does-not-exist")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", bad_url, sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )
    root = tmp_path / "retry-root"
    assert mod.main(["--root", str(root), "--timeout", "15"]) != 0
    state = json.loads((root / mod.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["sources"]["jmespath"]["status"] == "failed"
    assert state["sources"]["jmespath"]["owned"] is True
    assert mod.main(["--root", str(root), "--offline"]) != 0

    # Changing pinned origin on an existing repo without matching origin must fail closed.
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )
    assert mod.main(["--root", str(root), "--timeout", "15"]) != 0

    # Missing commit at the same expected URL fails, then recovers once commit is pushed.
    retry_remote_dir = tmp_path / "transient-jmespath"
    root2 = tmp_path / "missing-commit-root"
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(retry_remote_dir), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )
    assert mod.main(["--root", str(root2), "--timeout", "15"]) != 0
    state2 = json.loads((root2 / mod.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state2["sources"]["jmespath"]["status"] == "failed"
    assert mod.main(["--root", str(root2), "--offline"]) != 0

    subprocess.run(["git", "clone", "--quiet", str(remote_j), str(retry_remote_dir)], check=True)
    assert mod.main(["--root", str(root2), "--timeout", "15"]) == 0
    assert mod.main(["--root", str(root2), "--offline"]) == 0

    # Explicit missing commit SHA on an otherwise valid remote must fail cleanly.
    root3 = tmp_path / "bad-sha-root"
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), missing_sha),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )
    assert mod.main(["--root", str(root3), "--timeout", "15"]) != 0
    state3 = json.loads((root3 / mod.STATE_FILENAME).read_text(encoding="utf-8"))
    assert state3["sources"]["jmespath"]["status"] == "failed"
    assert mod.main(["--root", str(root3), "--offline"]) != 0


def test_online_fetch_into_existing_repo_preserves_head_and_candidate_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Online fetch into an existing valid repo keeps HEAD, candidate branch, and local files."""
    mod = _load_prepare_module()
    remote_j, sha_j, later_j = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, _ = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )

    root = tmp_path / "existing-online"
    root.mkdir()
    j_repo = root / "jmespath"
    j_repo.mkdir()
    _git(j_repo, "init", "-q")
    _git(j_repo, "remote", "add", "origin", str(remote_j))
    _git(j_repo, "fetch", "-q", "--depth=1", "origin", later_j)
    _git(j_repo, "checkout", "-q", "-b", "candidate", later_j)
    (j_repo / "local.txt").write_text("keep me\n", encoding="utf-8")

    b_repo = root / "boltons"
    subprocess.run(["git", "clone", "--quiet", str(remote_b), str(b_repo)], check=True)
    _git(b_repo, "branch", "candidate", sha_b)

    assert mod.main(["--root", str(root), "--timeout", "15"]) == 0
    assert _git(j_repo, "rev-parse", "HEAD") == later_j
    assert _git(j_repo, "rev-parse", "refs/heads/candidate") == later_j
    assert (j_repo / "local.txt").read_text(encoding="utf-8") == "keep me\n"

    clone_dir = tmp_path / "clone-j"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-local", "--no-checkout", str(j_repo), str(clone_dir)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(clone_dir), "checkout", "--quiet", "--detach", sha_j],
        check=True,
    )
    assert _git(clone_dir, "rev-parse", "HEAD") == sha_j


def test_run_git_stdout_overflow_fails_closed(tmp_path: Path) -> None:
    """Bounded query stdout rejects oversized git command output."""
    mod = _load_prepare_module()
    bin_dir = _make_fake_git_dir(
        tmp_path / "stdout-bin",
        """
import sys
sys.stdout.write("A" * 80000)
sys.stdout.flush()
""",
    )
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}:{old_path}"
    try:
        with pytest.raises(mod.PreparationError, match="exceeded"):
            mod._run_git(
                ["rev-parse", "--show-toplevel"],
                deadline=time.monotonic() + 5,
                max_output_bytes=1024,
            )
    finally:
        os.environ["PATH"] = old_path


def test_offline_rejects_missing_cache_bad_origin_and_orphan_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SF-DEV-01-A3: missing cache, missing/wrong origin, or unreferenced commit fails."""
    mod = _load_prepare_module()
    remote_j, sha_j, later_j = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, _ = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )

    # 1. Missing root in offline mode must fail and not create root.
    missing_root = tmp_path / "nonexistent"
    assert mod.main(["--root", str(missing_root), "--offline"]) != 0
    assert not missing_root.exists()

    # 2. Missing origin remote must fail in both offline and online modes, preserving files.
    root_no_origin = tmp_path / "no-origin"
    root_no_origin.mkdir()
    j_dir = root_no_origin / "jmespath"
    j_dir.mkdir()
    _git(j_dir, "init", "-q")
    _git(j_dir, "fetch", "-q", str(remote_j), sha_j)
    _git(j_dir, "checkout", "-q", "-b", "candidate", sha_j)
    (j_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
    b_dir = root_no_origin / "boltons"
    subprocess.run(["git", "clone", "--quiet", str(remote_b), str(b_dir)], check=True)
    _git(b_dir, "branch", "candidate", sha_b)

    assert mod.main(["--root", str(root_no_origin), "--offline"]) != 0
    assert mod.main(["--root", str(root_no_origin)]) != 0
    assert (j_dir / "keep.txt").read_text(encoding="utf-8") == "keep\n"

    # 3. Wrong origin URL must fail without modifying repo.
    _git(j_dir, "remote", "add", "origin", str(tmp_path / "wrong-remote"))
    assert mod.main(["--root", str(root_no_origin), "--offline"]) != 0
    assert mod.main(["--root", str(root_no_origin)]) != 0
    assert (j_dir / "keep.txt").read_text(encoding="utf-8") == "keep\n"

    # 4. Orphan commit (exists in object db but unreachable from refs/heads or refs/tags) fails offline.
    root_orphan = tmp_path / "orphan"
    root_orphan.mkdir()
    jo = root_orphan / "jmespath"
    jo.mkdir()
    _git(jo, "init", "-q")
    _git(jo, "remote", "add", "origin", str(remote_j))
    _git(jo, "fetch", "-q", "--depth=1", "origin", later_j)
    _git(jo, "checkout", "-q", "-b", "master", later_j)
    _git(jo, "fetch", "-q", "--depth=1", "origin", sha_j)
    bo = root_orphan / "boltons"
    subprocess.run(["git", "clone", "--quiet", str(remote_b), str(bo)], check=True)
    _git(bo, "branch", "candidate", sha_b)
    assert mod.main(["--root", str(root_orphan), "--offline"]) != 0


def test_rejects_symlinks_unknown_dirs_and_nested_parent_repo_without_modification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SF-DEV-01-A3: refuse symlinks, regular files, and unowned non-repo directories."""
    mod = _load_prepare_module()
    remote_j, sha_j, _ = _make_remote_repo(tmp_path, "jmespath", "pkg_j.py")
    remote_b, sha_b, _ = _make_remote_repo(tmp_path, "boltons", "pkg_b.py")
    monkeypatch.setattr(
        mod,
        "PINNED_SOURCES",
        (
            mod.PinnedEvalSource("jmespath", str(remote_j), sha_j),
            mod.PinnedEvalSource("boltons", str(remote_b), sha_b),
        ),
    )

    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    (real_dir / "keep.txt").write_text("safe\n", encoding="utf-8")

    # Symlink root (valid and dangling)
    sym_root = tmp_path / "sym-root"
    sym_root.symlink_to(real_dir)
    assert mod.main(["--root", str(sym_root)]) != 0
    assert mod.main(["--root", str(sym_root), "--offline"]) != 0
    assert not (real_dir / "jmespath").exists()

    dangling_root = tmp_path / "dangling-root"
    dangling_root.symlink_to(tmp_path / "missing-target")
    assert mod.main(["--root", str(dangling_root)]) != 0

    # Symlink ancestor
    assert mod.main(["--root", str(sym_root / "nested")]) != 0
    assert not (real_dir / "nested").exists()

    # Symlink target
    root_with_sym_target = tmp_path / "root-sym-target"
    root_with_sym_target.mkdir()
    (root_with_sym_target / "jmespath").symlink_to(real_dir)
    assert mod.main(["--root", str(root_with_sym_target)]) != 0
    assert not (real_dir / ".git").exists()

    # Symlink .git inside target
    root_sym_git = tmp_path / "root-sym-git"
    (root_sym_git / "jmespath").mkdir(parents=True)
    (root_sym_git / "jmespath" / ".git").symlink_to(real_dir)
    assert mod.main(["--root", str(root_sym_git)]) != 0

    # Symlink state file
    root_sym_state = tmp_path / "root-sym-state"
    root_sym_state.mkdir()
    outside_json = tmp_path / "outside.json"
    outside_json.write_text('{"owner":"x"}\n', encoding="utf-8")
    (root_sym_state / mod.STATE_FILENAME).symlink_to(outside_json)
    assert mod.main(["--root", str(root_sym_state)]) != 0
    assert outside_json.read_text(encoding="utf-8") == '{"owner":"x"}\n'

    # Unowned existing non-repo directory
    root_unknown = tmp_path / "root-unknown"
    unknown_j = root_unknown / "jmespath"
    unknown_j.mkdir(parents=True)
    (unknown_j / "important.txt").write_text("do not delete\n", encoding="utf-8")
    assert mod.main(["--root", str(root_unknown)]) != 0
    assert not (unknown_j / ".git").exists()
    assert not (root_unknown / "boltons").exists()
    assert (unknown_j / "important.txt").read_text(encoding="utf-8") == "do not delete\n"

    # Regular file at root or target
    file_root = tmp_path / "file-root"
    file_root.write_text("file\n", encoding="utf-8")
    assert mod.main(["--root", str(file_root)]) != 0
    assert file_root.read_text(encoding="utf-8") == "file\n"

    # Empty subdir inside a parent Git repo must not be mistaken for a repo root.
    parent_repo = tmp_path / "parent-repo"
    parent_repo.mkdir()
    _git(parent_repo, "init", "-q")
    _git(parent_repo, "remote", "add", "origin", str(remote_j))
    nested_root = parent_repo / "sub" / "repos"
    (nested_root / "jmespath").mkdir(parents=True)
    assert mod.main(["--root", str(nested_root), "--offline"]) != 0
    assert mod.main(["--root", str(nested_root)]) != 0


def test_bounded_output_and_corrupt_state_file_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SF-DEV-01-A3: huge git stderr/stdout and malformed state JSON fail closed safely."""
    mod = _load_prepare_module()
    root_bad_state = tmp_path / "bad-state"
    root_bad_state.mkdir()
    (root_bad_state / mod.STATE_FILENAME).write_text("{not valid json", encoding="utf-8")
    assert mod.main(["--root", str(root_bad_state)]) != 0
    assert mod.main(["--root", str(root_bad_state), "--offline"]) != 0

    # Excessive git stderr on failing fetch must remain bounded in error output.
    root_noisy = tmp_path / "noisy"
    root_noisy.mkdir()
    bin_dir = _make_fake_git_dir(
        tmp_path / "noisy-bin",
        """
import os, sys
args = sys.argv[1:]
if "fetch" in args:
    sys.stderr.write("X" * 200000 + "\\nEND_OF_NOISE\\n")
    sys.exit(128)
real_git = "/usr/bin/git"
os.execv(real_git, [real_git, *args])
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(root_noisy)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert len(proc.stderr) <= 2000
    capsys.readouterr()


def test_ci_workflow_uses_shared_prepare_eval_sources_script() -> None:
    """SF-DEV-01-A4: CI invokes scripts/prepare_eval_sources.py instead of duplicating git fetch."""
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/prepare_eval_sources.py" in ci_text
    assert "git -C .local/benchmark/repos/" not in ci_text
    assert "https://github.com/jmespath/jmespath.py.git" not in ci_text
    assert "https://github.com/mahmoud/boltons.git" not in ci_text
