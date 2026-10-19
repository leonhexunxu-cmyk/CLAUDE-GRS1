import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from agentlab import accounts, projects, uploads, vault


class FakeAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("Authorization") or self.headers.get("x-api-key") or ""
        if "good" not in auth:
            self.send_response(401)
            self.end_headers()
            return
        if self.path.startswith("/v1/models?"):
            body = {"data": [{"id": "claude-x", "display_name": "Claude X"}]}
        else:
            body = {"data": [{"id": "model-b", "owned_by": "me"}, {"id": "model-a"}]}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            p = mock.patch.dict("os.environ", {k: ""})
            p.start()
            self.addCleanup(p.stop)

    def test_connect_openai_and_anthropic_and_list(self):
        with mock.patch.object(accounts, "OPENAI_API", self.base + "/v1"), mock.patch.object(accounts, "ANTHROPIC_API", self.base):
            with self.assertRaises(accounts.AccountError):
                accounts.connect(self.root, "openai", "bad")
            self.assertIsNone(vault.get_secret(self.root, "openai"))
            who = accounts.connect(self.root, "openai", "sk-good")
            self.assertEqual(who["display"], "2 models available")
            accounts.connect(self.root, "anthropic", "sk-ant-good")
        st = accounts.status(self.root)
        self.assertTrue(st["openai"]["connected"] and st["anthropic"]["connected"])
        models = accounts.list_models(self.root, {"type": "openai", "base_url": self.base + "/v1", "key": "openai"})
        self.assertEqual([m["id"] for m in models], ["model-a", "model-b"])
        self.assertEqual(accounts.list_models(self.root, {"type": "anthropic", "base_url": self.base, "key": "anthropic"})[0]["name"], "Claude X")
        with self.assertRaises(accounts.AccountError):
            accounts.list_models(self.root, {"type": "cli"})


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.pid = projects.create(self.root, "U", quota_mb=1)["id"]

    def test_streamed_upload_with_progress(self):
        data = b"x" * 300_000
        out = uploads.receive(self.root, self.pid, "models/w.gguf", io.BytesIO(data), len(data), "abc", chunk=50_000)
        self.assertEqual(out["bytes"], 300_000)
        self.assertEqual(projects.resolve(self.root, self.pid, "models/w.gguf").read_bytes(), data)
        self.assertEqual(uploads.progress("abc")["percent"], 100)

    def test_quota_interrupt_and_paths(self):
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.receive(self.root, self.pid, "big.bin", io.BytesIO(b""), 5_000_000)
        self.assertEqual(cm.exception.code, 413)
        with self.assertRaises(uploads.UploadError):
            uploads.receive(self.root, self.pid, "cut.bin", io.BytesIO(b"abc"), 10)
        self.assertEqual([p.name for p in projects.workspace(self.root, self.pid).iterdir()], [])
        with self.assertRaises(projects.ProjectError):
            uploads.receive(self.root, self.pid, "../escape.bin", io.BytesIO(b"a"), 1)


if __name__ == "__main__":
    unittest.main()
