import tempfile
import time
import unittest
from unittest import mock

from agentlab import agents, jobs, projects

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script


class DebuggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "D")["id"]
        self.agent = agents.create(self.root, {"name": "Dbg", "mode": "auto", "provider": {"type": "mock"}})["id"]
        self.rt = jobs.Runtime(self.root)

    def wait_for(self, jid, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            j = self.rt.get(jid)
            if pred(j):
                return j
            time.sleep(0.02)
        self.fail(f"timed out; status {self.rt.get(jid)['status']}")

    def paused_at(self, jid, at):
        return self.wait_for(jid, lambda j: j["status"] == "paused" and j["pending"]["at"] == at)

    def test_trace_without_step_mode(self):
        calls = [("write_file", {"path": "a.txt", "content": "x"})]
        with mock.patch("agentlab.jobs.llm.chat", Script([R(calls=calls), R("done")])):
            j = self.rt.start(self.proj, self.agent, "go")
            self.wait_for(j["id"], lambda j: j["status"] in jobs.FINAL)
        t = self.rt.trace(j["id"])
        self.assertEqual([e["phase"] for e in t], ["model", "tool", "model"])
        self.assertEqual(t[1]["tool"], "write_file")
        self.assertEqual(t[0]["tool_calls"][0]["name"], "write_file")

    def test_step_edit_skip_and_continue(self):
        calls = [("write_file", {"path": "a.txt", "content": "original"})]
        calls2 = [("write_file", {"path": "b.txt", "content": "b"})]
        with mock.patch("agentlab.jobs.llm.chat", Script([R(calls=calls), R(calls=calls2), R("done")])):
            j = self.rt.start(self.proj, self.agent, "go", step_mode=True)
            self.paused_at(j["id"], "model")
            self.rt.resume(j["id"], "step")
            p = self.paused_at(j["id"], "tool")
            self.assertEqual(p["pending"]["arguments"]["content"], "original")
            with self.assertRaises(jobs.JobError):
                self.rt.resume(j["id"], "skip", arguments="nope")
            self.rt.resume(j["id"], "step", arguments={"path": "a.txt", "content": "edited"})
            self.paused_at(j["id"], "model")
            self.rt.resume(j["id"], "step")
            self.paused_at(j["id"], "tool")
            self.rt.resume(j["id"], "skip")
            self.paused_at(j["id"], "model")
            self.rt.resume(j["id"], "continue")
            j = self.wait_for(j["id"], lambda j: j["status"] in jobs.FINAL)
        self.assertEqual(j["status"], "succeeded")
        self.assertEqual(projects.read_file(self.root, self.proj, "a.txt")["content"], "edited")
        with self.assertRaises(projects.ProjectError):
            projects.read_file(self.root, self.proj, "b.txt")
        t = self.rt.trace(j["id"])
        self.assertTrue(t[1]["edited"])
        self.assertTrue(t[3]["skipped"])
        self.assertGreaterEqual(j["debug"]["breakpoints"], 5)

    def test_toggle_live_and_cancel_while_paused(self):
        with mock.patch("agentlab.jobs.llm.chat", Script([R(calls=[("list_files", {})]), R("done")])):
            j = self.rt.start(self.proj, self.agent, "go", step_mode=True)
            self.paused_at(j["id"], "model")
            self.rt.set_step_mode(j["id"], False)
            self.wait_for(j["id"], lambda j: j["status"] in jobs.FINAL)
        with mock.patch("agentlab.jobs.llm.chat", Script([R("done")])):
            j = self.rt.start(self.proj, self.agent, "go", step_mode=True)
            self.paused_at(j["id"], "model")
            self.rt.cancel(j["id"])
            j = self.wait_for(j["id"], lambda j: j["status"] in jobs.FINAL)
        self.assertEqual(j["status"], "cancelled")
        with self.assertRaises(jobs.JobError):
            self.rt.resume(j["id"])


if __name__ == "__main__":
    unittest.main()
