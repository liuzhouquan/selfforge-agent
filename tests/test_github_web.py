import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api.app import app
from selfforge_agent.coding.models import RepairReport
from selfforge_agent.integrations import github_pr
from selfforge_agent.integrations.github_pr import GitHubPR, GitHubPRError, parse_pr_url
from selfforge_agent.models import ChangedFile, DiffHunk, DiffLine, PRContext
from selfforge_agent.providers import TokenUsage
from selfforge_agent.review.deepseek import DeepSeekReviewer, DeepSeekReviewError


def sample_pr() -> GitHubPR:
    context = PRContext(
        repository="https://github.com/example/project",
        base_sha="a" * 40,
        head_sha="b" * 40,
        files=[
            ChangedFile(
                path="app.py",
                status="M",
                hunks=[
                    DiffHunk(
                        header="@@ -1 +1 @@",
                        old_start=1,
                        new_start=1,
                        lines=[DiffLine(kind="added", content="eval(value)", new_line=1)],
                    )
                ],
            )
        ],
    )
    return GitHubPR(
        url="https://github.com/example/project/pull/2",
        owner="example",
        repo="project",
        number=2,
        title="Change app",
        author="contributor",
        state="open",
        base_branch="main",
        head_branch="feature",
        head_repository="contributor/project",
        maintainer_can_modify=False,
        context=context,
        warnings=[],
    )


def test_pr_url_is_restricted_to_github() -> None:
    assert parse_pr_url("https://github.com/pallets/flask/pull/5918") == (
        "pallets", "flask", 5918
    )
    for url in (
        "http://github.com/pallets/flask/pull/1",
        "https://evil.example/pallets/flask/pull/1",
        "https://github.com/pallets/flask/pull/1/../../secrets",
    ):
        with pytest.raises(GitHubPRError):
            parse_pr_url(url)


def test_github_files_create_reviewable_context(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        "title": "Fix thing", "state": "open", "user": {"login": "alice"},
        "base": {"sha": "a" * 40, "ref": "main"},
        "head": {"sha": "b" * 40, "ref": "fix", "repo": {"full_name": "alice/repo"}},
        "maintainer_can_modify": True,
    }
    files = [
        {
            "filename": "app.py", "status": "modified",
            "patch": "@@ -1 +1,2 @@\n value = 1\n+eval(value)",
        },
        {"filename": "image.png", "status": "added"},
    ]
    monkeypatch.setattr(github_pr, "_api", lambda endpoint: files if "/files?" in endpoint else metadata)
    pr = github_pr.load_pr("https://github.com/alice/repo/pull/7")
    assert pr.context.files[0].hunks[0].lines[-1].new_line == 2
    assert pr.context.files[1].patch_available is False
    assert len(pr.warnings) == 1
    assert pr.maintainer_can_modify is True


def test_web_review_and_checkout_routes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("selfforge_agent.api.app.load_pr", lambda _url: sample_pr())
    monkeypatch.setattr(
        "selfforge_agent.api.app.checkout_pr",
        lambda _url, _root: github_pr.CheckoutResult(
            path=str(tmp_path), branch="vpa-pr-2", head_sha="b" * 40,
            diff_base_sha="a" * 40,
            pr_url="https://github.com/example/project/pull/2",
        ),
    )
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert "selfForge Agent · Python Coding Agent" in client.get("/").text
    assert "PR 审查与验证" in client.get("/").text
    response = client.post(
        "/api/github/review",
        json={"url": "https://github.com/example/project/pull/2"},
    )
    assert response.status_code == 200
    assert response.json()["report"]["findings"][0]["verification_status"] == "suspected"
    assert response.json()["report"]["test_results"] == []
    checkout = client.post(
        "/api/github/checkout", json={"url": "https://github.com/example/project/pull/2"}
    )
    assert checkout.status_code == 200
    assert checkout.json()["branch"] == "vpa-pr-2"
    assert checkout.json()["diff_base_sha"] == "a" * 40


