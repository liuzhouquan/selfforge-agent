"""Delivery workflow: commit worktree changes and create pull requests."""

import json
import math
import os
import posixpath
import re
import stat
import tempfile
from contextlib import ExitStack
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from selfforge_agent.coding.worktree import WorktreeError, git, git_bounded
from selfforge_agent.core.process import run_bounded
from selfforge_agent.runtime.execution import workspace_entry_excluded
from selfforge_agent.runtime.fingerprint import WorkspaceSnapshotError, workspace_fingerprint
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.workspace import CodingWorkspace
from selfforge_agent.runtime.workspace_diff import MAX_PATH_LIST_BYTES, _visible

_PR_URL_SEARCH_RE = re.compile(
    r"https://github\.com/(?P<owner>[a-zA-Z0-9][a-zA-Z0-9._-]*)/(?P<repo>[a-zA-Z0-9._-]+)/pull/(?P<number>[1-9][0-9]*)"
)
_PR_URL_EXACT_RE = re.compile(
    r"^https://github\.com/(?P<owner>[a-zA-Z0-9][a-zA-Z0-9._-]*)/(?P<repo>[a-zA-Z0-9._-]+)/pull/(?P<number>[1-9][0-9]*)/?$"
)
_PR_URL_PATTERN = _PR_URL_SEARCH_RE
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_REMOTE_NAME_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_SESSION_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_BATCH_SIZE = 100
MAX_EXISTING_PRS = 100
MAX_COMMIT_MESSAGE_LENGTH = 64 * 1024
MAX_PR_TITLE_LENGTH = 1024
MAX_PR_BODY_BYTES = 256 * 1024
MAX_GH_TIMEOUT = 300
MAX_GH_OUTPUT_BYTES = 10 * 1024 * 1024


class DeliveryError(ValueError):
    """A commit or pull request operation failed or was rejected."""


def _validate_commit_sha(sha: object) -> str:
    if not isinstance(sha, str):
        raise TypeError("commit_sha must be a string")
    norm = sha.strip().lower()
    if not _SHA40_RE.match(norm):
        raise ValueError(f"commit_sha must be a 40-character hexadecimal string: {sha}")
    return norm


def _validate_session_id(sess_id: object) -> str:
    if not isinstance(sess_id, str):
        raise TypeError("session_id must be a string")
    sess = sess_id.strip()
    if not _SESSION_ID_SAFE_RE.match(sess) or ".." in sess or "/" in sess or "\\" in sess:
        raise ValueError(f"Invalid session_id: {sess_id}")
    return sess


def _validate_git_ref_name(ref: object, param_name: str = "branch") -> str:
    if not isinstance(ref, str):
        raise TypeError(f"{param_name} must be a string")
    r = ref
    if (
        not r
        or len(r) > 256
        or r.startswith("-")
        or "\0" in r
        or "\n" in r
        or "\r" in r
        or any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in r)
        or r.startswith("/") or r == "@"
        or any(p.startswith(".") or p.endswith(".lock") for p in r.split("/"))
        or ".." in r
        or "//" in r
        or "@{" in r
        or r.endswith(("/", ".lock", "."))
    ):
        raise DeliveryError(f"Invalid {param_name} name: {ref}")
    return r


def _validate_relative_path(path: object) -> str:
    if not isinstance(path, str) or not path.strip():
        raise TypeError("path must be a non-empty string")
    p = path
    if "\0" in p or "\r" in p or "\n" in p:
        raise ValueError("path must not contain control characters or null bytes")
    if p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", p):
        raise ValueError(f"Absolute paths not permitted: {path}")
    norm = posixpath.normpath(p)
    if norm in (".", "..") or norm.startswith("../"):
        raise ValueError(f"Path traversal not permitted: {path}")
    return p


class CommitResult(BaseModel):
    session_id: str
    branch: str
    commit_sha: str
    message: str
    files_committed: list[str] = Field(default_factory=list)

    @field_validator("commit_sha", mode="before")
    @classmethod
    def _check_sha(cls, v: object) -> str:
        return _validate_commit_sha(v)

    @field_validator("session_id", mode="before")
    @classmethod
    def _check_session(cls, v: object) -> str:
        return _validate_session_id(v)

    @field_validator("branch", mode="before")
    @classmethod
    def _check_branch(cls, v: object) -> str:
        try:
            return _validate_git_ref_name(v, "branch")
        except DeliveryError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("message", mode="before")
    @classmethod
    def _check_message(cls, v: object) -> str:
        if not isinstance(v, str):
            raise TypeError("message must be a string")
        msg = v.strip()
        if not msg:
            raise ValueError("message must not be empty")
        if "\0" in v:
            raise ValueError("message must not contain null bytes")
        if len(v) > MAX_COMMIT_MESSAGE_LENGTH:
            raise ValueError(f"message exceeds maximum length of {MAX_COMMIT_MESSAGE_LENGTH}")
        return msg

    @field_validator("files_committed", mode="before")
    @classmethod
    def _check_files(cls, v: object) -> list[str]:
        if not isinstance(v, (list, tuple)):
            raise TypeError("files_committed must be a list")
        return [_validate_relative_path(p) for p in v]


