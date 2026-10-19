import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentlab import hooks
from agentlab.install import install_claude_code
from agentlab.recorder import load_run
from agentlab.sandbox import parse_policy
from agentlab.schema import redact
from agentlab.server import serve_in_thread

RULES = "allow read src/**\nallow write src/\nallow run pytest, git status\nnever read .env\n"


class RedactTests(unittest.TestCase):
    def test_masks_secrets(self):
        s = "key sk-abcdefghijklmnop1234 and API_KEY=hunter2hunter2 Bearer abcdefghijklmnop12345 ghp_" + "a" * 24
        out = redact(s)
        for leaked in ("sk-abcdef", "hunter2", "abcdefghijklmnop12345", "ghp_a"):
            self.assertNotIn(leaked, out)


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_policy(RULES)

    def ok(self, tool, **inp):
        return self.p.check_tool(tool, inp, "/proj")[0]

    def test_bash_chaining_and_substitution(self):
        self.assertTrue(self.ok("Bash", command="pytest -q && git status"))
        self.assertFalse(self.ok("Bash", command="pytest && rm -rf /"))
        self.assertFalse(self.ok("Bash", command="pytest $(rm -rf /)"))
        self.assertFalse(self.ok("Bash", command="pytest `id`"))
        self.assertFalse(self.ok("Bash", command="pytest > .github/x"))
        self.assertTrue(self.ok("Bash", command="pytest 2>&1"))

    def test_paths(self):
        self.assertTrue(self.ok("Read", file_path="/proj/src/a.py"))
        self.assertFalse(self.ok("Read", file_path="/proj/src/../.env"))
        self.assertFalse(self.ok("Write", file_path="/etc/passwd"))
        self.assertTrue(self.ok("mcp__other__tool"))  # not modelled -> not governed


class HookTests(unittest.TestCase):
    def test_logs_and_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".agentlab").mkdir()
            (Path(d) / ".agentlab" / "policy.txt").write_text(RULES)
            ev = {"session_id": "s1", "cwd": d}
            hooks.process({**ev, "hook_event_name": "UserPromptSubmit", "prompt": "go"}, d)
            code, _ = hooks.process({**ev, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                     "tool_input": {"command": "pytest"}}, d)
            self.assertEqual(code, 0)
            code, msg = hooks.process({**ev, "hook_event_name": "PreToolUse", "tool_name": "Read",
                                       "tool_input": {"file_path": f"{d}/.env"}}, d)
            self.assertEqual(code, 2)
            self.assertIn("blocked", msg)
            events = load_run(Path(d) / ".agentlab" / "runs" / "s1.jsonl")
            self.assertEqual([e["step"] for e in events], list(range(len(events))))
            self.assertTrue(any(e["content"].startswith("BLOCKED") for e in events))

    def test_install_is_idempotent_and_merges(self):
        with tempfile.TemporaryDirectory() as d:
            s = Path(d) / ".claude" / "settings.json"
            s.parent.mkdir()
            s.write_text(json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls:*)"]}}))
            for _ in range(2):
                install_claude_code(d, command="agentlab hook", policy_text=RULES, with_permissions=True)
            cfg = json.loads(s.read_text())
            self.assertEqual(cfg["model"], "x")
            self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)
            self.assertIn("Bash(ls:*)", cfg["permissions"]["allow"])
            self.assertIn("Read(.env)", cfg["permissions"]["deny"])
            with self.assertRaises(ValueError):
                install_claude_code(d, command="agentlab hook", policy_text="nonsense")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = serve_in_thread(cls.tmp.name)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_runs_fork_and_ingest(self):
        self.assertEqual(self.call("/api/events", {"run": "r1", "kind": "prompt", "content": "hi"})[0], 200)
        self.call("/api/events", {"run": "r1", "kind": "output", "content": "key=sk-abcdefghijklmnop1234"})
        _, runs = self.call("/api/runs")
        self.assertEqual(runs["runs"][0]["id"], "r1")
        _, run = self.call("/api/runs/r1")
        self.assertNotIn("sk-abc", json.dumps(run))
        _, f = self.call("/api/runs/r1/fork", {"step": 1})
        self.assertEqual(f["id"], "r1-fork1")
        self.assertEqual(len(self.call("/api/runs/r1-fork1")[1]["events"]), 2)

    def test_rejects_bad_input(self):
        self.assertEqual(self.call("/api/runs/..%2Fetc")[0], 400)
        self.assertEqual(self.call("/api/runs/nope")[0], 404)
        self.assertEqual(self.call("/api/events", {"run": "r", "kind": "bogus"})[0], 400)
        self.assertEqual(self.call("/api/policy/simulate", {"rules": "please be nice"})[0], 400)
        self.assertEqual(self.call("/api/health", headers={"Host": "evil.example"})[0], 403)
        req = urllib.request.Request(self.base + "/api/policy/check", data=b"{}", headers={"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 415)

    def test_policy_regress_tripwires(self):
        self.assertEqual(self.call("/api/policy", {"rules": RULES})[0], 200)
        self.assertEqual(self.call("/api/policy")[1]["rules"], RULES)
        _, sim = self.call("/api/policy/simulate", {"rules": RULES})
        self.assertFalse(any(r["allowed"] for r in sim["results"]))
        _, res = self.call("/api/regress", {"baseline": {"a": {"output": "hello world"}},
                                            "candidate": {"a": {"output": "totally different"}}})
        self.assertTrue(res["regressed"])
        _, planted = self.call("/api/tripwires/plant", {"seed": "k", "n": 2, "document": "a\n\nb"})
        expected = planted["questions"][0]["expected"]
        self.assertIn(expected, planted["document"])
        _, a = self.call("/api/tripwires/audit", {"seed": "k", "n": 2, "answer": "no idea"})
        self.assertTrue(a["healthy"])  # abstaining is not a hallucination

    def test_ui_served(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"agentlab", r.read())


if __name__ == "__main__":
    unittest.main()
