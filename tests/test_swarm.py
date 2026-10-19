import tempfile
import time
import unittest
from unittest import mock

from agentlab import agents, jobs, projects, usage

try:
    from tests.test_jobs import R, Script
except ImportError:
    from test_jobs import R, Script


class Router:
    """Routes by model name: the worker gets a script, the teammate answers once."""

    def __init__(self, worker, answer="Use a 3:1 contrast ratio and 16px minimum text."):
        self.worker, self.answer, self.asked = list(worker), answer, []

    def __call__(self, provider, system, msgs, tools=None, **kw):
        if provider["model"] == "mate":
            self.asked.append((system, msgs[-1]["content"], tools))
            return {"text": self.answer, "tool_calls": [], "usage": {"input": 30, "output": 12}, "stop": "end", "model": "mate"}
        return self.worker.pop(0)


class SwarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "P")["id"]
        self.mate = agents.create(self.root, {"name": "Mia", "role": "accessibility reviewer", "provider": {"type": "mock", "model": "mate"}})["id"]
        self.dev = agents.create(self.root, {"name": "Leo", "mode": "auto", "teammates": [self.mate], "provider": {"type": "mock", "model": "dev"}})["id"]
        self.rt = jobs.Runtime(self.root)

    def wait(self, jid, timeout=10):
        end = time.time() + timeout
        while self.rt.get(jid)["status"] not in jobs.FINAL and time.time() < end:
            time.sleep(0.02)
        return self.rt.get(jid, events=40)

    def test_agent_asks_a_teammate_and_gets_the_answer(self):
        r = Router([R(calls=[("ask_teammate", {"teammate": "Mia", "question": "What contrast do we need?"})]), R("done")])
        with mock.patch("agentlab.jobs.llm.chat", r):
            j = self.wait(self.rt.start(self.proj, self.dev, "make the page accessible")["id"])
        self.assertEqual(j["status"], "succeeded")
        self.assertEqual(len(r.asked), 1)
        system, question, tools = r.asked[0]
        self.assertIn("Mia", system)
        self.assertIn("asking you for help", system)
        self.assertIsNone(tools)  # a teammate never gets tools: no nesting, no runaway swarm
        self.assertIn("contrast", question)
        self.assertEqual(j["metrics"]["teammate_calls"], 1)
        self.assertGreaterEqual(j["metrics"]["input_tokens"], 30)  # the teammate's tokens count against this job
        self.assertEqual({k["key"]: k["calls"] for k in usage.summary(self.root, 1)["by_kind"]}["swarm"], 1)

    def test_unknown_teammate_and_no_teammates(self):
        r = Router([R(calls=[("ask_teammate", {"teammate": "Nobody", "question": "hi"})]), R("done")])
        with mock.patch("agentlab.jobs.llm.chat", r):
            j = self.wait(self.rt.start(self.proj, self.dev, "go")["id"])
        said = " ".join(e["content"] for e in j["events"] if e["kind"] == "tool_result")
        self.assertIn("Mia", said)
        self.assertIn("No teammate", said)
        plain = agents.create(self.root, {"name": "Solo", "provider": {"type": "mock"}})
        self.assertNotIn("ask_teammate", jobs.tool_names(plain))


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "P")["id"]
        self.agent = agents.create(self.root, {"name": "A", "mode": "auto", "provider": {"type": "mock"}})["id"]
        self.rt = jobs.Runtime(self.root)

    def test_agent_is_told_its_time_budget(self):
        script = Script([R("done")])
        with mock.patch("agentlab.jobs.llm.chat", script):
            j = self.rt.start(self.proj, self.agent, "go", deadline=time.time() + 25 * 60)
            end = time.time() + 10
            while self.rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        self.assertIn("deadline", script.calls[0]["system"])
        self.assertIn("24 minutes", script.calls[0]["system"])

    def test_passed_deadline_stops_the_job(self):
        script = Script([R("never")], repeat_last=True)
        with mock.patch("agentlab.jobs.llm.chat", script):
            j = self.rt.start(self.proj, self.agent, "go", deadline=time.time() - 1)
            end = time.time() + 10
            while self.rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        j = self.rt.get(j["id"])
        self.assertEqual((j["status"], j["stop_reason"]), ("stopped", "deadline reached"))
        self.assertEqual(script.calls, [])


if __name__ == "__main__":
    unittest.main()