class PRResult(BaseModel):
    session_id: str
    branch: str
    commit_sha: str
    pr_url: str
    pr_number: int | None = None
    title: str

    @field_validator("commit_sha", mode="before")
    @classmethod
    def _check_sha(cls, v: object) -> str:
        return _validate_commit_sha(v)

    @field_validator("session_id", mode="before")
    @classmethod
    def _check_session(cls, v: object) -> str:
        return _validate_session_id(v)

    @field_validator("branch", mode="before")
    @classmethod
    def _check_branch(cls, v: object) -> str:
        try:
            return _validate_git_ref_name(v, "branch")
        except DeliveryError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("pr_url", mode="before")
    @classmethod
    def _check_url(cls, v: object) -> str:
        if not isinstance(v, str):
            raise TypeError("pr_url must be a string")
        url = v.strip()
        if not _PR_URL_EXACT_RE.fullmatch(url):
            raise ValueError(f"Invalid pr_url format: {v}")
        return url

    @field_validator("pr_number", mode="before")
    @classmethod
    def _check_pr_number(cls, v: object) -> int | None:
        if v is None:
            return None
        if type(v) is not int or v <= 0:
            raise ValueError(f"pr_number must be a positive integer: {v}")
        return v

    @field_validator("title", mode="before")
    @classmethod
    def _check_title(cls, v: object) -> str:
        if not isinstance(v, str):
            raise TypeError("title must be a string")
        t = v.strip()
        if not t:
            raise ValueError("title must not be empty")
        if "\n" in v or "\r" in v or "\0" in v:
            raise ValueError("title must not contain newlines or null bytes")
        if len(v) > MAX_PR_TITLE_LENGTH:
            raise ValueError(f"title exceeds maximum length of {MAX_PR_TITLE_LENGTH}")
        return t


class RevertResult(BaseModel):
    session_id: str
    branch: str
    reverted_files: list[str] = Field(default_factory=list)

    @field_validator("session_id", mode="before")
    @classmethod
    def _check_session(cls, v: object) -> str:
        return _validate_session_id(v)

    @field_validator("branch", mode="before")
    @classmethod
    def _check_branch(cls, v: object) -> str:
        try:
            return _validate_git_ref_name(v, "branch")
        except DeliveryError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("reverted_files", mode="before")
    @classmethod
    def _check_files(cls, v: object) -> list[str]:
        if not isinstance(v, (list, tuple)):
            raise TypeError("reverted_files must be a list")
        return [_validate_relative_path(p) for p in v]


def _validate_workspace(workspace: CodingWorkspace) -> None:
    if not isinstance(workspace, CodingWorkspace):
        raise TypeError("workspace must be a CodingWorkspace instance")
    if not isinstance(workspace.path, Path):
        raise TypeError("workspace.path must be a Path instance")
    if workspace.path.is_symlink() or not workspace.path.is_dir():
        raise DeliveryError(f"Workspace path is invalid or a symlink: {workspace.path}")
    if not isinstance(workspace.repository, Path):
        raise TypeError("workspace.repository must be a Path instance")
    if workspace.repository.is_symlink() or not workspace.repository.is_dir():
        raise DeliveryError(f"Workspace repository is invalid or a symlink: {workspace.repository}")
    if not isinstance(workspace.session_id, str):
        raise TypeError("workspace.session_id must be a string")
    sess = workspace.session_id.strip()
    if not _SESSION_ID_SAFE_RE.match(sess) or ".." in sess or "/" in sess or "\\" in sess:
        raise DeliveryError(f"Invalid workspace session_id: {workspace.session_id}")
    _validate_git_ref_name(workspace.branch, "workspace branch")
    if not isinstance(workspace.base_sha, str):
        raise TypeError("workspace.base_sha must be a string")
    base_sha = workspace.base_sha.strip().lower()
    if not _SHA40_RE.match(base_sha):
        raise DeliveryError(f"Invalid workspace base_sha: {workspace.base_sha}")


