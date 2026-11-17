"""Tests for objective evaluation runner hardening, case validation, and execution isolation (SF-EVAL-11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfforge_agent.runtime.evaluation import (
    CASES,
    EvalCase,
    EvalOutcome,
    OfflineFixtureModel,
    evaluate,
)


class TestEvaluateEntryValidation:
    """SF-EVAL-11-A1: validate evaluate() parameter bounds, types, and division-by-zero guard."""

    @pytest.mark.parametrize("invalid_factory", [
        None,
        "not_callable",
        12345,
        [],
        object(),
    ])
    def test_evaluate_rejects_non_callable_factory(self, invalid_factory) -> None:
        with pytest.raises(TypeError, match="callable"):
            evaluate(invalid_factory)

    @pytest.mark.parametrize("invalid_steps", [
        0,
        -1,
        -100,
        10_001,
        "10",
        None,
        True,
        False,
        1.5,
    ])
    def test_evaluate_rejects_invalid_max_steps(self, invalid_steps) -> None:
        with pytest.raises((ValueError, TypeError)):
            evaluate(OfflineFixtureModel, max_steps=invalid_steps)

    @pytest.mark.parametrize("invalid_budget", [
        0,
        0.0,
        -1.0,
        -100.0,
        float("nan"),
        float("inf"),
        float("-inf"),
        "180",
        None,
        True,
        False,
    ])
    def test_evaluate_rejects_invalid_time_budget(self, invalid_budget) -> None:
        with pytest.raises((ValueError, TypeError)):
            evaluate(OfflineFixtureModel, time_budget=invalid_budget)

    @pytest.mark.parametrize("invalid_timeout", [
        0,
        0.0,
        -5.0,
        float("nan"),
        float("inf"),
        "30",
        None,
        True,
        False,
    ])
    def test_evaluate_rejects_invalid_test_timeout(self, invalid_timeout) -> None:
        with pytest.raises((ValueError, TypeError)):
            evaluate(OfflineFixtureModel, test_timeout=invalid_timeout)

    def test_evaluate_rejects_invalid_cases_collection(self) -> None:
        with pytest.raises(TypeError, match="EvalCase"):
            evaluate(OfflineFixtureModel, cases=["not_a_case"])

    def test_evaluate_handles_empty_cases_without_zero_division(self) -> None:
        report = evaluate(OfflineFixtureModel, cases=[])
        assert report["tasks"] == 0
        assert report["successes"] == 0
        assert report["success_rate"] == 0.0
        assert report["results"] == []


class TestEvalCaseInvariants:
    """SF-EVAL-11-A2: EvalCase structure invariants and untrusted input defense."""

    @pytest.mark.parametrize("invalid_name", [
        "",
        "   ",
        "addition/sub",
        "../traversal",
        "case\x00null",
        "case\nnewline",
        "a" * 200,
        123,
        None,
    ])
    def test_eval_case_rejects_invalid_name(self, invalid_name) -> None:
        with pytest.raises((ValueError, TypeError)):
            EvalCase(
                name=invalid_name,
                source="def f(): pass\n",
                test="def test_f(): pass\n",
                task="Fix f",
                old_text="pass",
                new_text="return 1",
            )

    @pytest.mark.parametrize("invalid_source", [
        "",
        "   ",
        "source\x00null",
        123,
        None,
        "x" * (1024 * 1024 + 10),
    ])
    def test_eval_case_rejects_invalid_source(self, invalid_source) -> None:
        with pytest.raises((ValueError, TypeError)):
            EvalCase(
                name="valid_case",
                source=invalid_source,
                test="def test_f(): pass\n",
                task="Fix f",
                old_text="pass",
                new_text="return 1",
            )

    @pytest.mark.parametrize("invalid_test", [
        "",
        "   ",
        "test\x00null",
        123,
        None,
    ])
    def test_eval_case_rejects_invalid_test(self, invalid_test) -> None:
        with pytest.raises((ValueError, TypeError)):
            EvalCase(
                name="valid_case",
                source="def f(): pass\n",
                test=invalid_test,
                task="Fix f",
                old_text="pass",
                new_text="return 1",
            )

    def test_eval_case_rejects_old_text_not_in_source(self) -> None:
        with pytest.raises(ValueError, match="old_text"):
            EvalCase(
                name="mismatched_text",
                source="def add(a, b):\n    return a + b\n",
                test="def test_add(): pass\n",
                task="Fix add",
                old_text="return a - b",
                new_text="return a + b",
            )

    def test_default_cases_integrity(self) -> None:
        assert len(CASES) >= 2
        for case in CASES:
            assert isinstance(case, EvalCase)
            assert case.old_text in case.source


class TestOfflineFixtureModelSecurity:
    """SF-EVAL-11-A3: OfflineFixtureModel parameter validation and turn defense."""

    def test_fixture_model_rejects_non_eval_case(self) -> None:
        with pytest.raises(TypeError, match="EvalCase"):
            OfflineFixtureModel("not_a_case")

    @pytest.mark.parametrize("bad_timeout", [
        0,
        -1.0,
        float("nan"),
        float("inf"),
        "10",
        None,
    ])
    def test_fixture_model_rejects_invalid_timeout(self, bad_timeout) -> None:
        model = OfflineFixtureModel(CASES[0])
        with pytest.raises((ValueError, TypeError)):
            model.complete([], [], bad_timeout)

    @pytest.mark.parametrize("bad_messages", [
        None,
        "not_a_list",
        123,
    ])
    def test_fixture_model_rejects_invalid_messages(self, bad_messages) -> None:
        model = OfflineFixtureModel(CASES[0])
        with pytest.raises(TypeError):
            model.complete(bad_messages, [], 10.0)


class TestEvaluateExecutionResilience:
    """SF-EVAL-11-A3: evaluate() execution isolation and fail-closed exception containment."""

    def test_evaluate_catches_model_factory_exception(self) -> None:
        def exploding_factory(_case):
            raise RuntimeError("Model service unavailable")

        report = evaluate(exploding_factory)
        assert report["tasks"] == len(CASES)
        assert report["successes"] == 0
        assert report["success_rate"] == 0.0
        for item in report["results"]:
            assert item["success"] is False
            assert item["status"] == "error"
            assert "model_factory_error:RuntimeError" in item["reason"]

    def test_evaluate_catches_agent_execution_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from selfforge_agent.runtime.agent import CodingAgent

        def exploding_run(self, _task):
            raise OSError("I/O error during tool loop")

        monkeypatch.setattr(CodingAgent, "run", exploding_run)
        report = evaluate(OfflineFixtureModel)
        assert report["tasks"] == len(CASES)
        assert report["successes"] == 0
        for item in report["results"]:
            assert item["success"] is False
            assert item["status"] == "error"
            assert "agent_error:OSError" in item["reason"]

    def test_evaluate_handles_removed_source_file_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from selfforge_agent.runtime.agent import AgentResult, CodingAgent

        def remove_source_and_succeed(self, _task):
            # Simulate a rogue agent that deleted app.py
            (self.session.root / "app.py").unlink(missing_ok=True)
            return AgentResult("completed", 2, None)

        monkeypatch.setattr(CodingAgent, "run", remove_source_and_succeed)
        report = evaluate(OfflineFixtureModel)
        assert report["tasks"] == len(CASES)
        assert report["successes"] == 0
        for item in report["results"]:
            assert item["success"] is False
            assert item["changed"] is False


class TestEvalOutcomeInvariants:
    """SF-EVAL-11-A4: EvalOutcome invariant checks and antifraud validations."""

    def test_eval_outcome_rejects_fraudulent_success_when_status_not_completed(self) -> None:
        with pytest.raises(ValueError, match="completed"):
            EvalOutcome(
                case="addition",
                success=True,
                status="error",
                changed=True,
                test_exit_code=0,
                reason="passed",
                steps=3,
                tools=["read_file", "edit_file"],
            )

    def test_eval_outcome_rejects_fraudulent_success_when_unchanged(self) -> None:
        with pytest.raises(ValueError, match="changed"):
            EvalOutcome(
                case="addition",
                success=True,
                status="completed",
                changed=False,
                test_exit_code=0,
                reason="passed",
                steps=3,
                tools=["read_file", "edit_file"],
            )

    def test_eval_outcome_rejects_fraudulent_success_when_test_failed(self) -> None:
        with pytest.raises(ValueError, match="test_exit_code"):
            EvalOutcome(
                case="addition",
                success=True,
                status="completed",
                changed=True,
                test_exit_code=1,
                reason="passed",
                steps=3,
                tools=["read_file", "edit_file"],
            )

    def test_eval_outcome_rejects_negative_steps(self) -> None:
        with pytest.raises(ValueError, match="steps"):
            EvalOutcome(
                case="addition",
                success=False,
                status="error",
                changed=False,
                test_exit_code=None,
                reason="failed",
                steps=-1,
                tools=[],
            )

    @pytest.mark.parametrize("invalid_tools", [
        None,
        "not_a_list",
        [123],
        ["read_file", ""],
    ])
    def test_eval_outcome_rejects_invalid_tools(self, invalid_tools) -> None:
        with pytest.raises((ValueError, TypeError)):
            EvalOutcome(
                case="addition",
                success=False,
                status="completed",
                changed=False,
                test_exit_code=1,
                reason="failed",
                steps=2,
                tools=invalid_tools,
            )


class TestCliEvalIntegrationAndSecurity:
    """SF-EVAL-11-A4: CLI eval argument defense and safe output writing."""

    def test_cli_eval_rejects_invalid_test_timeout(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        from selfforge_agent import cli

        monkeypatch.setattr("sys.argv", ["selfforge-agent", "eval", "--test-timeout", "-5.0"])
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code != 0

    def test_cli_eval_rejects_symlink_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from selfforge_agent import cli

        target = tmp_path / "real_file.json"
        symlink = tmp_path / "link.json"
        symlink.symlink_to(target)

        monkeypatch.setattr("sys.argv", ["selfforge-agent", "eval", "--offline", "--output", str(symlink)])
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code != 0

    def test_cli_eval_safe_output_writes_valid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        from selfforge_agent import cli

        out_file = tmp_path / "eval_report.json"
        monkeypatch.setattr("sys.argv", ["selfforge-agent", "eval", "--offline", "--output", str(out_file)])
        exit_code = cli.main()
        assert exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["tasks"] == 2
        assert data["successes"] == 2


def _scripted_model(program, *, run_tests=False):
    import json
    import shlex
    import sys

    from selfforge_agent.runtime.model import ModelTurn

    class Model:
        step = 0

        def complete(self, _messages, _tools, _timeout):
            self.step += 1
            if self.step == 1:
                name, arguments = 'bash', {'command': f'{shlex.quote(sys.executable)} -c {shlex.quote(program)}'}
            elif run_tests and self.step == 2:
                name, arguments = 'run_tests', {'path': 'tests'}
            else:
                return ModelTurn({'role': 'assistant', 'content': 'fixture done'}, 'stop', 'fixture', 'script', None)
            return ModelTurn({'role': 'assistant', 'tool_calls': [{'id': f'c{self.step}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}]}, 'tool_calls', 'fixture', 'script', None)

    return Model()


def test_evaluation_cannot_verify_a_wrong_solution_by_weakening_tests():
    program = "from pathlib import Path; Path('app.py').write_text('def add(a,b): return 0\\n'); Path('tests/test_app.py').write_text('def test_fake(): assert True\\n')"
    result = evaluate(lambda _case: _scripted_model(program, run_tests=True), cases=[CASES[0]])
    assert result['results'][0]['status'] == 'completed'
    assert result['successes'] == 0
    assert result['results'][0]['test_exit_code'] != 0


def test_invalid_source_encoding_fails_one_case_without_aborting_the_suite():
    def factory(case):
        return _scripted_model("from pathlib import Path; Path('app.py').write_bytes(bytes([255]))") if case.name == 'addition' else OfflineFixtureModel(case)

    result = evaluate(factory, cases=[CASES[0], CASES[1]])
    assert result['tasks'] == 2
    assert result['results'][0]['success'] is False
    assert result['results'][1]['success'] is True


@pytest.mark.parametrize('stage', ['factory', 'model'])
def test_public_evaluation_reports_only_sanitized_error_categories(stage):
    import json

    secret = 'Bearer fixture-secret /private/session/path'

    class BrokenModel:
        def complete(self, *_a, **_k):
            raise RuntimeError(secret)

    def factory(_case):
        if stage == 'factory':
            raise RuntimeError(secret)
        return BrokenModel()

    result = evaluate(factory, cases=[CASES[0]])
    assert result['successes'] == 0
    assert 'fixture-secret' not in json.dumps(result)
    assert '/private/session' not in json.dumps(result)


def test_evaluation_requires_positive_junit_evidence_for_skipped_tests(monkeypatch):
    from selfforge_agent.runtime.agent import AgentResult, CodingAgent

    case = EvalCase('skipped', 'VALUE = 1\n', 'import pytest\ndef test_skip(): pytest.skip("fixture")\n', 'fix VALUE', '1', '2')

    def pretend_completed(self, _task):
        (self.tools.root / 'app.py').write_text('VALUE = 2\n')
        return AgentResult(self.session.session_id, 'completed', 'fixture', 1, 0)

    monkeypatch.setattr(CodingAgent, 'run', pretend_completed)
    result = evaluate(OfflineFixtureModel, cases=[case])
    assert result['successes'] == 0


def test_preset_cases_cannot_be_modified_in_place():
    original = tuple(CASES)
    try:
        with pytest.raises(TypeError):
            CASES[0] = CASES[1]
    finally:
        if isinstance(CASES, list):
            CASES[:] = original


@pytest.mark.parametrize('name', [' addition', 'addition ', ' '*200+'addition'], ids=['leading', 'trailing', 'raw_overflow'])
def test_case_names_are_validated_without_hiding_invalid_whitespace(name):
    with pytest.raises(ValueError):
        EvalCase(name, 'VALUE = 1\n', 'def test_value(): assert True\n', 'fixture', '1', '2')


@pytest.mark.parametrize('parameter', ['time_budget', 'test_timeout'])
def test_huge_evaluation_budgets_are_controlled(parameter):
    with pytest.raises(ValueError):
        evaluate(OfflineFixtureModel, cases=[], **{parameter: 10**1000})


def test_evaluation_step_limit_matches_the_shared_agent_contract():
    with pytest.raises(ValueError):
        evaluate(OfflineFixtureModel, cases=[], max_steps=101)
