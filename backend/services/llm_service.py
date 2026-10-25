"""Disabled-by-default provider boundary and optional compatible HTTP adapter.

No test/model simulator is selected by production configuration. A real provider
can be attached later without changing retrieval, prompts or grounding logic.
"""

import json
import os
from typing import Protocol
from urllib.parse import urlparse

import httpx


class LLMNotConfiguredError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


class LLMProvider(Protocol):
    model_name: str

    def generate(self, messages, *, max_output_tokens, response_schema) -> dict: ...


class UnconfiguredLLM:
    configured = False
    provider_name = "disabled"
    model_name = None

    def generate(self, messages, *, max_output_tokens, response_schema):
        raise LLMNotConfiguredError("LLM provider is not configured. Context preview and retrieval are available; select a provider before generating answers.")


class ChatCompletionsLLM:
    """Explicit endpoint, model and credentials; no default external endpoint.

    Some providers/models differ in token-limit and structured-output support.
    Those choices are configuration, not silently retried against another API.
    """

    configured = True
    provider_name = "chat_completions"

    def __init__(self, *, endpoint, model, api_key=None, timeout=45, response_format="json_schema",
                 token_limit_field="max_completion_tokens", transport=None):
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("LLM endpoint must be an HTTP(S) URL without credentials, query or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote LLM endpoints require HTTPS")
        if not model or len(model) > 100 or any(ord(char) < 32 for char in model):
            raise ValueError("Set a valid LLM model name of at most 100 characters")
        if response_format not in {"json_schema", "json_object", "none"}:
            raise ValueError("Unsupported LLM response format")
        if token_limit_field not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token limit field")
        self.endpoint, self.model_name, self._api_key = endpoint, model, api_key
        self.timeout, self.response_format, self.token_limit_field, self.transport = timeout, response_format, token_limit_field, transport

    def generate(self, messages, *, max_output_tokens, response_schema):
        payload = {"model": self.model_name, "messages": messages, self.token_limit_field: max_output_tokens,
                   "stream": False}
        if self.response_format == "json_schema":
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "grounded_evidence", "strict": True, "schema": response_schema}}
        elif self.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False, transport=self.transport, trust_env=False) as client:
                with client.stream("POST", self.endpoint, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        raise LLMProviderError(f"The LLM provider returned HTTP {response.status_code}. Check its local configuration.")
                    pieces, size = [], 0
                    for piece in response.iter_bytes():
                        size += len(piece)
                        if size > 256 * 1024:
                            raise LLMResponseError("The LLM response exceeded the permitted size")
                        pieces.append(piece)
            envelope = json.loads(b"".join(pieces))
            choice = envelope["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise LLMResponseError("The LLM response was incomplete or refused; no answer was published")
            content = choice["message"]["content"]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except (LLMProviderError, LLMResponseError):
            raise
        except httpx.HTTPError as exc:
            raise LLMProviderError("The LLM provider could not be reached within its configured timeout") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("The LLM provider returned an invalid JSON response; no answer was published") from exc


def configured_llm():
    provider = os.getenv("LLM_PROVIDER", "disabled").strip().lower()
    if provider in {"", "disabled", "none"}:
        return UnconfiguredLLM()
    if provider != "chat_completions":
        raise ValueError("Unsupported LLM_PROVIDER; use disabled or add a provider adapter")
    endpoint, model = os.getenv("LLM_ENDPOINT", "").strip(), os.getenv("LLM_MODEL", "").strip()
    if not endpoint or not model:
        return UnconfiguredLLM()
    return ChatCompletionsLLM(endpoint=endpoint, model=model, api_key=os.getenv("LLM_API_KEY"),
        response_format=os.getenv("LLM_RESPONSE_FORMAT", "json_schema"),
        token_limit_field=os.getenv("LLM_TOKEN_LIMIT_FIELD", "max_completion_tokens"))
