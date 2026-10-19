import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentlab import agentise, agents, jobs, llm, projects, vault

try:
    from tests.test_llm import Fake, TOOLS
except ImportError:
    from test_llm import Fake, TOOLS


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.fake = Fake()
        self.addCleanup(self.fake.close)
        vault.set_secret(self.root, "mykey", "secret-key-123456789")

    def http(self, **kw):
        return llm.validate_provider({"type": "http", "url": self.fake.url + "/generate", "key": "mykey", "headers": {"Authorization": "Bearer {key}", "X-Model": "{model}"},
                                      "body": {"model": "{model}", "inputs": "{prompt}", "parameters": {"max_new_tokens": "{max_tokens}", "who": "{user}"}},
                                      "response_path": "outputs.0.text", "usage_in_path": "meta.in", "usage_out_path": "meta.out", "model": "my-model", **kw})

    def chat(self, p, messages=None, tools=None, **kw):
        return llm.chat(p, "be brief", messages or [{"role": "user", "content": "hello there"}], tools, project=self.root, backoff=0, **kw)


class HttpAdapterTests(AdapterCase):
    def test_request_template_headers_response_path_and_usage(self):
        self.fake.queue.append((200, {"outputs": [{"text": "general kenobi"}], "meta": {"in": 12, "out": 4}}))
        r = self.chat(self.http(), max_tokens=77)
        req = self.fake.requests[0]
        self.assertEqual(req["path"], "/generate")
        self.assertEqual(req["headers"]["authorization"], "Bearer secret-key-123456789")
        self.assertEqual(req["headers"]["x-model"], "my-model")
        b = req["body"]
        self.assertEqual((b["model"], b["parameters"]["max_new_tokens"], b["parameters"]["who"]), ("my-model", 77, "hello there"))  # an exact {placeholder} keeps its number type
        self.assertIn("[System]\nbe brief", b["inputs"])
        self.assertIn("[User]\nhello there", b["inputs"])
        self.assertEqual((r["text"], r["usage"], r["tool_calls"]), ("general kenobi", {"input": 12, "output": 4}, []))

    def test_usage_is_estimated_when_the_api_does_not_report_it(self):
        self.fake.queue.append((200, {"outputs": [{"text": "x" * 40}]}))
        r = self.chat(self.http(usage_in_path="", usage_out_path=""))
        self.assertEqual(r["usage"]["output"], 10)
        self.assertGreater(r["usage"]["input"], 0)

    def test_plain_text_responses_and_failures(self):
        p = self.http(response_path="")
        self.fake.queue.append((200, {"anything": 1}))
        self.assertIn("anything", self.chat(p)["text"])  # no path: the whole body is the answer
        self.fake.queue.append((200, {"outputs": []}))
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(self.http())
        self.assertIn("outputs.0.text", str(cm.exception))
        self.fake.queue += [(500, {"error": "boom secret-key-123456789"})] * 3
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(self.http(), retries=2)
        self.assertNotIn("secret-key-123456789", str(cm.exception))
        self.assertEqual(len(self.fake.requests), 5)  # 1 + 1 + 3 attempts

    def test_validation(self):
        for bad in ({"url": "ftp://x"}, {"body": "string"}, {"headers": {"a": 1}}, {"method": "DELETE"}, {"tool_mode": "magic"}):
            with self.assertRaises(ValueError, msg=bad):
                self.http(**bad)
        self.assertEqual(self.http()["tool_mode"], "prompt")  # text APIs get the text tool protocol by default
        self.assertEqual(llm.validate_provider({"type": "openai", "base_url": "http://x", "model": "m"})["tool_mode"], "native")


