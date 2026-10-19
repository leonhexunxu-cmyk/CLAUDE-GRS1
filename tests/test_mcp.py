import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentlab.install import install_mcp_config
from agentlab.mcp import Server, serve
from agentlab.recorder import load_run


def call(srv, name, **args):
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]["content"][0]["text"], r["result"]["isError"]


class McpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.srv = Server(self.tmp.name)

    def policy(self, text):
        d = Path(self.tmp.name) / ".agentlab"
        d.mkdir(exist_ok=True)
        (d / "policy.txt").write_text(text)

    def test_handshake_and_listing(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        r = self.srv.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in self.srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["check_policy", "record_note", "plant_canaries", "audit_answer"])

    def test_errors(self):
        self.assertEqual(self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"], -32601)
        r = self.srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(self.srv.handle({"id": 1})["error"]["code"], -32600)

    def test_check_policy(self):
        self.assertTrue(call(self.srv, "check_policy", action="read", target=".env")[0].startswith("ALLOWED: no policy"))
        self.policy("allow read src/**\nnever read .env\n")
        self.assertTrue(call(self.srv, "check_policy", action="read", target="src/a.py")[0].startswith("ALLOWED"))
        self.assertTrue(call(self.srv, "check_policy", action="read", target=".env")[0].startswith("BLOCKED"))
        self.assertTrue(call(self.srv, "check_policy", action="fly", target="x")[1])  # isError

    def test_record_note_redacts_and_rejects_bad_run(self):
        text, bad = call(self.srv, "record_note", content="used key sk-abcdefghijklmnop1234")
        self.assertFalse(bad)
        events = load_run(Path(self.tmp.name) / ".agentlab" / "runs" / f"{self.srv.run}.jsonl")
        self.assertEqual(events[-1]["kind"], "decision")
        self.assertNotIn("sk-abc", events[-1]["content"])
        self.assertTrue(call(self.srv, "record_note", content="x", run="../evil")[1])

    def test_canaries_roundtrip(self):
        text, _ = call(self.srv, "plant_canaries", seed="k", document="a\n\nb", n=2)
        value = text.split("expect ")[1].split("\n")[0]
        self.assertIn(value, text)
        self.assertTrue(call(self.srv, "audit_answer", seed="k", n=2, answer="I don't know")[0].startswith("looks healthy"))
        self.assertFalse(call(self.srv, "audit_answer", seed="", answer="x")[0] == "")

    def test_stdio_framing(self):
        inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        out = io.StringIO()
        serve(self.tmp.name, inp, out)
        lines = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)  # ping reply + parse error; notification gets none
        self.assertEqual(lines[1]["error"]["code"], -32700)

    def test_real_subprocess(self):
        req = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        p = subprocess.run([sys.executable, "-m", "agentlab", "mcp", "--project", self.tmp.name],
                           input=req, capture_output=True, text=True, timeout=20)
        self.assertEqual(len(json.loads(p.stdout)["result"]["tools"]), 4)

    def test_mcp_config_merge(self):
        path = Path(self.tmp.name) / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        install_mcp_config(self.tmp.name)
        cfg = json.loads(path.read_text())
        self.assertIn("other", cfg["mcpServers"])
        self.assertEqual(cfg["mcpServers"]["agentlab"]["args"][-3:], ["mcp", "--project", "."])


if __name__ == "__main__":
    unittest.main()
