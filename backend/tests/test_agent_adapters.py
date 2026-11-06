import unittest
from typing import ClassVar
from unittest.mock import patch

from app import agents


class FakeResponse:
    status_code = 200
    is_error = False

    def json(self):
        return {
            "model": "mock/served-model",
            "choices": [{"message": {"content": "OK"}}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
                "cost": 0.001,
            },
        }


class FakeClient:
    calls: ClassVar[list] = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        return FakeResponse()


class AgentAdapterTests(unittest.TestCase):
    def test_each_provider_uses_its_gateway_model_and_records_usage_and_latency(self):
        settings = agents.settings
        original = {
            name: getattr(settings, name)
            for name in (
                "openai_api_key",
                "openai_model_id",
                "openrouter_api_key",
                "openrouter_qwen_model_id",
                "openrouter_kimi_model_id",
                "openrouter_deepseek_model_id",
                "openrouter_base_url",
            )
        }
        settings.openai_api_key = "mock-openai-key"
        settings.openai_model_id = "mock/openai"
        settings.openrouter_api_key = "mock-openrouter-key"
        settings.openrouter_qwen_model_id = "mock/qwen"
        settings.openrouter_kimi_model_id = "mock/kimi"
        settings.openrouter_deepseek_model_id = "mock/deepseek"
        settings.openrouter_base_url = "https://openrouter.example/v1"
        FakeClient.calls = []
        expected = {
            "openai": ("https://api.openai.com/v1/chat/completions", "mock/openai"),
            "qwen": ("https://openrouter.example/v1/chat/completions", "mock/qwen"),
            "kimi": ("https://openrouter.example/v1/chat/completions", "mock/kimi"),
            "deepseek": (
                "https://openrouter.example/v1/chat/completions",
                "mock/deepseek",
            ),
        }
        try:
            with patch.object(agents.httpx, "Client", FakeClient):
                for provider, (expected_url, expected_model) in expected.items():
                    text, actual_model, metadata = agents.complete(
                        provider, "test prompt", "test system"
                    )
                    url, headers, payload = FakeClient.calls[-1]
                    self.assertEqual(
                        (url, payload["model"]), (expected_url, expected_model)
                    )
                    self.assertTrue(headers["Authorization"].startswith("Bearer mock-"))
                    self.assertEqual((text, actual_model), ("OK", "mock/served-model"))
                    self.assertEqual(metadata["usage"]["cost"], 0.001)
                    self.assertGreaterEqual(metadata["request_duration_ms"], 0)
        finally:
            for name, value in original.items():
                setattr(settings, name, value)


if __name__ == "__main__":
    unittest.main()
