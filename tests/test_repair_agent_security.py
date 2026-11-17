"""Security and lifecycle hardening reproduction tests for RepairAgent (SF-REPAIR-01)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from selfforge_agent.coding.agent import RepairAgent, _editable_files, _test_output
from selfforge_agent.coding.models import CodeEdit, RepairObservation
from selfforge_agent.models import ChangedFile, PRContext, TestResult
from selfforge_agent.runtime.session import SessionStore

TestResult.__test__ = False


class DummyValidModel:
    def propose(self, observation: RepairObservation) -> CodeEdit:
        return CodeEdit(path="calculator.py", old_text="return a - b",
                        new_text="return a + b", rationale="fix")


class DummyRunner:
    requires_trust = False

    def run(self, worktree, **kwargs):
        return TestResult(status="passed", passed=1, failed=0, errors=0,
                          skipped=0, duration_seconds=0.1, environment="host")


class TestRepairAgentInitAndParameterDefense:
    """SF-REPAIR-01-A1: Constructor and parameter gating tests."""

    def test_init_rejects_none_or_invalid_model(self):
        with pytest.raises((TypeError, ValueError)):
            RepairAgent(None)

        with pytest.raises((TypeError, ValueError)):
            RepairAgent("not-a-model")

        with pytest.raises((TypeError, ValueError)):
            RepairAgent(object())

    def test_init_rejects_invalid_runner(self):
        with pytest.raises((TypeError, ValueError)):
            RepairAgent(DummyValidModel(), runner="not-a-runner")

        with pytest.raises((TypeError, ValueError)):
            RepairAgent(DummyValidModel(), runner=123)

    def test_init_rejects_invalid_session(self):
        with pytest.raises((TypeError, ValueError)):
            RepairAgent(DummyValidModel(), session=123)

    def test_run_rejects_non_string_task(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        with pytest.raises((ValueError, TypeError)):
            agent.run(repo, base, head, 123, trusted=True)

        with pytest.raises((ValueError, TypeError)):
            agent.run(repo, base, head, None, trusted=True)

        with pytest.raises((ValueError, TypeError)):
            agent.run(repo, base, head, ["fix"], trusted=True)

    def test_run_rejects_empty_or_whitespace_task(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        with pytest.raises(ValueError):
            agent.run(repo, base, head, "", trusted=True)

        with pytest.raises(ValueError):
            agent.run(repo, base, head, "   \n\t  ", trusted=True)

    def test_run_rejects_oversized_task(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        with pytest.raises(ValueError, match="length"):
            agent.run(repo, base, head, "x" * 100_001, trusted=True)

    def test_run_rejects_invalid_max_attempts(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        for invalid in [True, False, 1.5, 0, 4, -1, "2"]:
            with pytest.raises(ValueError):
                agent.run(repo, base, head, "Fix add", trusted=True, max_attempts=invalid)

    def test_run_rejects_invalid_timeout(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        for invalid in [-10, 0, float("nan"), float("inf"), True, False, "60"]:
            with pytest.raises(ValueError):
                agent.run(repo, base, head, "Fix add", trusted=True, timeout=invalid)

    def test_run_rejects_invalid_trusted(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        for invalid in [1, 0, "yes", None]:
            with pytest.raises((ValueError, TypeError)):
                agent.run(repo, base, head, "Fix add", trusted=invalid)

    def test_run_rejects_invalid_test_path(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        for invalid in ["/etc/passwd", "../tests", "tests\0evil", "", "   "]:
            with pytest.raises(ValueError):
                agent.run(repo, base, head, "Fix add", trusted=True, test_path=invalid)

    def test_run_rejects_invalid_base_or_head(self, demo_repo):
        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel())
        for invalid in ["", "  ", "--upload-pack", "head\0evil", "head\nevil", 123]:
            with pytest.raises(ValueError):
                agent.run(repo, invalid, head, "Fix add", trusted=True)
            with pytest.raises(ValueError):
                agent.run(repo, base, invalid, "Fix add", trusted=True)

    def test_run_rejects_non_directory_repository(self, tmp_path):
        agent = RepairAgent(DummyValidModel())
        fake_file = tmp_path / "not_a_dir"
        fake_file.write_text("hello")
        with pytest.raises((ValueError, OSError)):
            agent.run(fake_file, "base", "head", "Fix add", trusted=True)

        with pytest.raises((ValueError, OSError)):
            agent.run(tmp_path / "nonexistent", "base", "head", "Fix add", trusted=True)


class TestSafeSourceAndPatchDefense:
    """SF-REPAIR-01-A2: Source file extraction and patch defense."""

    def test_test_output_handles_none_stdout_or_stderr(self):
        # Baseline raises TypeError if stdout or stderr is None or result is None
        res1 = SimpleNamespace(stdout=None, stderr="error output")
        assert "error output" in _test_output(res1)

        res2 = SimpleNamespace(stdout="normal output", stderr=None)
        assert "normal output" in _test_output(res2)

        res3 = SimpleNamespace(stdout=None, stderr=None)
        assert _test_output(res3) == "\n"

        assert _test_output(None) == ""

    def test_editable_files_filters_absolute_and_traversal_paths(self, tmp_path):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / "valid.py").write_text("x = 1")
        outside = tmp_path / "secret.py"
        outside.write_text("secret = 42")

        ctx = PRContext(
            repository=str(worktree),
            base_sha="base",
            head_sha="head",
            files=[
                ChangedFile(path="/etc/passwd", status="M", hunks=[]),
                ChangedFile(path="../../secret.py", status="M", hunks=[]),
                ChangedFile(path="valid.py", status="M", hunks=[]),
            ],
        )
        sources = _editable_files(ctx, worktree)
        paths = [s.path for s in sources]
        assert "/etc/passwd" not in paths
        assert "../../secret.py" not in paths
        assert paths == ["valid.py"]

    def test_editable_files_filters_symlink_directories(self, tmp_path):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        outside = tmp_path / "external"
        outside.mkdir()
        (outside / "sneaky.py").write_text("sneaky = 1")

        # Symlink a directory into the worktree
        (worktree / "linked_dir").symlink_to(outside, target_is_directory=True)

        ctx = PRContext(
            repository=str(worktree),
            base_sha="base",
            head_sha="head",
            files=[
                ChangedFile(path="linked_dir/sneaky.py", status="M", hunks=[]),
            ],
        )
        sources = _editable_files(ctx, worktree)
        assert len(sources) == 0

    def test_editable_files_handles_oserror_gracefully(self, tmp_path, monkeypatch):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / "valid.py").write_text("x = 1")

        ctx = PRContext(
            repository=str(worktree),
            base_sha="base",
            head_sha="head",
            files=[ChangedFile(path="valid.py", status="M", hunks=[])],
        )

        import os
        real_open = os.open

        def bad_open(path, *args, **kwargs):
            if path == "valid.py" and kwargs.get("dir_fd") is not None:
                raise OSError("I/O error during read")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", bad_open)
        sources = _editable_files(ctx, worktree)
        assert len(sources) == 0

    def test_editable_files_deduplicates_paths(self, tmp_path):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / "calc.py").write_text("x = 1")

        ctx = PRContext(
            repository=str(worktree),
            base_sha="base",
            head_sha="head",
            files=[
                ChangedFile(path="calc.py", status="M", hunks=[]),
                ChangedFile(path="calc.py", status="M", hunks=[]),
            ],
        )
        sources = _editable_files(ctx, worktree)
        assert len(sources) == 1


class TestModelInteractionAndRepairLoopIsolation:
    """SF-REPAIR-01-A3: Model proposal anomaly and runner error isolation."""

    def test_repair_loop_handles_none_edit(self, demo_repo):
        class NoneModel:
            def propose(self, observation):
                return None

        repo, base, head = demo_repo
        agent = RepairAgent(NoneModel())
        report = agent.run(repo, base, head, "Fix add", trusted=True, max_attempts=1)
        assert report.status == "unresolved"
        assert len(report.attempts) == 1
        assert "CodeEdit" in report.attempts[0].error or "None" in report.attempts[0].error

    def test_repair_loop_handles_malformed_edit_object(self, demo_repo):
        class MalformedModel:
            def propose(self, observation):
                return {"not": "a CodeEdit"}

        repo, base, head = demo_repo
        agent = RepairAgent(MalformedModel())
        report = agent.run(repo, base, head, "Fix add", trusted=True, max_attempts=1)
        assert report.status == "unresolved"
        assert len(report.attempts) == 1
        assert report.attempts[0].error is not None

    def test_repair_loop_handles_non_string_edit_fields(self, demo_repo):
        class NonStringModel:
            def propose(self, observation):
                return SimpleNamespace(path=123, old_text=None, new_text=True)

        repo, base, head = demo_repo
        agent = RepairAgent(NonStringModel())
        report = agent.run(repo, base, head, "Fix add", trusted=True, max_attempts=1)
        assert report.status == "unresolved"
        assert len(report.attempts) == 1
        assert report.attempts[0].error is not None

    def test_repair_loop_handles_runner_exception(self, demo_repo):
        class CrashingRunner:
            requires_trust = False

            def run(self, worktree, **kwargs):
                raise RuntimeError("Docker daemon exploded")

        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel(), runner=CrashingRunner())
        report = agent.run(repo, base, head, "Fix add", trusted=True)
        assert report.status == "error"
        assert any("Docker daemon exploded" in err for err in report.errors)

    def test_repair_loop_handles_runner_returning_none(self, demo_repo):
        class NoneRunner:
            requires_trust = False

            def run(self, worktree, **kwargs):
                return None

        repo, base, head = demo_repo
        agent = RepairAgent(DummyValidModel(), runner=NoneRunner())
        report = agent.run(repo, base, head, "Fix add", trusted=True)
        assert report.status == "error"

    def test_repair_loop_recovers_after_malformed_edit(self, demo_repo):
        class RecoveringModel:
            def __init__(self):
                self.calls = 0

            def propose(self, observation):
                self.calls += 1
                if self.calls == 1:
                    return None  # Malformed attempt
                return CodeEdit(
                    path="calculator.py",
                    old_text="return a - b",
                    new_text="return a + b",
                    rationale="Fix on retry",
                )

        repo, base, head = demo_repo
        agent = RepairAgent(RecoveringModel())
        report = agent.run(repo, base, head, "Fix add", trusted=True, max_attempts=2)
        assert report.status == "verified"
        assert len(report.attempts) == 2
        assert report.attempts[0].error is not None
        assert report.attempts[1].test_result.status == "passed"


class TestTelemetryAndStatusAlignment:
    """SF-REPAIR-01-A4: Status machine alignment and robust finally block."""

    def test_finally_handles_malformed_model_attempts(self, demo_repo):
        class ModelWithMalformedAttempts:
            def __init__(self):
                # Contains non-object and corrupted attempt entries
                self.attempts = [None, 123, {"provider": "bad"}, "corrupt"]

            def propose(self, observation):
                return CodeEdit(path="calculator.py", old_text="return a - b",
                                new_text="return a + b", rationale="fix")

        repo, base, head = demo_repo
        agent = RepairAgent(ModelWithMalformedAttempts())
        # Must not raise AttributeError during finally
        report = agent.run(repo, base, head, "Fix add", trusted=True)
        assert report.status == "verified"
        assert report.total_tokens >= 0

    def test_finally_handles_corrupted_token_usage(self, demo_repo):
        class ModelWithCorruptedUsage:
            def __init__(self):
                self.attempts = [
                    SimpleNamespace(provider="deepseek", model="deepseek-chat",
                                    usage=SimpleNamespace(total_tokens=-500)),
                    SimpleNamespace(provider="deepseek", model="deepseek-chat",
                                    usage=SimpleNamespace(total_tokens="not-an-int")),
                    SimpleNamespace(provider="deepseek", model="deepseek-chat",
                                    usage=None),
                ]

            def propose(self, observation):
                return CodeEdit(path="calculator.py", old_text="return a - b",
                                new_text="return a + b", rationale="fix")

        repo, base, head = demo_repo
        agent = RepairAgent(ModelWithCorruptedUsage())
        report = agent.run(repo, base, head, "Fix add", trusted=True)
        assert report.status == "verified"
        assert report.total_tokens >= 0

    def test_session_status_aligned_across_all_outcomes(self, demo_repo):
        repo, base, head = demo_repo

        # Outcome 1: Verified
        session1 = SessionStore(repo)
        agent1 = RepairAgent(DummyValidModel(), session=session1)
        rep1 = agent1.run(repo, base, head, "Fix add", trusted=True)
        assert rep1.status == "verified"
        assert session1.load()[1] == "verified"

        # Outcome 2: Unresolved
        class FailingModel:
            def propose(self, obs):
                return CodeEdit(path="calculator.py", old_text="return a - b",
                                new_text="return a * b", rationale="wrong fix")

        session2 = SessionStore(repo)
        agent2 = RepairAgent(FailingModel(), session=session2)
        rep2 = agent2.run(repo, base, head, "Fix add", trusted=True, max_attempts=1)
        assert rep2.status == "unresolved"
        assert session2.load()[1] == "unresolved"

        # Outcome 3: Inconclusive (when head test already passes)
        session3 = SessionStore(repo)
        agent3 = RepairAgent(DummyValidModel(), session=session3)
        rep3 = agent3.run(repo, base, base, "Fix add", trusted=True)
        assert rep3.status == "inconclusive"
        assert session3.load()[1] == "inconclusive"

    def test_trace_test_handles_none_or_malformed_test_result(self, demo_repo):
        repo, _base, _head = demo_repo
        session = SessionStore(repo)
        agent = RepairAgent(DummyValidModel(), session=session)
        # Should not raise exception
        agent._trace_test("test", None)
        agent._trace_test("test", SimpleNamespace(status="passed"))


@pytest.mark.parametrize("alias", ["tests", ".env.private"])
def test_editable_observation_rejects_directory_aliases(tmp_path, alias):
    tree = tmp_path / 'tree'
    tree.mkdir()
    protected = tree / alias
    protected.mkdir()
    (protected / 'secret.py').write_text('private_fixture = 1\n')
    (tree / 'src').symlink_to(protected, target_is_directory=True)
    context = PRContext(repository=str(tree), base_sha='a'*40, head_sha='b'*40,
                        files=[ChangedFile(path='src/secret.py', status='M', hunks=[])])
    assert _editable_files(context, tree) == []


@pytest.mark.parametrize("missing", ['usage', 'rate'])
def test_repair_withholds_partial_cost_for_unknown_calls(demo_repo, monkeypatch, missing):
    import json

    from selfforge_agent.providers import ModelAttempt, TokenUsage

    monkeypatch.setenv('SELFFORGE_PRICING_JSON', json.dumps({'deepseek': {'fixture': {
        'prompt_per_million': 1, 'completion_per_million': 1, 'currency': 'USD',
        'source': 'fixture', 'as_of': '2026-11-17'}}}))
    usage = TokenUsage(prompt_tokens=100, completion_tokens=0, total_tokens=100)

    class Model(DummyValidModel):
        def __init__(self):
            self.attempts = []

        def propose(self, observation):
            self.attempts.extend([
                ModelAttempt(provider='deepseek', model='fixture', status='success', usage=usage),
                ModelAttempt(provider='deepseek', model='unpriced' if missing == 'rate' else 'fixture',
                             status='error', usage=usage if missing == 'rate' else None)])
            return super().propose(observation)

    repo, base, head = demo_repo
    result = RepairAgent(Model()).run(repo, base, head, 'fix regression', trusted=True, max_attempts=1)
    assert result.status == 'verified'
    assert result.total_tokens == (200 if missing == 'rate' else 100)
    assert result.monetary_cost is None
