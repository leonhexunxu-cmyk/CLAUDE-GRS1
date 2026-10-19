import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agentlab import accounts, importers, projects, vault

GH_TOKEN, HF_TOKEN = "ghp_validtoken1234", "hf_validtoken1234"


class Server:
    """Local HTTP server driven by a function (method, path, headers) -> (status, headers, body | iterator)."""

    def __init__(self, fn):
        self.log = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                outer.log.append((self.path, hdrs))
                status, headers, body = fn(self.path, hdrs)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if isinstance(body, bytes):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    for chunk in ([body] if isinstance(body, bytes) else body):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def J(obj, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(obj).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        clean = {e: "" for e in vault.ENV_FALLBACK.values()}
        env = mock.patch.dict(os.environ, clean)
        env.start()
        self.addCleanup(env.stop)
        self.proj = projects.create(self.root, "Imports", quota_mb=5)["id"]
        self.imp = importers.Importer(self.root)

    def serve(self, fn):
        s = Server(fn)
        self.addCleanup(s.close)
        return s

    def wait(self, import_id, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            r = self.imp.get(import_id)
            if r["status"] in importers.FINAL:
                return r
            time.sleep(0.03)
        raise AssertionError("import stuck")


class AccountTests(Base):
    def hub(self):
        def fn(path, h):
            auth = h.get("authorization", "")
            if path.startswith("/user/repos"):
                return J([{"full_name": "octo/app", "description": "mine", "private": True, "stargazers_count": 3, "default_branch": "main", "updated_at": "2026-01-01"}]) if auth == f"Bearer {GH_TOKEN}" else J({}, 401)
            if path.startswith("/user"):
                return J({"login": "octo", "name": "Octo Cat"}) if auth == f"Bearer {GH_TOKEN}" else J({"message": "Bad credentials"}, 401)
            if path.startswith("/search/repositories"):
                return J({"items": [{"full_name": "a/hello", "description": None, "stargazers_count": 9, "default_branch": "master"}]})
            if path.startswith("/api/whoami-v2"):
                return J({"name": "hfuser", "fullname": "HF User", "type": "user"}) if auth == f"Bearer {HF_TOKEN}" else J({}, 401)
            if path.startswith("/api/models?") or path.startswith("/api/datasets?"):
                return J([{"id": "org/tiny", "downloads": 5, "likes": 1, "pipeline_tag": "text-generation", "tags": list("abcdefgh"), "gated": False}])
            return J({}, 404)
        s = self.serve(fn)
        for attr in ("GITHUB_API", "HF_API"):
            p = mock.patch.object(accounts, attr, s.url)
            p.start()
            self.addCleanup(p.stop)
        return s

    def test_connect_verify_store_and_disconnect(self):
        self.hub()
        with self.assertRaises(accounts.AccountError) as cm:
            accounts.connect(self.root, "github", "ghp_wrong")
        self.assertEqual(cm.exception.status, 401)
        self.assertFalse(accounts.status(self.root)["github"]["connected"])  # a rejected token is never stored
        r = accounts.connect(self.root, "github", GH_TOKEN)
        self.assertEqual((r["name"], r["display"]), ("octo", "Octo Cat"))
        st = accounts.status(self.root)
        self.assertEqual((st["github"]["connected"], st["github"]["identity"]["name"], st["github"]["hint"]), (True, "octo", "…1234"))
        self.assertNotIn(GH_TOKEN, json.dumps(st))
        self.assertEqual(vault.get_secret(self.root, "github_token"), GH_TOKEN)
        self.assertEqual(accounts.connect(self.root, "huggingface", HF_TOKEN)["name"], "hfuser")
        self.assertEqual(accounts.github_repos(self.root)[0]["full_name"], "octo/app")
        accounts.disconnect(self.root, "github")
        self.assertFalse(accounts.status(self.root)["github"]["connected"])
        with self.assertRaises(accounts.AccountError):
            accounts.github_repos(self.root)  # listing your own repos needs a token
        self.assertEqual(accounts.github_repos(self.root, "hello")[0], {"full_name": "a/hello", "description": "", "private": False, "stars": 9, "default_branch": "master", "updated": ""})
        for bad in (("gitlab", "x"), ("github", "  ")):
            with self.assertRaises(accounts.AccountError):
                accounts.connect(self.root, *bad)

    def test_hf_search_and_unreachable(self):
        self.hub()
        r = accounts.hf_search(self.root, "models", "tiny")
        self.assertEqual((r[0]["id"], r[0]["task"], len(r[0]["tags"])), ("org/tiny", "text-generation", 6))
        with self.assertRaises(accounts.AccountError):
            accounts.hf_search(self.root, "spaces", "x")
        with mock.patch.object(accounts, "HF_API", "http://127.0.0.1:1"):
            with self.assertRaises(accounts.AccountError):
                accounts.hf_search(self.root, "models", "x")


class HuggingFaceImportTests(Base):
    CONTENT = {"config.json": b'{"model_type": "tiny"}', "README.md": b"# readme", "weights.gguf": b"GGUF" + b"\x00" * 3000, "sub/tok.json": b"{}"}

    def tree(self, extra=()):
        out = [{"type": "file", "path": p, "size": len(b), **({"lfs": {"oid": hashlib.sha256(b).hexdigest()}} if p.endswith(".gguf") else {})} for p, b in self.CONTENT.items()]
        return out + [{"type": "directory", "path": "sub"}] + list(extra)

    def hf(self, gated=False, tree=None, corrupt=False, slow=False):
        cdn = self.serve(lambda path, h: (200, {}, self.CONTENT["weights.gguf"] if not corrupt else b"GGUF" + b"\x01" * 3000))
        self.cdn = cdn

        def fn(path, h):
            authed = h.get("authorization") == f"Bearer {HF_TOKEN}"
            if gated and not authed:
                return J({"error": "gated"}, 401)
            if "/tree/" in path:
                return J(tree if tree is not None else self.tree())
            if path.endswith("/resolve/main/weights.gguf"):
                return 302, {"Location": cdn.url + "/blob"}, b""  # cross-host redirect, like Hugging Face's CDN
            for rel, body in self.CONTENT.items():
                if path.endswith("/resolve/main/" + rel.replace("/", "/")) or path.endswith("/resolve/main/" + rel):
                    if slow:
                        return 200, {"Content-Length": str(len(body) * 200)}, (body for _ in range(200) if time.sleep(0.02) is None)
                    return 200, {}, body
            return J({}, 404)
        s = self.serve(fn)
        p = mock.patch.object(accounts, "HF_API", s.url)
        p.start()
        self.addCleanup(p.stop)
        self.hf_server = s
        return s

    def test_downloads_selected_files_with_progress_and_checksums(self):
        self.hf()
        rec = self.imp.start_hf(self.proj, "models", "org/tiny", files=["config.json", "weights.gguf"])
        self.assertEqual((rec["progress"]["total"], rec["progress"]["files_total"], rec["dest"]), (len(self.CONTENT["config.json"]) + len(self.CONTENT["weights.gguf"]), 2, "tiny"))
        r = self.wait(rec["id"])
        self.assertEqual((r["status"], r["progress"]["files_done"], r["progress"]["done"]), ("succeeded", 2, r["progress"]["total"]))
        self.assertEqual(projects.read_file(self.root, self.proj, "tiny/config.json")["content"], '{"model_type": "tiny"}')
        self.assertEqual((Path(projects.workspace(self.root, self.proj)) / "tiny" / "weights.gguf").read_bytes()[:4], b"GGUF")
        self.assertEqual([f for f in projects.tree(self.root, self.proj) if f.endswith(".part")], [])

    def test_default_patterns_and_pattern_selection(self):
        self.hf()
        r = self.wait(self.imp.start_hf(self.proj, "models", "org/tiny")["id"])
        self.assertEqual(sorted(projects.tree(self.root, self.proj)), ["tiny/README.md", "tiny/config.json", "tiny/sub/tok.json"])  # weights are opt-in
        r = self.wait(self.imp.start_hf(self.proj, "models", "org/tiny", patterns=["*.gguf"], dest="w")["id"])
        self.assertIn("w/weights.gguf", projects.tree(self.root, self.proj))

    def test_token_is_sent_to_the_hub_but_never_to_another_host(self):
        self.hf(gated=True)
        with self.assertRaises(importers.ImportFailed) as cm:
            self.imp.start_hf(self.proj, "models", "org/tiny", files=["weights.gguf"])
        self.assertEqual(cm.exception.code, 401)
        self.assertIn("connect Hugging Face", str(cm.exception))
        vault.set_secret(self.root, "hf_token", HF_TOKEN)
        r = self.wait(self.imp.start_hf(self.proj, "models", "org/tiny", files=["weights.gguf"])["id"])
        self.assertEqual(r["status"], "succeeded")
        self.assertTrue(any(h.get("authorization") == f"Bearer {HF_TOKEN}" for _, h in self.hf_server.log))
        self.assertTrue(self.cdn.log and all("authorization" not in h for _, h in self.cdn.log))

    def test_corrupt_download_fails_and_cleans_up(self):
        self.hf(corrupt=True)
        r = self.wait(self.imp.start_hf(self.proj, "models", "org/tiny", files=["weights.gguf"])["id"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("checksum mismatch", r["error"])
        self.assertEqual([f for f in projects.tree(self.root, self.proj)], [])

    def test_limits_and_unsafe_repositories_are_refused_up_front(self):
        big = [{"type": "file", "path": "huge.bin", "size": 50_000_000}]
        self.hf(tree=big)
        with self.assertRaises(importers.ImportFailed) as cm:
            self.imp.start_hf(self.proj, "models", "org/tiny", files=["huge.bin"])
        self.assertEqual(cm.exception.code, 413)  # bigger than the project's free space
        with self.assertRaises(importers.ImportFailed):
            self.imp.start_hf(self.proj, "models", "org/tiny", files=["huge.bin"], max_bytes=10)
        self.hf(tree=[{"type": "file", "path": "../../evil.txt", "size": 1}, {"type": "file", "path": "ok.json", "size": 1}])
        with self.assertRaises(importers.ImportFailed) as cm:
            self.imp.start_hf(self.proj, "models", "org/tiny", files=["../../evil.txt"])
        self.assertIn("unsafe", str(cm.exception))
        for bad in (("models", "../etc", "main"), ("models", "org/x", "../main"), ("spaces", "org/x", "main"), ("models", "a b", "main")):
            with self.assertRaises(importers.ImportFailed, msg=bad):
                importers.hf_tree(self.root, *bad)
        with self.assertRaises(importers.ImportFailed):
            self.imp.start_hf(self.proj, "models", "org/tiny", files=["not-there.bin"])

    def test_cancel_stops_a_slow_download_and_removes_partials(self):
        self.hf(slow=True)
        rec = self.imp.start_hf(self.proj, "models", "org/tiny", files=["config.json"])
        end = time.time() + 10
        while self.imp.get(rec["id"])["progress"]["done"] == 0 and time.time() < end:
            time.sleep(0.02)
        self.imp.cancel(rec["id"])
        r = self.wait(rec["id"])
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(projects.tree(self.root, self.proj), [])
        with self.assertRaises(importers.ImportFailed):
            self.imp.cancel(rec["id"])

    def test_datasets_use_the_datasets_prefix_and_restart_recovery(self):
        seen = []

        def fn(path, h):
            seen.append(path)
            if "/tree/" in path:
                return J([{"type": "file", "path": "train.csv", "size": 4}])
            return 200, {}, b"a,b\n"
        s = self.serve(fn)
        with mock.patch.object(accounts, "HF_API", s.url):
            r = self.wait(self.imp.start_hf(self.proj, "datasets", "org/ds", files=["train.csv"])["id"])
        self.assertEqual(r["status"], "succeeded")
        self.assertTrue(any(p.startswith("/datasets/org/ds/resolve/main/train.csv") for p in seen))
        self.assertTrue(any(p.startswith("/api/datasets/org/ds/tree/main") for p in seen))
        stuck = dict(r, id="feedbeef01", status="running", ended=None)
        self.imp._save(stuck)
        self.assertEqual(importers.Importer(self.root).get("feedbeef01")["status"], "failed")


class GitHubCloneTests(Base):
    def make_repo(self, files=None):
        repo = Path(self.root) / "origin"
        repo.mkdir()
        for name, text in (files or {"README.md": "# hello", "src/app.py": "print(1)\n"}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        git = lambda *a: subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "init.defaultBranch=main", *a], cwd=repo, check=True, capture_output=True)
        git("init")
        git("add", "-A")
        git("commit", "-m", "first")
        return f"file://{repo}"

    def setUp(self):
        super().setUp()
        p = mock.patch.object(importers, "ALLOW_FILE_URLS", True)
        p.start()
        self.addCleanup(p.stop)

    def test_clone_into_the_workspace_with_progress(self):
        url = self.make_repo()
        rec = self.imp.start_github(self.proj, "octo/hello-app", clone_url=url)
        self.assertEqual((rec["source"], rec["dest"]), ("octo/hello-app", "hello-app"))
        r = self.wait(rec["id"])
        self.assertEqual(r["status"], "succeeded", r["error"])
        self.assertEqual(projects.read_file(self.root, self.proj, "hello-app/src/app.py")["content"], "print(1)\n")
        self.assertTrue((Path(projects.workspace(self.root, self.proj)) / "hello-app" / ".git").is_dir())
        self.assertGreater(r["progress"]["files"] if "files" in r["progress"] else r["progress"]["files_done"], 0)
        with self.assertRaises(importers.ImportFailed):
            self.imp.start_github(self.proj, "octo/hello-app", clone_url=url)  # destination already exists

    def test_token_uses_askpass_and_leaves_no_trace(self):
        vault.set_secret(self.root, "github_token", "ghp_secrettoken1234")
        before = {p for p in os.listdir(tempfile.gettempdir()) if p.startswith("agentlab-askpass-")}
        r = self.wait(self.imp.start_github(self.proj, "octo/x", clone_url=self.make_repo())["id"])
        self.assertEqual(r["status"], "succeeded")
        self.assertEqual({p for p in os.listdir(tempfile.gettempdir()) if p.startswith("agentlab-askpass-")}, before)  # helper script deleted
        config = (Path(projects.workspace(self.root, self.proj)) / "x" / ".git" / "config").read_text()
        self.assertNotIn("ghp_secrettoken1234", config)

    def test_failures_clean_up_and_inputs_are_validated(self):
        r = self.wait(self.imp.start_github(self.proj, "octo/missing", clone_url="file:///nonexistent/repo")["id"])
        self.assertEqual(r["status"], "failed")
        self.assertFalse((Path(projects.workspace(self.root, self.proj)) / "missing").exists())
        big = self.make_repo({"blob.bin": "x" * 6_000_000})  # larger than the 5 MB project quota
        shutil_target = Path(self.root) / "origin"
        r = self.wait(self.imp.start_github(self.proj, "octo/big", clone_url=big)["id"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("quota", r["error"])
        self.assertFalse((Path(projects.workspace(self.root, self.proj)) / "big").exists())
        for bad in ({"repo": "https://evil.example/a/b"}, {"repo": "a/b", "ref": "--upload-pack=x"}, {"repo": "a/b", "ref": "../x"}, {"repo": "a/b", "dest": "../out"}, {"repo": "a/b", "dest": "x/../../out"}):
            with self.assertRaises((importers.ImportFailed, projects.ProjectError), msg=bad):
                self.imp.start_github(self.proj, **bad)


if __name__ == "__main__":
    unittest.main()
