"""Delivery workflow: commit worktree changes and create pull requests."""

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from selfforge_agent.coding.worktree import WorktreeError, git
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace
from selfforge_agent.runtime.workspace_diff import _visible

_PR_URL_PATTERN = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/pull/(?P<number>[1-9][0-9]*)"
)


class DeliveryError(ValueError):
    """A commit or pull request operation failed or was rejected."""


class CommitResult(BaseModel):
    session_id: str
    branch: str
    commit_sha: str
    message: str
    files_committed: list[str] = Field(default_factory=list)


class PRResult(BaseModel):
    session_id: str
    branch: str
    commit_sha: str
    pr_url: str
    pr_number: int | None = None
    title: str


def _paths(workspace: CodingWorkspace, *args: str) -> list[str]:
    try:
        output = git(workspace.path, *args)
    except WorktreeError as exc:
        raise DeliveryError(f"Git query failed: {exc}") from exc
    return [p for p in output.split("\0") if p]


def commit_workspace(
    workspace: CodingWorkspace,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
) -> CommitResult:
    """Stage allowed modified and untracked files and commit them to the worktree branch."""
    if not message or not message.strip():
        raise DeliveryError("Commit message must not be empty")

    tracked = _paths(workspace, "diff", "--name-only", "-z", "HEAD")
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    visible_changes = [path for path in set(tracked + untracked) if _visible(path)]

    cached_before = _paths(workspace, "diff", "--cached", "--name-only", "-z")
    visible_cached = [path for path in cached_before if _visible(path)]

    if not visible_changes and not visible_cached:
        raise DeliveryError("No changes to commit in this workspace")

    if visible_changes:
        try:
            # Stage files in batches using git add
            git(workspace.path, "add", "--", *visible_changes)
        except WorktreeError as exc:
            raise DeliveryError(f"Failed to stage changes: {exc}") from exc

    cached_now = _paths(workspace, "diff", "--cached", "--name-only", "-z")
    visible_to_commit = [path for path in cached_now if _visible(path)]
    if not visible_to_commit:
        raise DeliveryError("No changes to commit in this workspace")

    cmd = ["commit", "-m", message.strip()]
    # Use explicit author, repository config, or sensible fallback for headless environments
    try:
        config_name = git(workspace.path, "config", "user.name").strip() or None
    except WorktreeError:
        config_name = None
    try:
        config_email = git(workspace.path, "config", "user.email").strip() or None
    except WorktreeError:
        config_email = None

    effective_name = author_name or config_name or "selfForge Agent"
    effective_email = author_email or config_email or "agent@selfforge.local"

    cmd = ["-c", f"user.name={effective_name}", "-c", f"user.email={effective_email}"] + cmd

    try:
        git(workspace.path, *cmd)
    except WorktreeError as exc:
        raise DeliveryError(f"Git commit failed: {exc}") from exc

    commit_sha = git(workspace.path, "rev-parse", "HEAD").strip()
    return CommitResult(
        session_id=workspace.session_id,
        branch=workspace.branch,
        commit_sha=commit_sha,
        message=message.strip(),
        files_committed=sorted(visible_to_commit),
    )


def _run_gh(args: list[str], *, cwd: Path, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DeliveryError(
            "GitHub CLI ('gh') is not installed or not in PATH. Please install and authenticate 'gh'."
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError(f"GitHub operation failed: {exc}") from exc

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "GitHub operation failed"
        raise DeliveryError(f"GitHub PR creation failed: {error_msg}")
    return result.stdout


def create_pull_request(
    workspace: CodingWorkspace,
    title: str,
    *,
    body: str = "",
    base_branch: str | None = None,
    remote: str = "origin",
    draft: bool = False,
    session_store: SessionStore | None = None,
) -> PRResult:
    """Push branch to remote and create a GitHub pull request using gh CLI."""
    if not title or not title.strip():
        raise DeliveryError("PR title must not be empty")

    # Verify that commits exist ahead of base
    try:
        ahead_count = int(
            git(workspace.path, "rev-list", "--count", f"{workspace.base_sha}..HEAD").strip()
        )
    except (WorktreeError, ValueError) as exc:
        raise DeliveryError(f"Could not verify commits ahead of base: {exc}") from exc

    if ahead_count == 0:
        raise DeliveryError(
            "No commits ahead of base; commit your changes before creating a pull request"
        )

    # Check remote existence
    try:
        remotes = [r.strip() for r in git(workspace.repository, "remote").splitlines() if r.strip()]
    except WorktreeError as exc:
        raise DeliveryError(f"Failed to query remotes: {exc}") from exc

    if remote not in remotes:
        raise DeliveryError(f"Remote '{remote}' does not exist in repository")

    # Push branch without force
    try:
        git(workspace.path, "push", "--set-upstream", remote, workspace.branch)
    except WorktreeError as exc:
        raise DeliveryError(f"Failed to push branch to remote: {exc}") from exc

    # Prepare PR body with verification metadata if session is available
    pr_body = body.strip()
    if session_store:
        passed = session_store.verification_passed()
        evidence_note = (
            f"\n\n---\n**Verification Evidence**: "
            f"{'Verified (tests passed with workspace content match)' if passed else 'Unverified'}\n"
            f"**Session ID**: `{workspace.session_id}`"
        )
        pr_body = (pr_body + evidence_note).strip()

    gh_args = [
        "pr", "create",
        "--head", workspace.branch,
        "--title", title.strip(),
        "--body", pr_body,
    ]
    if base_branch:
        gh_args.extend(["--base", base_branch])
    if draft:
        gh_args.append("--draft")

    output = _run_gh(gh_args, cwd=workspace.repository)
    match = _PR_URL_PATTERN.search(output)
    pr_url = match.group(0) if match else output.strip().splitlines()[-1]
    pr_number = int(match.group("number")) if match else None
    commit_sha = git(workspace.path, "rev-parse", "HEAD").strip()

    return PRResult(
        session_id=workspace.session_id,
        branch=workspace.branch,
        commit_sha=commit_sha,
        pr_url=pr_url,
        pr_number=pr_number,
        title=title.strip(),
    )