class PromptToolTests(AdapterCase):
    def test_tool_calls_are_parsed_from_plain_text_even_in_code_fences(self):
        p = self.http()
        for text, expected_prose in (('{"tool": "read_file", "arguments": {"path": "a.txt"}}', ""), ('Let me look.\n```json\n{"tool": "read_file", "arguments": {"path": "a.txt"}}\n```', "Let me look.")):
            self.fake.queue.append((200, {"outputs": [{"text": text}]}))
            r = self.chat(p, tools=TOOLS)
            self.assertEqual((r["stop"], r["text"], r["tool_calls"][0]["name"], r["tool_calls"][0]["arguments"]), ("tool", expected_prose, "read_file", {"path": "a.txt"}))
        self.fake.queue.append((200, {"outputs": [{"text": "All done, nothing to run."}]}))
        r = self.chat(p, tools=TOOLS)
        self.assertEqual((r["stop"], r["tool_calls"]), ("end", []))
        sent = self.fake.requests[0]["body"]["inputs"]
        self.assertIn("read_file: Read a file", sent)  # the tools are described in the prompt
        self.assertIn('"tool": "<name>"', sent)

    def test_history_with_tool_results_is_rewritten_as_text(self):
        self.fake.queue.append((200, {"outputs": [{"text": "ok"}]}))
        history = [{"role": "user", "content": "read a"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "read_file", "arguments": {"path": "a"}}]},
                   {"role": "tool", "tool_call_id": "1", "name": "read_file", "content": "FILE BODY"}]
        self.chat(self.http(), history, TOOLS)
        sent = self.fake.requests[0]["body"]["inputs"]
        self.assertIn('{"tool": "read_file", "arguments": {"path": "a"}}', sent)
        self.assertIn("Tool result (read_file):\nFILE BODY", sent)

    def test_openai_compatible_server_without_function_calling_can_use_prompt_mode(self):
        self.fake.queue.append((200, {"choices": [{"message": {"content": '{"tool": "read_file", "arguments": {"path": "z"}}'}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))
        p = llm.validate_provider({"type": "openai", "base_url": self.fake.url + "/v1", "model": "tiny", "tool_mode": "prompt"})
        r = self.chat(p, tools=TOOLS)
        self.assertNotIn("tools", self.fake.requests[0]["body"])  # nothing native was sent
        self.assertEqual(r["tool_calls"][0]["arguments"], {"path": "z"})

    def test_any_text_model_becomes_a_working_agent(self):
        """End to end: a plain REST text model, no function calling, builds a file and finishes."""
        replies = ['{"tool": "write_file", "arguments": {"path": "hello.txt", "content": "hi from a text model"}}',
                   'Writing is done.\n{"tool": "finish", "arguments": {"summary": "created hello.txt"}}']
        for t in replies:
            self.fake.queue.append((200, {"outputs": [{"text": t}], "meta": {"in": 50, "out": 10}}))
        proj = projects.create(self.root, "Text model")["id"]
        agent = agents.create(self.root, {"name": "Plain", "mode": "auto", "provider": self.http()})
        rt = jobs.Runtime(self.root)
        j = rt.start(proj, agent["id"], "make hello.txt")
        end = time.time() + 20
        while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
            time.sleep(0.05)
        j = rt.get(j["id"])
        self.assertEqual((j["status"], j["summary"]), ("succeeded", "created hello.txt"))
        self.assertEqual(projects.read_file(self.root, proj, "hello.txt")["content"], "hi from a text model")
        self.assertEqual((j["metrics"]["llm_calls"], j["metrics"]["input_tokens"]), (2, 100))
        # the model's second request included the first tool result as text
        self.assertIn("Tool result (write_file)", self.fake.requests[1]["body"]["inputs"])


class CliAdapterTests(AdapterCase):
    def script(self, body, name="fake-agent"):
        path = Path(self.root) / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_runs_a_command_with_the_conversation_in_a_file(self):
        exe = self.script('echo "agent $1 read $(wc -c < "$3") bytes: $(grep -c "hello there" "$3") match"')
        p = llm.validate_provider({"type": "cli", "command": [exe, "main", "--file", "{message_file}"]})
        r = self.chat(p)
        self.assertRegex(r["text"], r"agent main read \d+ bytes: 1 match")
        self.assertEqual(r["tool_calls"], [])
        self.assertEqual([f for f in os.listdir(tempfile.gettempdir()) if f.startswith("agentlab-msg-")], [])  # the temp file is removed

    def test_failures_are_clear(self):
        bad = llm.validate_provider({"type": "cli", "command": [self.script("echo oops >&2; exit 3"), "{message}"]})
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(bad)
        self.assertIn("exited with 3: oops", str(cm.exception))
        missing = llm.validate_provider({"type": "cli", "command": ["no-such-agent-binary", "{message}"]})
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(missing)
        self.assertIn("not found", str(cm.exception))
        slow = llm.validate_provider({"type": "cli", "command": [self.script("sleep 10"), "{message}"], "timeout": 1})
        with self.assertRaises(llm.LLMError) as cm:
            self.chat(slow)
        self.assertTrue(cm.exception.retryable)
        for badspec in ({"command": []}, {"command": ["x"]}, {"command": ["x", "{message}"] * 11}, {"command": "x {message}"}):
            with self.assertRaises(ValueError, msg=badspec):
                llm.validate_provider({"type": "cli", **badspec})

    def test_command_is_never_run_through_a_shell(self):
        marker = Path(self.root) / "pwned"
        exe = self.script("echo ran")
        p = llm.validate_provider({"type": "cli", "command": [exe, "{message}"]})
        self.chat(p, [{"role": "user", "content": f"x; touch {marker}; $(touch {marker})"}])
        self.assertFalse(marker.exists())


class OpenClawTests(AdapterCase):
    def test_gateway_preset_makes_an_external_agent(self):
        self.fake.queue.append((200, {"model": "openclaw/default", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}))
        agent = agentise.from_api(self.root, {"preset": "openclaw", "base_url": self.fake.url + "/v1", "api_key": "gateway-token-123456", "name": "Claw"})
        self.assertEqual((agent["native_tools"], agent["source"], agent["provider"]["model"], agent["provider"]["key"]), (False, {"kind": "api", "preset": "openclaw", "external": True}, "openclaw/default", "openclaw_token"))
        self.assertEqual(self.fake.requests[0]["headers"]["authorization"], "Bearer gateway-token-123456")

    def test_external_agent_gets_no_tool_schema_and_one_turn(self):
        self.fake.queue.append((200, {"choices": [{"message": {"content": "I handled it with my own tools."}}], "usage": {"prompt_tokens": 9, "completion_tokens": 7}}))
        agent = agentise.from_api(self.root, {"preset": "openclaw", "base_url": self.fake.url + "/v1", "skip_test": True, "no_auth": True, "name": "Claw", "instructions": "You are the ops agent."})
        self.assertEqual(agent["provider"]["key"], "")
        proj = projects.create(self.root, "Ext")["id"]
        rt = jobs.Runtime(self.root)
        j = rt.start(proj, agent["id"], "check the servers")
        end = time.time() + 15
        while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
            time.sleep(0.05)
        j = rt.get(j["id"])
        self.assertEqual((j["status"], j["summary"]), ("succeeded", "I handled it with my own tools."))
        body = self.fake.requests[0]["body"]
        self.assertNotIn("tools", body)
        self.assertNotIn("update_progress", json.dumps(body))
        self.assertEqual(body["messages"][1], {"role": "user", "content": "check the servers"})

    def test_cli_preset_needs_an_agent_id(self):
        with self.assertRaises(agentise.AgentiseError):
            agentise.from_api(self.root, {"preset": "openclaw_cli", "skip_test": True})
        with self.assertRaises(agentise.AgentiseError):
            agentise.from_api(self.root, {"preset": "openclaw_cli", "openclaw_agent": "bad id; rm", "skip_test": True})
        a = agentise.from_api(self.root, {"preset": "openclaw_cli", "openclaw_agent": "ops-1", "skip_test": True})
        self.assertEqual((a["provider"]["command"][:4], a["native_tools"]), (["openclaw", "agent", "--agent", "ops-1"], False))


if __name__ == "__main__":
    unittest.main()
