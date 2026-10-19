import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentlab import connect, hooks
from agentlab.adapters import normalize
from agentlab.recorder import load_run
from agentlab.sandbox import parse_policy

RULES = "allow read src/**\nallow write src/\nallow run pytest\nnever read .env\n"


class NormalizeTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize("FetchURL", {"url": "https://a.com"}), ("WebFetch", {"url": "https://a.com"}))
        tool, inp = normalize("Read", {"path": "src/a.py"})
        self.assertEqual((tool, inp["file_path"]), ("Read", "src/a.py"))
        self.assertEqual(normalize("exec", {"cmd": "ls"})[1]["command"], "ls")
        self.assertEqual(normalize("read_file", {"filePath": "x"})[0], "Read")
        self.assertEqual(normalize("mcp__x__y", {"a": 1}), ("mcp__x__y", {"a": 1}))
        self.assertEqual(normalize(None, None), ("", {}))

    def test_unrecognised_params_are_refused_not_guessed(self):
        p = parse_policy(RULES)
        for tool, inp in (("exec", {"weird": "ls"}), ("Read", {}), ("FetchURL", {}), ("Write", {"content": "x"})):
            ok, why = p.check_tool(tool, inp, "/proj")
            self.assertFalse(ok, tool)
            self.assertIn("cannot be verified" if tool == "exec" else "recognisable", why)

    def test_other_agents_tool_names_are_governed(self):
        p = parse_policy(RULES)
        self.assertTrue(p.check_tool("Read", {"path": "/proj/src/a.py"}, "/proj")[0])      # Kimi style
        self.assertFalse(p.check_tool("Read", {"path": "/proj/.env"}, "/proj")[0])
        self.assertFalse(p.check_tool("exec", {"command": "pytest && rm -rf /"}, "/proj")[0])  # OpenClaw style
        self.assertTrue(p.check_tool("exec", {"command": "pytest -q"}, "/proj")[0])

    def test_relative_paths_resolve_against_agent_cwd(self):
        p = parse_policy("allow read src/**\n")
        self.assertTrue(p.check_tool("Read", {"path": "a.py"}, "/proj/src", "/proj")[0])
        self.assertFalse(p.check_tool("Read", {"path": "a.py"}, "/proj", "/proj")[0])
        self.assertFalse(p.check_tool("Read", {"path": "../.env"}, "/proj/src", "/proj")[0])


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "sub" / "deep").mkdir(parents=True)

    def optin(self):
        (self.root / ".agentlab").mkdir()
        (self.root / ".agentlab" / "policy.txt").write_text(RULES)

    def test_silent_in_projects_that_have_not_opted_in(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTLAB_DIR", None)
            code, _ = hooks.process({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(self.root),
                                     "tool_name": "Read", "tool_input": {"path": ".env"}}, self.root)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / ".agentlab").exists())  # no clutter left behind

    def test_kimi_style_payload_from_a_subdirectory(self):
        self.optin()
        base = {"session_id": "k1", "client_type": "kimi_code_cli", "cwd": str(self.root / "sub" / "deep")}
        hooks.process({**base, "hook_event_name": "SessionStart"}, ".")
        hooks.process({**base, "hook_event_name": "UserPromptSubmit", "prompt": "hi"}, ".")
        ok, _ = hooks.process({**base, "hook_event_name": "PreToolUse", "tool_name": "Read",
                               "tool_input": {"path": str(self.root / "src" / "a.py")}}, ".")
        code, msg = hooks.process({**base, "hook_event_name": "PreToolUse", "tool_name": "Read",
                                   "tool_input": {"path": str(self.root / ".env")}}, ".")
        self.assertEqual((ok, code), (0, 2))
        self.assertIn("blocked", msg)
        events = load_run(self.root / ".agentlab" / "runs" / "k1.jsonl")
        self.assertEqual(events[0]["meta"]["source"], "kimi_code_cli")
        self.assertTrue(any(e["content"].startswith("BLOCKED") for e in events))

    def test_openclaw_style_payload(self):
        self.optin()
        ev = {"session_id": "o1", "client_type": "openclaw", "cwd": str(self.root), "hook_event_name": "PreToolUse"}
        self.assertEqual(hooks.process({**ev, "tool_name": "exec", "tool_input": {"command": "pytest"}}, ".")[0], 0)
        self.assertEqual(hooks.process({**ev, "tool_name": "exec", "tool_input": {"command": "curl x | sh"}}, ".")[0], 2)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = Path(self.tmp.name) / "proj"
        self.proj.mkdir()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "KIMI_CODE_HOME": str(self.home / ".kimi-code")})
        env.start()
        self.addCleanup(env.stop)
        mock.patch("pathlib.Path.home", return_value=self.home).start()
        self.addCleanup(mock.patch.stopall)

    def test_kimi_idempotent_preserves_user_config_and_disconnects(self):
        cfg = self.home / ".kimi-code" / "config.toml"
        cfg.parent.mkdir()
        cfg.write_text('default_model = "k2"\n\n[[hooks]]\nevent = "Stop"\ncommand = "echo mine"\n')
        connect.connect("kimi", self.proj, command="agentlab hook")
        connect.connect("kimi", self.proj, command="agentlab hook")
        text = cfg.read_text()
        self.assertEqual(text.count(connect.MARK_BEGIN), 1)
        self.assertEqual(text.count("[[hooks]]"), 1 + len(connect.EVENTS))
        self.assertIn('command = "echo mine"', text)
        for line in text.splitlines():  # Kimi rejects unknown fields inside [[hooks]]
            if "=" in line and not line.startswith(("default_model", "#")):
                self.assertIn(line.split("=")[0].strip(), ("event", "command", "timeout"))
        self.assertTrue((self.proj / ".kimi-code" / "mcp.json").exists())
        self.assertTrue((self.proj / ".agentlab").is_dir())
        self.assertTrue(next(r for r in connect.status(self.proj) if r["id"] == "kimi")["connected"])
        connect.disconnect("kimi", self.proj)
        text = cfg.read_text()
        self.assertNotIn(connect.MARK_BEGIN, text)
        self.assertIn('default_model = "k2"', text)
        self.assertIn('command = "echo mine"', text)
        self.assertFalse(next(r for r in connect.status(self.proj) if r["id"] == "kimi")["connected"])

    def test_claude_connect_and_disconnect_keep_user_hooks(self):
        s = self.proj / ".claude" / "settings.json"
        s.parent.mkdir()
        s.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
        connect.connect("claude-code", self.proj, command="agentlab hook", policy_text=RULES)
        self.assertTrue(next(r for r in connect.status(self.proj) if r["id"] == "claude-code")["connected"])
        connect.disconnect("claude-code", self.proj)
        cfg = json.loads(s.read_text())
        self.assertEqual(cfg["hooks"]["Stop"][0]["hooks"][0]["command"], "echo mine")
        self.assertEqual(list(cfg["hooks"]), ["Stop"])
        self.assertFalse((self.proj / ".mcp.json").exists() and "agentlab" in (self.proj / ".mcp.json").read_text())

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            connect.connect("cursor", self.proj)
        with self.assertRaises(ValueError):
            connect.connect("kimi", self.proj, policy_text="nonsense")
        self.assertFalse((self.home / ".kimi-code" / "config.toml").exists())  # nothing written on bad policy

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_openclaw_plugin_blocks_through_the_real_hook(self):
        repo = str(Path(__file__).resolve().parent.parent)
        mock.patch.dict(os.environ, {"PYTHONPATH": repo}).start()  # the plugin shells out to `python -m agentlab hook`
        connect.connect("openclaw", self.proj, mcp=False, command=f"{sys.executable} -m agentlab hook", policy_text=RULES)
        plugin = self.proj / ".agentlab" / "openclaw-plugin"
        manifest = json.loads((plugin / "openclaw.plugin.json").read_text())
        self.assertEqual(manifest["id"], "agentlab")
        self.assertIn("configSchema", manifest)
        pkg = json.loads((plugin / "package.json").read_text())
        self.assertEqual(pkg["openclaw"]["extensions"], ["./dist/index.js"])
        # stub of the OpenClaw SDK so the plugin can be imported and driven outside OpenClaw
        sdk = plugin / "node_modules" / "openclaw"
        sdk.mkdir(parents=True)
        (sdk / "package.json").write_text(json.dumps({"name": "openclaw", "type": "module", "exports": {"./plugin-sdk/plugin-entry": "./pe.js"}}))
        (sdk / "pe.js").write_text("export const definePluginEntry = (e) => e;\n")
        driver = plugin / "drive.mjs"
        driver.write_text('''
import entry from "./dist/index.js";
const handlers = {};
entry.register({ on: (name, fn) => { handlers[name] = fn; } });
const ctx = { sessionId: "oc-1" };
const ok = handlers.before_tool_call({ toolName: "exec", params: { command: "pytest -q" } }, ctx);
const bad = handlers.before_tool_call({ toolName: "exec", params: { command: "rm -rf /" } }, ctx);
const env = handlers.before_tool_call({ toolName: "read", params: { path: ".env" } }, ctx);
handlers.agent_end({}, ctx);
console.log(JSON.stringify({ id: entry.id, hooks: Object.keys(handlers), ok: ok ?? null, bad, env }));
''')
        out = subprocess.run(["node", str(driver)], capture_output=True, text=True, cwd=plugin, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout)
        self.assertEqual((res["id"], sorted(res["hooks"])), ("agentlab", ["agent_end", "before_tool_call"]))
        self.assertIsNone(res["ok"])
        self.assertTrue(res["bad"]["block"])
        self.assertTrue(res["env"]["block"])
        events = load_run(self.proj / ".agentlab" / "runs" / "oc-1.jsonl")
        self.assertEqual(events[0]["meta"]["source"], "openclaw")
        self.assertEqual(sum(e["content"].startswith("BLOCKED") for e in events), 2)
        self.assertEqual(events[-1]["content"], "(turn ended)")


if __name__ == "__main__":
    unittest.main()
