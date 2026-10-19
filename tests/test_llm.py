import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from agentlab import llm, vault

TOOLS = [{"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
HISTORY = [
    {"role": "user", "content": "read a and b"},
    {"role": "assistant", "content": "ok", "tool_calls": [{"id": "t1", "name": "read_file", "arguments": {"path": "a"}}, {"id": "t2", "name": "read_file", "arguments": {"path": "b"}}]},
    {"role": "tool", "tool_call_id": "t1", "name": "read_file", "content": "AAA"},
    {"role": "tool", "tool_call_id": "t2", "name": "read_file", "content": "BBB"},
]


class Fake:
    """Local server that records requests and replays queued (status, body) responses."""

    def __init__(self):
        self.requests, self.queue = [], []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, payload = fake.queue.pop(0)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class LLMCase(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        vault.set_secret(self.tmp.name, "anthropic", "sk-ant-test-key-123456")
        vault.set_secret(self.tmp.name, "openai", "sk-openai-test-key-123456")

    def provider(self, kind, **kw):
        return llm.validate_provider({"type": kind, "base_url": self.fake.url + ("" if kind == "anthropic" else "/v1"), "model": "m1",
                                      "key": "anthropic" if kind == "anthropic" else "openai", **kw})

    def chat(self, p, **kw):
        return llm.chat(p, "be brief", HISTORY, TOOLS, project=self.tmp.name, retries=kw.pop("retries", 2), backoff=0, **kw)


class AnthropicTests(LLMCase):
    def test_request_shape_and_reply_parsing(self):
        self.fake.queue.append((200, {"model": "claude-x", "stop_reason": "tool_use", "usage": {"input_tokens": 11, "output_tokens": 7},
                                      "content": [{"type": "text", "text": "reading"}, {"type": "tool_use", "id": "u1", "name": "read_file", "input": {"path": "c"}}]}))
        r = self.chat(self.provider("anthropic"), max_tokens=321, temperature=0.2)
        req = self.fake.requests[0]
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["headers"]["x-api-key"], "sk-ant-test-key-123456")
        self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        b = req["body"]
        self.assertEqual((b["model"], b["max_tokens"], b["system"], b["temperature"]), ("m1", 321, "be brief", 0.2))
        self.assertEqual(b["tools"][0]["input_schema"]["required"], ["path"])
        self.assertEqual(b["messages"][1]["content"][1], {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}})
        results = b["messages"][2]  # both tool results in ONE user message, as the API requires
        self.assertEqual((results["role"], [c["tool_use_id"] for c in results["content"]]), ("user", ["t1", "t2"]))
        self.assertEqual(len(b["messages"]), 3)
        self.assertEqual((r["text"], r["stop"], r["usage"]), ("reading", "tool", {"input": 11, "output": 7}))
        self.assertEqual(r["tool_calls"], [{"id": "u1", "name": "read_file", "arguments": {"path": "c"}}])

    def test_max_tokens_stop_reason(self):
        self.fake.queue.append((200, {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "cut"}], "usage": {}}))
        self.assertEqual(self.chat(self.provider("anthropic"))["stop"], "length")


