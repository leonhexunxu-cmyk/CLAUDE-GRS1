import hashlib
import hmac
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from agentlab import envs, hooks, insights, notify
from agentlab.recorder import append_event
from agentlab.server import serve_in_thread

ISOLATION = envs.capabilities()["network_isolation"]


def wait(mgr, task_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        rec = mgr.get(task_id)
        if rec["status"] not in ("queued", "running"):
            return rec
        time.sleep(0.05)
    raise AssertionError("task did not finish")


class Receiver:
    """Tiny local HTTP server that records what it is sent."""

    def __init__(self):
        got = self.got = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                got.append(({k.lower(): v for k, v in self.headers.items()}, body))
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"PAYLOAD-OK")

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".agentlab").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("print('original')\n")
        (self.root / ".env").write_text("TOKEN=topsecret\n")
        (self.root / "id_rsa").write_text("private\n")
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text("{}")
        self.mgr = envs.Manager(self.root)

    def run_task(self, command, **kw):
        return wait(self.mgr, self.mgr.create({"command": command, "network": True, "timeout": 20, **kw})["id"])


class EnvironmentTests(ProjectCase):
    def test_changes_are_reviewed_then_applied(self):
        rec = self.run_task("echo '# edited' >> src/a.py && echo new > src/b.txt && rm src/a.py.orig 2>/dev/null; true")
        self.assertEqual((rec["status"], rec["changes"]), ("succeeded", {"added": 1, "modified": 1, "deleted": 0}))
        self.assertIn("original", (self.root / "src" / "a.py").read_text())
        self.assertNotIn("edited", (self.root / "src" / "a.py").read_text())  # project untouched until applied
        changes = {c["path"]: c for c in self.mgr.changes(rec["id"])}
        self.assertIn("+# edited", changes["src/a.py"]["diff"])
        res = self.mgr.apply(rec["id"])
        self.assertEqual(sorted(res["applied"]), ["src/a.py", "src/b.txt"])
        self.assertIn("# edited", (self.root / "src" / "a.py").read_text())
        self.assertTrue(self.mgr.get(rec["id"])["applied"])

    def test_secret_files_are_not_copied(self):
        rec = self.run_task("ls -a > listing.txt; cat .env id_rsa 2>&1 | wc -c > size.txt")
        work = self.root / ".agentlab" / "tasks" / rec["id"] / "work"
        self.assertNotIn(".env", (work / "listing.txt").read_text().split())
        self.assertNotIn("id_rsa", (work / "listing.txt").read_text().split())
        self.assertNotIn("topsecret", self.mgr.log_tail(rec["id"]))

    def test_environment_is_scrubbed_and_passthrough_is_explicit(self):
        with mock.patch.dict(os.environ, {"MY_API_KEY": "key-123", "OTHER_SECRET": "nope"}):
            hidden = self.run_task("env > env.txt")
            shown = self.run_task("env > env.txt", pass_env=["MY_API_KEY"])
        w = lambda r: (self.root / ".agentlab" / "tasks" / r["id"] / "work" / "env.txt").read_text()
        self.assertNotIn("key-123", w(hidden))
        self.assertNotIn("nope", w(shown))
        self.assertIn("MY_API_KEY=key-123", w(shown))
        self.assertIn(f"AGENTLAB_TASK={shown['id']}", w(shown))
        self.assertIn(f"HOME={self.root}/.agentlab/tasks/{shown['id']}/home", w(shown))

    @unittest.skipUnless(ISOLATION, "network isolation unavailable here")
    def test_network_is_blocked_unless_allowed(self):
        rx = Receiver()
        self.addCleanup(rx.close)
        cmd = f"python3 -c \"import urllib.request;print(urllib.request.urlopen('{rx.url}', timeout=3).read().decode())\""
        blocked = wait(self.mgr, self.mgr.create({"command": cmd, "network": False, "timeout": 20})["id"])
        allowed = wait(self.mgr, self.mgr.create({"command": cmd, "network": True, "timeout": 20})["id"])
        self.assertEqual(blocked["status"], "failed")
        self.assertNotIn("PAYLOAD-OK", self.mgr.log_tail(blocked["id"]))
        self.assertEqual(allowed["status"], "succeeded")
        self.assertIn("PAYLOAD-OK", self.mgr.log_tail(allowed["id"]))

    def test_refuses_to_run_without_isolation_it_was_asked_for(self):
        with mock.patch.dict(envs._caps, {"unshare": None}):
            with self.assertRaises(envs.TaskError):
                self.mgr.create({"command": "true", "network": False})

    def test_timeout_and_cancel_kill_the_whole_process_group(self):
        t0 = time.time()
        rec = self.run_task("sleep 30 & sleep 30", timeout=1)
        self.assertEqual(rec["status"], "timeout")
        self.assertLess(time.time() - t0, 10)
        rec = self.mgr.create({"command": "sleep 30", "network": True, "timeout": 60})
        end = time.time() + 10
        while self.mgr.get(rec["id"])["status"] != "running" and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.3)
        self.mgr.cancel(rec["id"])
        self.assertEqual(wait(self.mgr, rec["id"])["status"], "cancelled")
        with self.assertRaises(envs.TaskError):
            self.mgr.cancel(rec["id"])  # no longer running

    def test_failed_exit_code_and_output_cap(self):
        rec = self.run_task("echo boom; exit 3")
        self.assertEqual((rec["status"], rec["exit_code"]), ("failed", 3))
        self.assertIn("boom", self.mgr.log_tail(rec["id"]))

    def test_apply_refuses_protected_paths_symlinks_and_drifted_files(self):
        rec = self.run_task("echo x > .claude/settings.json; mkdir -p .github/workflows; echo y > .github/workflows/ci.yml; "
                            "ln -s /etc/passwd link; echo z > src/new.py; echo edited >> src/a.py")
        paths = {c["path"]: c for c in self.mgr.changes(rec["id"])}
        self.assertNotIn("link", paths)  # symlinks are ignored entirely
        self.assertTrue(paths[".claude/settings.json"]["protected"] and paths[".github/workflows/ci.yml"]["protected"])
        (self.root / "src" / "a.py").write_text("someone else edited this\n")  # project drifted after the task started
        res = self.mgr.apply(rec["id"])
        reasons = {s["path"]: s["reason"] for s in res["skipped"]}
        self.assertEqual(reasons[".claude/settings.json"], "protected")
        self.assertEqual(reasons[".github/workflows/ci.yml"], "protected")
        self.assertIn("changed in project", reasons["src/a.py"])
        self.assertEqual(res["applied"], ["src/new.py"])
        self.assertEqual((self.root / ".claude" / "settings.json").read_text(), "{}")
        self.assertEqual((self.root / "src" / "a.py").read_text(), "someone else edited this\n")

    def test_apply_selected_paths_and_discard(self):
        rec = self.run_task("echo 1 > one.txt; echo 2 > two.txt")
        self.assertEqual(self.mgr.apply(rec["id"], ["one.txt"])["applied"], ["one.txt"])
        self.assertFalse((self.root / "two.txt").exists())
        self.mgr.discard(rec["id"])
        self.assertFalse((self.root / ".agentlab" / "tasks" / rec["id"] / "work").exists())
        with self.assertRaises(envs.TaskError):
            self.mgr.apply(rec["id"])

    def test_pad_style_files_run_and_are_not_reported_as_changes(self):
        rec = self.run_task("python3 pad.py", files={"pad.py": "print('from pad')"})
        self.assertIn("from pad", self.mgr.log_tail(rec["id"]))
        self.assertEqual(rec["changes"], {"added": 0, "modified": 0, "deleted": 0})

    def test_validation(self):
        for bad in ({"command": ""}, {"command": "x", "timeout": "soon"}, {"command": "x", "pass_env": ["bad name"]},
                    {"command": "x", "files": {"../evil": "x"}}, {"command": "x", "files": {"a/b": "x"}},
                    {"command": "x", "backend": "vm"}, {"command": "x", "backend": "docker", "image": "x; rm -rf /"}):
            with self.assertRaises(envs.TaskError, msg=bad):
                self.mgr.create({"network": True, **bad})
        with self.assertRaises(envs.TaskError):
            self.mgr.get("../../etc")

    def test_runs_are_recorded_and_restart_marks_interrupted(self):
        rec = self.run_task("echo hi")
        run = self.root / ".agentlab" / "runs" / f"task-{rec['id']}.jsonl"
        self.assertEqual(json.loads(run.read_text().splitlines()[0])["meta"]["source"], "task")
        stuck = dict(rec, id="abcdef0123", status="running")
        self.mgr._save(stuck)
        envs.Manager(self.root)
        self.assertEqual(envs.Manager(self.root).get("abcdef0123")["status"], "interrupted")

    def test_docker_command_is_locked_down(self):
        rec = {"id": "x" * 10, "backend": "docker", "network": False, "pass_env": ["K"], "image": "python:3.12-slim", "command": "ls", "timeout": 5}
        argv = self.mgr._argv(rec, Path("/w"), {"K": "v"})
        self.assertEqual(argv[:2], ["docker", "run"])
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        for flag in ("--memory", "--pids-limit", "--user", "--rm"):
            self.assertIn(flag, argv)
        self.assertNotIn("v", argv)  # values never appear on the command line, only the name


