import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from agentlab import agents, embeddings, jobs, knowledge, projects, vault

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script

SITE = {
    "/robots.txt": "User-agent: *\nDisallow: /private",
    "/": "<html><title>Home</title><body><nav>menu</nav><p>Welcome to the Zorblat museum. The Zorblat is a rare purple bird that sings at dawn in the mountains of Kel.</p><a href='/about'>about</a><a href='/private/x'>p</a><a href='https://other.example/'>x</a></body></html>",
    "/about": "<html><body><script>var secret=1</script><p>The museum opens every day at nine and closes at five. Tickets cost ten coins for adults and are free for children.</p></body></html>",
}


class EmbeddingTests(unittest.TestCase):
    def test_builtin_is_unit_and_similar_texts_score_higher(self):
        a, b, c = embeddings.embed(None, ["the cat sat on the mat", "a cat sat on a mat", "quarterly revenue grew"])
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=5)
        self.assertGreater(embeddings.cosine(a, b), embeddings.cosine(a, c))

    def test_openai_compatible_endpoint(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.update(path=self.path, auth=self.headers.get("Authorization"), body=body)
                out = json.dumps({"data": [{"index": i, "embedding": [3.0, 4.0]} for i in range(len(body["input"]))]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with tempfile.TemporaryDirectory() as root:
            vault.set_secret(root, "emb", "sk-test")
            vecs = embeddings.embed({"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "e", "key": "emb"}, ["a", "b"], root)
        self.assertEqual(vecs[0], [0.6, 0.8])
        self.assertEqual(seen["path"], "/v1/embeddings")
        self.assertEqual(seen["auth"], "Bearer sk-test")
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.validate({"type": "openai", "base_url": "ftp://x", "model": "m"})


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.kb = knowledge.create(self.root, "Docs")

    def test_chunking(self):
        text = ("Sentence number one is here. " * 80 + "\n\n") * 3
        parts = knowledge.chunk(text)
        self.assertTrue(all(len(p) <= knowledge.CHUNK + 5 for p in parts))
        self.assertGreater(len(parts), 3)

    def test_add_search_remove(self):
        kid = self.kb["id"]
        s1 = knowledge.add_text(self.root, kid, "Our refund policy: customers can return items within 30 days for a full refund.", "Policy")
        knowledge.add_text(self.root, kid, "The office cat is called Miso and likes boxes.", "Cat")
        hits = knowledge.search(self.root, kid, "how many days to return an item for a refund")
        self.assertEqual(hits[0]["source"], "Policy")
        pid = projects.create(self.root, "P")["id"]
        projects.write_file(self.root, pid, "notes.md", "Deploys happen on Tuesdays after the standup.")
        knowledge.add_project_file(self.root, kid, pid, "notes.md")
        self.assertEqual(knowledge.search(self.root, kid, "when do deploys happen")[0]["source"], "notes.md")
        knowledge.remove_source(self.root, kid, s1["id"])
        self.assertEqual(len(knowledge.get(self.root, kid)["sources"]), 2)
        self.assertNotIn("Policy", [h["source"] for h in knowledge.search(self.root, kid, "refund", 10)])
        with self.assertRaises(knowledge.KnowledgeError):
            knowledge.get(self.root, "../x")

    def test_crawl_respects_robots_and_site(self):
        fetched = []

        def fetch(url):
            fetched.append(url)
            path = url.split("example.com", 1)[1] or "/"
            if path not in SITE:
                raise ValueError("HTTP 404")
            return SITE[path]
        res = knowledge.scrape(self.root, self.kb["id"], "https://example.com/", max_pages=5, fetch=fetch, sleep=lambda s: None)
        self.assertEqual(len(res["added"]), 2)
        self.assertTrue(any("robots" in s["reason"] for s in res["skipped"]))
        self.assertFalse(any("other.example" in u for u in fetched))
        self.assertFalse(any("/private" in u for u in fetched if "robots" not in u))
        hit = knowledge.search(self.root, self.kb["id"], "what does the zorblat bird do at dawn")[0]
        self.assertIn("sings at dawn", hit["text"])
        self.assertNotIn("menu", hit["text"])
        about = knowledge.search(self.root, self.kb["id"], "ticket price for adults")[0]
        self.assertNotIn("secret", about["text"])

    def test_agent_gets_passages_and_tool(self):
        kid = self.kb["id"]
        knowledge.add_text(self.root, kid, "The launch code word is PELICAN and it is only used on Fridays.", "Ops")
        a = agents.create(self.root, {"name": "K", "mode": "auto", "knowledge": [kid, "bad id"], "provider": {"type": "mock"}})
        self.assertEqual(a["knowledge"], [kid])
        self.assertIn("search_knowledge", jobs.tool_names(a))
        pid = projects.create(self.root, "P")["id"]
        rt = jobs.Runtime(self.root)
        script = Script([R(calls=[("search_knowledge", {"query": "launch code word"})]), R("done")])
        with mock.patch("agentlab.jobs.llm.chat", script):
            j = rt.start(pid, a["id"], "what is the launch code word?")
            import time
            end = time.time() + 10
            while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        self.assertIn("PELICAN", script.calls[0]["system"])
        self.assertIn("PELICAN", json.dumps(script.calls[1]["messages"]))


if __name__ == "__main__":
    unittest.main()
