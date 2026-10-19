import tempfile
import threading
import time
import unittest
from unittest import mock

from agentlab import agents, batches, jobs, projects

try:
    from tests.test_jobs import R
except ImportError:
    from test_jobs import R


def done(text="done"):
    return R(text)


class LimiterTests(unittest.TestCase):
    def test_resize_releases_waiters(self):
        lim = jobs.Limiter(1)
        got = []
        lim.__enter__()
        t = threading.Thread(target=lambda: (lim.__enter__(), got.append(1)))
        t.start()
        time.sleep(0.05)
        self.assertEqual(got, [])
        lim.set_limit(2)
        t.join(2)
        self.assertEqual(got, [1])
        with self.assertRaises(jobs.JobError):
            lim.set_limit(0)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "Base")["id"]
        projects.write_file(self.root, self.proj, "a.txt", "hello")
        self.a = agents.create(self.root, {"name": "A", "mode": "auto", "provider": {"type": "mock"}})["id"]
        self.b = agents.create(self.root, {"name": "B", "mode": "auto", "provider": {"type": "mock"}})["id"]
        self.rt = jobs.Runtime(self.root, max_concurrent=2)

    def wait(self, bid):
        end = time.time() + 20
        while time.time() < end:
            st = batches.status(self.root, self.rt, bid)
            if st["finished"]:
                return st
            time.sleep(0.03)
        self.fail("batch did not finish")

    def test_compare_isolated_copies(self):
        with mock.patch("agentlab.jobs.llm.chat", side_effect=lambda *a, **k: done()):
            st = batches.start(self.root, self.rt, {"project": self.proj, "kind": "compare", "agents": [self.a, self.b], "prompt": "go"})
            st = self.wait(st["id"])
        self.assertEqual([r["status"] for r in st["items"]], ["succeeded", "succeeded"])
        pids = {r["project"] for r in st["items"]}
        self.assertEqual(len(pids), 2)
        self.assertNotIn(self.proj, pids)
        for pid in pids:
            self.assertEqual(projects.read_file(self.root, pid, "a.txt")["content"], "hello")
            self.assertEqual(projects.get(self.root, pid)["cloned_from"], self.proj)
        self.assertEqual(st["progress"]["percent"], 100)
        self.assertIsNotNone(st["fastest"])

    def test_fanout_same_project_and_errors(self):
        with mock.patch("agentlab.jobs.llm.chat", side_effect=lambda *a, **k: done()):
            st = batches.start(self.root, self.rt, {"project": self.proj, "kind": "fanout", "agent": self.a, "prompts": ["one", "two", "three"]})
            st = self.wait(st["id"])
        self.assertEqual({r["project"] for r in st["items"]}, {self.proj})
        self.assertEqual(len(batches.list_batches(self.root, self.rt)), 1)
        for spec in ({"project": self.proj, "kind": "compare", "agents": [self.a], "prompt": "x"}, {"project": self.proj, "kind": "x"},
                     {"project": self.proj, "kind": "fanout", "agent": self.a, "prompts": []}):
            with self.assertRaises(batches.BatchError):
                batches.start(self.root, self.rt, spec)
        with mock.patch("agentlab.jobs.llm.chat", side_effect=lambda *a, **k: done()):
            st = batches.start(self.root, self.rt, {"project": self.proj, "kind": "compare", "agents": [self.a, "nope"], "prompt": "x", "isolate": False})
            self.assertEqual(st["items"][1]["status"], "failed")
            self.wait(st["id"])

    def test_concurrency_info(self):
        self.assertEqual(self.rt.concurrency(3)["limit"], 3)


if __name__ == "__main__":
    unittest.main()
