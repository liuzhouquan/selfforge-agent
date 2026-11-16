"""Security and input defense tests for GitHub PR integration (SF-GH-01)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure local worktree src is loaded first
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from selfforge_agent.integrations import github_pr
from selfforge_agent.integrations.github_pr import (
    GitHubPRError,
    checkout_pr,
    load_pr,
    parse_pr_url,
)


def test_parse_pr_url_rejects_dangerous_owner_and_repo_names() -> None:
    bad_urls = [
        # Hyphen-prefixed owner/repo (flag injection in gh repo clone)
        "https://github.com/-evil/repo/pull/1",
        "https://github.com/alice/-repo/pull/1",
        "https://github.com/--flag/repo/pull/1",
        # Path traversal dot names
        "https://github.com/alice/../pull/1",
        "https://github.com/alice/./pull/1",
        "https://github.com/../repo/pull/1",
        # Disallowed suffixes
        "https://github.com/alice/repo.git/pull/1",
        # Non-https or bad host
        "http://github.com/alice/repo/pull/1",
        "https://gitlab.com/alice/repo/pull/1",
        "https://github.com/alice/repo/issues/1",
        # Zero, negative or overflow PR numbers
        "https://github.com/alice/repo/pull/0",
        "https://github.com/alice/repo/pull/-5",
        "https://github.com/alice/repo/pull/99999999999999999999",
        # Control characters and whitespace
        "https://github.com/alice\r\n/repo/pull/1",
        "https://github.com/alice/repo\x00/pull/1",
        "https://github.com/alice/repo /pull/1",
        "",
        "not a url",
    ]
    for url in bad_urls:
        with pytest.raises(GitHubPRError):
            parse_pr_url(url)

    # Valid URLs
    owner, repo, number = parse_pr_url("https://github.com/alice/good-repo/pull/42")
    assert owner == "alice"
    assert repo == "good-repo"
    assert number == 42


def test_load_pr_rejects_path_traversal_and_malformed_filenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "title": "Fix thing",
        "state": "open",
        "user": {"login": "alice"},
        "base": {"sha": "a" * 40, "ref": "main"},
        "head": {"sha": "b" * 40, "ref": "fix", "repo": {"full_name": "alice/repo"}},
        "maintainer_can_modify": True,
    }

    # Malicious changed file paths
    for bad_file in (
        "../../etc/passwd",
        "/etc/passwd",
        "foo/../../bar.py",
        "app\x00.py",
        "app\r\n.py",
        "app\t.py",
        "",
        "   ",
        None,
    ):
        files = [{"filename": bad_file, "status": "modified"}]
        monkeypatch.setattr(
            github_pr,
            "_api",
            lambda endpoint, cur_files=files: cur_files if "/files?" in endpoint else metadata,
        )
        with pytest.raises(GitHubPRError):
            load_pr("https://github.com/alice/repo/pull/7")


def test_load_pr_normalizes_changed_file_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        "title": "Fix thing",
        "state": "open",
        "user": {"login": "alice"},
        "base": {"sha": "a" * 40, "ref": "main"},
        "head": {"sha": "b" * 40, "ref": "fix", "repo": {"full_name": "alice/repo"}},
        "maintainer_can_modify": True,
    }

    # Standard status mappings
    files = [
        {"filename": "a.py", "status": "added"},
        {"filename": "m.py", "status": "modified"},
        {"filename": "d.py", "status": "removed"},
        {"filename": "r.py", "status": "renamed"},
        {"filename": "c.py", "status": "copied"},
    ]
    monkeypatch.setattr(
        github_pr, "_api", lambda endpoint: files if "/files?" in endpoint else metadata
    )
    pr = load_pr("https://github.com/alice/repo/pull/7")
    statuses = [f.status for f in pr.context.files]
    assert statuses == ["A", "M", "D", "R", "C"]

    # Unknown or malformed status should be rejected safely
    for bad_status in ("evil-unknown-status", "", 123, None):
        bad_status_files = [{"filename": "x.py", "status": bad_status}]
        monkeypatch.setattr(
            github_pr,
            "_api",
            lambda endpoint, cur_bad=bad_status_files: cur_bad if "/files?" in endpoint else metadata,
        )
        with pytest.raises(GitHubPRError):
            load_pr("https://github.com/alice/repo/pull/7")


def test_load_pr_rejects_malformed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Non-dict metadata
    monkeypatch.setattr(github_pr, "_api", lambda _ep: ["not", "a", "dict"])
    with pytest.raises(GitHubPRError):
        load_pr("https://github.com/alice/repo/pull/7")

    # 2. Missing base or head sha
    for bad_sha in ("short", "G" * 40, "a" * 39, "a" * 41, "", None):
        bad_meta = {
            "title": "Fix",
            "state": "open",
            "user": {"login": "alice"},
            "base": {"sha": bad_sha, "ref": "main"},
            "head": {"sha": "b" * 40, "ref": "fix"},
        }
        monkeypatch.setattr(github_pr, "_api", lambda _ep, m=bad_meta: m)
        with pytest.raises(GitHubPRError):
            load_pr("https://github.com/alice/repo/pull/7")

    # 3. Missing login or empty title
    for missing_field_meta in (
        {
            "title": "",
            "state": "open",
            "user": {"login": "alice"},
            "base": {"sha": "a" * 40, "ref": "main"},
            "head": {"sha": "b" * 40, "ref": "fix"},
        },
        {
            "title": "Fix",
            "state": "open",
            "user": {"login": ""},
            "base": {"sha": "a" * 40, "ref": "main"},
            "head": {"sha": "b" * 40, "ref": "fix"},
        },
    ):
        monkeypatch.setattr(github_pr, "_api", lambda _ep, m=missing_field_meta: m)
        with pytest.raises(GitHubPRError):
            load_pr("https://github.com/alice/repo/pull/7")


def test_checkout_pr_rejects_symlink_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "real_dir"
    target_dir.mkdir()
    symlink_root = tmp_path / "symlink_root"
    os.symlink(target_dir, symlink_root)

    with pytest.raises(GitHubPRError, match="symlink"):
        checkout_pr("https://github.com/alice/repo/pull/7", symlink_root)

    # Workspace root is a regular file
    file_root = tmp_path / "regular_file"
    file_root.write_text("not a dir")
    with pytest.raises(GitHubPRError, match="directory"):
        checkout_pr("https://github.com/alice/repo/pull/7", file_root)


def test_command_execution_bounded_output_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the real producer instead of mocking post-hoc captured output.
    import sys

    with pytest.raises(GitHubPRError, match="size"):
        github_pr._run([sys.executable, "-c", "import os;os.write(1,b'A'*2048)"], max_output_bytes=1024)

    # 2. Invalid timeouts (NaN, negative, zero, boolean)
    for bad_timeout in (float("nan"), float("inf"), -1.0, 0, True, False, "60"):
        with pytest.raises(GitHubPRError, match="Timeout"):
            github_pr._run(["echo", "test"], timeout=bad_timeout)  # type: ignore[arg-type]

    # 3. Bad API endpoints (flag injection)
    with pytest.raises(GitHubPRError, match="Invalid API endpoint"):
        github_pr._api("--help")
    with pytest.raises(GitHubPRError, match="Invalid API endpoint"):
        github_pr._api("-X")


def _valid_metadata():
    return {"title": "Fix", "state": "open", "user": {"login": "alice"},
            "base": {"sha": "A" * 40, "ref": "main"},
            "head": {"sha": "B" * 40, "ref": "feature", "repo": None},
            "maintainer_can_modify": False}


@pytest.mark.parametrize("name", [".github", "_tools", "tools_", "tools-", "tools."])
def test_valid_repository_names_remain_accepted(name: str) -> None:
    assert parse_pr_url(f"https://github.com/alice/{name}/pull/1") == ("alice", name, 1)


def test_load_pr_preserves_exact_whitespace_in_git_filename(monkeypatch) -> None:
    path = " space.py "
    metadata = _valid_metadata()
    files = [{"filename": path, "status": "added", "patch": "@@ -0,0 +1 @@\n+eval('x')"}]
    monkeypatch.setattr(github_pr, "_api", lambda endpoint: files if "/files?" in endpoint else metadata)
    report = load_pr("https://github.com/alice/repo/pull/1")
    assert report.context.files[0].path == path
    assert report.context.files[0].hunks[0].lines[0].content == "eval('x')"


@pytest.mark.parametrize("field,value", [("title", None), ("state", None), ("base_ref", []),
                                        ("head_ref", None), ("login", ["fake"]), ("permission", "false")])
def test_load_pr_rejects_metadata_that_would_be_silently_coerced(monkeypatch, field, value) -> None:
    metadata = _valid_metadata()
    if field in {"title", "state"}:
        metadata[field] = value
    elif field in {"base_ref", "head_ref"}:
        metadata[field.split("_")[0]]["ref"] = value
    elif field == "login":
        metadata["user"]["login"] = value
    else:
        metadata["maintainer_can_modify"] = value
    monkeypatch.setattr(github_pr, "_api", lambda endpoint: [] if "/files?" in endpoint else metadata)
    with pytest.raises(GitHubPRError):
        load_pr("https://github.com/alice/repo/pull/1")


@pytest.mark.parametrize("path", ["dir/../file.py", "bad.py\n", "bad.py\t"])
def test_changed_paths_cannot_hide_traversal_or_controls_by_normalization(monkeypatch, path) -> None:
    monkeypatch.setattr(github_pr, "_api", lambda endpoint:
                        [{"filename": path, "status": "added"}] if "/files?" in endpoint else _valid_metadata())
    with pytest.raises(GitHubPRError):
        load_pr("https://github.com/alice/repo/pull/1")
