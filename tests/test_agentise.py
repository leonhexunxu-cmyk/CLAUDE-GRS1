import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from agentlab import agentise, agents, jobs, llm, project_env, projects, runners, vault

try:
    from tests.test_llm import Fake
except ImportError:
    from test_llm import Fake

FAKE_LLAMA = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for llama-server: same flags, /health (503 while 'loading'), OpenAI-compatible /v1/chat/completions."""
    import json, sys, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    a = sys.argv[1:]
    opt = lambda f: a[a.index(f) + 1]
    port, alias, started = int(opt("--port")), opt("--alias"), time.time()
    assert "-m" in a and opt("--host") == "127.0.0.1"
    class H(BaseHTTPRequestHandler):
        def _send(self, status, obj):
            data = json.dumps(obj).encode(); self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            ready = time.time() - started > 0.5
            self._send(200 if ready else 503, {"status": "ok" if ready else "loading model"})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            used_tool = any(m["role"] == "tool" for m in body["messages"])
            if body.get("tools") and not used_tool:
                msg = {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "finish", "arguments": json.dumps({"summary": f"hello from {alias}"})}}]}
            else:
                msg = {"content": f"hello from {alias}"}
            self._send(200, {"model": alias, "choices": [{"finish_reason": "stop", "message": msg}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        def log_message(self, *x): pass
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        env = mock.patch.dict(os.environ, {e: "" for e in vault.ENV_FALLBACK.values()})
        env.start()
        self.addCleanup(env.stop)
        self.proj = projects.create(self.root, "Weights", quota_mb=50)["id"]
        self.bin = Path(self.root) / "llama-server"
        self.bin.write_text(FAKE_LLAMA)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        ws = projects.workspace(self.root, self.proj)
        (ws / "model.gguf").write_bytes(b"GGUF" + b"\x00" * 100)
        (ws / "model.safetensors").write_bytes(b"\x08\x00\x00\x00{}")


class RunnerTests(Base):
    def test_runner_lifecycle_and_a_real_agent_job_against_it(self):
        result = agentise.from_weights(self.root, {"project_id": self.proj, "path": "model.gguf", "name": "My Tiny LM", "mode": "auto", "ctx": 2048}, binary=str(self.bin))
        runner, agent = result["runner"], result["agent"]
        self.addCleanup(lambda: runners.stop(self.root, runner["id"]))
        self.assertEqual((runner["alias"], runner["runtime"], runner["ctx"]), ("my-tiny-lm", "llama-server", 2048))
        self.assertEqual(agent["provider"], {**agent["provider"], "type": "openai", "model": "my-tiny-lm", "base_url": f"http://127.0.0.1:{runner['port']}/v1"})
        self.assertEqual(agent["source"], {"kind": "weights", "runtime": "llama-server", "path": "model.gguf", "runner": runner["id"]})
        self.assertEqual(runners.get(self.root, runner["id"])["status"], "starting")  # the model is still 'loading'
        self.assertEqual(runners.wait_ready(self.root, runner, 15)["status"], "ready")
        rt = jobs.Runtime(self.root)
        job = rt.start(self.proj, agent["id"], "say hi")  # real HTTP to the runner, no mocks
        end = time.time() + 20
        while rt.get(job["id"])["status"] not in jobs.FINAL and time.time() < end:
            time.sleep(0.05)
        j = rt.get(job["id"])
        self.assertEqual((j["status"], j["summary"]), ("succeeded", "hello from my-tiny-lm"))
        self.assertEqual((j["metrics"]["input_tokens"], j["metrics"]["output_tokens"]), (7, 3))
        stopped = runners.stop(self.root, runner["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(runners._health(runner["port"], 0.5))
        self.assertEqual([r["status"] for r in runners.list_runners(self.root)], ["stopped"])

    def test_refusals_come_with_help(self):
        with self.assertRaises(agentise.AgentiseError) as cm:
            agentise.from_weights(self.root, {"project_id": self.proj, "path": "model.safetensors"}, binary=str(self.bin))
        self.assertIn("GGUF", str(cm.exception))
        with mock.patch("agentlab.runners.shutil.which", return_value=None):
            with self.assertRaises(agentise.AgentiseError) as cm:
                agentise.from_weights(self.root, {"project_id": self.proj, "path": "model.gguf"})
        self.assertIn("llama-server was not found", str(cm.exception))
        for bad in ({"path": "../x.gguf"}, {"path": "missing.gguf"}, {"path": "model.gguf", "runtime": "tensorrt"}):
            with self.assertRaises((agentise.AgentiseError, projects.ProjectError)):
                agentise.from_weights(self.root, {"project_id": self.proj, **bad}, binary=str(self.bin))
        self.assertEqual(agents.list_agents(self.root), [])  # failures leave no half-made agents behind

    def test_ollama_registration_uses_a_modelfile(self):
        fake = Path(self.root) / "ollama"
        fake.write_text(f'#!/bin/sh\necho "$@" > "{self.root}/ollama-args.txt"\ncat "$4" >> "{self.root}/ollama-args.txt"\n')
        fake.chmod(0o755)
        res = agentise.from_weights(self.root, {"project_id": self.proj, "path": "model.gguf", "runtime": "ollama", "name": "Tiny One"}, binary=str(fake))
        self.assertEqual((res["agent"]["provider"]["base_url"], res["agent"]["provider"]["model"], res["runner"]), ("http://127.0.0.1:11434/v1", "tiny-one", None))
        args = (Path(self.root) / "ollama-args.txt").read_text()
        self.assertIn("create tiny-one -f", args)
        self.assertIn("FROM ./model.gguf", args)


class ApiAgentiseTests(Base):
    def test_hosted_model_becomes_an_agent_with_a_tested_stored_key(self):
        fake = Fake()
        self.addCleanup(fake.close)
        fake.queue.append((200, {"model": "gpt-x", "choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1}}))
        agent = agentise.from_api(self.root, {"preset": "openai", "base_url": fake.url + "/v1", "model": "gpt-x", "api_key": "sk-test-abcdef123456", "name": "Helper",
                                              "instructions": "Be kind.", "mode": "ask", "price_in": 2, "price_out": 8, "limits": {"max_output_tokens": 500}})
        self.assertEqual((agent["name"], agent["mode"], agent["limits"]["max_output_tokens"], agent["source"]), ("Helper", "ask", 500, {"kind": "api", "preset": "openai"}))
        self.assertEqual(agent["provider"]["key"], "openai")
        self.assertNotIn("sk-test-abcdef123456", json.dumps(agent))  # the agent only knows the key's *name*
        self.assertEqual(vault.get_secret(self.root, "openai"), "sk-test-abcdef123456")
        req = fake.requests[0]
        self.assertEqual(req["headers"]["authorization"], "Bearer sk-test-abcdef123456")
        self.assertEqual(req["body"]["max_completion_tokens"], 16)  # OpenAI's newer parameter name, from the preset

    def test_a_model_that_does_not_answer_creates_nothing(self):
        fake = Fake()
        self.addCleanup(fake.close)
        fake.queue.append((401, {"error": "invalid key"}))
        with self.assertRaises(agentise.AgentiseError) as cm:
            agentise.from_api(self.root, {"preset": "custom", "base_url": fake.url + "/v1", "model": "m", "api_key": "sk-wrong-123456789"})
        self.assertIn("did not answer", str(cm.exception))
        self.assertEqual(agents.list_agents(self.root), [])
        self.assertFalse(agentise.test_provider(self.root, {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "m"})["ok"])
        self.assertTrue(agentise.test_provider(self.root, llm.PRESETS["mock"])["ok"])
        self.assertEqual(agentise.from_api(self.root, {"preset": "mock", "name": "Demo"})["provider"]["type"], "mock")
        for bad in ({"preset": "nope"}, {"preset": "custom", "base_url": "ftp://x", "model": "m"}, {"preset": "openai", "model": "m", "api_key": " ", "skip_test": True}):
            with self.assertRaises(agentise.AgentiseError, msg=bad):
                agentise.from_api(self.root, bad)


class EnvironmentTests(Base):
    def run_job(self, command, **kw):
        agent = agents.create(self.root, {"name": "Runner", "mode": "auto", "provider": {"type": "mock", "script": [
            {"tool_calls": [{"name": "run_command", "arguments": {"command": command, **kw}}]}, {"text": "done"}]}})["id"]
        rt = jobs.Runtime(self.root)
        j = rt.start(self.proj, agent, "go")
        end = time.time() + 20
        while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
            time.sleep(0.05)
        return [e["content"] for e in rt.get(j["id"], events=50)["events"] if e["kind"] == "tool_result"][0]

    def test_validation(self):
        for bad in ({"python": {"packages": ["requests; rm -rf /"]}}, {"python": {"packages": ["git+https://x/y.git"]}}, {"python": {"packages": ["-e ."]}},
                    {"vars": {"PATH": "/evil"}}, {"vars": {"lower case": "x"}}, {"vars": {"A": 1}}, {"secrets": ["Bad Name"]}, {"network": "sometimes"}, {"timeout": 0}, {"timeout": "x"}):
            with self.assertRaises(project_env.EnvError, msg=bad):
                project_env.save(self.root, self.proj, bad)
        cfg = project_env.save(self.root, self.proj, {"python": {"enabled": True, "packages": ["requests>=2.31", "rich[jupyter]==13.*"]}, "vars": {"MODE": "dev"}})
        self.assertEqual(project_env.get(self.root, self.proj)["python"]["packages"], ["requests>=2.31", "rich[jupyter]==13.*"])
        self.assertEqual(cfg["network"], "allowed")

    def test_vars_secrets_are_explicit_opt_in(self):
        vault.set_secret(self.root, "openai", "sk-openai-visible-123456")
        vault.set_secret(self.root, "hf_token", "hf_hidden_token_123456")
        out = self.run_job('echo "$MODE|$OPENAI_API_KEY|$HF_TOKEN"')
        self.assertIn("||", out)  # nothing is shared by default
        project_env.save(self.root, self.proj, {"vars": {"MODE": "dev"}, "secrets": ["openai"]})
        out = self.run_job('echo "$MODE|$OPENAI_API_KEY|$HF_TOKEN"')
        self.assertIn("dev|[REDACTED]|", out)  # the command did receive the key, but run logs never store it
        self.assertNotIn("hf_hidden", out)
        stored = "".join(p.read_text() for p in (Path(self.root) / ".agentlab" / "runs").glob("*.jsonl"))
        self.assertNotIn("sk-openai-visible-123456", stored)

    def test_venv_is_created_and_used_by_commands(self):
        project_env.save(self.root, self.proj, {"python": {"enabled": True, "packages": []}})
        self.assertNotIn("VIRTUAL_ENV", project_env.command_env(self.root, self.proj))  # not built yet
        rec = project_env.setup_python(self.root, self.proj, wait=True)
        self.assertEqual(rec["status"], "succeeded", rec["log"])
        self.assertTrue((project_env.venv_dir(self.root, self.proj) / "bin" / "python").exists())
        self.assertFalse((projects.workspace(self.root, self.proj) / "venv").exists())  # outside the workspace and its quota
        out = self.run_job("python -c 'import sys; print(sys.prefix)'")
        self.assertIn(str(project_env.venv_dir(self.root, self.proj)), out)
        self.assertEqual(project_env.setup_status(self.root, self.proj)["status"], "succeeded")
        project_env.save(self.root, self.proj, {"python": {"enabled": False}})
        with self.assertRaises(project_env.EnvError):
            project_env.setup_python(self.root, self.proj)

    def test_offline_environment_overrides_agent_authorisation(self):
        project_env.save(self.root, self.proj, {"network": "off"})
        out = self.run_job("echo hi", network=True)
        self.assertIn("offline", out)
        self.assertTrue(self.run_job("echo hi").startswith("exit 0"))


if __name__ == "__main__":
    unittest.main()
