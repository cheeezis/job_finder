"""Minimal client for Ollama's local structured chat API."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:12b"


class OllamaError(RuntimeError):
    """Raised when the local Ollama API cannot return a usable response."""


class OllamaClient:
    """Call a local Ollama server without an additional SDK dependency."""

    def __init__(self, base_url=DEFAULT_BASE_URL, timeout=300, opener=urlopen):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener

    def chat(self, model, messages, output_schema):
        """Return a parsed structured response and Ollama runtime metadata."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": output_schema,
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_ctx": 16384,
            },
        }
        response = self._request("/api/chat", payload)
        content = response.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama-Antwort enthaelt keinen Inhalt")

        try:
            analysis = json.loads(content)
        except json.JSONDecodeError as error:
            raise OllamaError("Ollama-Antwort ist kein gueltiges JSON") from error

        metadata = {
            key: response.get(key)
            for key in (
                "total_duration",
                "load_duration",
                "prompt_eval_count",
                "prompt_eval_duration",
                "eval_count",
                "eval_duration",
                "done_reason",
            )
        }
        return analysis, metadata

    def list_models(self):
        """Return model names currently installed in Ollama."""
        response = self._request("/api/tags")
        return [model["name"] for model in response.get("models", [])]

    def _request(self, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(f"{self.base_url}{path}", data=data, headers=headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            message = f"Ollama antwortet mit HTTP {error.code}: {detail}"
            raise OllamaError(message) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OllamaError(f"Ollama ist nicht erreichbar: {error}") from error
        except json.JSONDecodeError as error:
            message = "Ollama-API antwortet nicht mit gueltigem JSON"
            raise OllamaError(message) from error
