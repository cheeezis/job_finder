"""OpenAI Responses API client for structured job-agent output."""

import json

from openai import OpenAI
from openai import OpenAIError as SDKOpenAIError

from job_agent.llm.errors import LLMError


DEFAULT_MODEL = "gpt-5.4-mini"
UNSUPPORTED_SCHEMA_KEYS = {"uniqueItems"}


class OpenAIProviderError(LLMError):
    """Raised when OpenAI cannot return a usable structured response."""


def prepare_output_schema(schema):
    """Copy a schema while removing constraints unsupported by OpenAI."""
    if isinstance(schema, dict):
        return {
            key: prepare_output_schema(value)
            for key, value in schema.items()
            if key not in UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [prepare_output_schema(value) for value in schema]
    return schema


class OpenAIClient:
    """Call OpenAI with the same small interface used by the local client."""

    provider = "openai"

    def __init__(
        self,
        timeout=300,
        sdk_client=None,
        reasoning_effort="low",
        max_output_tokens=8192,
    ):
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        try:
            self.client = sdk_client or OpenAI(timeout=timeout)
        except SDKOpenAIError as error:
            raise OpenAIProviderError(
                "OpenAI-Client konnte nicht initialisiert werden. "
                "Ist OPENAI_API_KEY gesetzt?"
            ) from error

    def chat(self, model, messages, output_schema):
        """Return parsed structured output and token-usage metadata."""
        try:
            response = self.client.responses.create(
                model=model,
                input=messages,
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=self.max_output_tokens,
                store=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "job_agent_response",
                        "strict": True,
                        "schema": prepare_output_schema(output_schema),
                    }
                },
            )
        except SDKOpenAIError as error:
            raise OpenAIProviderError(
                f"OpenAI-Anfrage fehlgeschlagen: {error}"
            ) from error

        content = response.output_text
        if not isinstance(content, str) or not content.strip():
            raise OpenAIProviderError("OpenAI-Antwort enthaelt keinen Inhalt")

        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            raise OpenAIProviderError(
                "OpenAI-Antwort ist kein gueltiges JSON"
            ) from error

        usage = response.usage
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        metadata = {
            "provider": self.provider,
            "response_id": response.id,
            "response_model": response.model,
            "prompt_eval_count": getattr(usage, "input_tokens", None),
            "eval_count": getattr(usage, "output_tokens", None),
            "cached_input_tokens": getattr(input_details, "cached_tokens", None),
            "reasoning_tokens": getattr(output_details, "reasoning_tokens", None),
        }
        return result, metadata