def _paths(workspace: CodingWorkspace, *args: str) -> list[str]:
    _validate_workspace(workspace)
    try:
        output, truncated = git_bounded(workspace.path, *args, max_bytes=MAX_PATH_LIST_BYTES)
    except WorktreeError as exc:
        raise DeliveryError(f"Git query failed: {exc}") from exc
    if truncated:
        raise DeliveryError("Changed-path list exceeds the delivery limit")
    return [p for p in output.split("\0") if p]


def _sanitize_git_author_field(val: str | None) -> str | None:
    if not isinstance(val, str):
        return None
    v = val.strip()
    if not v or len(v) > 256 or "\n" in v or "\r" in v or "\0" in v or v.startswith("-"):
        return None
    return v


def commit_workspace(
    workspace: CodingWorkspace,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
) -> CommitResult:
    """Stage allowed modified and untracked files and commit them to the worktree branch."""
    _validate_workspace(workspace)
    if not isinstance(message, str):
        raise TypeError("Commit message must be a string")
    if not message.strip():
        raise DeliveryError("Commit message must not be empty")
    if "\0" in message:
        raise DeliveryError("Commit message must not contain null bytes")
    if len(message) > MAX_COMMIT_MESSAGE_LENGTH:
        raise DeliveryError(f"Commit message exceeds maximum length of {MAX_COMMIT_MESSAGE_LENGTH} characters")

    if author_name is not None:
        if not isinstance(author_name, str):
            raise TypeError("author_name must be a string")
        if "\n" in author_name or "\r" in author_name or "\0" in author_name:
            raise DeliveryError("author_name must not contain newlines or null bytes")
        if author_name.startswith("-"):
            raise DeliveryError("author_name must not start with a hyphen")
        if len(author_name) > 256 or not author_name.strip():
            raise DeliveryError("author_name must be a non-empty string up to 256 characters")

    if author_email is not None:
        if not isinstance(author_email, str):
            raise TypeError("author_email must be a string")
        if "\n" in author_email or "\r" in author_email or "\0" in author_email:
            raise DeliveryError("author_email must not contain newlines or null bytes")
        if author_email.startswith("-"):
            raise DeliveryError("author_email must not start with a hyphen")
        if len(author_email) > 256 or not author_email.strip():
            raise DeliveryError("author_email must be a non-empty string up to 256 characters")

    tracked = _paths(workspace, "diff", "--no-renames", "--name-only", "-z", "HEAD")
    untracked = _paths(workspace, "ls-files", "--others", "--exclude-standard", "-z")

    def _safe_commit_path(p: str) -> bool:
        if not isinstance(p, str) or not p or "\0" in p:
            return False
        if not _visible(p):
            return False
        candidate = workspace.path / p
        try:
            if candidate.is_symlink():
                resolved = candidate.resolve()
                if not resolved.is_relative_to(workspace.path):
                    return False
                if not _visible(resolved.relative_to(workspace.path).as_posix()):
                    return False
                st = os.stat(candidate)
                if not stat.S_ISREG(st.st_mode):
                    return False
            elif candidate.exists():
                if not (candidate.is_file() or candidate.is_dir()):
                    return False
        except (OSError, RuntimeError):
            return False
        return True

    def _validate_staged(paths: list[str]) -> None:
        if any(not _visible(path) for path in paths):
            raise DeliveryError("Protected files are already staged; unstage them before committing")
        if any(not _safe_commit_path(path) for path in paths):
            raise DeliveryError("Protected or unsafe files are staged; unstage them before committing")
        # Validate the index itself: a staged symlink may differ from the current file.
        entries = _paths(workspace, "ls-files", "--stage", "-z")
        selected = set(paths)
        for entry in entries:
            header, name = entry.split("\t", 1)
            mode, object_id, _stage = header.split()
            if name not in selected or mode != "120000":
                continue
            link, truncated = git_bounded(workspace.path, "cat-file", "blob", object_id, max_bytes=4096)
            try:
                resolved = (workspace.path / name).parent.joinpath(link).resolve()
                safe = (not truncated and resolved.is_relative_to(workspace.path)
                        and _visible(resolved.relative_to(workspace.path).as_posix()))
            except (OSError, RuntimeError):
                safe = False
            if not safe:
                raise DeliveryError("Unsafe symlink is staged; unstage it before committing")

    visible_changes = [path for path in set(tracked + untracked) if _safe_commit_path(path)]

    cached_before = _paths(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z")
    _validate_staged(cached_before)
    visible_cached = cached_before

    if not visible_changes and not visible_cached:
        raise DeliveryError("No changes to commit in this workspace")

    if visible_changes:
        try:
            # Stage files in batches using git add
            for i in range(0, len(visible_changes), _BATCH_SIZE):
                batch = visible_changes[i : i + _BATCH_SIZE]
                git(workspace.path, "--literal-pathspecs", "add", "--", *batch)
        except WorktreeError as exc:
            raise DeliveryError(f"Failed to stage changes: {exc}") from exc

    cached_now = _paths(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z")
    _validate_staged(cached_now)
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

    effective_name = author_name or _sanitize_git_author_field(config_name) or "selfForge Agent"
    effective_email = author_email or _sanitize_git_author_field(config_email) or "agent@selfforge.local"

    cmd = ["-c", f"user.name={effective_name}", "-c", f"user.email={effective_email}"] + cmd

    try:
        git(workspace.path, *cmd)
    except WorktreeError as exc:
        raise DeliveryError(f"Git commit failed: {exc}") from exc

    commit_sha = git(workspace.path, "rev-parse", "HEAD").strip().lower()
    if not _SHA40_RE.match(commit_sha):
        raise DeliveryError(f"Invalid commit SHA produced: {commit_sha}")

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
    _validate_workspace(workspace)
    if session is not None:
        if not isinstance(session, SessionStore):
            raise TypeError("session must be a SessionStore instance or None")
        if session.session_id != workspace.session_id:
            raise DeliveryError(
                f"SessionStore session_id '{session.session_id}' does not match "
                f"workspace session_id '{workspace.session_id}'"
            )

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
            for i in range(0, len(visible_tracked), _BATCH_SIZE):
                batch = visible_tracked[i : i + _BATCH_SIZE]
                git(workspace.path, "--literal-pathspecs", "restore", "--source=HEAD",
                    "--staged", "--worktree", "--", *batch)
            reverted.extend(visible_tracked)
        except WorktreeError as exc:
            raise DeliveryError(f"Failed to revert tracked changes: {exc}") from exc

    if visible_untracked:
        for rel in visible_untracked:
            if ".." in rel or "\0" in rel:
                continue
            target = workspace.path / rel
            try:
                if not target.parent.resolve().is_relative_to(workspace.path):
                    continue
                if target.is_symlink() or not target.is_dir():
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


def _run_gh(args: list[str], *, cwd: Path, timeout: float = 60) -> str:
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise TypeError("args must be a list of strings")
    if any("\0" in a for a in args):
        raise DeliveryError("gh args must not contain null bytes")
    if not isinstance(cwd, Path):
        raise TypeError("cwd must be a Path instance")
    if cwd.is_symlink() or not cwd.is_dir():
        raise DeliveryError(f"cwd must be an existing directory without symlinks: {cwd}")
    if (
        not isinstance(timeout, (int, float))
        or type(timeout) is bool
        or timeout <= 0
        or timeout > MAX_GH_TIMEOUT
        or not math.isfinite(timeout)
    ):
        raise DeliveryError(f"timeout must be a positive number up to {MAX_GH_TIMEOUT} seconds")

    try:
        result = run_bounded(["gh", *args], cwd=cwd, timeout=timeout,
                             max_bytes=MAX_GH_OUTPUT_BYTES)
    except FileNotFoundError as exc:
        raise DeliveryError(
            "GitHub CLI ('gh') is not installed or not in PATH. Please install and authenticate 'gh'."
        ) from exc
    except (OSError, TimeoutError, ValueError) as exc:
        raise DeliveryError(f"GitHub operation failed: {exc}") from exc

    if result.stdout_limited or result.stderr_limited:
        raise DeliveryError("GitHub CLI output exceeded maximum allowed size")
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raw_error = stderr.strip() or stdout.strip() or "GitHub operation failed"
        error_msg = raw_error[:1024] if len(raw_error) > 1024 else raw_error
        raise DeliveryError(f"GitHub PR creation failed: {error_msg}")
    return stdout


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
    if len(requests) > MAX_EXISTING_PRS:
        raise DeliveryError(
            f"Existing PR list count ({len(requests)}) exceeds maximum allowed ({MAX_EXISTING_PRS})"
        )
    if len(requests) > 1:
        raise DeliveryError("Multiple open PRs use this head branch; specify a base branch")
    if not requests:
        return None
    pr = requests[0]
    url = pr.get("url")
    number = pr.get("number")
    title = pr.get("title")
    if (
        not isinstance(url, str)
        or not _PR_URL_EXACT_RE.fullmatch(url.strip())
        or type(number) is not int
        or number <= 0
        or not isinstance(title, str)
        or not title.strip()
        or "\0" in title
        or "\r" in title
        or "\n" in title
        or len(title) > MAX_PR_TITLE_LENGTH
    ):
        raise DeliveryError("GitHub returned an incomplete or invalid existing-PR record")
    return pr


def _verified_commit(workspace: CodingWorkspace, session: SessionStore | None,
                     commit_sha: str) -> bool:
    if session is None or session.session_id != workspace.session_id:
        return False
    if not isinstance(commit_sha, str) or not _SHA40_RE.match(commit_sha.lower()):
        return False
    saved = session.verification_snapshot()
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


def _verified_regression(workspace: CodingWorkspace, session: SessionStore | None,
                         commit_sha: str) -> bool:
    if session is None or session.session_id != workspace.session_id:
        return False
    if not isinstance(commit_sha, str) or not _SHA40_RE.match(commit_sha.lower()):
        return False
    saved = session.confirmed_regression_snapshot(base_sha=workspace.base_sha)
    if saved is None:
        return False
    try:
        changed = _paths(workspace, "diff", "--name-only", "-z", commit_sha)
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
    _validate_workspace(workspace)
    if not isinstance(title, str):
        raise TypeError("title must be a string")
    if not title.strip():
        raise DeliveryError("PR title must not be empty")
    if "\n" in title or "\r" in title or "\0" in title:
        raise DeliveryError("PR title must not contain newlines or null bytes")
    if len(title) > MAX_PR_TITLE_LENGTH:
        raise DeliveryError(f"PR title exceeds maximum length of {MAX_PR_TITLE_LENGTH} characters")

    if not isinstance(body, str):
        raise TypeError("body must be a string")
    if "\0" in body:
        raise DeliveryError("PR body must not contain null bytes")
    if len(body) > MAX_PR_BODY_BYTES:
        raise DeliveryError(f"PR body exceeds maximum length of {MAX_PR_BODY_BYTES} characters")

    if type(draft) is not bool:
        raise TypeError("draft must be a boolean")

    if not isinstance(remote, str) or not remote.strip():
        raise DeliveryError("remote must be a non-empty string")
    if remote.startswith("-") or not _REMOTE_NAME_SAFE_RE.match(remote.strip()) or "\0" in remote or "\n" in remote:
        raise DeliveryError(f"Invalid remote name: {remote}")
    remote = remote.strip()

    if base_branch is not None:
        base_branch = _validate_git_ref_name(base_branch, "base branch")
        if base_branch == workspace.branch:
            raise DeliveryError(f"base_branch must not be identical to workspace branch: {base_branch}")

    if session_store is not None:
        if not isinstance(session_store, SessionStore):
            raise TypeError("session_store must be a SessionStore instance or None")
        if session_store.session_id != workspace.session_id:
            raise DeliveryError(
                f"SessionStore session_id '{session_store.session_id}' does not match "
                f"workspace session_id '{workspace.session_id}'"
            )

    commit_sha = git(workspace.path, "rev-parse", "HEAD").strip().lower()
    if not _SHA40_RE.match(commit_sha):
        raise DeliveryError(f"Invalid commit SHA in worktree: {commit_sha}")

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
        reg_passed = _verified_regression(workspace, session_store, commit_sha)
        reg_note = (
            "\n**Regression Validation**: Confirmed (old commit failed, repaired worktree passed)"
            if reg_passed else ""
        )
        evidence_note = (
            f"\n\n---\n**Verification for commit `{commit_sha}`**: "
            f"{'Verified (tests passed with workspace content match)' if passed else 'Unverified'}"
            f"{reg_note}\n"
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

    with ExitStack() as cleanup:
        if len(pr_body.encode("utf-8")) > 64 * 1024:
            body_file = cleanup.enter_context(tempfile.NamedTemporaryFile(mode="w", encoding="utf-8"))
            try:
                os.chmod(body_file.name, 0o600)
            except OSError:
                pass
            body_file.write(pr_body)
            body_file.flush()
            body_index = gh_args.index("--body")
            gh_args[body_index:body_index + 2] = ["--body-file", body_file.name]
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
