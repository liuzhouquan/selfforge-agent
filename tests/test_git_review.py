from pathlib import Path

import pytest
from pydantic import ValidationError

from selfforge_agent.core.git_context import GitContextError, build_context
from selfforge_agent.models import ChangeKind, Finding
from selfforge_agent.review.baseline import BaselineReviewer


def test_diff_hunks_and_heuristic(demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    context = build_context(repo, base[:12], head)
    assert context.base_sha == base
    assert len(context.files) == 1
    changed = context.files[0]
    assert changed.path == "calculator.py"
    assert changed.status == "M"
    assert changed.hunks[0].header.startswith("@@ -1,2 +1,3 @@")
    added = [line for line in changed.hunks[0].lines if line.kind == ChangeKind.added]
    assert [(line.new_line, line.content) for line in added] == [
        (2, "    eval('1 + 1')"),
        (3, "    return a - b"),
    ]
    findings = BaselineReviewer().review(context)
    assert len(findings) == 1
    assert findings[0].line_number == 2
    assert findings[0].verification_status == "suspected"
    assert findings[0].source == "heuristic"


def test_invalid_repository_and_commit(tmp_path: Path, demo_repo: tuple[Path, str, str]) -> None:
    repo, base, head = demo_repo
    with pytest.raises(GitContextError):
        build_context(tmp_path / "missing", base, head)
    with pytest.raises(GitContextError):
        build_context(tmp_path, base, head)
    with pytest.raises(GitContextError):
        build_context(repo, "--help", head)
    with pytest.raises(GitContextError):
        build_context(repo, "f" * 40, head)


def test_finding_validation() -> None:
    with pytest.raises(ValidationError):
        Finding(
            file_path="x.py",
            line_number=0,
            severity="high",
            category="example",
            description="Issue",
            evidence="line",
            verification_status="confirmed",
        )
