"""Tests for the structured OpenAI provider client."""

import unittest
from types import SimpleNamespace

from job_agent.llm.openai import OpenAIClient, OpenAIProviderError


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


class OpenAIClientTests(unittest.TestCase):
    def make_sdk_client(self, output_text='{"result": "ok"}'):
        usage = SimpleNamespace(
            input_tokens=120,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
            output_tokens_details=SimpleNamespace(reasoning_tokens=5),
        )
        response = SimpleNamespace(
            id="resp_test",
            model="gpt-5.4-mini",
            output_text=output_text,
            usage=usage,
        )
        return SimpleNamespace(responses=FakeResponses(response))

    def test_chat_sends_strict_schema_without_storing_response(self):
        sdk_client = self.make_sdk_client()
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            },
        }
        client = OpenAIClient(sdk_client=sdk_client)

        result, metadata = client.chat(
            "gpt-5.4-mini",
            [{"role": "user", "content": "test"}],
            schema,
        )

        request = sdk_client.responses.requests[0]
        self.assertEqual(result, {"result": "ok"})
        sent_schema = request["text"]["format"]["schema"]
        self.assertNotIn("uniqueItems", sent_schema["properties"]["values"])
        self.assertTrue(schema["properties"]["values"]["uniqueItems"])
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])
        self.assertEqual(request["reasoning"], {"effort": "low"})
        self.assertEqual(metadata["prompt_eval_count"], 120)
        self.assertEqual(metadata["eval_count"], 30)
        self.assertEqual(metadata["cached_input_tokens"], 20)
        self.assertEqual(metadata["reasoning_tokens"], 5)

    def test_chat_rejects_non_json_output(self):
        client = OpenAIClient(sdk_client=self.make_sdk_client("not json"))

        with self.assertRaises(OpenAIProviderError):
            client.chat("gpt-5.4-mini", [], {"type": "object"})


if __name__ == "__main__":
    unittest.main()
