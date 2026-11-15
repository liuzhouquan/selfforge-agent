"""Security, fail-closed input defense, and boundary tests for coding model clients."""

import json

import pytest

from selfforge_agent.coding.model_client import (
    FallbackCodingModel,
    ModelError,
    OpenAICompatibleCodingModel,
)
from selfforge_agent.coding.models import RepairObservation, SourceFile
from selfforge_agent.integrations.model_http import ProviderHTTPError
from selfforge_agent.providers import ProviderConfig


def test_coding_model_timeout_validation() -> None:
    """Timeout must be a finite, positive number; reject bool, nan, inf, non-positive."""
    valid_url = "http://localhost:8080/v1"
    for bad_timeout in [True, False, float("nan"), float("inf"), float("-inf"), 0, -1, -0.5, "30", None]:
        with pytest.raises((ValueError, TypeError)):
            OpenAICompatibleCodingModel(model="gpt-test", base_url=valid_url, timeout=bad_timeout)  # type: ignore[arg-type]

    # FallbackCodingModel must also enforce finite positive timeout
    provider = ProviderConfig(
        name="test", label="Test", key="k", model="m",
        base_url="https://api.example.com/v1", status_url="",
    )
    for bad_timeout in [True, False, float("nan"), float("inf"), 0, -5, "10", None]:
        with pytest.raises((ValueError, TypeError)):
            FallbackCodingModel([provider], timeout=bad_timeout)  # type: ignore[arg-type]


def test_coding_model_base_url_and_credentials_validation() -> None:
    """Reject credentials, query, fragment, non-https remote, empty model, or missing key."""
    # Model name validation
    for bad_model in ["", "   ", None]:
        with pytest.raises((ValueError, TypeError)):
            OpenAICompatibleCodingModel(model=bad_model, base_url="http://localhost:8080/v1")  # type: ignore[arg-type]

    # Base URL validation
    for bad_url in [
        "",
        "   ",
        "https://user:pass@api.openai.com/v1",
        "https://api.openai.com/v1?query=1",
        "https://api.openai.com/v1#section",
        "http://insecure-remote.com/v1",
        "ftp://api.openai.com/v1",
        "file:///etc/passwd",
        "https:///v1",
    ]:
        with pytest.raises(ValueError):
            OpenAICompatibleCodingModel(model="gpt-test", base_url=bad_url, api_key="secret")

    # Remote base URL requires api_key
    for bad_key in ["", "   "]:
        with pytest.raises(ValueError):
            OpenAICompatibleCodingModel(model="gpt-test", base_url="https://api.openai.com/v1", api_key=bad_key)


def test_coding_model_observation_input_defense() -> None:
    """propose() must reject non-RepairObservation inputs and bounded payload overflows."""
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    for bad_obs in [None, "string", {"task": "fix"}, 123, [1, 2, 3]]:
        with pytest.raises((TypeError, ModelError)):
            client.propose(bad_obs)  # type: ignore[arg-type]

    # Oversized observation payload defense
    giant_observation = RepairObservation(
        task="T" * (3 * 1024 * 1024),
        attempt=1,
        files=[],
        failing_test_output="err",
    )
    with pytest.raises(ModelError):
        client.propose(giant_observation)


def test_coding_model_handles_finish_reason_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncated model response (finish_reason='length') must raise ModelError."""
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {
            "choices": [
                {
                    "message": {"content": '{"path": "a.py", "old_text": "foo", "new_text": "bar"'},
                    "finish_reason": "length",
                }
            ]
        },
    )
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    obs = RepairObservation(task="fix", attempt=1, files=[], failing_test_output="fail")
    with pytest.raises(ModelError, match="truncated|length"):
        client.propose(obs)


def test_coding_model_strips_markdown_code_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    """Markdown code fences (```json ... ```) must be parsed safely."""
    fenced_content = """```json
{
  "path": "src/lib.py",
  "old_text": "def old(): pass",
  "new_text": "def old(): return 42",
  "rationale": "fix bug"
}
```"""
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {
            "choices": [
                {
                    "message": {"content": fenced_content},
                    "finish_reason": "stop",
                }
            ]
        },
    )
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    obs = RepairObservation(
        task="fix",
        attempt=1,
        files=[SourceFile(path="src/lib.py", content="def old(): pass")],
        failing_test_output="fail",
    )
    edit = client.propose(obs)
    assert edit.path == "src/lib.py"
    assert edit.new_text == "def old(): return 42"
    assert edit.rationale == "fix bug"


def test_coding_model_extracts_json_with_surrounding_commentary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Surrounding conversational commentary with code block should be extracted."""
    text_with_block = """Here is the suggested fix:
```json
{
  "path": "app.py",
  "old_text": "x = 1",
  "new_text": "x = 2",
  "rationale": "increment"
}
```
Let me know if this works!"""
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {
            "choices": [{"message": {"content": text_with_block}, "finish_reason": "stop"}]
        },
    )
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    obs = RepairObservation(task="fix", attempt=1, files=[], failing_test_output="fail")
    edit = client.propose(obs)
    assert edit.path == "app.py"
    assert edit.new_text == "x = 2"


