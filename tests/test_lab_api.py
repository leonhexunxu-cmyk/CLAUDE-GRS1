import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from agentlab.server import serve_in_thread


class LabApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = serve_in_thread(cls.tmp.name)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.lab.stop()
        time.sleep(0.2)
        cls.tmp.cleanup()

    def call(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/octet-stream" if raw is not None else "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_project_agent_job_flow(self):
        code, p = self.call("/api/lab/projects", {"name": "Site", "quota_mb": 5})
        self.assertEqual(code, 200)
        pid = p["id"]
        code, a = self.call("/api/lab/agents", {"name": "Dev", "role_preset": "developer", "mode": "auto", "provider": {"type": "mock"}})
        self.assertEqual(code, 200, a)
        self.assertTrue(a["instructions"])
        code, j = self.call("/api/lab/jobs", {"project": pid, "agent": a["id"], "prompt": "say hi"})
        self.assertEqual(code, 200, j)
        end = time.time() + 15
        while self.call(f"/api/lab/jobs/{j['id']}")[1]["status"] not in ("succeeded", "failed", "stopped", "cancelled") and time.time() < end:
            time.sleep(0.05)
        self.assertIn(self.call(f"/api/lab/jobs/{j['id']}")[1]["status"], ("succeeded", "stopped"))
        self.assertIn("trace", self.call(f"/api/lab/jobs/{j['id']}/trace")[1])
        self.assertEqual(self.call("/api/lab/projects/nope-1234")[0], 404)
        self.assertEqual(self.call("/api/lab/jobs", {"project": pid})[0], 400)

    def test_private_agent_needs_unlock(self):
        _, a = self.call("/api/lab/agents", {"name": "Vault", "provider": {"type": "mock"}})
        aid = a["id"]
        self.assertEqual(self.call(f"/api/lab/agents/{aid}/private", {"password": "s3cret-pass"})[0], 200)
        code, body = self.call(f"/api/lab/agents/{aid}")
        self.assertEqual(code, 403)
        listed = next(x for x in self.call("/api/lab/agents")[1]["agents"] if x["id"] == aid)
        self.assertTrue(listed["locked"])
        self.assertNotIn("instructions", listed)
        self.assertEqual(self.call(f"/api/lab/agents/{aid}/unlock", {"password": "wrong-pass"})[0], 403)
        tok = self.call(f"/api/lab/agents/{aid}/unlock", {"password": "s3cret-pass"})[1]["token"]
        code, body = self.call(f"/api/lab/agents/{aid}", headers={"X-Agent-Unlock": tok})
        self.assertEqual((code, body["private"]), (200, True))

    def test_upload_and_files_and_templates(self):
        _, p = self.call("/api/lab/projects", {"name": "Up", "quota_mb": 1})
        code, up = self.call(f"/api/lab/projects/{p['id']}/upload?path=data/x.bin&id=up1", raw=b"\x00\x01" * 1000)
        self.assertEqual((code, up["bytes"]), (200, 2000), up)
        self.assertEqual(self.call("/api/lab/uploads/up1")[1]["percent"], 100)
        self.assertEqual(self.call(f"/api/lab/projects/{p['id']}/files?path=data")[1]["entries"][0]["name"], "x.bin")
        self.assertEqual(self.call(f"/api/lab/projects/{p['id']}/upload?path=big.bin", raw=b"x" * 1_200_000)[0], 413)
        code, t = self.call("/api/lab/templates?kind=game")
        self.assertTrue(all(x["kind"] == "game" for x in t["templates"]))
        code, out = self.call("/api/lab/build", {"template": "snake"})
        self.assertEqual(code, 200)
        self.assertIn("<canvas", self.call(f"/api/lab/projects/{out['project']['id']}/file?path=index.html")[1]["content"])

    def test_misc_reads(self):
        for path in ("/api/lab/usage?days=7", "/api/lab/notifications", "/api/lab/suggestions", "/api/lab/library", "/api/lab/commands", "/api/lab/models",
                     "/api/lab/presets", "/api/lab/hardware", "/api/lab/knowledge", "/api/lab/schedules", "/api/lab/rooms", "/api/lab/batches", "/api/lab/deploys", "/api/lab/runners"):
            code, body = self.call(path)
            self.assertEqual(code, 200, f"{path}: {body}")
        code, r = self.call("/api/lab/playground/chat", {"messages": [{"role": "user", "content": "make me a dice roller"}]})
        self.assertEqual(code, 200, r)
        self.assertEqual(r["widgets"][0]["type"], "html")


if __name__ == "__main__":
    unittest.main()
