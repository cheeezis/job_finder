"""Tests for the small local Ollama HTTP client."""

import json
import unittest

from job_agent.ollama import OllamaClient, OllamaError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class OllamaClientTests(unittest.TestCase):
    def test_chat_sends_schema_and_deterministic_options(self):
        requests = []
        schema = {"type": "object"}

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                {
                    "message": {"content": '{"result": "ok"}'},
                    "eval_count": 12,
                }
            )

        client = OllamaClient(timeout=45, opener=opener)
        analysis, metadata = client.chat(
            "test-model",
            [{"role": "user", "content": "test"}],
            schema,
        )
        payload = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertEqual(analysis, {"result": "ok"})
        self.assertEqual(metadata["eval_count"], 12)
        self.assertEqual(payload["format"], schema)
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["seed"], 42)
        self.assertFalse(payload["stream"])
        self.assertEqual(requests[0][1], 45)

    def test_chat_rejects_non_json_model_content(self):
        def opener(request, timeout):
            return FakeResponse({"message": {"content": "not json"}})

        client = OllamaClient(opener=opener)

        with self.assertRaises(OllamaError):
            client.chat("test-model", [], {"type": "object"})

    def test_list_models_returns_installed_names(self):
        def opener(request, timeout):
            return FakeResponse(
                {"models": [{"name": "llama3.1:8b"}, {"name": "gemma3:12b"}]}
            )

        client = OllamaClient(opener=opener)

        self.assertEqual(
            client.list_models(),
            ["llama3.1:8b", "gemma3:12b"],
        )


if __name__ == "__main__":
    unittest.main()
