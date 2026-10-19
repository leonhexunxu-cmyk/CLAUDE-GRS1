import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentlab import apigen, apikeys, pads
from agentlab.server import serve_in_thread

UI = Path(__file__).resolve().parent.parent / "agentlab" / "ui"


class Base(unittest.TestCase):
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


class PadTests(Base):
    def test_crud_and_validation(self):
        _, pad = self.call("/api/pads", {"name": "Scratch", "language": "javascript", "content": "1+1"})
        self.assertRegex(pad["id"], "^[a-f0-9]{8}$")
        _, upd = self.call("/api/pads", {"id": pad["id"], "name": "Renamed", "language": "python", "content": "x"})
        self.assertEqual((upd["id"], upd["language"]), (pad["id"], "python"))
        self.assertEqual(self.call("/api/pads/" + pad["id"])[1]["content"], "x")
        self.assertIn(pad["id"], [p["id"] for p in self.call("/api/pads")[1]["pads"]])
        self.assertEqual(self.call("/api/pads", {"name": "x", "language": "cobol", "content": ""})[0], 400)
        self.assertEqual(self.call("/api/pads", {"id": "deadbeef", "name": "x", "language": "text", "content": ""})[0], 400)
        self.assertEqual(self.call("/api/pads/..%2f..%2fx")[0], 404)
        self.assertEqual(self.call(f"/api/pads/{pad['id']}/delete", {})[0], 200)
        self.assertEqual(self.call("/api/pads/" + pad["id"])[0], 404)

    def test_module_rejects_oversize(self):
        with self.assertRaises(ValueError):
            pads.save(self.tmp.name, None, "big", "text", "x" * (pads.MAX_CONTENT + 1))


class KeyAuthTests(unittest.TestCase):
    def test_keys_gate_the_api(self):
        with tempfile.TemporaryDirectory() as d:
            srv = serve_in_thread(d)
            base = f"http://127.0.0.1:{srv.server_port}"
            self.addCleanup(srv.shutdown)

            def call(path, body=None, headers=None):
                req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json", **(headers or {})})
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())

            self.assertEqual(call("/api/runs")[0], 200)  # open until a key exists
            code, made = call("/api/keys", {"label": "ci"})
            self.assertEqual(code, 200)
            key = made["key"]
            self.assertTrue(key.startswith("alab_"))
            self.assertNotIn("hash", made)
            self.assertNotIn(key, (Path(d) / ".agentlab" / "keys.json").read_text())  # only a hash is stored
            self.assertEqual(call("/api/runs")[0], 401)
            self.assertEqual(call("/api/runs", headers={"Authorization": "Bearer nope"})[0], 401)
            self.assertEqual(call("/api/runs", headers={"Authorization": "Bearer " + key})[0], 200)
            self.assertEqual(call("/api/health")[0], 200)
            with urllib.request.urlopen(base + "/") as r:  # the UI gets a token injected
                token = r.read().decode().split('name="agentlab-token" content="')[1].split('"')[0]
            self.assertEqual(call("/api/runs", headers={"X-AgentLab-UI": token})[0], 200)
            self.assertEqual(call(f"/api/keys/{made['id']}/revoke", {}, {"Authorization": "Bearer " + key})[0], 200)
            self.assertEqual(call("/api/runs")[0], 200)  # no keys left -> open again

    def test_module(self):
        with tempfile.TemporaryDirectory() as d:
            rec, key = apikeys.create(d, "a")
            self.assertTrue(apikeys.verify(d, key))
            self.assertFalse(apikeys.verify(d, key + "x"))
            self.assertFalse(apikeys.verify(d, None))
            self.assertTrue(apikeys.revoke(d, rec["id"]))
            self.assertFalse(apikeys.revoke(d, rec["id"]))