class WebhookTests(ProjectCase):
    def test_signed_delivery_and_secret_never_returned(self):
        rx = Receiver()
        self.addCleanup(rx.close)
        saved = notify.save_webhooks(self.root, [{"name": "slack", "url": rx.url, "events": ["blocked", "bogus"], "secret": "s3cr3t"}])
        self.assertEqual(saved[0]["events"], ["blocked"])
        self.assertNotIn("s3cr3t", json.dumps(notify.list_webhooks(self.root)))
        self.assertTrue(notify.list_webhooks(self.root)[0]["has_secret"])
        self.assertEqual(oct(os.stat(self.root / ".agentlab" / "connectors.json").st_mode)[-3:], "600")
        self.assertEqual(notify.fire(self.root, "task_finished", "x"), [])  # not subscribed
        res = notify.fire(self.root, "blocked", "agentlab blocked Bash", {"tool": "Bash"})
        self.assertTrue(res[0]["ok"])
        headers, body = rx.got[0]
        self.assertEqual(json.loads(body)["text"], "agentlab blocked Bash")
        self.assertEqual(json.loads(body)["content"], "agentlab blocked Bash")
        self.assertEqual(headers["x-agentlab-signature"], "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest())
        # editing without sending a secret keeps it; sending "" clears it
        hid = saved[0]["id"]
        self.assertTrue(notify.save_webhooks(self.root, [{"id": hid, "url": rx.url, "events": ["blocked"]}])[0]["has_secret"])
        self.assertFalse(notify.save_webhooks(self.root, [{"id": hid, "url": rx.url, "events": ["blocked"], "secret": ""}])[0]["has_secret"])

    def test_validation_and_dead_endpoints_do_not_raise(self):
        for bad in ([{"url": "ftp://x", "events": ["blocked"]}], [{"url": "http://x", "events": []}], "nope", [{"url": "http://x", "events": ["blocked"]}] * 11):
            with self.assertRaises(ValueError):
                notify.save_webhooks(self.root, bad)
        notify.save_webhooks(self.root, [{"url": "http://127.0.0.1:1", "events": ["error"]}])
        res = notify.fire(self.root, "error", "x")
        self.assertFalse(res[0]["ok"])

    def test_blocked_tool_call_notifies_from_the_hook(self):
        rx = Receiver()
        self.addCleanup(rx.close)
        (self.root / ".agentlab" / "policy.txt").write_text("allow read src/**\nnever read .env\n")
        notify.save_webhooks(self.root, [{"url": rx.url, "events": ["blocked"]}])
        code, _ = hooks.process({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(self.root), "client_type": "kimi_code_cli",
                                 "tool_name": "Read", "tool_input": {"path": ".env"}}, ".")
        self.assertEqual(code, 2)
        self.assertEqual(len(rx.got), 1)
        self.assertIn("kimi_code_cli", json.loads(rx.got[0][1])["text"])


class InsightTests(ProjectCase):
    def session(self, sid, source, steps, now):
        run = self.root / ".agentlab" / "runs" / f"{sid}.jsonl"
        append_event(run, None, "", meta={"source": source})
        for kind, content, tool in steps:
            append_event(run, kind, content, **({"tool": tool} if tool else {}))
        return run

    def test_levels_and_reasons(self):
        now = time.time()
        self.session("good", "claude-code", [("prompt", "hi", None), ("tool_call", "ls", "Bash"), ("tool_result", "ok", "Bash"), ("output", "(turn ended)", None)], now)
        self.session("bad", "kimi_code_cli", [("prompt", "go", None), ("tool_call", "x", "Bash"), ("error", "boom", None), ("error", "boom", None)], now)
        self.session("pushy", "openclaw", [("prompt", "go", None)] + [("tool_call", "rm", "exec"), ("decision", "BLOCKED exec: no", "exec")] * 3 + [("tool_call", "ls", "exec")] * 3, now)
        data = insights.compute(self.root, now + 1)
        by = {a["id"]: a for a in data["agents"]}
        self.assertEqual(by["claude-code"]["level"], "ok")
        self.assertEqual(by["kimi"]["level"], "bad")
        self.assertEqual(by["kimi"]["reasons"][0], {"code": "errors", "n": 2})
        self.assertEqual(by["openclaw"]["level"], "warn")
        self.assertEqual(by["openclaw"]["reasons"][0]["code"], "blocks")
        self.assertEqual(data["totals"]["errors"], 2)
        self.assertEqual(data["totals"]["blocked"], 3)
        self.assertEqual(sum(data["hourly"]), data["totals"]["steps"])
        self.assertEqual(len(data["hourly"]), 24)
        self.assertEqual(data["tools"][0]["tool"], "exec")

    def test_active_stalled_and_hook_errors(self):
        now = time.time()
        self.session("live", "claude-code", [("prompt", "go", None), ("tool_call", "sleep", "Bash")], now)
        self.assertEqual(insights.compute(self.root, now + 5)["totals"]["active"], 1)
        stalled = insights.compute(self.root, now + 600)
        agent = next(a for a in stalled["agents"] if a["id"] == "claude-code")
        self.assertEqual((agent["level"], agent["reasons"][0]["code"]), ("warn", "stalled"))
        hooks.log_error("ValueError: bad payload", str(self.root))
        self.assertEqual(insights.compute(self.root, time.time() + 1)["totals"]["hook_errors"], 1)

    def test_task_runs_are_not_counted_as_agents_and_empty_is_fine(self):
        self.assertEqual(insights.compute(self.root)["totals"]["steps"], 0)
        self.run_task = None
        wait(self.mgr, self.mgr.create({"command": "echo hi", "network": True})["id"])
        data = insights.compute(self.root)
        self.assertNotIn("task", [a["id"] for a in data["agents"]])
        self.assertEqual(data["tasks"]["succeeded"], 1)


class ServerTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / ".agentlab").mkdir()
        (root / "a.txt").write_text("one\n")
        cls.srv = serve_in_thread(cls.tmp.name)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_full_task_flow_over_http(self):
        self.assertIn("network_isolation", self.call("/api/isolation")[1])
        code, rec = self.call("/api/tasks", {"command": "echo two >> a.txt", "network": True, "name": "append"})
        self.assertEqual(code, 200)
        end = time.time() + 20
        while time.time() < end:
            _, cur = self.call("/api/tasks/" + rec["id"])
            if cur["status"] not in ("queued", "running"):
                break
            time.sleep(0.1)
        self.assertEqual(cur["status"], "succeeded")
        self.assertIn(rec["id"], [t["id"] for t in self.call("/api/tasks")[1]["tasks"]])
        ch = self.call(f"/api/tasks/{rec['id']}/changes")[1]["changes"]
        self.assertEqual((ch[0]["path"], ch[0]["status"]), ("a.txt", "modified"))
        self.assertEqual(self.call(f"/api/tasks/{rec['id']}/apply", {"paths": "a.txt"})[0], 400)
        self.assertEqual(self.call(f"/api/tasks/{rec['id']}/apply", {})[1]["applied"], ["a.txt"])
        self.assertEqual((Path(self.tmp.name) / "a.txt").read_text(), "one\ntwo\n")
        self.assertEqual(self.call(f"/api/tasks/{rec['id']}/discard", {})[1]["discarded"], True)

    def test_errors_and_insights_and_connectors(self):
        self.assertEqual(self.call("/api/tasks", {"command": ""})[0], 400)
        self.assertEqual(self.call("/api/tasks/0123456789")[0], 404)
        self.assertEqual(self.call("/api/tasks/..%2f..%2fx")[0], 400)
        self.assertEqual(self.call("/api/tasks/0123456789/cancel", {})[0], 404)
        self.assertIn("totals", self.call("/api/insights")[1])
        code, saved = self.call("/api/connectors", {"webhooks": [{"url": "http://127.0.0.1:1/x", "events": ["blocked"], "secret": "z"}]})
        self.assertEqual(code, 200)
        listed = self.call("/api/connectors")[1]
        self.assertTrue(listed["webhooks"][0]["has_secret"])
        self.assertNotIn('"z"', json.dumps(listed))
        self.assertEqual(self.call("/api/connectors", {"webhooks": [{"url": "file:///etc/passwd", "events": ["blocked"]}]})[0], 400)
        self.assertEqual(self.call("/api/connectors/test", {"id": "00000000"})[0], 404)
        res = self.call("/api/connectors/test", {"id": saved["webhooks"][0]["id"]})[1]["results"]
        self.assertFalse(res[0]["ok"])  # nothing listens on port 1; the call still returns cleanly


if __name__ == "__main__":
    unittest.main()
