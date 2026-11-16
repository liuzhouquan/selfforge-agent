"""Security and isolation tests for GitHub PR integration and Git context (SF-INT-01)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

from selfforge_agent.core.git_context import GitContextError, build_context, parse_hunks
from selfforge_agent.integrations import github_pr
from selfforge_agent.integrations.github_pr import GitHubPRError, checkout_pr, parse_pr_url


def test_parse_pr_url_rejects_argument_injection_and_invalid_chars() -> None:
    # 1. Argument injection via leading hyphen or reserved repo names
    for bad_url in (
        "https://github.com/-o/repo/pull/1",
        "https://github.com/--help/repo/pull/1",
        "https://github.com/owner/-repo/pull/1",
        "https://github.com/owner/--repo/pull/1",
        "https://github.com/owner/repo.git/pull/1",
        "https://github.com/owner/./pull/1",
        "https://github.com/owner/../pull/1",
        "https://github.com/./repo/pull/1",
        "https://github.com/../repo/pull/1",
        "https://github.com/owner/repo/pull/0",
        "https://github.com/owner/repo/pull/-1",
        "https://github.com/owner/repo/pull/2147483648",
        "https://user:pass@github.com/owner/repo/pull/1",
        "https://github.com:443/owner/repo/pull/1",
        "https://github.com/owner/repo/pull/1\x00",
        "https://github.com/owner/repo/pull/1\r\n",
        f"https://github.com/{'a' * 40}/repo/pull/1",
        f"https://github.com/owner/{'b' * 101}/pull/1",
    ):
        with pytest.raises(GitHubPRError, match="GitHub PR URL|owner|repo|number"):
            parse_pr_url(bad_url)


def test_checkout_pr_rejects_symlink_workspace_root_and_parent(tmp_path: Path) -> None:
    # 1. Symlink workspace_root
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    sym_root = tmp_path / "sym_root"
    sym_root.symlink_to(real_root)

    with pytest.raises(GitHubPRError, match="symlink|directory"):
        checkout_pr("https://github.com/owner/repo/pull/1", sym_root)

    # 2. Symlink parent directory
    sym_parent = tmp_path / "sym_parent"
    sym_parent.symlink_to(real_root)
    nested_root = sym_parent / "nested_workspace"

    with pytest.raises(GitHubPRError, match="symlink|directory"):
        checkout_pr("https://github.com/owner/repo/pull/1", nested_root)

    # 3. FIFO or file workspace_root
    fifo_root = tmp_path / "fifo_root"
    os.mkfifo(fifo_root)
    with pytest.raises(GitHubPRError, match="directory|FIFO"):
        checkout_pr("https://github.com/owner/repo/pull/1", fifo_root)


def test_run_validates_timeout_and_bounds_output(tmp_path: Path) -> None:
    for bad_timeout in (0, -1, float("nan"), float("inf"), True, False, 300.1):
        with pytest.raises(GitHubPRError, match="(?i)timeout"):
            github_pr._run(["git", "version"], timeout=bad_timeout)  # type: ignore[arg-type]

    # symlink cwd rejected
    real_dir = tmp_path / "real_cwd"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_cwd"
    sym_dir.symlink_to(real_dir)
    with pytest.raises(GitHubPRError, match="symlink|directory"):
        github_pr._run(["git", "version"], cwd=sym_dir)


def test_git_context_build_context_rejects_symlink_repository(
    tmp_path: Path, demo_repo: tuple[Path, str, str]
) -> None:
    repo, base, head = demo_repo
    sym_repo = tmp_path / "sym_repo"
    sym_repo.symlink_to(repo)

    with pytest.raises(GitContextError, match="symlink|regular directory"):
        build_context(sym_repo, base, head)


def test_git_context_normalizes_sha_case_and_rejects_null_bytes(
    demo_repo: tuple[Path, str, str]
) -> None:
    repo, base, head = demo_repo
    # uppercase hex SHA should be accepted and normalized to lowercase
    upper_base = base.upper()
    upper_head = head.upper()
    context = build_context(repo, upper_base, upper_head)
    assert context.base_sha == base.lower()
    assert context.head_sha == head.lower()

    # Null bytes in base or head
    with pytest.raises(GitContextError, match="(?i)commit|hexadecimal SHA|null byte"):
        build_context(repo, base + "\x00", head)


def test_parse_hunks_bounds_lines_and_rejects_null_bytes() -> None:
    # Null bytes in patch
    with pytest.raises(GitContextError, match="null byte|patch"):
        parse_hunks("@@ -1,1 +1,1 @@\n+content\x00")

    # Excessive patch lines
    oversized = "@@ -1,1 +1,50001 @@\n" + ("+line\n" * 50001)
    with pytest.raises(GitContextError, match="line limit|patch size"):
        parse_hunks(oversized)

    # Negative or invalid hunk range numbers
    with pytest.raises(GitContextError, match="hunk header|line number"):
        parse_hunks("@@ -99999999999999999999,1 +1,1 @@\n+line")


def test_api_rejects_malicious_endpoints() -> None:
    for bad_endpoint in ("--help", "-X", "repos/owner/repo\x00", "repos/owner/repo\r\n"):
        with pytest.raises(GitHubPRError, match="API endpoint"):
            github_pr._api(bad_endpoint)


def test_checkout_pr_rejects_existing_destination_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from selfforge_agent.models import ChangedFile, PRContext

    context = PRContext(
        repository="https://github.com/example/project",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=[ChangedFile(path="app.py", status="M", hunks=[])],
    )
    fake_pr = github_pr.GitHubPR(
        url="https://github.com/example/project/pull/1",
        owner="example",
        repo="project",
        number=1,
        title="Test PR",
        author="alice",
        state="open",
        base_branch="main",
        head_branch="feature",
        head_repository=None,
        maintainer_can_modify=False,
        context=context,
        warnings=[],
    )
    monkeypatch.setattr(github_pr, "load_pr", lambda _url: fake_pr)

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    # Pre-create a colliding destination directory
    monkeypatch.setattr(github_pr, "uuid4", lambda: type("U", (), {"hex": "123456"})())
    colliding = workspace_root / f"example-project-pr1-{'b' * 8}-123456"
    colliding.mkdir()

    with pytest.raises(GitHubPRError, match="already exists"):
        checkout_pr("https://github.com/example/project/pull/1", workspace_root)

