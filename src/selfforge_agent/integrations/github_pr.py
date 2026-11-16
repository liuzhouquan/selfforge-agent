"""Read any accessible GitHub PR by URL and optionally prepare an editable checkout."""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from selfforge_agent.core.git_context import parse_hunks
from selfforge_agent.models import ChangedFile, PRContext

_PR_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9][0-9]*)(?:/)?\Z"
)
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\Z")
_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?\Z")
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
_STATUS = {"added": "A", "removed": "D", "modified": "M", "renamed": "R", "copied": "C"}
MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # 8 MiB


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
        raise TypeError("GitHub PR URL must be a string")
    if any(c in url for c in ("\x00", "\r", "\n", "\t")):
        raise GitHubPRError("GitHub PR URL cannot contain control characters")
    cleaned = url.strip()
    if not cleaned or len(cleaned) > 2048:
        raise GitHubPRError("GitHub PR URL length out of range")
    if "@" in cleaned or re.search(r"https://[^/]+:\d+", cleaned):
        raise GitHubPRError("GitHub PR URL cannot contain credentials or explicit ports")
    match = _PR_URL.fullmatch(cleaned)
    if not match:
        raise GitHubPRError("Use a GitHub PR URL such as https://github.com/owner/repo/pull/123")
    owner = match["owner"]
    repo = match["repo"]
    number_str = match["number"]

    if (
        owner.startswith(("-", "."))
        or repo.startswith(("-", "."))
        or repo.endswith(("-", ".", ".git"))
        or owner in (".", "..")
        or repo in (".", "..")
        or len(owner) > 39
        or len(repo) > 100
        or not _OWNER_RE.fullmatch(owner)
        or not _REPO_RE.fullmatch(repo)
    ):
        raise GitHubPRError(f"Invalid repository owner '{owner}' or repo '{repo}'")

    try:
        number = int(number_str)
        if number < 1 or number > 2_147_483_647:
            raise ValueError
    except (ValueError, OverflowError) as exc:
        raise GitHubPRError(f"Invalid PR number: {number_str}") from exc

    return owner, repo, number


def _run(command: list[str], *, cwd: Path | None = None, timeout: float = 60) -> str:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise GitHubPRError("Timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise GitHubPRError("Timeout must be a finite positive number between 1 and 300")
    if cwd is not None:
        if cwd.is_symlink():
            raise GitHubPRError("Working directory must not be a symlink")
        if not cwd.is_dir():
            raise GitHubPRError("Working directory must be an existing directory")
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubPRError(f"GitHub operation failed: {exc}") from exc
    if len(result.stdout) > MAX_OUTPUT_BYTES or len(result.stderr) > MAX_OUTPUT_BYTES:
        raise GitHubPRError("Command output exceeded size limit")
    if result.returncode:
        raise GitHubPRError(result.stderr.strip()[-1000:] or "GitHub operation failed")
    return result.stdout


def _api(endpoint: str) -> object:
    if not isinstance(endpoint, str) or endpoint.startswith("-") or any(c in endpoint for c in ("\x00", "\r", "\n")):
        raise GitHubPRError("Invalid API endpoint")
    try:
        return json.loads(_run(["gh", "api", endpoint]))
    except json.JSONDecodeError as exc:
        raise GitHubPRError("GitHub returned invalid JSON") from exc


def load_pr(url: str) -> GitHubPR:
    owner, repo, number = parse_pr_url(url)
    endpoint = f"repos/{owner}/{repo}/pulls/{number}"
    metadata = _api(endpoint)
    if not isinstance(metadata, dict):
        raise GitHubPRError("GitHub returned invalid PR metadata")
    try:
        base_sha = metadata["base"]["sha"]
        head_sha = metadata["head"]["sha"]
        if not isinstance(base_sha, str) or not isinstance(head_sha, str):
            raise GitHubPRError("Commit SHA must be a string")
        base_sha = base_sha.lower()
        head_sha = head_sha.lower()
        if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(head_sha):
            raise GitHubPRError("Invalid commit SHA")
        changed: list[ChangedFile] = []
        warnings: list[str] = []
        for page in range(1, 5):
            files = _api(f"{endpoint}/files?per_page=100&page={page}")
            if not isinstance(files, list):
                raise GitHubPRError("GitHub returned invalid changed-file data")
            for item in files:
                filename = item.get("filename")
                if not isinstance(filename, str) or "\x00" in filename:
                    raise GitHubPRError("Invalid filename in changed files")
                patch = item.get("patch")
                available = isinstance(patch, str)
                if not available:
                    warnings.append(f"Patch unavailable for {filename}")
                changed.append(
                    ChangedFile(
                        path=filename,
                        previous_path=item.get("previous_filename"),
                        status=_STATUS.get(item["status"], item["status"]),
                        hunks=parse_hunks(patch) if available else [],
                        patch_available=available,
                    )
                )
            if len(changed) > 300:
                raise GitHubPRError("PR exceeds the 300-file review limit")
            if len(files) < 100:
                break
        else:
            raise GitHubPRError("PR exceeds the 300-file review limit")
        head_repo = metadata["head"].get("repo")
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
            title=metadata["title"],
            author=metadata["user"]["login"],
            state=metadata["state"],
            base_branch=metadata["base"]["ref"],
            head_branch=metadata["head"]["ref"],
            head_repository=head_repo.get("full_name") if head_repo else None,
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
    if workspace_root.is_symlink():
        raise GitHubPRError("Workspace root must not be a symlink")
    if any(p.is_symlink() for p in workspace_root.parents):
        raise GitHubPRError("Workspace root path must not contain symlinks")
    if workspace_root.exists() and not workspace_root.is_dir():
        raise GitHubPRError("Workspace root must be a directory, not a file or FIFO")
    workspace_root.mkdir(parents=True, exist_ok=True)
    if workspace_root.is_symlink() or not workspace_root.is_dir():
        raise GitHubPRError("Workspace root must be a directory, not a symlink")

    pr = load_pr(url)
    destination = workspace_root / (
        f"{pr.owner}-{pr.repo}-pr{pr.number}-{pr.context.head_sha[:8]}-{uuid4().hex[:6]}"
    )
    if destination.exists() or destination.is_symlink():
        raise GitHubPRError(f"Destination directory already exists: {destination}")

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
        actual_sha = _run(["git", "rev-parse", "HEAD"], cwd=destination).strip().lower()
        if actual_sha != pr.context.head_sha:
            raise GitHubPRError("PR head changed during checkout; retry with its new head")
        diff_base_sha = _run(
            ["git", "merge-base", pr.context.base_sha, actual_sha], cwd=destination
        ).strip().lower()
        if not _SHA.fullmatch(diff_base_sha):
            raise GitHubPRError("Could not resolve the PR merge base")
    except Exception:
        if destination.is_symlink():
            destination.unlink(missing_ok=True)
        elif destination.is_dir():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return CheckoutResult(
        path=str(destination), branch=branch, head_sha=actual_sha,
        diff_base_sha=diff_base_sha, pr_url=pr.url,
    )
