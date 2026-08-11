import os
import unittest
from unittest import mock

from jobhub import ai_tools


def _fake_response(status_code=200, payload=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    response.text = text or str(payload)
    response.json.return_value = payload or {}
    return response


class GeminiKeyTests(unittest.TestCase):
    def tearDown(self):
        for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "AI_PROVIDER", "OPENAI_API_KEY"):
            os.environ.pop(name, None)

    def test_default_gemini_model(self):
        self.assertEqual(ai_tools.jobhub_gemini_model(), "gemini-2.5-flash")

    def test_gemini_model_env_override(self):
        os.environ["GEMINI_MODEL"] = "gemini-2.5-pro"
        self.assertEqual(ai_tools.jobhub_gemini_model(), "gemini-2.5-pro")

    def test_gemini_enabled_with_key(self):
        os.environ["GEMINI_API_KEY"] = "secret-value"
        self.assertTrue(ai_tools.gemini_enabled())

    def test_gemini_enabled_without_key(self):
        self.assertFalse(ai_tools.gemini_enabled())

    def test_gemini_key_cleaned(self):
        os.environ["GEMINI_API_KEY"] = "Bearer sk-123\n"
        self.assertEqual(ai_tools.jobhub_gemini_api_key(), "sk-123")


class GeminiProviderRoutingTests(unittest.TestCase):
    def tearDown(self):
        for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "AI_PROVIDER", "OPENAI_API_KEY", "RENDER"):
            os.environ.pop(name, None)

    def test_provider_explicit_gemini(self):
        os.environ["AI_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "key"
        self.assertEqual(ai_tools.ai_provider(), "gemini")

    def test_provider_auto_prefers_gemini(self):
        os.environ["AI_PROVIDER"] = "auto"
        os.environ["GEMINI_API_KEY"] = "key"
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["RENDER"] = ""
        self.assertEqual(ai_tools.ai_provider(), "gemini")

    def test_backend_ready_gemini(self):
        os.environ["AI_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "key"
        ok, message = ai_tools.ai_backend_ready()
        self.assertTrue(ok)
        self.assertIn("gemini-2.5-flash", message)

    def test_backend_ready_gemini_missing_key(self):
        os.environ["AI_PROVIDER"] = "gemini"
        ok, message = ai_tools.ai_backend_ready()
        self.assertFalse(ok)
        self.assertIn("GEMINI_API_KEY", message)


class GeminiCallTests(unittest.TestCase):
    def setUp(self):
        os.environ["AI_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"

    def tearDown(self):
        for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "AI_PROVIDER", "OPENAI_API_KEY"):
            os.environ.pop(name, None)

    def test_missing_key_returns_error(self):
        os.environ.pop("GEMINI_API_KEY", None)
        answer, error = ai_tools.gemini_answer("hello")
        self.assertIsNone(answer)
        self.assertIn("GEMINI_API_KEY", error)

    def test_parses_candidate_text(self):
        payload = {
            "candidates": [
                {"content": {"parts": [{"text": "Hello from"}, {"text": " Gemini."}]}}
            ]
        }
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(200, payload)) as post:
            answer, error = ai_tools.gemini_answer("say hello")
        self.assertIsNone(error)
        self.assertEqual(answer, "Hello from\n Gemini.")
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertIn("params", kwargs)
        self.assertEqual(kwargs["params"]["key"], "test-key")
        self.assertNotIn("tools", kwargs["json"])

    def test_include_web_adds_google_search_tool(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "web answer"}]}}]}
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(200, payload)) as post:
            answer, error = ai_tools.gemini_answer("q", include_web=True)
        self.assertIsNone(error)
        self.assertEqual(answer, "web answer")
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [{"google_search": {}}])

    def test_404_returns_model_hint(self):
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(404, {}, "not found")):
            answer, error = ai_tools.gemini_answer("q")
        self.assertIsNone(answer)
        self.assertIn("404", error)
        self.assertIn("GEMINI_MODEL", error)

    def test_empty_response_returns_error(self):
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(200, {"candidates": []})):
            answer, error = ai_tools.gemini_answer("q")
        self.assertIsNone(answer)
        self.assertIn("empty response", error)

    def test_jobhub_ai_answer_routes_to_gemini(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "director summary"}]}}]}
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(200, payload)) as post:
            answer, error = ai_tools.jobhub_ai_answer("summary?", "context here")
        self.assertIsNone(error)
        self.assertEqual(answer, "director summary")
        self.assertIn("generativelanguage.googleapis.com", post.call_args.args[0])
        self.assertIn("gemini-2.5-flash", post.call_args.args[0])
        self.assertNotIn("api.openai.com", post.call_args.args[0])

    def test_app_builder_ai_call_routes_to_gemini(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "build plan"}]}}]}
        with mock.patch("jobhub.ai_tools.requests.post", return_value=_fake_response(200, payload)) as post:
            answer, error = ai_tools.app_builder_ai_call("plan a feature")
        self.assertIsNone(error)
        self.assertEqual(answer, "build plan")
        self.assertIn("generativelanguage.googleapis.com", post.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
