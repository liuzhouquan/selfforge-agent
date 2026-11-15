"""Read any accessible GitHub PR by URL and optionally prepare an editable checkout."""

from __future__ import annotations

import json
import math
import os
import posixpath
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from selfforge_agent.core.git_context import parse_hunks
from selfforge_agent.models import ChangedFile, PRContext

MAX_COMMAND_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_PR_FILES = 300
MAX_PR_NUMBER = 2_147_483_647

# GitHub username/organization: 1-39 characters, alphanumeric or single hyphens, cannot begin or end with hyphen.
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

# GitHub repository name: 1-100 characters, alphanumeric, ., _, -, cannot start with hyphen or dot,
# cannot be . or .., and cannot end in .git.
_REPO_RE = re.compile(r"^(?!\.\.?$)(?!.*\.git$)[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")

_PR_URL = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>[1-9][0-9]*)/?$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_STATUS = {"added": "A", "removed": "D", "modified": "M", "renamed": "R", "copied": "C"}
_VALID_STATUS_CODES = frozenset({"A", "D", "M", "R", "C"})


class GitHubPRError(ValueError):
    """The GitHub PR could not be read or checked out."""


class GitHubPR(BaseModel):
    url: str
    owner: str
    repo: str
    number: int
    title: str
    author: str
    state: str
    base_branch: str
    head_branch: str
    head_repository: str | None
    maintainer_can_modify: bool
    context: PRContext
    warnings: list[str]


class CheckoutResult(BaseModel):
    path: str
    branch: str
    head_sha: str
    diff_base_sha: str
    pr_url: str


def parse_pr_url(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str):
        raise GitHubPRError("PR URL must be a string")
    trimmed = url.strip()
    if any(c in trimmed for c in ("\x00", "\r", "\n", "\t", " ")):
        raise GitHubPRError("PR URL contains invalid whitespace or control characters")
    match = _PR_URL.fullmatch(trimmed)
    if not match:
        raise GitHubPRError("Use a GitHub PR URL such as https://github.com/owner/repo/pull/123")
    owner, repo, num_str = match["owner"], match["repo"], match["number"]
    if not _OWNER_RE.fullmatch(owner):
        raise GitHubPRError(f"Invalid GitHub owner name: {owner!r}")
    if not _REPO_RE.fullmatch(repo):
        raise GitHubPRError(f"Invalid GitHub repository name: {repo!r}")
    number = int(num_str)
    if number < 1 or number > MAX_PR_NUMBER:
        raise GitHubPRError(f"Invalid PR number: {number}")
    return owner, repo, number


def _validate_changed_path(path: str | None, *, label: str = "filename") -> str:
    if not isinstance(path, str) or not path.strip():
        raise GitHubPRError(f"Changed file {label} must be a non-empty string")
    clean = path.strip()
    if any(c in clean for c in ("\x00", "\r", "\n", "\t")):
        raise GitHubPRError(f"Changed file {label} contains control characters: {path!r}")
    if clean.startswith(("/", "\\")):
        raise GitHubPRError(f"Changed file {label} must not be an absolute path: {path!r}")
    norm = posixpath.normpath(clean)
    if norm == "." or norm.startswith("..") or "/../" in f"/{norm}/":
        raise GitHubPRError(f"Path traversal detected in {label}: {path!r}")
    return clean


def _normalize_file_status(raw_status: Any) -> str:
    if not isinstance(raw_status, str):
        raise GitHubPRError(f"Invalid file status type: {type(raw_status).__name__}")
    normalized = _STATUS.get(raw_status.lower(), raw_status.upper())
    if normalized not in _VALID_STATUS_CODES:
        raise GitHubPRError(f"Unrecognized file status code: {raw_status!r}")
    return normalized


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60,
    max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
) -> str:
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise GitHubPRError("Timeout must be a finite positive number")

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubPRError(f"GitHub operation failed: {exc}") from exc

    if (
        len(result.stdout.encode("utf-8")) > max_output_bytes
        or len(result.stderr.encode("utf-8")) > max_output_bytes
    ):
        raise GitHubPRError("Command output exceeded maximum allowed size")

    if result.returncode:
        raise GitHubPRError(result.stderr.strip()[-1000:] or "GitHub operation failed")
    return result.stdout


def _api(endpoint: str) -> object:
    if not isinstance(endpoint, str) or not endpoint or endpoint.startswith("-"):
        raise GitHubPRError(f"Invalid API endpoint: {endpoint!r}")
    try:
        output = _run(["gh", "api", endpoint])
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise GitHubPRError("GitHub returned invalid JSON") from exc


