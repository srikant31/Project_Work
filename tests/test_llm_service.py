"""Provider contract tests use an HTTP transport double, never an external API."""

import json

import httpx
import pytest

from backend.prompts.rag_prompts import RESPONSE_SCHEMA
from backend.services.llm_service import (
    ChatCompletionsLLM, LLMNotConfiguredError, LLMProviderError, LLMResponseError, configured_llm,
)


MESSAGES = [{"role": "system", "content": "Return JSON evidence."}, {"role": "user", "content": "test evidence"}]


@pytest.fixture(autouse=True)
def isolate_local_timeout_setting(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)


@pytest.mark.parametrize("selection", ["groq", "chat_completions", "ollama"])
@pytest.mark.parametrize("value,expected", [(None, 45), ("180", 180), ("5", 5), ("600", 600), ("12.5", 12.5)])
def test_configured_timeout_default_and_custom(monkeypatch, selection, value, expected):
    monkeypatch.setenv("LLM_PROVIDER", selection)
    monkeypatch.setenv("LLM_ENDPOINT", "https://model.example.test/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    monkeypatch.setenv("LLM_API_KEY", "fixture-key")
    if value is not None:
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    assert configured_llm().timeout == expected


@pytest.mark.parametrize("value", ["", "abc", "4.99", "600.01", "-1", "nan", "inf", "-inf"])
def test_configured_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="LLM_TIMEOUT_SECONDS"):
        configured_llm()


def test_private_key_file_configuration_does_not_require_key_in_source(monkeypatch, tmp_path):
    key = tmp_path / 'private.key'; key.write_text('fixture-private-key\n')
    monkeypatch.setenv('LLM_PROVIDER', 'chat_completions')
    monkeypatch.setenv('LLM_ENDPOINT', 'https://model.example.test/chat/completions')
    monkeypatch.setenv('LLM_MODEL', 'fixture-model')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.setenv('LLM_API_KEY_FILE', str(key))
    assert configured_llm()._api_key == 'fixture-private-key'
    key.write_text('bad key with whitespace')
    with pytest.raises(ValueError, match='private key file'):
        configured_llm()


def test_rate_limit_message_is_actionable_without_exposing_provider_payload():
    from backend.services.llm_service import LLMRateLimitError
    with pytest.raises(LLMRateLimitError, match='free-tier limit') as caught:
        generate(provider(lambda request: httpx.Response(429,text='private diagnostic payload')))
    assert 'private diagnostic payload' in str(caught.value)


def provider(handler, **kwargs):
    return ChatCompletionsLLM(endpoint="https://model.example.test/chat/completions", model="configured-model",
                              transport=httpx.MockTransport(handler), **kwargs)


def generate(client):
    return client.generate(MESSAGES, max_output_tokens=512, response_schema=RESPONSE_SCHEMA)


@pytest.mark.parametrize("selection", [None, "groq", "chat_completions"])
def test_groq_selection_preserves_existing_request_contract(monkeypatch, selection):
    if selection is None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("LLM_PROVIDER", selection)
    monkeypatch.setenv("LLM_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "json_schema")
    monkeypatch.setenv("LLM_TOKEN_LIMIT_FIELD", "max_completion_tokens")
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    llm = configured_llm()
    assert llm.provider_name == ("chat_completions" if selection == "chat_completions" else "groq")
    assert llm.endpoint == "https://api.groq.com/openai/v1/chat/completions"
    assert llm.model_name == "openai/gpt-oss-20b"
    assert llm.timeout == 45

    def handle(request):
        body = json.loads(request.content)
        assert body == {"model": "openai/gpt-oss-20b", "messages": MESSAGES,
                        "max_completion_tokens": 512, "stream": False,
                        "response_format": {"type": "json_schema", "json_schema": {
                            "name": "grounded_evidence", "strict": True, "schema": RESPONSE_SCHEMA}}}
        assert request.headers["authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"claims":[],"insufficient_evidence":true}'}}]})
    llm.transport = httpx.MockTransport(handle)
    assert generate(llm)["insufficient_evidence"]


def test_explicit_disabled_provider_stays_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    monkeypatch.setenv("LLM_API_KEY", "test-only-unused-secret")
    llm = configured_llm()
    assert llm.configured is False
    with pytest.raises(LLMNotConfiguredError):
        generate(llm)


def test_ollama_uses_local_settings_without_reading_groq_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "remote-model")
    monkeypatch.setenv("LLM_API_KEY", "remote-secret")
    monkeypatch.setenv("LLM_API_KEY_FILE", "nonexistent-private-file")
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "none")
    monkeypatch.setenv("LLM_TOKEN_LIMIT_FIELD", "max_completion_tokens")
    llm = configured_llm()
    assert llm.provider_name == "ollama"

    def handle(request):
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "qwen2.5:3b"
        assert body["messages"] == MESSAGES
        assert body["temperature"] == 0.1
        assert body["max_tokens"] == 512 and "max_completion_tokens" not in body
        assert body["stream"] is False
        assert body["response_format"]["json_schema"]["schema"] == RESPONSE_SCHEMA
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"claims":[],"insufficient_evidence":true}'}}]})
    llm.transport = httpx.MockTransport(handle)
    assert generate(llm)["insufficient_evidence"]
    assert configured_llm()._api_key is None


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


def test_provider_error_body_is_included():
    with pytest.raises(LLMProviderError) as error:
        generate(provider(lambda request: httpx.Response(401, text="private-key-and-provider-diagnostics")))
    assert "401" in str(error.value) and "private-key-and-provider-diagnostics" in str(error.value)


@pytest.mark.parametrize("status", [400, 404, 429])
def test_provider_error_details_redact_keys_before_truncating(status):
    body = "Unknown model. gsk_exampleCredential Bearer otherCredential sk-exampleSecret configured-secret " + "x" * 250
    with pytest.raises(LLMProviderError) as caught:
        generate(provider(lambda request: httpx.Response(status, text=body), api_key="configured-secret"))
    message = str(caught.value)
    assert "Unknown model." in message and "[REDACTED]" in message
    assert all(secret not in message for secret in ["gsk_exampleCredential", "otherCredential", "sk-exampleSecret", "configured-secret"])
    assert len(message.split("Provider detail: ", 1)[1]) == 200


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