def test_coding_model_defensive_bounds_on_code_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proposed edit must satisfy boundary and path-safety checks."""
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    obs = RepairObservation(task="fix", attempt=1, files=[], failing_test_output="fail")

    for bad_edit in [
        # Null byte in path
        {"path": "src/bad\x00.py", "old_text": "a", "new_text": "b", "rationale": "fix"},
        # Absolute path
        {"path": "/etc/passwd", "old_text": "a", "new_text": "b", "rationale": "fix"},
        # Directory traversal
        {"path": "../secret.py", "old_text": "a", "new_text": "b", "rationale": "fix"},
        {"path": "src/../../secret.py", "old_text": "a", "new_text": "b", "rationale": "fix"},
        # Empty path
        {"path": "", "old_text": "a", "new_text": "b", "rationale": "fix"},
        # Path exceeding max length (1024 chars)
        {"path": "a/" + "b" * 1025 + ".py", "old_text": "a", "new_text": "b", "rationale": "fix"},
        # Old/new text exceeding limit (1 MB)
        {"path": "valid.py", "old_text": "x" * (1_000_001), "new_text": "b", "rationale": "fix"},
        {"path": "valid.py", "old_text": "a", "new_text": "x" * (1_000_001), "rationale": "fix"},
        # Rationale exceeding limit (100 KB)
        {"path": "valid.py", "old_text": "a", "new_text": "b", "rationale": "r" * 100_001},
    ]:
        monkeypatch.setattr(
            "selfforge_agent.coding.model_client.request_json",
            lambda *_a, edit_dict=bad_edit, **_k: {
                "choices": [{"message": {"content": json.dumps(edit_dict)}, "finish_reason": "stop"}]
            },
        )
        with pytest.raises(ModelError):
            client.propose(obs)


def test_coding_model_malformed_response_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """All malformed payloads, HTTP errors, and schema violations map to ModelError."""
    client = OpenAICompatibleCodingModel(model="gpt-test", base_url="http://localhost:8080/v1")
    obs = RepairObservation(task="fix", attempt=1, files=[], failing_test_output="fail")

    # Missing choices
    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json", lambda *_a, **_k: {})
    with pytest.raises(ModelError):
        client.propose(obs)

    # Empty choices
    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json", lambda *_a, **_k: {"choices": []})
    with pytest.raises(ModelError):
        client.propose(obs)

    # Choices not a list
    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json", lambda *_a, **_k: {"choices": "not a list"})
    with pytest.raises(ModelError):
        client.propose(obs)

    # Missing message
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {"choices": [{"finish_reason": "stop"}]},
    )
    with pytest.raises(ModelError):
        client.propose(obs)

    # Missing content
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {"choices": [{"message": {}, "finish_reason": "stop"}]},
    )
    with pytest.raises(ModelError):
        client.propose(obs)

    # Content is None (e.g., tool call only)
    monkeypatch.setattr(
        "selfforge_agent.coding.model_client.request_json",
        lambda *_a, **_k: {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
    )
    with pytest.raises(ModelError):
        client.propose(obs)

    # Provider HTTP Error maps to ModelError
    def raise_http(*_a: object, **_k: object) -> dict[str, object]:
        raise ProviderHTTPError("502 Bad Gateway", status_code=502)

    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json", raise_http)
    with pytest.raises(ModelError, match="502 Bad Gateway"):
        client.propose(obs)


def test_fallback_coding_model_fail_closed_validation() -> None:
    """FallbackCodingModel validates provider list and propagates observation type defense."""
    # Empty providers list
    with pytest.raises(ValueError, match="At least one"):
        FallbackCodingModel([])

    # Non-list providers
    with pytest.raises(TypeError):
        FallbackCodingModel("not a list")  # type: ignore[arg-type]

    # Invalid elements in providers
    with pytest.raises(TypeError):
        FallbackCodingModel([{"name": "bad"}])  # type: ignore[list-item]

    # Propose with invalid observation
    provider = ProviderConfig(
        name="test", label="Test", key="k", model="m",
        base_url="https://api.example.com/v1", status_url="",
    )
    router = FallbackCodingModel([provider])
    with pytest.raises((TypeError, ModelError)):
        router.propose("invalid observation")  # type: ignore[arg-type]
