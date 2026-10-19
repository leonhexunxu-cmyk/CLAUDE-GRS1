import copy
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agentlab import agents, jobs, llm, memory, projects, usage
from agentlab.recorder import load_run


def R(text="", calls=(), usage_=(10, 5), stop=None):
    """Build a scripted model reply."""
    tc = [{"id": f"c{i}", "name": n, "arguments": a} for i, (n, a) in enumerate(calls)]
    return {"text": text, "tool_calls": tc, "usage": {"input": usage_[0], "output": usage_[1]}, "stop": stop or ("tool" if tc else "end"), "model": "scripted"}


class Script:
    """Stands in for llm.chat: returns queued replies and records what the runtime sent."""

    def __init__(self, replies, repeat_last=False):
        self.replies, self.calls, self.repeat_last = list(replies), [], repeat_last

    def __call__(self, provider, system, messages, tools=None, **kw):
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tools": [t["name"] for t in tools or []], **kw})
        if isinstance(self.replies[0], Exception):
            raise self.replies.pop(0)
        return self.replies.pop(0) if len(self.replies) > 1 or not self.repeat_last else self.replies[0]


class JobCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "Demo", quota_mb=1)["id"]
        self.rt = jobs.Runtime(self.root, approval_timeout=10)
        self.p = None

    def agent(self, **kw):
        spec = {"name": "Bot", "mode": "auto", "provider": {"type": "mock"}, **kw}
        return agents.create(self.root, spec)["id"]

    def run_job(self, script, agent_id=None, prompt="do it", wait_for=jobs.FINAL, timeout=20, **kw):
        patcher = mock.patch("agentlab.jobs.llm.chat", script)
        patcher.start()
        self.addCleanup(patcher.stop)
        job = self.rt.start(self.proj, agent_id or self.agent(), prompt, **kw)
        return self.wait(job["id"], wait_for, timeout)

    def next_approval(self, job_id, old_id=None, timeout=20):
        """Wait for a pending approval that is not `old_id` (the job needs a moment to move on after a decision)."""
        end = time.time() + timeout
        while time.time() < end:
            j = self.rt.get(job_id)
            if j["status"] == "waiting_approval" and j["pending"] and j["pending"]["id"] != old_id:
                return j
            time.sleep(0.02)
        raise AssertionError("no new approval requested")

    def wait(self, job_id, statuses, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            j = self.rt.get(job_id)
            if j["status"] in statuses:
                return j
            time.sleep(0.02)
        raise AssertionError(f"job stuck in {self.rt.get(job_id)['status']}")


class AutonomyTests(JobCase):
    def test_full_auto_job_with_progress_metrics_usage_and_replayable_events(self):
        script = Script([R("starting", [("write_file", {"path": "index.html", "content": "<h1>hi</h1>"})]),
                         R(calls=[("run_command", {"command": "echo hello > out.txt && cat out.txt"})]),
                         R(calls=[("update_progress", {"percent": 60, "note": "almost"})]),
                         R(calls=[("finish", {"summary": "built it"})])])
        job = self.run_job(script)
        self.assertEqual((job["status"], job["summary"]), ("succeeded", "built it"))
        self.assertEqual(job["progress"]["percent"], 100)
        self.assertEqual(projects.read_file(self.root, self.proj, "index.html")["content"], "<h1>hi</h1>")
        self.assertEqual(projects.read_file(self.root, self.proj, "out.txt")["content"], "hello\n")
        m = job["metrics"]
        self.assertEqual((m["llm_calls"], m["tool_calls"], m["input_tokens"], m["output_tokens"], m["files_written"], m["tool_errors"]), (4, 4, 40, 20, 1, 0))
        self.assertGreaterEqual(m["duration_s"], 0)
        u = usage.summary(self.root)
        self.assertEqual((u["totals"]["calls"], u["totals"]["input"], u["totals"]["output"]), (4, 40, 20))
        events = load_run(Path(self.root) / ".agentlab" / "runs" / f"job-{job['id']}.jsonl")
        self.assertEqual(events[0]["meta"]["source"], "agent")
        self.assertEqual({e["kind"] for e in events}, {"start", "prompt", "output", "tool_call", "tool_result", "file_edit"})
        self.assertIn("exit 0\nhello", next(e for e in events if e["kind"] == "tool_result" and e["tool"] == "run_command")["content"])

    def test_plain_text_reply_finishes_a_simple_question(self):
        job = self.run_job(Script([R("The answer is 4.")]))
        self.assertEqual((job["status"], job["summary"]), ("succeeded", "The answer is 4."))

    def test_prompt_system_and_limits_reach_the_model(self):
        aid = self.agent(instructions="Always be polite.", preferences={"style": "concise", "language": "French"},
                         limits={"max_output_tokens": 333, "temperature": 0.3}, role="Helper")
        memory.add(self.root, aid, "The user's deploy target is Cloudflare")
        script = Script([R("ok")])
        self.run_job(script, aid, prompt="deploy to Cloudflare please")
        call = script.calls[0]
        for needle in ("You are Bot, Helper.", "Always be polite.", "Be brief", "Reply in French.", 'project "Demo"', "Cloudflare"):
            self.assertIn(needle, call["system"])
        self.assertEqual((call["max_tokens"], call["temperature"]), (333, 0.3))
        self.assertEqual(call["messages"], [{"role": "user", "content": "deploy to Cloudflare please"}])
        self.assertEqual(set(call["tools"]), {"list_files", "read_file", "write_file", "delete_file", "run_command", "web_fetch", "update_progress", "finish", "remember", "recall"})

    def test_memory_tools_round_trip(self):
        aid = self.agent()
        script = Script([R(calls=[("remember", {"fact": "Prefers tabs over spaces"})]), R(calls=[("recall", {"query": "tabs"})]), R("done")])
        self.run_job(script, aid)
        self.assertIn("Prefers tabs over spaces", script.calls[2]["messages"][-1]["content"])
        self.assertEqual(len(memory.list_facts(self.root, aid)), 1)

    def test_model_mistakes_become_tool_errors_not_crashes(self):
        script = Script([R(calls=[("write_file", {"path": "../escape.txt", "content": "x"}), ("read_file", {"path": "/etc/passwd"}), ("nope", {}),
                                   ("write_file", {"path": "big.txt", "content": "x" * 2_000_000}), ("read_file", {"path": "missing.txt"}),
                                   ("run_command", {"command": "echo ok"}), ("read_file", {"_invalid_json": "{oops"})]), R("recovered")])
        job = self.run_job(script)
        results = [m["content"] for m in script.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["metrics"]["tool_errors"], 6)
        self.assertTrue(all(r.startswith("ERROR") for r in results[:5] + results[6:]))
        self.assertIn("exit 0", results[5])
        self.assertIn("quota", results[3])
        self.assertFalse((Path(self.root) / "escape.txt").exists())

    def test_llm_failure_fails_the_job_and_is_logged(self):
        job = self.run_job(Script([llm.LLMError("401: bad key")]))
        self.assertEqual(job["status"], "failed")
        self.assertIn("401", job["error"])
        self.assertEqual(usage.summary(self.root)["totals"]["errors"], 1)

    def test_output_cut_off_by_the_token_limit_is_continued(self):
        script = Script([R("partial...", stop="length"), R("complete")])
        job = self.run_job(script)
        self.assertEqual(job["summary"], "complete")
        self.assertIn("cut off", script.calls[1]["messages"][-1]["content"])


class BudgetTests(JobCase):
    def test_token_budget_step_limit_and_daily_cap(self):
        job = self.run_job(Script([R(calls=[("list_files", {})], usage_=(600, 600))], repeat_last=True), self.agent(limits={"token_budget": 1000}))
        self.assertEqual((job["status"], job["stop_reason"]), ("stopped", "token budget reached"))
        self.assertEqual(job["metrics"]["llm_calls"], 1)
        job = self.run_job(Script([R(calls=[("list_files", {})])], repeat_last=True), self.agent(limits={"max_steps": 3}))
        self.assertEqual((job["status"], job["metrics"]["llm_calls"]), ("stopped", 3))
        self.assertIn("step limit", job["stop_reason"])
        aid = self.agent(limits={"daily_token_cap": 100})
        usage.record(self.root, agent=aid, provider="mock", model="m", input_tokens=90, output_tokens=20)
        job = self.run_job(Script([R("never reached")]), aid)
        self.assertEqual((job["status"], job["stop_reason"]), ("stopped", "daily token cap reached"))

    def test_progress_falls_back_to_step_fraction(self):
        script = Script([R(calls=[("list_files", {})])] * 3 + [R("done")])
        job = self.run_job(script, self.agent(limits={"max_steps": 10}))
        self.assertEqual(job["progress"]["percent"], 100)
        self.assertEqual(job["progress"]["max_steps"], 10)

    def test_cancel_kills_a_running_command(self):
        script = Script([R(calls=[("run_command", {"command": "sleep 30"})]), R("never")])
        patcher = mock.patch("agentlab.jobs.llm.chat", script)
        patcher.start()
        self.addCleanup(patcher.stop)
        job = self.rt.start(self.proj, self.agent(), "go")
        end = time.time() + 10
        while time.time() < end and not any(e["kind"] == "tool_call" for e in self.rt.get(job["id"], events=20).get("events", [])):
            time.sleep(0.05)
        time.sleep(0.3)
        t0 = time.time()
        self.rt.cancel(job["id"])
        self.assertEqual(self.wait(job["id"], jobs.FINAL, 10)["status"], "cancelled")
        self.assertLess(time.time() - t0, 5)
        with self.assertRaises(jobs.JobError):
            self.rt.cancel(job["id"])

    def test_concurrency_cap_queues_extra_jobs(self):
        rt = jobs.Runtime(self.root, max_concurrent=1)
        script = Script([R(calls=[("run_command", {"command": "sleep 1"})]), R("ok")], repeat_last=True)
        with mock.patch("agentlab.jobs.llm.chat", script):
            a = rt.start(self.proj, self.agent(), "one")
            time.sleep(0.4)
            b = rt.start(self.proj, self.agent(), "two")
            time.sleep(0.3)
            self.assertEqual((rt.get(a["id"])["status"], rt.get(b["id"])["status"]), ("running", "queued"))
            for j in (a, b):
                end = time.time() + 20
                while rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                    time.sleep(0.05)


class AuthorizationTests(JobCase):
    def test_ask_mode_pauses_then_deny_approve_and_always(self):
        aid = self.agent(mode="ask", authorizations={"read": "allow"})
        script = Script([R(calls=[("write_file", {"path": "a.txt", "content": "A"})]), R(calls=[("write_file", {"path": "b.txt", "content": "B"})]),
                         R(calls=[("write_file", {"path": "c.txt", "content": "C"})]), R(calls=[("finish", {"summary": "ok"})])])
        patcher = mock.patch("agentlab.jobs.llm.chat", script)
        patcher.start()
        self.addCleanup(patcher.stop)
        job = self.rt.start(self.proj, aid, "write three files")
        j = self.next_approval(job["id"])
        self.assertEqual((j["pending"]["tool"], j["pending"]["category"], j["pending"]["arguments"]["path"]), ("write_file", "write", "a.txt"))
        first = j["pending"]["id"]
        self.rt.decide(job["id"], first, False)
        j = self.next_approval(job["id"], first)
        self.assertEqual(j["pending"]["arguments"]["path"], "b.txt")
        self.rt.decide(job["id"], j["pending"]["id"], True, always=True)  # c.txt will not ask again
        j = self.wait(job["id"], jobs.FINAL)
        self.assertEqual(j["status"], "succeeded")
        self.assertEqual((j["metrics"]["approvals_asked"], j["metrics"]["approvals_denied"]), (2, 1))
        self.assertEqual(sorted(p for p in projects.tree(self.root, self.proj)), ["b.txt", "c.txt"])
        denial = next(m["content"] for m in script.calls[1]["messages"] if m["role"] == "tool")
        self.assertEqual(denial, "ERROR: denied by the user")
        kinds = [e["content"].split()[0] for e in self.rt.get(job["id"], events=50)["events"] if e["kind"] == "decision"]
        self.assertEqual(kinds, ["ASKED", "DENIED", "ASKED", "ALLOWED"])

    def test_approval_times_out_into_a_denial(self):
        rt = jobs.Runtime(self.root, approval_timeout=0.4)
        with mock.patch("agentlab.jobs.llm.chat", Script([R(calls=[("run_command", {"command": "echo hi"})]), R("moved on")])):
            job = rt.start(self.proj, self.agent(mode="ask_risky"), "go")
            end = time.time() + 10
            while rt.get(job["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.05)
        j = rt.get(job["id"])
        self.assertEqual((j["status"], j["metrics"]["approvals_denied"]), ("succeeded", 1))

    def test_deny_overrides_and_network_needs_its_own_permission(self):
        aid = self.agent(mode="auto", authorizations={"run": "deny"})
        script = Script([R(calls=[("run_command", {"command": "echo hi"})]), R("ok")])
        self.run_job(script, aid)
        self.assertIn("not permitted", script.calls[1]["messages"][-1]["content"])
        aid = self.agent(mode="auto", authorizations={"network": "deny"})
        script = Script([R(calls=[("run_command", {"command": "echo local"}), ("run_command", {"command": "echo net", "network": True})]), R("ok")])
        self.run_job(script, aid)
        tool = [m["content"] for m in script.calls[1]["messages"] if m["role"] == "tool"]
        self.assertTrue(tool[0].startswith("exit 0") and tool[1].startswith("ERROR: not permitted"))

    def test_job_level_overrides_and_mode_defaults(self):
        aid = self.agent(mode="ask_risky")
        self.assertEqual([agents.effective(agents.get(self.root, aid), c) for c in agents.CATEGORIES], ["allow", "allow", "ask", "ask"])
        script = Script([R(calls=[("run_command", {"command": "echo hi"})]), R("ok")])
        job = self.run_job(script, aid, overrides={"run": "allow"})  # unattended run, pre-authorised
        self.assertEqual(job["metrics"]["approvals_asked"], 0)

    def test_web_fetch_refuses_private_addresses(self):
        script = Script([R(calls=[("web_fetch", {"url": "http://127.0.0.1:1/"}), ("web_fetch", {"url": "file:///etc/passwd"}), ("web_fetch", {"url": "http://169.254.169.254/latest"})]), R("ok")])
        self.run_job(script)
        results = [m["content"] for m in script.calls[1]["messages"] if m["role"] == "tool"]
        self.assertTrue(all(r.startswith("ERROR") for r in results), results)
        self.assertIn("private", results[0])

    def test_web_fetch_reads_a_page_when_allowed(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<p>page body</p>")

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        with mock.patch.object(jobs, "ALLOW_PRIVATE_FETCH", True):
            script = Script([R(calls=[("web_fetch", {"url": f"http://127.0.0.1:{srv.server_port}/"})]), R("ok")])
            self.run_job(script)
        self.assertIn("page body", script.calls[1]["messages"][-1]["content"])


class StoreTests(JobCase):
    def test_validation_listing_and_restart_recovery(self):
        for bad in ({"prompt": ""}, {"prompt": "x" * 20001}):
            with self.assertRaises(jobs.JobError):
                self.rt.start(self.proj, self.agent(), **bad)
        with self.assertRaises(projects.ProjectError):
            self.rt.start("nope-0000", self.agent(), "x")
        with self.assertRaises(agents.AgentError):
            self.rt.start(self.proj, "ghost-0000", "x")
        with self.assertRaises(jobs.JobError):
            self.rt.decide("abc", "x", True)
        with self.assertRaises(jobs.JobError):
            self.rt.get("../x")
        j = self.run_job(Script([R("hi")]))
        self.assertEqual([x["id"] for x in self.rt.list(project_id=self.proj)], [j["id"]])
        self.assertEqual(self.rt.list(status="failed"), [])
        stuck = dict(j, id="deadbeef01", status="running", ended=None)
        self.rt._save(stuck)
        fresh = jobs.Runtime(self.root)
        self.assertEqual((fresh.get("deadbeef01")["status"], "server stopped" in fresh.get("deadbeef01")["error"]), ("failed", True))


if __name__ == "__main__":
    unittest.main()