def load_pr(url: str) -> GitHubPR:
    owner, repo, number = parse_pr_url(url)
    endpoint = f"repos/{owner}/{repo}/pulls/{number}"
    metadata = _api(endpoint)
    if not isinstance(metadata, dict):
        raise GitHubPRError("GitHub returned invalid PR metadata")
    try:
        base_obj = metadata.get("base")
        head_obj = metadata.get("head")
        if not isinstance(base_obj, dict) or not isinstance(head_obj, dict):
            raise GitHubPRError("GitHub PR metadata missing base or head branch object")

        base_sha = str(base_obj.get("sha", "")).lower()
        head_sha = str(head_obj.get("sha", "")).lower()
        if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(head_sha):
            raise GitHubPRError("Invalid commit SHA in PR metadata")

        base_ref = str(base_obj.get("ref", ""))
        head_ref = str(head_obj.get("ref", ""))
        if not base_ref or not head_ref:
            raise GitHubPRError("Missing branch reference in PR metadata")

        changed: list[ChangedFile] = []
        warnings: list[str] = []
        for page in range(1, 5):
            files = _api(f"{endpoint}/files?per_page=100&page={page}")
            if not isinstance(files, list):
                raise GitHubPRError("GitHub returned invalid changed-file data")
            for item in files:
                if not isinstance(item, dict):
                    raise GitHubPRError("Changed file item must be a dictionary")
                filename = _validate_changed_path(item.get("filename"), label="filename")
                prev_name = item.get("previous_filename")
                if prev_name is not None:
                    prev_name = _validate_changed_path(prev_name, label="previous_filename")
                status = _normalize_file_status(item.get("status"))

                patch = item.get("patch")
                available = isinstance(patch, str)
                if not available:
                    warnings.append(f"Patch unavailable for {filename}")
                changed.append(
                    ChangedFile(
                        path=filename,
                        previous_path=prev_name,
                        status=status,
                        hunks=parse_hunks(patch) if available else [],
                        patch_available=available,
                    )
                )
            if len(changed) > MAX_PR_FILES:
                raise GitHubPRError(f"PR exceeds the {MAX_PR_FILES}-file review limit")
            if len(files) < 100:
                break
        else:
            raise GitHubPRError(f"PR exceeds the {MAX_PR_FILES}-file review limit")

        user_obj = metadata.get("user")
        if not isinstance(user_obj, dict) or not user_obj.get("login"):
            raise GitHubPRError("Missing or invalid user login in PR metadata")
        author = str(user_obj["login"])

        title = str(metadata.get("title", ""))
        if not title:
            raise GitHubPRError("PR title cannot be empty")

        state = str(metadata.get("state", "open"))
        head_repo = head_obj.get("repo")
        head_repository = head_repo.get("full_name") if isinstance(head_repo, dict) else None

        context = PRContext(
            repository=f"https://github.com/{owner}/{repo}",
            base_sha=base_sha,
            head_sha=head_sha,
            files=changed,
        )
        return GitHubPR(
            url=f"https://github.com/{owner}/{repo}/pull/{number}",
            owner=owner,
            repo=repo,
            number=number,
            title=title,
            author=author,
            state=state,
            base_branch=base_ref,
            head_branch=head_ref,
            head_repository=head_repository,
            maintainer_can_modify=bool(metadata.get("maintainer_can_modify")),
            context=context,
            warnings=warnings,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, GitHubPRError):
            raise
        raise GitHubPRError(f"GitHub PR data is incomplete: {exc}") from exc


def checkout_pr(url: str, workspace_root: Path) -> CheckoutResult:
    """Clone a PR into a fresh directory; never reset or overwrite an existing checkout."""
    if not isinstance(workspace_root, Path):
        workspace_root = Path(workspace_root)

    # Symlink and directory type defense
    try:
        st = os.lstat(workspace_root)
        if stat.S_ISLNK(st.st_mode):
            raise GitHubPRError(f"workspace_root cannot be a symlink: {workspace_root}")
        if not stat.S_ISDIR(st.st_mode):
            raise GitHubPRError(f"workspace_root must be a directory: {workspace_root}")
    except FileNotFoundError:
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
            st = os.lstat(workspace_root)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise GitHubPRError(f"workspace_root is invalid after creation: {workspace_root}")
        except OSError as exc:
            raise GitHubPRError(f"Failed to create workspace_root {workspace_root}: {exc}") from exc
    except OSError as exc:
        raise GitHubPRError(f"Cannot access workspace_root {workspace_root}: {exc}") from exc

    pr = load_pr(url)
    dest_name = f"{pr.owner}-{pr.repo}-pr{pr.number}-{pr.context.head_sha[:8]}-{uuid4().hex[:6]}"
    destination = workspace_root / dest_name

    # Ensure destination does not exist and is within workspace_root
    if destination.exists() or os.path.islink(destination):
        raise GitHubPRError(f"Destination already exists or is a symlink: {destination}")

    repository = f"{pr.owner}/{pr.repo}"
    branch = f"vpa-pr-{pr.number}"
    try:
        _run(
            [
                "gh", "repo", "clone", repository, str(destination), "--",
                "--filter=blob:none", "--no-checkout",
            ],
            timeout=180,
        )
        _run(
            ["gh", "pr", "checkout", str(pr.number), "-R", repository, "--branch", branch],
            cwd=destination,
            timeout=180,
        )
        actual_sha = _run(["git", "rev-parse", "HEAD"], cwd=destination).strip()
        if actual_sha != pr.context.head_sha:
            raise GitHubPRError("PR head changed during checkout; retry with its new head")
        diff_base_sha = _run(
            ["git", "merge-base", pr.context.base_sha, actual_sha], cwd=destination
        ).strip()
        if not _SHA.fullmatch(diff_base_sha):
            raise GitHubPRError("Could not resolve the PR merge base")
    except GitHubPRError:
        if os.path.islink(destination):
            try:
                os.unlink(destination)
            except OSError:
                pass
        elif destination.is_dir():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return CheckoutResult(
        path=str(destination), branch=branch, head_sha=actual_sha,
        diff_base_sha=diff_base_sha, pr_url=pr.url,
    )
