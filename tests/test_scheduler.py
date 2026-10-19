import tempfile
import unittest
from datetime import datetime, timezone

from agentlab import agents, notifications, projects, scheduler, suggestions


class FakeRuntime:
    def __init__(self):
        self.jobs, self.started, self.fail_start = {}, [], False

    def start(self, project, agent, prompt, **kw):
        if self.fail_start:
            raise RuntimeError("no")
        jid = f"j{len(self.started)}"
        self.jobs[jid] = {"id": jid, "status": "running"}
        self.started.append((jid, kw))
        return self.jobs[jid]

    def get(self, jid):
        return self.jobs[jid]


class CronTests(unittest.TestCase):
    def test_parse(self):
        m, h, *_ = scheduler.parse_cron("*/15 9-17 * * 1-5")
        self.assertEqual(m, {0, 15, 30, 45})
        self.assertEqual(h, set(range(9, 18)))
        self.assertEqual(scheduler.parse_cron("0 0 * * 7")[4], {0})
        self.assertEqual(scheduler.parse_cron("0 0 * * 5-7")[4], {5, 6, 0})
        self.assertEqual(scheduler.parse_cron("5/20 * * * *")[0], {5, 25, 45})
        for bad in ("* * *", "61 * * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *"):
            with self.assertRaises(scheduler.ScheduleError):
                scheduler.parse_cron(bad)

    def test_next_cron(self):
        base = datetime(2026, 10, 19, 8, 59, tzinfo=timezone.utc).timestamp()  # a Monday
        nxt = datetime.fromtimestamp(scheduler.next_cron("30 9 * * 1-5", base), timezone.utc)
        self.assertEqual((nxt.hour, nxt.minute, nxt.day), (9, 30, 19))
        sat = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(datetime.fromtimestamp(scheduler.next_cron("0 9 * * 1-5", sat), timezone.utc).day, 26)
        # with a +2h offset, 9:00 local is 7:00 UTC
        self.assertEqual(datetime.fromtimestamp(scheduler.next_cron("0 9 * * *", base, 120), timezone.utc).hour, 7)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "P")["id"]
        self.agent = agents.create(self.root, {"name": "A", "provider": {"type": "mock"}})["id"]
        self.now = [1_000_000.0]
        self.rt = FakeRuntime()
        self.events = []
        self.s = scheduler.Scheduler(self.root, self.rt, on_event=lambda *a: self.events.append(a), clock=lambda: self.now[0])

    def make(self, **kw):
        return self.s.create({"project": self.proj, "agent": self.agent, "prompt": "check", "every_minutes": 10, **kw})

    def test_validation(self):
        for spec in ({}, {"prompt": "x", "project": self.proj, "agent": self.agent},
                     {"prompt": "x", "project": self.proj, "agent": self.agent, "every_minutes": 5, "cron": "* * * * *"},
                     {"prompt": "x", "project": self.proj, "agent": self.agent, "every_minutes": 0}):
            with self.assertRaises(Exception):
                self.s.create(spec)

    def test_runs_when_due_without_overlap_or_catch_up(self):
        rec = self.make(overrides={"run": "allow", "bogus": "allow"})
        self.assertEqual(rec["overrides"], {"run": "allow"})
        self.assertEqual(self.s.tick(self.now[0]), [])
        self.now[0] += 601
        self.assertEqual(len(self.s.tick()), 1)
        self.assertTrue(self.rt.started[0][1]["autonomous"])
        self.now[0] += 601
        self.assertEqual(self.s.tick(), [])  # previous run still going
        self.assertIn("still going", self.s.get(rec["id"])["note"])
        self.rt.jobs["j0"]["status"] = "succeeded"
        self.now[0] += 10 * 3600  # long downtime: one run, not sixty
        self.assertEqual(len(self.s.tick()), 1)
        self.assertGreater(self.s.get(rec["id"])["next_run"], self.now[0])

    def test_pauses_after_repeated_failures(self):
        rec = self.make()
        for _ in range(scheduler.MAX_FAILURES + 1):
            self.now[0] += 601
            self.s.tick()
            for j in self.rt.jobs.values():
                j["status"] = "failed"
        got = self.s.get(rec["id"])
        self.assertFalse(got["enabled"])
        self.assertEqual(self.events[0][0], "schedule_paused")
        self.assertTrue(any(x["key"] == f"sched:{rec['id']}" for x in suggestions.compute(self.root)))
        self.s.update(rec["id"], enabled=True)
        self.assertEqual(self.s.get(rec["id"])["failures"], 0)

    def test_max_runs_and_delete(self):
        rec = self.make(max_runs=1)
        self.now[0] += 601
        self.s.tick()
        self.assertFalse(self.s.get(rec["id"])["enabled"])
        self.s.delete(rec["id"])
        with self.assertRaises(scheduler.ScheduleError):
            self.s.get(rec["id"])


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_feed(self):
        a = notifications.emit(self.root, "job", "Done", level="success")
        notifications.emit(self.root, "approval", "Needs you", dedupe_key="k")
        notifications.emit(self.root, "approval", "Needs you again", dedupe_key="k")
        self.assertEqual(notifications.unread_count(self.root), 2)
        self.assertEqual(notifications.list_notifications(self.root)[0]["title"], "Needs you again")
        self.assertEqual(notifications.mark_read(self.root, [a["id"]]), 1)
        self.assertEqual(len(notifications.list_notifications(self.root, unread_only=True)), 1)
        with self.assertRaises(ValueError):
            notifications.emit(self.root, "nope", "x")
        notifications.clear(self.root)
        self.assertEqual(notifications.unread_count(self.root), 0)

    def test_suggestions_dismiss(self):
        projects.create(self.root, "P")
        keys = [s["key"] for s in suggestions.compute(self.root)]
        self.assertIn("first-agent", keys)
        suggestions.dismiss(self.root, "first-agent")
        self.assertNotIn("first-agent", [s["key"] for s in suggestions.compute(self.root)])


if __name__ == "__main__":
    unittest.main()
