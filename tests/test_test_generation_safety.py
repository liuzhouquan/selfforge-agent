"""Safety tests for differential test generation, causal investigation, and artifact isolation."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from selfforge_agent.coding.causal_investigation import investigate_causality
from selfforge_agent.coding.models import RiskTarget
from selfforge_agent.coding.risk_prioritization import prioritize_risks
from selfforge_agent.coding.test_generation import (
    _observation,
    _parse_test,
    _write_test_artifact,
    remaining_budget,
)
from selfforge_agent.models import ChangedFile, DiffHunk, DiffLine, PRContext
from selfforge_agent.models import TestFailure as _TestFailure
from selfforge_agent.models import TestResult as _TestResult


def test_write_test_artifact_rejects_symlink_and_existing_files(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    # Case 1: Symlink at target pointing to victim file
    victim = tmp_path / "victim.txt"
    victim.write_text("original content", encoding="utf-8")

    symlink_target = worktree / "test_attack.py"
    symlink_target.symlink_to(victim)

    with pytest.raises(ValueError, match="already exists or is a symlink"):
        _write_test_artifact(worktree, "test_attack.py", "def test_evil(): assert True\n")

    # Victim must not be overwritten
    assert victim.read_text(encoding="utf-8") == "original content"

    # Case 2: Target already exists as a regular file
    existing = worktree / "test_existing.py"
    existing.write_text("def test_old(): assert 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists or is a symlink"):
        _write_test_artifact(worktree, "test_existing.py", "def test_new(): assert 2\n")

    # Case 3: Target path attempts traversal with ..
    with pytest.raises(ValueError, match="Invalid test artifact path"):
        _write_test_artifact(worktree, "../test_traversal.py", "def test_t(): assert True\n")


def test_write_test_artifact_rejects_escaping_parent_directory(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    # Symlink directory inside worktree pointing outside
    sym_dir = worktree / "escaped_tests"
    sym_dir.symlink_to(outside_dir)

    with pytest.raises(ValueError, match="parent directory escapes worktree"):
        _write_test_artifact(worktree, "escaped_tests/test_escaped.py", "def test_e(): assert True\n")


def test_write_test_artifact_successfully_creates_file(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    created = _write_test_artifact(worktree, "test_safe.py", "def test_ok():\n    assert 1 == 1\n")
    assert created.is_file()
    assert not created.is_symlink()
    assert created.read_text(encoding="utf-8") == "def test_ok():\n    assert 1 == 1\n"


def test_parse_test_rejects_top_level_executable_statements() -> None:
    import json

    # Payload with top-level executable side-effects (e.g. os.system or print)
    malicious = {
        "test_code": "import os\nos.system('echo compromised')\n\ndef test_dummy():\n    assert True\n",
        "rationale": "Testing injection",
    }
    with pytest.raises(ValueError, match="top-level executable statements"):
        _parse_test(json.dumps(malicious))


def test_parse_test_catches_uncontrolled_exceptions_gracefully() -> None:
    import json

    # Mock ast.parse raising RecursionError
    with patch("selfforge_agent.coding.test_generation.ast.parse", side_effect=RecursionError("Stack overflow")):
        payload = json.dumps({"test_code": "def test_rec(): assert True\n", "rationale": "valid"})
        with pytest.raises(ValueError, match="Model returned invalid pytest source"):
            _parse_test(payload)

    # Missing assert statement in test function
    no_assert = json.dumps({"test_code": "def test_no_assert():\n    x = 1\n", "rationale": "no assert"})
    with pytest.raises(ValueError, match="needs a test_\\* function with an assert"):
        _parse_test(no_assert)


def test_observation_skips_symlinks_and_fifos(tmp_path: Path) -> None:
    head_tree = tmp_path / "head"
    head_tree.mkdir()

    # Normal file
    normal_file = head_tree / "normal.py"
    normal_file.write_text("def hello(): pass\n", encoding="utf-8")

    # Symlink file pointing to normal file
    symlink_file = head_tree / "symlink.py"
    symlink_file.symlink_to(normal_file)

    # FIFO file
    fifo_file = head_tree / "pipe.py"
    try:
        os.mkfifo(fifo_file)
    except (AttributeError, OSError):
        pytest.skip("mkfifo not supported on this platform")

    context = PRContext(
        repository="repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=[
            ChangedFile(
                path="normal.py",
                status="M",
                hunks=[DiffHunk(header="@@ -1 +1 @@", old_start=1, new_start=1,
                                lines=[DiffLine(kind="added", content="def hello(): pass", new_line=1)])],
            ),
            ChangedFile(
                path="symlink.py",
                status="M",
                hunks=[DiffHunk(header="@@ -1 +1 @@", old_start=1, new_start=1,
                                lines=[DiffLine(kind="added", content="symlink line", new_line=1)])],
            ),
            ChangedFile(
                path="pipe.py",
                status="M",
                hunks=[DiffHunk(header="@@ -1 +1 @@", old_start=1, new_start=1,
                                lines=[DiffLine(kind="added", content="pipe line", new_line=1)])],
            ),
        ],
    )

    import json
    obs_json = _observation(context, head_tree, "Find changes")
    data = json.loads(obs_json)
    included_paths = [f["path"] for f in data["files"]]

    assert "normal.py" in included_paths
    assert "symlink.py" not in included_paths
    assert "pipe.py" not in included_paths


def test_investigate_causality_bounds_massive_failure_output() -> None:
    target = RiskTarget(
        file_path="src/module.py",
        line_number=42,
        description="Potential boundary bug",
        evidence="x = data[0]",
        category="boundary-condition",
    )

    # Place traceback near start so 64KB truncation still includes it
    massive_stdout = '\nFile "src/module.py", line 42, in calculate\n' + "x" * (1024 * 1024)
    head_test = _TestResult(
        command=["pytest"],
        environment="host",
        exit_code=1,
        duration_seconds=1.5,
        status="failed",
        stdout=massive_stdout,
        stderr="stderr " * 1000,
        passed=0,
        failed=1,
        errors=0,
        skipped=0,
        failure_details=[_TestFailure(test_id="test_fail", kind="failure", message="AssertionError", detail="detail text")],
    )

    investigation = investigate_causality(target, head_test)
    assert investigation.matched_file is True
    assert investigation.matched_line is True
    assert investigation.confidence == "high"


def test_investigate_causality_rejects_invalid_line_number() -> None:
    # Use model_construct to simulate unvalidated input
    target = RiskTarget.model_construct(
        file_path="src/module.py",
        line_number=-1,
        description="Bad line",
        evidence="invalid",
        category="test",
    )
    head_test = _TestResult(
        command=["pytest"],
        environment="host",
        exit_code=1,
        duration_seconds=0.5,
        status="failed",
        stdout="failure",
        stderr="",
        passed=0,
        failed=1,
        errors=0,
        skipped=0,
    )
    inv = investigate_causality(target, head_test)
    assert inv.causally_linked is False
    assert inv.confidence == "none"
    assert "Invalid target line number" in inv.explanation


def test_prioritize_risks_validates_top_k() -> None:
    context = PRContext(repository="r", base_sha="a" * 40, head_sha="b" * 40, files=[])
    with pytest.raises(ValueError, match="top_k must be an integer between 1 and 100"):
        prioritize_risks(context, top_k=-1)

    with pytest.raises(ValueError, match="top_k must be an integer between 1 and 100"):
        prioritize_risks(context, top_k=0)


def test_remaining_budget_rejects_non_finite_deadline() -> None:
    with pytest.raises(ValueError, match="finite"):
        remaining_budget(float("nan"))

    with pytest.raises(ValueError, match="finite"):
        remaining_budget(float("inf"))


@pytest.mark.parametrize("declaration", [
    "VALUE = print('import side effect')",
    "def helper(value=print('default side effect')): pass",
    "@print('decorator side effect')\ndef helper(): pass",
    "class Helper:\n    VALUE = print('class side effect')",
    "class Helper(factory()): pass",
])
def test_parse_rejects_explicit_execution_hidden_in_declarations(declaration: str) -> None:
    import json

    with pytest.raises(ValueError, match="top-level executable"):
        _parse_test(json.dumps({"test_code": declaration + "\n\ndef test_ok(): assert True\n",
                               "rationale": "declaration boundary"}))


def test_parse_preserves_literal_constants_and_pytest_decorators() -> None:
    import json

    code = ("import pytest\nCASES = [1, 2]\nclass Helper:\n    VALUE = 1\n"
            "@pytest.fixture(scope='module')\ndef value(): return 1\n"
            "@pytest.mark.parametrize('number', [1, 2])\n"
            "def test_ok(number, value): assert number >= value\n")
    assert _parse_test(json.dumps({"test_code": code, "rationale": "compatible"})).test_code == code


def test_observation_cannot_reveal_protected_source_through_directory_alias(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.mkdir()
    (secret / "secret.py").write_text("SECRET_PROTECTED_BODY")
    (tmp_path / "alias").symlink_to(secret, target_is_directory=True)
    context = PRContext(repository="repo", base_sha="a" * 40, head_sha="b" * 40,
                        files=[ChangedFile(path="alias/secret.py", status="D", hunks=[])])
    assert "SECRET_PROTECTED_BODY" not in _observation(context, tmp_path, "test")


def test_artifact_parent_swap_cannot_write_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    parent = tree / "tests"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and (str(path).endswith("test_candidate.py") or Path(path).name == "tests"):
            swapped = True
            parent.rename(tree / "original-tests")
            parent.symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    try:
        _write_test_artifact(tree, "tests/test_candidate.py", "def test_ok(): assert True\n")
    except ValueError:
        pass
    assert list(outside.iterdir()) == []


def test_causality_handles_arbitrary_positive_integer_without_float_conversion() -> None:
    target = RiskTarget(file_path="app.py", line_number=10**1000, description="large",
                        evidence="line", category="boundary")
    assert investigate_causality(target, None).confidence == "none"


def test_risk_limit_rejects_boolean() -> None:
    context = PRContext(repository="r", base_sha="a" * 40, head_sha="b" * 40, files=[])
    with pytest.raises(ValueError, match="top_k"):
        prioritize_risks(context, top_k=True)