def test_web_repair_requires_operator_and_request_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    payload = {
        "url": "https://github.com/example/project/pull/2",
        "task": "Fix the regression",
        "trust_local_tests": True,
        "sandbox": "host",
    }
    monkeypatch.delenv("SELFFORGE_ENABLE_HOST_TESTS", raising=False)
    assert client.post("/api/github/repair", json=payload).status_code == 403
    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    payload["trust_local_tests"] = False
    assert client.post("/api/github/repair", json=payload).status_code == 403


def test_web_repair_uses_checkout_merge_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from selfforge_agent.api import app as api_module

    called: dict[str, object] = {}
    pr = sample_pr()
    merge_base = "c" * 40

    class FakeAgent:
        def __init__(self, _model: object, *, runner: object, session: object) -> None:
            called.update(runner=type(runner).__name__, session_root=session.root)

        def run(self, repository: Path, base: str, head: str, task: str, *,
                trusted: bool, timeout: float) -> RepairReport:
            called.update(repository=repository, base=base, head=head, task=task,
                          trusted=trusted, timeout=timeout)
            now = datetime.now(UTC)
            return RepairReport(
                run_id="test", repository=str(repository), base_sha=base, head_sha=head,
                task=task, context=pr.context, findings=[], status="inconclusive",
                started_at=now, finished_at=now,
            )

    monkeypatch.setenv("SELFFORGE_ENABLE_HOST_TESTS", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(api_module, "load_pr", lambda _url: pr)
    monkeypatch.setattr(api_module, "checkout_pr", lambda _url, _root: github_pr.CheckoutResult(
        path=str(tmp_path), branch="vpa-pr-2", head_sha="b" * 40,
        diff_base_sha=merge_base, pr_url=pr.url,
    ))
    class FakeRouter:
        def __init__(self, _candidates: object) -> None:
            self.selected_provider = None
            self.attempts: list[object] = []

    monkeypatch.setattr(api_module, "FallbackCodingModel", FakeRouter)
    monkeypatch.setattr(api_module, "RepairAgent", FakeAgent)
    response = TestClient(app).post("/api/github/repair", json={
        "url": pr.url, "task": "Fix the regression", "trust_local_tests": True,
        "sandbox": "host",
    })
    assert response.status_code == 200
    assert called == {
        "repository": tmp_path, "base": merge_base, "head": "b" * 40,
        "task": "Fix the regression", "trusted": True, "timeout": 60,
        "runner": "TestRunner", "session_root": tmp_path,
    }


def test_web_repair_defaults_to_docker_without_host_trust(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    from selfforge_agent.api import app as api_module

    called: dict[str, object] = {}
    pr = sample_pr()

    class FakeAgent:
        def __init__(self, _model: object, *, runner: object, session: object) -> None:
            called.update(runner=type(runner).__name__, session_root=session.root, image=runner.image)

        def run(self, repository: Path, base: str, head: str, task: str, *,
                trusted: bool, timeout: float) -> RepairReport:
            called["trusted"] = trusted
            called["timeout"] = timeout
            now = datetime.now(UTC)
            return RepairReport(
                run_id="test", repository=str(repository), base_sha=base, head_sha=head,
                task=task, context=pr.context, findings=[], status="inconclusive",
                started_at=now, finished_at=now,
            )

    class FakeRouter:
        def __init__(self, _candidates: object) -> None:
            self.selected_provider = None
            self.attempts: list[object] = []

    monkeypatch.delenv("SELFFORGE_ENABLE_HOST_TESTS", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(api_module, "load_pr", lambda _url: pr)
    monkeypatch.setattr(api_module, "checkout_pr", lambda _url, _root: github_pr.CheckoutResult(
        path=str(tmp_path), branch="vpa-pr-2", head_sha="b" * 40,
        diff_base_sha="a" * 40, pr_url=pr.url,
    ))
    monkeypatch.setattr(api_module, "FallbackCodingModel", FakeRouter)
    monkeypatch.setattr(api_module, "RepairAgent", FakeAgent)
    response = TestClient(app).post("/api/github/repair", json={
        "url": pr.url, "task": "Fix the regression", "image": "custom-sandbox:v1", "timeout": 123,
    })
    assert response.status_code == 200
    assert called == {"runner": "DockerTestRunner", "session_root": tmp_path,
                      "trusted": False, "image": "custom-sandbox:v1", "timeout": 123}


def test_deepseek_findings_must_point_to_added_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    findings = [
        {"file_path": "app.py", "line_number": 1, "severity": "high", "category": "bug", "description": "Risk"},
        {"file_path": "app.py", "line_number": 99, "severity": "high", "category": "bug", "description": "Invented"},
    ]
    monkeypatch.setattr(
        "selfforge_agent.review.deepseek.request_json",
        lambda *_a, **_k: {
            "choices": [{"message": {"content": json.dumps({"findings": findings})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )
    findings = DeepSeekReviewer("test-key").review(sample_pr().context)
    assert len(findings) == 1
    assert findings[0].evidence == "eval(value)"
    assert findings[0].source == "deepseek"
    assert findings[0].verification_status == "suspected"


def test_provider_response_has_wall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class TrickleResponse:
        fp = None

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read1(self, _limit: int) -> bytes:
            return b" "

    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr("selfforge_agent.integrations.model_http.monotonic", lambda: next(clock))
    monkeypatch.setattr(
        "selfforge_agent.integrations.model_http.urlopen", lambda *_a, **_k: TrickleResponse()
    )
    with pytest.raises(DeepSeekReviewError, match="timed out"):
        DeepSeekReviewer("test-key", timeout=1).review(sample_pr().context)


def test_review_switches_provider_and_reports_actual_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from selfforge_agent.api import app as api_module

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setattr(api_module, "load_pr", lambda _url: sample_pr())

    def fake_review(self: object, _context: PRContext) -> tuple[list, TokenUsage]:
        if self.provider.name == "deepseek":
            raise DeepSeekReviewError("Provider response timed out")
        return [], TokenUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15)

    monkeypatch.setattr(api_module.ProviderReviewer, "review_with_usage", fake_review)
    response = TestClient(app).post("/api/github/review", json={
        "url": sample_pr().url, "provider": "auto", "fallback": True,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["model_provider"] == "openrouter"
    assert [attempt["status"] for attempt in data["model_attempts"]] == ["error", "success"]
    assert data["model_attempts"][1]["usage"]["total_tokens"] == 15
    assert data["report"]["execution_status"] == "completed"
    no_fallback = TestClient(app).post("/api/github/review", json={
        "url": sample_pr().url, "provider": "deepseek", "fallback": False,
    }).json()
    assert no_fallback["model_provider"] is None
    assert len(no_fallback["model_attempts"]) == 1
    assert no_fallback["report"]["execution_status"] == "error"


def test_provider_status_check_does_not_expose_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from selfforge_agent import providers

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")

    def fake_request(url: str, _key: str, **_kwargs: object) -> dict:
        if "deepseek.com" in url:
            return {"is_available": True, "balance_infos": [
                {"currency": "USD", "total_balance": "5.00"}
            ]}
        return {"data": {"limit_remaining": 2.5, "usage": 1.25}}

    monkeypatch.setattr(providers, "request_json", fake_request)
    client = TestClient(app)
    config = client.get("/api/config")
    assert config.status_code == 200
    assert [item["configured"] for item in config.json()["providers"]] == [True, True]
    assert "test-key" not in config.text
    statuses = client.post("/api/providers/check", json={"provider": "all"})
    assert statuses.status_code == 200
    assert [item["status"] for item in statuses.json()] == ["ready", "ready"]
    assert statuses.json()[0]["balance"] == "5.00 USD"
    assert statuses.json()[1]["balance"] == "$2.5000 remaining"
    assert "test-key" not in statuses.text
