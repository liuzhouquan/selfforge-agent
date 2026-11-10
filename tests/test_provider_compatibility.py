"""Provider presets must validate health and retain consumer protocol compatibility."""
import json

import pytest

from selfforge_agent import providers
from selfforge_agent.coding.model_client import OpenAICompatibleCodingModel
from selfforge_agent.coding.models import RepairObservation
from selfforge_agent.models import ChangedFile, DiffHunk, DiffLine, PRContext
from selfforge_agent.review.deepseek import ProviderReviewer


def claude_provider():
    return providers.ProviderConfig(
        name="claude", label="Claude", key="fixture-only", model="claude-sonnet-4-6",
        base_url="https://api.anthropic.com/v1", status_url="https://api.anthropic.com/v1/models",
    )


@pytest.mark.parametrize("response", [{}, {"error": {"message": "invalid key"}},
                                     {"data": "invalid"}, {"data": [{}]}, {"data": []}])
def test_generic_health_rejects_invalid_models_response(monkeypatch, response):
    monkeypatch.setattr(providers, "request_json", lambda *a, **kw: response)
    assert providers.check_provider(claude_provider()).status == "unavailable"


def test_claude_review_and_repair_omit_unsupported_json_object(monkeypatch):
    payloads = []
    def review_request(*args, payload, **kwargs):
        payloads.append(payload)
        assert "response_format" not in payload
        return {"choices": [{"message": {"content": '{"findings":[]}'}}]}
    monkeypatch.setattr("selfforge_agent.review.deepseek.request_json", review_request)
    context = PRContext(repository="fixture", base_sha="a"*40, head_sha="b"*40, files=[
        ChangedFile(path="a.py", status="M", hunks=[DiffHunk(
            header="@@ -1 +1 @@", old_start=1, new_start=1,
            lines=[DiffLine(kind="added", content="x = 1", new_line=1)],
        )]),
    ])
    assert ProviderReviewer(claude_provider()).review(context) == []
    def repair_request(*args, payload, **kwargs):
        payloads.append(payload)
        assert "response_format" not in payload
        edit = {"path":"a.py", "old_text":"old", "new_text":"new", "rationale":"fix"}
        return {"choices": [{"message": {"content": json.dumps(edit)}}]}
    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json", repair_request)
    model = OpenAICompatibleCodingModel(model="claude-sonnet-4-6",
                                       base_url="https://api.anthropic.com/v1", api_key="fixture")
    observation = RepairObservation(task="fix", attempt=1, files=[], failing_test_output="failure")
    assert model.propose(observation).new_text == "new"
    assert len(payloads) == 2


def test_opencode_models_use_documented_catalog_without_rewriting_custom_endpoint(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "fixture-opencode")
    item = next(p for p in providers.configured_providers() if p.name == "opencode")
    seen = []
    def catalog(url, key, **kwargs):
        seen.append(url)
        return {"data": [{"id": "glm-5.3"}]}
    monkeypatch.setattr(providers, "request_json", catalog)
    assert providers.list_models(item)[0].id == "glm-5.3"
    assert providers.check_provider(item).status == "ready"
    assert seen == ["https://opencode.ai/inference/v1/models"] * 2
    assert providers.model_list_url("opencode", "https://custom.example/v1") == "https://custom.example/v1/models"


def test_claude_repair_still_rejects_invalid_json(monkeypatch):
    from selfforge_agent.coding.model_client import ModelError
    monkeypatch.setattr("selfforge_agent.coding.model_client.request_json",
                        lambda *args, **kwargs: {"choices": [{"message": {"content": "not json"}}]})
    model = OpenAICompatibleCodingModel(model="claude-sonnet-4-6",
                                       base_url="https://api.anthropic.com/v1", api_key="fixture")
    with pytest.raises(ModelError):
        model.propose(RepairObservation(task="fix", attempt=1, files=[], failing_test_output="bad"))
