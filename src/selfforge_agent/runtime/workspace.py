"""Persistent, reviewable Git worktrees for general Coding sessions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selfforge_agent.coding.worktree import WorktreeError, git

_SESSION_ID = re.compile(r"[A-Za-z0-9]{1,64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class WorkspaceError(ValueError):
    """A Coding worktree cannot be created, resumed, or removed safely."""


def workspace_lock_path(workspace: Path, root: Path | None = None) -> Path:
    """Return the filesystem lock file path for a workspace directory."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    env_locks = os.environ.get("SELFFORGE_LOCKS_ROOT")
    locks_dir = (
        root or (Path(env_locks) if env_locks else data_home / "selfforge-agent" / "locks")
    ).expanduser().resolve()
    locks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()
    return locks_dir / digest


def try_workspace_lock(workspace: Path, root: Path | None = None) -> int | None:
    """Attempt non-blocking exclusive file lock on a workspace. Returns fd or None."""
    path = workspace_lock_path(workspace, root=root)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def unlock_workspace(fd: int) -> None:
    """Release and close a workspace file lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def workspace_guard(workspace: Path, root: Path | None = None,
                    message: str = "Workspace is locked by another task or process"):
    """Context manager acquiring an exclusive file lock on a workspace."""
    fd = try_workspace_lock(workspace, root=root)
    if fd is None:
        raise WorkspaceError(message)
    try:
        yield
    finally:
        unlock_workspace(fd)


@dataclass(frozen=True)
class CodingWorkspace:
    repository: Path
    path: Path
    branch: str
    base_sha: str
    session_id: str


class CodingWorkspaceManager:
    def __init__(self, root: Path | None = None, locks_root: Path | None = None) -> None:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        env_root = os.environ.get("SELFFORGE_WORKSPACES_ROOT")
        self.root = (
            root
            or (Path(env_root) if env_root else data_home / "selfforge-agent" / "worktrees")
        ).expanduser().resolve()
        env_locks = os.environ.get("SELFFORGE_LOCKS_ROOT")
        self.locks_root = (
            locks_root.expanduser().resolve()
            if locks_root
            else (Path(env_locks).expanduser().resolve() if env_locks else None)
        )

    def guard(self, workspace: Path | CodingWorkspace,
              message: str = "Workspace is locked by another task or process"):
        path = workspace.path if isinstance(workspace, CodingWorkspace) else workspace
        return workspace_guard(path, root=self.locks_root, message=message)

    @staticmethod
    def validate_repository(repository: Path) -> Path:
        repo = repository.expanduser().resolve()
        if not repo.is_dir():
            raise WorkspaceError("Repository directory does not exist")
        try:
            top = Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
        except WorktreeError as exc:
            raise WorkspaceError("Choose a Git repository root") from exc
        if top != repo:
            raise WorkspaceError(f"Use the Git repository root: {top}")
        try:
            git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        except WorktreeError as exc:
            raise WorkspaceError("Repository needs an initial commit before creating a worktree") from exc
        return repo

    def _paths(self, repository: Path, session_id: str) -> tuple[Path, Path, str]:
        if not _SESSION_ID.fullmatch(session_id):
            raise WorkspaceError("Invalid session ID")
        fingerprint = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:20]
        directory = self.root / fingerprint
        return directory / session_id, directory / f"{session_id}.json", f"selfforge/{session_id}"

    def session_root(self, repository: Path, session_id: str | None = None,
                     *, legacy_roots: tuple[Path, ...] = ()) -> Path:
        repo = repository.expanduser().resolve()
        fingerprint = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:20]
        canonical = self.root.parent / "sessions" / fingerprint
        if session_id:
            if not _SESSION_ID.fullmatch(session_id):
                raise WorkspaceError("Invalid session ID")
            for root in (canonical, *legacy_roots):
                if (root / ".selfforge" / "sessions" / f"{session_id}.jsonl").is_file():
                    return root
        return canonical

    @staticmethod
    def _common_git_dir(repository: Path) -> Path:
        raw = Path(git(repository, "rev-parse", "--git-common-dir").strip())
        return (repository / raw).resolve() if not raw.is_absolute() else raw.resolve()

    def create(self, repository: Path, session_id: str) -> CodingWorkspace:
        repo = self.validate_repository(repository)
        path, metadata, branch = self._paths(repo, session_id)
        if path.is_relative_to(repo):
            raise WorkspaceError("Worktree storage must be outside the source repository")
        if path.exists() or metadata.exists():
            raise WorkspaceError("This Coding workspace already exists")
        base_sha = git(repo, "rev-parse", "HEAD").strip()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        git(repo, "worktree", "add", "-b", branch, str(path), base_sha)
        payload = {"repository": str(repo), "path": str(path), "branch": branch,
                   "base_sha": base_sha, "session_id": session_id}
        try:
            path.chmod(0o700)
            fd = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.write("\n")
        except (OSError, ValueError):
            metadata.unlink(missing_ok=True)
            git(repo, "worktree", "remove", str(path))
            git(repo, "branch", "-D", branch)
            raise
        return CodingWorkspace(repo, path, branch, base_sha, session_id)

    def get(self, repository: Path, session_id: str) -> CodingWorkspace:
        repo = self.validate_repository(repository)
        path, metadata, branch = self._paths(repo, session_id)
        if not metadata.is_file():
            raise WorkspaceError("Unknown Coding workspace; use direct mode for older sessions")
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkspaceError("Coding workspace metadata is unreadable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("base_sha"), str):
            raise WorkspaceError("Coding workspace metadata is invalid")
        if (payload.get("repository") != str(repo) or payload.get("path") != str(path)
                or payload.get("branch") != branch or payload.get("session_id") != session_id
                or not _COMMIT.fullmatch(payload.get("base_sha", ""))):
            raise WorkspaceError("Coding workspace metadata does not match this repository")
        if not path.is_dir() or path.is_symlink():
            raise WorkspaceError("Coding worktree is missing or replaced")
        if self._common_git_dir(path) != self._common_git_dir(repo):
            raise WorkspaceError("Coding worktree belongs to a different repository")
        if git(path, "branch", "--show-current").strip() != branch:
            raise WorkspaceError("Coding worktree branch changed; inspect it manually")
        return CodingWorkspace(repo, path, branch, payload["base_sha"], session_id)

    def list(self, repository: Path) -> list[CodingWorkspace]:
        repo = self.validate_repository(repository)
        _, metadata, _ = self._paths(repo, "placeholder")
        workspaces = []
        for entry in sorted(metadata.parent.glob("*.json")):
            try:
                workspaces.append(self.get(repo, entry.stem))
            except (WorkspaceError, WorktreeError) as exc:
                warnings.warn(f"Skipping invalid Coding workspace metadata {entry}: {exc}",
                              RuntimeWarning, stacklevel=2)
        return workspaces

    def remove(self, repository: Path, session_id: str) -> CodingWorkspace:
        workspace = self.get(repository, session_id)
        _, metadata, _ = self._paths(workspace.repository, session_id)
        # Keep Git's dirty-worktree guard for persistent user workspaces.
        git(workspace.repository, "worktree", "remove", str(workspace.path))
        metadata.unlink()
        return workspace

    def audit(self, repository: Path, *, head_ref: str = "HEAD") -> list[dict[str, Any]]:
        repo = self.validate_repository(repository)
        workspaces = self.list(repo)
        results: list[dict[str, Any]] = []

        for ws in workspaces:
            # 1. Advisory file lock check
            fd = try_workspace_lock(ws.path, root=self.locks_root)
            if fd is None:
                is_locked = True
            else:
                unlock_workspace(fd)
                is_locked = False

            # 2. Dirty worktree check
            try:
                status = git(ws.path, "status", "--porcelain").strip()
                is_dirty = bool(status)
            except WorktreeError:
                is_dirty = True

            # 3. Merged check against head_ref
            try:
                proc = subprocess.run(
                    ["git", "-C", str(repo), "merge-base", "--is-ancestor", ws.branch, head_ref],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                is_merged = (proc.returncode == 0)
            except (subprocess.SubprocessError, OSError):
                is_merged = False

            prunable = (not is_locked) and (not is_dirty) and is_merged

            results.append({
                "session_id": ws.session_id,
                "path": str(ws.path),
                "branch": ws.branch,
                "base_sha": ws.base_sha,
                "is_locked": is_locked,
                "is_dirty": is_dirty,
                "is_merged": is_merged,
                "prunable": prunable,
            })

        return results

    def prune(
        self,
        repository: Path,
        *,
        dry_run: bool = False,
        merged_only: bool = True,
        head_ref: str = "HEAD",
    ) -> dict[str, Any]:
        repo = self.validate_repository(repository)
        audit_items = self.audit(repo, head_ref=head_ref)

        pruned: list[str] = []
        skipped_locked: list[str] = []
        skipped_dirty: list[str] = []
        skipped_unmerged: list[str] = []

        for item in audit_items:
            session_id = item["session_id"]
            ws_path = Path(item["path"])

            if item["is_locked"]:
                skipped_locked.append(session_id)
                continue

            if item["is_dirty"]:
                skipped_dirty.append(session_id)
                continue

            if merged_only and not item["is_merged"]:
                skipped_unmerged.append(session_id)
                continue

            if dry_run:
                pruned.append(session_id)
            else:
                # Re-verify lock before removing
                fd = try_workspace_lock(ws_path, root=self.locks_root)
                if fd is None:
                    skipped_locked.append(session_id)
                    continue
                try:
                    # Re-verify dirty before removing
                    if git(ws_path, "status", "--porcelain").strip():
                        skipped_dirty.append(session_id)
                        continue
                    self.remove(repo, session_id)
                    pruned.append(session_id)
                finally:
                    unlock_workspace(fd)

        return {
            "pruned": pruned,
            "skipped_locked": skipped_locked,
            "skipped_dirty": skipped_dirty,
            "skipped_unmerged": skipped_unmerged,
        }
