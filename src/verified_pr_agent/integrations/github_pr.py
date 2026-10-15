"""Read any accessible GitHub PR by URL and optionally prepare an editable checkout."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from verified_pr_agent.core.git_context import parse_hunks
from verified_pr_agent.models import ChangedFile, PRContext

_PR_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9][0-9]*)(?:/)?\Z"
)
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_STATUS = {"added": "A", "removed": "D", "modified": "M", "renamed": "R", "copied": "C"}


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
    match = _PR_URL.fullmatch(url.strip())
    if not match:
        raise GitHubPRError("Use a GitHub PR URL such as https://github.com/owner/repo/pull/123")
    return match["owner"], match["repo"], int(match["number"])


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubPRError(f"GitHub operation failed: {exc}") from exc
    if result.returncode:
        raise GitHubPRError(result.stderr.strip()[-1000:] or "GitHub operation failed")
    return result.stdout


def _api(endpoint: str) -> object:
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
        if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(head_sha):
            raise ValueError("Invalid commit SHA")
        changed: list[ChangedFile] = []
        warnings: list[str] = []
        for page in range(1, 5):
            files = _api(f"{endpoint}/files?per_page=100&page={page}")
            if not isinstance(files, list):
                raise GitHubPRError("GitHub returned invalid changed-file data")
            for item in files:
                patch = item.get("patch")
                available = isinstance(patch, str)
                if not available:
                    warnings.append(f"Patch unavailable for {item['filename']}")
                changed.append(
                    ChangedFile(
                        path=item["filename"],
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
    pr = load_pr(url)
    workspace_root.mkdir(parents=True, exist_ok=True)
    destination = workspace_root / (
        f"{pr.owner}-{pr.repo}-pr{pr.number}-{pr.context.head_sha[:8]}-{uuid4().hex[:6]}"
    )
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
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return CheckoutResult(
        path=str(destination), branch=branch, head_sha=actual_sha,
        diff_base_sha=diff_base_sha, pr_url=pr.url,
    )