class ApigenTests(Base):
    def test_every_spec_route_is_served(self):
        for e in apigen.SPEC:
            if e["name"] == "create_key":  # creating a key would lock this shared server; covered in KeyAuthTests
                continue
            path = e["path"].replace("{run_id}", "abc").replace("{pad_id}", "deadbeef").replace("{key_id}", "deadbeef")
            code, body = self.call(path, {} if e["method"] == "POST" else None)
            self.assertNotEqual(body.get("error"), "not found", f"{e['method']} {e['path']} is not routed")

    def test_openapi_matches_spec(self):
        spec = self.call("/api/openapi.json")[1]
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("post", spec["paths"]["/api/runs/{run_id}/fork"])
        self.assertEqual(spec["paths"]["/api/tripwires/plant"]["post"]["requestBody"]["content"]["application/json"]["schema"]["required"],
                         ["seed", "document"])

    def test_generated_python_client_really_works(self):
        code = self.call("/api/generate", {"language": "python", "base_url": self.base})[1]["code"]
        ns = {}
        exec(compile(code, "client.py", "exec"), ns)
        c = ns["AgentLab"](self.base)
        self.assertTrue(c.health()["ok"])
        planted = c.plant_canaries("seed", "a\n\nb", n=1)
        self.assertEqual(len(planted["questions"]), 1)
        self.assertTrue(c.audit_answer("seed", "I do not know", n=1)["healthy"])
        c.ingest_event("gen-run", "prompt", "hello")
        self.assertEqual(c.get_run("gen-run")["events"][1]["content"], "hello")
        with self.assertRaises(RuntimeError):
            c.get_run("missing-run")

    def test_other_languages_are_syntactically_valid(self):
        with tempfile.TemporaryDirectory() as d:
            if shutil.which("node"):
                f = Path(d) / "c.mjs"
                f.write_text(apigen.generate("javascript"))
                self.assertEqual(subprocess.run(["node", "--check", str(f)], capture_output=True).returncode, 0)
                # run it against the live server
                script = Path(d) / "run.mjs"
                script.write_text(f'import {{ AgentLab }} from "./c.mjs";\nconst c = new AgentLab("{self.base}");\n'
                                  'const r = await c.health(); if (!r.ok) process.exit(1);\n'
                                  'const p = await c.plantCanaries({ seed: "s", document: "x", n: 1 }); if (p.questions.length !== 1) process.exit(2);\n')
                self.assertEqual(subprocess.run(["node", str(script)], capture_output=True, text=True).returncode, 0)
            sh = Path(d) / "c.sh"
            sh.write_text(apigen.generate("curl"))
            self.assertEqual(subprocess.run(["bash", "-n", str(sh)], capture_output=True).returncode, 0)
        ts = apigen.generate("typescript")
        self.assertIn("forkRun(runId: string, body: { step: number })", ts)

    def test_generator_rejects_bad_input(self):
        self.assertEqual(self.call("/api/generate", {"language": "cobol"})[0], 400)
        self.assertEqual(self.call("/api/generate", {"language": "python", "base_url": 'http://x"; import os #'})[0], 400)
        self.assertEqual(self.call("/api/generate", {"language": "python", "base_url": "javascript:alert(1)"})[0], 400)


class StaticTests(Base):
    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.headers, r.read()

    def test_assets_and_csp(self):
        for name in ("style.css", "app.js", "i18n.json", "runner.html"):
            self.assertEqual(self.get("/static/" + name)[0], 200)
        _, headers, _ = self.get("/static/runner.html")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self'", self.get("/")[1]["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline'; frame", self.get("/")[1]["Content-Security-Policy"].split("script-src")[1][:20])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/static/../server.py")
        self.assertEqual(cm.exception.code, 404)


class I18nTests(unittest.TestCase):
    def test_all_languages_complete_and_consistent(self):
        data = json.loads((UI / "i18n.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data), 6)
        en = data["en"]
        for lang, strings in data.items():
            self.assertEqual(set(strings), set(en), f"{lang} keys differ from en")
            for key, val in strings.items():
                self.assertTrue(val.strip(), f"{lang}.{key} is empty")
                self.assertEqual(sorted(__import__("re").findall(r"\{\w+\}", val)),
                                 sorted(__import__("re").findall(r"\{\w+\}", en[key])), f"{lang}.{key} placeholders")

    def test_every_key_used_by_the_app_exists(self):
        import re
        en = json.loads((UI / "i18n.json").read_text(encoding="utf-8"))["en"]
        js = (UI / "app.js").read_text(encoding="utf-8")
        used = set(re.findall(r"""\bt\("([a-z_]+)\"""", js)) | set(re.findall(r'"(tab_\w+|\w+_title|\w+_sub)"', js))
        used |= {f"k_{k}" for k in ("prompt", "tool_call", "tool_result", "file_edit", "decision", "output", "error")}
        used |= {f"st_{s}" for s in ("fail", "drift", "missing", "new", "ok")} | {f"s_{s}" for s in ("correct", "contradicted", "unsupported", "unused")}
        used = {k for k in used if not k.endswith("_")}  # dynamic prefixes are expanded explicitly above
        self.assertEqual(sorted(used - set(en)), [])


if __name__ == "__main__":
    unittest.main()
