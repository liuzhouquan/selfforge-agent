"""Persistent, reviewable Git worktrees for general Coding sessions."""

import hashlib
import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

from selfforge_agent.coding.worktree import WorktreeError, git

_SESSION_ID = re.compile(r"[A-Za-z0-9]{1,64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class WorkspaceError(ValueError):
    """A Coding worktree cannot be created, resumed, or removed safely."""


@dataclass(frozen=True)
class CodingWorkspace:
    repository: Path
    path: Path
    branch: str
    base_sha: str
    session_id: str


class CodingWorkspaceManager:
    def __init__(self, root: Path | None = None) -> None:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        self.root = (root or data_home / "selfforge-agent" / "worktrees").expanduser().resolve()

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
        # Git refuses to remove modified or untracked worktrees without --force.
        git(workspace.repository, "worktree", "remove", str(workspace.path))
        metadata.unlink()
        return workspace
