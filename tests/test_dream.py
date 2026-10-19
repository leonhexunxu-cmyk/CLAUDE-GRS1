import json
import tempfile
import unittest
from datetime import datetime, timezone

from agentlab import agents, diary, dream, llm, memory, notifications, usage

REAL = {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m", "price_in": 1, "price_out": 1}


def ts(date, hhmm, tz=0):
    d = datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return d.timestamp() - tz * 60


def reply(obj, tokens=(200, 100)):
    return {"text": "```json\n" + json.dumps(obj) + "\n```", "tool_calls": [], "usage": {"input": tokens[0], "output": tokens[1]}, "stop": "end", "model": "m"}


class DreamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def agent(self, **kw):
        return agents.create(self.root, {"name": "Luna", "provider": REAL, "dream": {"enabled": True, "start": "23:00", "end": "06:00"}, **kw})["id"]

    def seed(self, aid, n=3):
        for i in range(n):
            diary.add_entry(self.root, aid, title=f"job {i}", body="did things", lessons=["Check paths before reading files"], outcome="failed" if i == 0 else "succeeded")
        memory.add(self.root, aid, "The user prefers short answers")


class WindowTests(unittest.TestCase):
    def test_window_inside_outside_and_across_midnight(self):
        cfg = {"start": "23:00", "end": "06:00", "tz_minutes": 0}
        self.assertEqual(dream.window_night(cfg, ts("2026-03-10", "23:30")), "2026-03-10")  # before midnight: tonight
        self.assertEqual(dream.window_night(cfg, ts("2026-03-11", "02:00")), "2026-03-10")  # after midnight: the night that began yesterday
        self.assertIsNone(dream.window_night(cfg, ts("2026-03-11", "06:00")))
        self.assertIsNone(dream.window_night(cfg, ts("2026-03-11", "12:00")))
        day = {"start": "13:00", "end": "15:00", "tz_minutes": 0}
        self.assertEqual(dream.window_night(day, ts("2026-03-10", "14:59")), "2026-03-10")
        self.assertIsNone(dream.window_night(day, ts("2026-03-10", "15:00")))
        self.assertIsNone(dream.window_night({"start": "01:00", "end": "01:00", "tz_minutes": 0}, ts("2026-03-10", "01:00")))
        tz = {"start": "23:00", "end": "06:00", "tz_minutes": 120}  # UTC+2: 23:30 local is 21:30 UTC
        self.assertEqual(dream.window_night(tz, ts("2026-03-10", "23:30", tz=120)), "2026-03-10")
        self.assertEqual(dream.window_night(tz, ts("2026-03-10", "23:30", tz=0)), "2026-03-10")  # 23:30 UTC is 01:30 local the next day: still that night
        self.assertIsNone(dream.window_night(tz, ts("2026-03-10", "08:00", tz=0)))  # 10:00 local: awake

    def test_validation(self):
        for bad in ({"start": "25:00"}, {"end": "6am"}, {"token_budget": 10}, {"cycles": 9}, {"tz_minutes": 9999}, {"token_budget": "lots"}):
            with self.assertRaises(agents.AgentError, msg=bad):
                agents.validate({"name": "x", "dream": bad})
        self.assertEqual(agents.validate({"name": "x"})["dream"]["enabled"], False)  # off unless you turn it on


class DreamTests(DreamCase):
    def test_dreaming_reflects_updates_memory_and_logs_without_tools(self):
        aid = self.agent()
        self.seed(aid)
        seen = []

        def chat(provider, system, messages, tools=None, **kw):
            seen.append({"tools": tools, "system": system, "max_tokens": kw["max_tokens"], "user": json.loads(messages[0]["content"])})
            n = len(seen)
            return reply({"themes": ["paths"], "insights": [f"insight {n}"], "ideas": [f"idea {n}"], "worries": ["flaky tests"], "plan_for_tomorrow": ["retry job 0"],
                          "memory_updates": [{"fact": "Always list files before reading", "importance": 4}]})
        e = dream.dream(self.root, aid, ts("2026-03-11", "02:00"), chat=chat)
        self.assertEqual((e["night"], e["source"], e["tokens"]), ("2026-03-10", "llm", 600))  # two cycles of 300 tokens
        self.assertEqual((e["insights"], e["ideas"], e["plan_for_tomorrow"], e["memory_added"]), (["insight 1", "insight 2"], ["idea 1", "idea 2"], ["retry job 0"], 1))
        self.assertTrue(all(s["tools"] is None for s in seen))  # a dream never gets tools
        self.assertIn("no tools", seen[0]["system"])
        self.assertEqual(seen[1]["user"]["so_far"]["insights"], ["insight 1"])  # the second pass builds on the first
        self.assertEqual(seen[0]["user"]["diary"][0]["lessons"], ["Check paths before reading files"])
        mem = {f["fact"]: f for f in memory.list_facts(self.root, aid)}
        self.assertEqual((mem["Always list files before reading"]["source"], mem["Always list files before reading"]["importance"]), ("dream", 4))
        self.assertEqual(dream.journal(self.root, aid)[0]["id"], e["id"])
        k = {x["key"]: x for x in usage.summary(self.root, 30, ts("2026-03-11", "03:00"))["by_kind"]}
        self.assertEqual((k["dream"]["calls"], k["dream"]["input"]), (2, 400))
        note = notifications.list_notifications(self.root)[0]
        self.assertIn("Luna dreamed", note["title"])
        self.assertEqual(note["link"], {"page": "agents", "id": aid})

    def test_only_inside_the_window_only_once_a_night_only_if_enabled(self):
        aid = self.agent()
        self.seed(aid)
        calls = []
        chat = lambda *a, **k: (calls.append(1), reply({"insights": ["x"], "ideas": ["y"]}))[1]
        self.assertIsNone(dream.dream(self.root, aid, ts("2026-03-11", "12:00"), chat=chat))  # daytime
        self.assertIsNotNone(dream.dream(self.root, aid, ts("2026-03-11", "01:00"), chat=chat))
        self.assertIsNone(dream.dream(self.root, aid, ts("2026-03-11", "03:00"), chat=chat))  # already dreamed tonight
        self.assertIsNotNone(dream.dream(self.root, aid, ts("2026-03-12", "01:00"), chat=chat))  # a new night
        self.assertEqual(len(dream.journal(self.root, aid)), 2)
        off = self.agent(dream={"enabled": False, "start": "23:00", "end": "06:00"})
        self.assertIsNone(dream.dream(self.root, off, ts("2026-03-11", "01:00"), chat=chat))
        self.assertIsNotNone(dream.dream(self.root, off, ts("2026-03-11", "12:00"), force=True, chat=chat))  # 'dream now'

    def test_token_budget_and_early_stop_and_daily_cap(self):
        aid = self.agent(dream={"enabled": True, "start": "23:00", "end": "06:00", "token_budget": 1000, "cycles": 5})
        self.seed(aid)
        calls = []
        def chat(*a, **k):
            calls.append(k["max_tokens"])
            return reply({"insights": [f"i{len(calls)}"]}, tokens=(500, 200))
        e = dream.dream(self.root, aid, ts("2026-03-11", "01:00"), chat=chat)
        self.assertEqual((len(calls), e["tokens"]), (1, 700))  # 300 left < 800 minimum: stop
        self.assertLessEqual(calls[0], 1000)
        capped = self.agent(limits={"daily_token_cap": 100})
        self.seed(capped)
        usage.record(self.root, agent=capped, provider="x", model="m", input_tokens=90, output_tokens=20, ts=ts("2026-03-11", "00:30"))
        calls.clear()
        e = dream.dream(self.root, capped, ts("2026-03-11", "01:00"), chat=chat)
        self.assertEqual((calls, e["source"]), ([], "rule"))  # the agent's own daily cap is respected
        stale = self.agent()
        self.seed(stale)
        e = dream.dream(self.root, stale, ts("2026-03-11", "01:00"), chat=lambda *a, **k: reply({"insights": []}))
        self.assertEqual(e["source"], "llm")  # nothing new: it stopped after one pass

    def test_failures_fall_back_to_rules_and_demo_models_dream_for_free(self):
        aid = self.agent()
        self.seed(aid)
        def boom(*a, **k):
            raise llm.LLMError("down")
        e = dream.dream(self.root, aid, ts("2026-03-11", "01:00"), chat=boom)
        self.assertEqual((e["source"], e["tokens"]), ("rule", 0))
        self.assertTrue(any("paths" in t for t in e["themes"]))
        self.assertTrue(any("job 0" in w for w in e["worries"]))
        mock_agent = self.agent(provider={"type": "mock"})
        self.seed(mock_agent)
        self.assertEqual(dream.dream(self.root, mock_agent, ts("2026-03-11", "01:00"), chat=boom)["source"], "rule")
        junk = self.agent()
        self.seed(junk)
        e = dream.dream(self.root, junk, ts("2026-03-11", "01:00"), chat=lambda *a, **k: {"text": "I dreamt of sheep", "tool_calls": [], "usage": {"input": 5, "output": 5}, "stop": "end", "model": "m"})
        self.assertEqual(e["source"], "rule")

    def test_consolidation_merges_near_duplicates_only(self):
        aid = self.agent(provider={"type": "mock"})
        for f, imp in (("The deploy target is Cloudflare Pages", 3), ("deploy target is Cloudflare pages", 1), ("User likes tabs over spaces", 2), ("Totally different fact about databases", 1)):
            memory.add(self.root, aid, f, importance=imp)
        self.assertEqual(dream.consolidate(self.root, aid), 1)
        left = [f["fact"] for f in memory.list_facts(self.root, aid)]
        self.assertIn("The deploy target is Cloudflare Pages", left)  # the more important wording survives
        self.assertEqual(len(left), 3)

    def test_dreamer_ticks_each_enabled_agent_once_and_proposes_evolution_but_never_applies_it(self):
        aid = self.agent(evolve="suggest", instructions="Base.")
        self.seed(aid, 3)
        off = self.agent(dream={"enabled": False})
        events = []
        d = dream.Dreamer(self.root, on_event=lambda *a: events.append(a))
        import agentlab.dream as dm
        orig = dm.llm.chat
        dm.llm.chat = lambda *a, **k: reply({"insights": ["i"], "ideas": ["x"]})
        try:
            self.assertEqual(d.tick(ts("2026-03-11", "01:00")), [aid])
            self.assertEqual(d.tick(ts("2026-03-11", "02:00")), [])
        finally:
            dm.llm.chat = orig
        self.assertEqual(events[0][0], "dream_finished")
        from agentlab import evolution
        self.assertEqual([p["status"] for p in evolution.proposals(self.root, aid)], ["pending"])
        self.assertEqual(agents.get(self.root, aid)["version"], 1)  # dreaming never rewrites the agent
        self.assertEqual(dream.journal(self.root, off), [])


if __name__ == "__main__":
    unittest.main()
