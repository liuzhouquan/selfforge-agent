"""Delivery workflow: commit worktree changes and create pull requests."""

import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from selfforge_agent.coding.worktree import WorktreeError, git, git_bounded
from selfforge_agent.runtime.execution import workspace_entry_excluded
from selfforge_agent.runtime.fingerprint import WorkspaceSnapshotError, workspace_fingerprint
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace
from selfforge_agent.runtime.workspace_diff import MAX_PATH_LIST_BYTES, _visible

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


class RevertResult(BaseModel):
    session_id: str
    branch: str
    reverted_files: list[str] = Field(default_factory=list)


def _paths(workspace: CodingWorkspace, *args: str) -> list[str]:
    try:
        output, truncated = git_bounded(workspace.path, *args, max_bytes=MAX_PATH_LIST_BYTES)
    except WorktreeError as exc:
        raise DeliveryError(f"Git query failed: {exc}") from exc
    if truncated:
        raise DeliveryError("Changed-path list exceeds the delivery limit")
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

    tracked = _paths(workspace, "diff", "--no-renames", "--name-only", "-z", "HEAD")
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    visible_changes = [path for path in set(tracked + untracked) if _visible(path)]

    cached_before = _paths(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z")
    if any(not _visible(path) for path in cached_before):
        raise DeliveryError("Protected files are already staged; unstage them before committing")
    visible_cached = cached_before

    if not visible_changes and not visible_cached:
        raise DeliveryError("No changes to commit in this workspace")

    if visible_changes:
        try:
            # Stage files in batches using git add
            git(workspace.path, "--literal-pathspecs", "add", "--", *visible_changes)
        except WorktreeError as exc:
            raise DeliveryError(f"Failed to stage changes: {exc}") from exc

    cached_now = _paths(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z")
    if any(not _visible(path) for path in cached_now):
        raise DeliveryError("Protected files are staged; unstage them before committing")
    visible_to_commit = cached_now
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


def revert_workspace(
    workspace: CodingWorkspace,
    session: SessionStore | None = None,
) -> RevertResult:
    """Discard all uncommitted changes and remove untracked files in the worktree."""
    tracked = _paths(workspace, "diff", "--no-renames", "--name-only", "-z", "HEAD")
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    cached = _paths(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z")

    visible_tracked = [p for p in set(tracked + cached) if _visible(p)]
    visible_untracked = [p for p in untracked if _visible(p)]

    if session is not None and (visible_tracked or visible_untracked):
        session.record_verification(False)

    reverted: list[str] = []
    if visible_tracked:
        try:
            git(workspace.path, "--literal-pathspecs", "restore", "--source=HEAD",
                "--staged", "--worktree", "--", *visible_tracked)
            reverted.extend(visible_tracked)
        except WorktreeError as exc:
            raise DeliveryError(f"Failed to revert tracked changes: {exc}") from exc

    if visible_untracked:
        for rel in visible_untracked:
            target = workspace.path / rel
            try:
                if not target.parent.resolve(strict=True).is_relative_to(workspace.path):
                    continue
                if target.is_symlink() or target.is_file():
                    # Unlink the entry, never the target of an untracked symlink.
                    target.unlink(missing_ok=True)
                    reverted.append(rel)
            except OSError as exc:
                raise DeliveryError(f"Failed to delete untracked file {rel}: {exc}") from exc

    return RevertResult(
        session_id=workspace.session_id,
        branch=workspace.branch,
        reverted_files=sorted(set(reverted)),
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


def _existing_pull_request(workspace: CodingWorkspace, base_branch: str | None) -> dict | None:
    args = ["pr", "list", "--state", "open", "--head", workspace.branch,
            "--json", "number,title,url,baseRefName"]
    if base_branch:
        args.extend(["--base", base_branch])
    output = _run_gh(args, cwd=workspace.repository)
    try:
        requests = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise DeliveryError("Could not read the existing-PR list") from exc
    if not isinstance(requests, list) or any(not isinstance(pr, dict) for pr in requests):
        raise DeliveryError("GitHub returned an invalid existing-PR list")
    if len(requests) > 1:
        raise DeliveryError("Multiple open PRs use this head branch; specify a base branch")
    if not requests:
        return None
    pr = requests[0]
    if (not isinstance(pr.get("url"), str) or not _PR_URL_PATTERN.fullmatch(pr["url"])
            or type(pr.get("number")) is not int or not isinstance(pr.get("title"), str)):
        raise DeliveryError("GitHub returned an incomplete existing-PR record")
    return pr


def _verified_commit(workspace: CodingWorkspace, session: SessionStore | None,
                     commit_sha: str) -> bool:
    saved = session.verification_snapshot() if session is not None else None
    if saved is None:
        return False
    try:
        changed = _paths(workspace, "diff", "--name-only", "-z", commit_sha)
        # Include ignored inputs and empty directories, but collapse wholly
        # untracked directories so excluded caches do not flood the path list.
        changed += _paths(workspace, "ls-files", "--others", "--directory", "-z")
        if any(not any(workspace_entry_excluded(part) for part in Path(path).parts)
               for path in changed):
            return False
        return workspace_fingerprint(workspace.path) == saved
    except (DeliveryError, WorkspaceSnapshotError, OSError, TimeoutError):
        return False


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

    commit_sha = git(workspace.path, "rev-parse", "HEAD").strip()

    # Verify that commits exist ahead of base
    try:
        ahead_count = int(
            git(workspace.path, "rev-list", "--count", f"{workspace.base_sha}..{commit_sha}").strip()
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
        git(workspace.path, "push", "--", remote, f"{commit_sha}:refs/heads/{workspace.branch}")
    except WorktreeError as exc:
        raise DeliveryError(f"Failed to push branch to remote: {exc}") from exc

    # Recover an already-created PR after a lost response without creating duplicates.
    existing = _existing_pull_request(workspace, base_branch)
    if existing is not None:
        return PRResult(session_id=workspace.session_id, branch=workspace.branch,
                        commit_sha=commit_sha, pr_url=existing["url"],
                        pr_number=existing["number"], title=existing["title"])

    # Attach evidence to this specific pushed commit, rather than a mutable branch.
    pr_body = body.strip()
    if session_store:
        passed = _verified_commit(workspace, session_store, commit_sha)
        evidence_note = (
            f"\n\n---\n**Verification for commit `{commit_sha}`**: "
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
    if match is None:
        raise DeliveryError("GitHub did not return a valid PR URL; retry to recover an existing PR")
    pr_url = match.group(0)
    pr_number = int(match.group("number"))

    return PRResult(
        session_id=workspace.session_id,
        branch=workspace.branch,
        commit_sha=commit_sha,
        pr_url=pr_url,
        pr_number=pr_number,
        title=title.strip(),
    )
