"""Live-model failure -> edit -> retest demo in a disposable Docker workspace."""

import argparse
import json
import tempfile
from pathlib import Path

from selfforge_agent.providers import route_providers
from selfforge_agent.runtime.agent import CodingAgent
from selfforge_agent.runtime.execution import DockerExecutor
from selfforge_agent.runtime.model import OpenAIChatAdapter
from selfforge_agent.runtime.session import SessionStore
from selfforge_agent.runtime.tools import ToolRegistry
from selfforge_agent.verification.runner import DockerTestRunner

SOURCE = "def unique_in_order(values):\n    return sorted(set(values))\n"
TESTS = '''from app import unique_in_order


def test_order():
    assert unique_in_order([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_generator():
    assert unique_in_order(iter([2, 1, 2])) == [2, 1]


def test_empty():
    assert unique_in_order([]) == []


def test_single():
    assert unique_in_order([7, 7]) == [7]
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deepseek", "openrouter"], default="deepseek")
    parser.add_argument("--image", default="selfforge-agent-sandbox:latest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    model = OpenAIChatAdapter(route_providers(args.provider, fallback=False))
    with tempfile.TemporaryDirectory(prefix="selfforge-recovery-demo-") as temp:
        root = Path(temp)
        workspace = root / "source"
        (workspace / "tests").mkdir(parents=True)
        (workspace / "app.py").write_text(SOURCE)
        test = workspace / "tests/test_order.py"
        test.write_text(TESTS)
        session = SessionStore(root / "session")
        executor = DockerExecutor(workspace, args.image)
        agent = CodingAgent(model, ToolRegistry(workspace, executor),
                            session, max_steps=12, time_budget=180)
        result = agent.run(
            "First call run_tests on tests to observe the failures. Then inspect tests/source "
            "and repair app.py: unique_in_order must deduplicate hashable values while "
            "preserving first-seen order, including generators. Do not modify tests. "
            "Use run_tests after your final edit, then report the result.")
        evidence = DockerTestRunner(executor.image).run(workspace, test_path="tests", timeout=30)
        tools = [e for e in session.trace_events() if e["event"] == "tool"]
        observed_failure = bool(tools and tools[0]["name"] == "run_tests" and not tools[0]["ok"])
        tests_unchanged = test.read_text() == TESTS
        changed = (workspace / "app.py").read_text() != SOURCE
        success = (result.status == "completed" and observed_failure and tests_unchanged
                   and changed and evidence.status == "passed" and evidence.passed == 4)
        report = {"kind": "live-generated-fixture", "provider": args.provider,
                  "image_id": executor.image,
                  "success": success, "agent_status": result.status,
                  "observed_initial_test_failure": observed_failure, "tests_unchanged": tests_unchanged,
                  "source_changed": changed, "steps": result.steps, "tools_used": result.tools_used,
                  "independent_tests": {"status": evidence.status, "passed": evidence.passed,
                                        "failed": evidence.failed, "errors": evidence.errors},
                  "tool_trace": [{"name": event["name"], "ok": event["ok"]} for event in tools]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
