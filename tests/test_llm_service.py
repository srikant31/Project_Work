"""Provider contract tests use an HTTP transport double, never an external API."""

import json

import httpx
import pytest

from backend.prompts.rag_prompts import RESPONSE_SCHEMA
from backend.services.llm_service import (
    ChatCompletionsLLM, LLMNotConfiguredError, LLMProviderError, LLMResponseError, configured_llm,
)


MESSAGES = [{"role": "system", "content": "Return JSON evidence."}, {"role": "user", "content": "test evidence"}]


def provider(handler, **kwargs):
    return ChatCompletionsLLM(endpoint="https://model.example.test/chat/completions", model="configured-model",
                              transport=httpx.MockTransport(handler), **kwargs)


def generate(client):
    return client.generate(MESSAGES, max_output_tokens=512, response_schema=RESPONSE_SCHEMA)


def test_default_provider_is_disabled_even_if_other_credentials_exist(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-only-unused-secret")
    llm = configured_llm()
    assert llm.configured is False
    with pytest.raises(LLMNotConfiguredError):
        generate(llm)


def test_compatible_adapter_sends_structured_contract_and_parses_json():
    expected = {"insufficient_evidence": True, "claims": []}

    def handle(request):
        body = json.loads(request.content)
        assert body["messages"] == MESSAGES
        assert body["max_completion_tokens"] == 512
        assert body["stream"] is False
        assert body["response_format"]["json_schema"]["schema"] == RESPONSE_SCHEMA
        assert request.headers["authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(expected)}}]})

    assert generate(provider(handle, api_key="test-only-key")) == expected


def test_provider_specific_json_mode_and_limit_field_are_explicit():
    def handle(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 512 and "max_completion_tokens" not in body
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"claims":[],"insufficient_evidence":true}'}}]})

    assert generate(provider(handle, response_format="json_object", token_limit_field="max_tokens"))["insufficient_evidence"]


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", None])
def test_truncated_filtered_or_unexpected_completion_never_becomes_an_answer(reason):
    client = provider(lambda request: httpx.Response(200, json={"choices": [{"finish_reason": reason, "message": {"content": "{}"}}]}))
    with pytest.raises(LLMResponseError, match="incomplete or refused"):
        generate(client)


@pytest.mark.parametrize("envelope", [{}, {"choices": []}, {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}])
def test_malformed_responses_are_explicit_errors(envelope):
    with pytest.raises(LLMResponseError):
        generate(provider(lambda request: httpx.Response(200, json=envelope)))


def test_provider_error_body_is_not_exposed():
    with pytest.raises(LLMProviderError) as error:
        generate(provider(lambda request: httpx.Response(401, text="private-key-and-provider-diagnostics")))
    assert "401" in str(error.value) and "private-key" not in str(error.value)


def test_oversized_provider_response_is_rejected():
    with pytest.raises(LLMResponseError, match="size"):
        generate(provider(lambda request: httpx.Response(200, content=b"x" * (256 * 1024 + 1))))


def test_no_redirect_follows_credentials_to_another_endpoint():
    calls = []

    def handle(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"Location": "https://unrelated.example.test"})

    with pytest.raises(LLMProviderError):
        generate(provider(handle, api_key="test-only-key"))
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint", ["http://remote.example.test/chat", "https://name:password@example.test/chat", "https://model.example.test/chat?key=secret"])
def test_remote_http_and_credentials_in_urls_are_rejected(endpoint):
    with pytest.raises(ValueError):
        ChatCompletionsLLM(endpoint=endpoint, model="test")