class OpenAITests(LLMCase):
    def test_request_shape_and_reply_parsing(self):
        self.fake.queue.append((200, {"model": "m1", "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                                      "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                                          {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"z\"}"}}]}}]}))
        r = self.chat(self.provider("openai", max_tokens_param="max_completion_tokens"), max_tokens=99)
        req = self.fake.requests[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["headers"]["authorization"], "Bearer sk-openai-test-key-123456")
        b = req["body"]
        self.assertEqual(b["max_completion_tokens"], 99)
        self.assertNotIn("max_tokens", b)
        self.assertEqual(b["messages"][0], {"role": "system", "content": "be brief"})
        self.assertEqual(b["messages"][2]["tool_calls"][0]["function"], {"name": "read_file", "arguments": "{\"path\": \"a\"}"})  # arguments are a JSON string
        self.assertEqual([m["role"] for m in b["messages"]], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(b["tools"][0]["function"]["parameters"]["type"], "object")
        self.assertEqual((r["stop"], r["usage"], r["tool_calls"][0]["arguments"]), ("tool", {"input": 5, "output": 3}, {"path": "z"}))

    def test_local_server_needs_no_key_and_bad_tool_json_is_survivable(self):
        self.fake.queue.append((200, {"choices": [{"finish_reason": "stop", "message": {"content": "hi", "tool_calls": [
            {"id": "x", "function": {"name": "read_file", "arguments": "{broken"}}]}}]}))
        p = llm.validate_provider({"type": "openai", "base_url": self.fake.url + "/v1", "model": "local"})
        r = llm.chat(p, "", [{"role": "user", "content": "hi"}], TOOLS, project=self.tmp.name)
        self.assertNotIn("authorization", self.fake.requests[0]["headers"])
        self.assertEqual(r["tool_calls"][0]["arguments"], {"_invalid_json": "{broken"})

    def test_cloudflare_account_id_is_substituted(self):
        vault.set_secret(self.tmp.name, "cloudflare_account", "acct123")
        vault.set_secret(self.tmp.name, "cloudflare_token", "cf-token-123456789")
        self.fake.queue.append((200, {"choices": [{"message": {"content": "ok"}}]}))
        p = llm.validate_provider({**llm.PRESETS["cloudflare"], "base_url": self.fake.url + "/client/v4/accounts/{account_id}/ai/v1"})
        llm.chat(p, "", [{"role": "user", "content": "hi"}], project=self.tmp.name)
        self.assertEqual(self.fake.requests[0]["path"], "/client/v4/accounts/acct123/ai/v1/chat/completions")
        self.assertEqual(self.fake.requests[0]["headers"]["authorization"], "Bearer cf-token-123456789")


class ErrorTests(LLMCase):
    def test_retries_then_succeeds(self):
        self.fake.queue += [(429, {"error": "slow down"}), (503, {"error": "busy"}), (200, {"choices": [{"message": {"content": "finally"}}]})]
        self.assertEqual(self.chat(self.provider("openai"))["text"], "finally")
        self.assertEqual(len(self.fake.requests), 3)

    def test_gives_up_and_does_not_retry_client_errors(self):
        self.fake.queue += [(401, {"error": "bad key sk-openai-test-key-123456"})]
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(self.provider("openai"))
        self.assertEqual((cm.exception.status, cm.exception.retryable), (401, False))
        self.assertNotIn("sk-openai-test-key-123456", str(cm.exception))  # keys echoed by a server are scrubbed
        self.assertEqual(len(self.fake.requests), 1)
        self.fake.queue += [(500, {})] * 3
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(self.provider("openai"), retries=2)
        self.assertTrue(cm.exception.retryable)

    def test_unreachable_and_missing_key_and_garbage(self):
        dead = llm.validate_provider({"type": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "m"})
        with self.assertRaises(llm.LLMError):
            llm.chat(dead, "", [{"role": "user", "content": "x"}], project=self.tmp.name, retries=0, timeout=2)
        nokey = llm.validate_provider({"type": "openai", "base_url": self.fake.url + "/v1", "model": "m", "key": "missing_key"})
        with self.assertRaises(llm.LLMError) as cm:
            llm.chat(nokey, "", [{"role": "user", "content": "x"}], project=self.tmp.name)
        self.assertIn("missing_key", str(cm.exception))
        self.fake.queue.append((200, {"unexpected": True}))
        with self.assertRaises(llm.LLMError):
            self.chat(self.provider("openai"))


class ProviderTests(unittest.TestCase):
    def test_validation(self):
        for bad in (None, {"type": "gemini"}, {"type": "openai", "base_url": "ftp://x", "model": "m"}, {"type": "openai", "base_url": "http://x", "model": ""},
                    {"type": "openai", "base_url": "http://x", "model": "m", "key": "Bad Name!"}, {"type": "openai", "base_url": "http://x", "model": "m", "max_tokens_param": "n"}):
            with self.assertRaises(ValueError, msg=bad):
                llm.validate_provider(bad)
        p = llm.validate_provider({"type": "openai", "base_url": "http://x/v1/", "model": "m", "price_in": 3, "price_out": 15})
        self.assertEqual(p["base_url"], "http://x/v1")
        self.assertEqual(llm.cost(p, {"input": 1_000_000, "output": 100_000}), 4.5)

    def test_every_preset_is_valid(self):
        for name, preset in llm.PRESETS.items():
            filled = {**preset, "model": preset.get("model") or "some-model", "base_url": preset.get("base_url") or "http://x/v1", "url": preset.get("url") or "http://x/api",
                      "command": [c.replace("{openclaw_agent}", "main") for c in preset.get("command", [])] or None}
            llm.validate_provider({k: v for k, v in filled.items() if v is not None})

    def test_mock_script(self):
        p = llm.validate_provider({"type": "mock", "script": [{"text": "step1", "tool_calls": [{"name": "t", "arguments": {"a": 1}}]}, {"text": "done"}]})
        first = llm.chat(p, "", [{"role": "user", "content": "go"}])
        self.assertEqual((first["stop"], first["tool_calls"][0]["name"]), ("tool", "t"))
        second = llm.chat(p, "", [{"role": "user", "content": "go"}, {"role": "assistant", "content": "step1"}])
        self.assertEqual((second["text"], second["stop"]), ("done", "end"))


class VaultTests(unittest.TestCase):
    def test_store_hide_and_fallback(self):
        clean = {env: "" for env in vault.ENV_FALLBACK.values()}  # ignore whatever keys the host environment has
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, clean):
            vault.set_secret(d, "anthropic", "sk-ant-abcdef123456")
            self.assertEqual(oct(os.stat(os.path.join(d, ".agentlab", "secrets.json")).st_mode)[-3:], "600")
            listed = vault.list_secrets(d)
            self.assertEqual(listed, [{"name": "anthropic", "hint": "…3456", "source": "vault"}])
            self.assertNotIn("abcdef", json.dumps(listed))
            with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_envtoken1234"}):
                self.assertEqual(vault.get_secret(d, "hf_token"), "hf_envtoken1234")
                self.assertIn("hf_token", [s["name"] for s in vault.list_secrets(d)])
            self.assertIsNone(vault.get_secret(d, "github_token"))
            self.assertEqual(vault.redact_value("token sk-ant-abcdef123456 leaked", d), "token [REDACTED] leaked")
            self.assertTrue(vault.delete_secret(d, "anthropic"))
            self.assertFalse(vault.delete_secret(d, "anthropic"))
            for bad in ("Bad", "1abc", "a" * 41, ""):
                with self.assertRaises(ValueError):
                    vault.set_secret(d, bad, "v")
            with self.assertRaises(ValueError):
                vault.set_secret(d, "ok", "  ")


if __name__ == "__main__":
    unittest.main()
