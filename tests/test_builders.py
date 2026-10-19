import tempfile
import time
import unittest
import urllib.request
from unittest import mock

from agentlab import agents, builders, deploy, jobs, projects, vault

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_catalog_covers_every_builder(self):
        kinds = {t["kind"] for t in builders.catalog()}
        self.assertEqual(kinds, {"website", "game", "model", "infra"})
        self.assertTrue(all(t["files"] for t in builders.catalog()))
        for t in builders.catalog("game"):
            html = builders.preview(t["id"])["files"]["index.html"]
            self.assertIn("<script>", html)
            self.assertNotIn("http://", html.replace("http://127.0.0.1", ""))  # self-contained, no external loads

    def test_create_from_template_and_build_with_ai(self):
        out = builders.create(self.root, "landing", "My site")
        pid = out["project"]["id"]
        self.assertEqual(out["project"]["kind"], "website")
        self.assertIn("Do more", projects.read_file(self.root, pid, "index.html")["content"])
        a = agents.create(self.root, {"name": "Builder", "mode": "auto", "provider": {"type": "mock"}})["id"]
        rt = jobs.Runtime(self.root)
        script = Script([R("done")])
        with mock.patch("agentlab.jobs.llm.chat", script):
            out = builders.create(self.root, "snake", runtime=rt, agent_id=a, prompt="make the snake blue")
            end = time.time() + 10
            while rt.get(out["job"]["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        self.assertIn("make the snake blue", script.calls[0]["messages"][0]["content"])
        with self.assertRaises(projects.ProjectError):
            builders.create(self.root, "nope")


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.pid = builders.create(self.root, "blog")["project"]["id"]

    def test_localhost(self):
        info = deploy.serve_local(self.root, self.pid)
        self.addCleanup(lambda: self.pid in [s["project"] for s in deploy.local_servers()] and deploy.stop_local(self.pid))
        self.assertTrue(info["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(deploy.serve_local(self.root, self.pid)["port"], info["port"])  # idempotent
        body = urllib.request.urlopen(info["url"] + "index.html", timeout=5).read().decode()
        self.assertIn("My blog", body)
        with self.assertRaises(Exception):
            urllib.request.urlopen(info["url"] + "../../project.json", timeout=5).read()
        deploy.stop_local(self.pid)
        self.assertEqual(deploy.local_servers(), [])
        self.assertEqual(deploy.history(self.root, self.pid)[0]["target"], "localhost")
        with self.assertRaises(deploy.DeployError):
            deploy.serve_local(self.root, self.pid, "missing-folder")

    def test_cloudflare_pages_and_worker(self):
        with self.assertRaises(deploy.DeployError):
            deploy.deploy_cloudflare(self.root, self.pid, runner=lambda *a: (0, ""))
        vault.set_secret(self.root, "cloudflare_api_token", "cf-secret-token")
        vault.set_secret(self.root, "cloudflare_account", "acc123")
        seen = {}

        def runner(cmd, cwd, env):
            seen.update(cmd=cmd, env=env)
            return 0, "Uploading... ✨ Deployment complete! https://abc.my-blog.pages.dev token=cf-secret-token"
        out = deploy.deploy_cloudflare(self.root, self.pid, name="My Blog", runner=runner)
        self.assertEqual(out["url"], "https://abc.my-blog.pages.dev")
        self.assertIn("my-blog", seen["cmd"])
        self.assertNotIn("cf-secret-token", " ".join(seen["cmd"]))
        self.assertEqual(seen["env"]["CLOUDFLARE_API_TOKEN"], "cf-secret-token")
        self.assertNotIn("cf-secret-token", out["output"])
        w = builders.create(self.root, "cloudflare-ai-worker")["project"]["id"]
        deploy.deploy_cloudflare(self.root, w, runner=runner)
        self.assertEqual(seen["cmd"][-1], "deploy")
        with self.assertRaises(deploy.DeployError):
            deploy.deploy_cloudflare(self.root, self.pid, runner=lambda *a: (1, "auth error"))


if __name__ == "__main__":
    unittest.main()
