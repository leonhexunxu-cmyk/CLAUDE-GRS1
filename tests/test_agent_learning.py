import json
import tempfile
import time
import unittest
from unittest import mock

from agentlab import agent_stats, agents, diary, evolution, jobs, llm, memory, projects, usage

try:
    from tests.test_jobs import R, Script
except ImportError:  # run via discover -s tests
    from test_jobs import R, Script

REAL = {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m", "price_in": 1, "price_out": 1}


class Router:
    """One stand-in for llm.chat: job turns (which pass tools) go to the script, reflection calls (no tools) to `other`."""

    def __init__(self, script, other):
        self.script, self.other = script, other

    def __call__(self, provider, system, messages, tools=None, **kw):
        if tools is None:
            if isinstance(self.other, Exception):
                raise self.other
            return self.other
        return self.script(provider, system, messages, tools, **kw)


def fenced(obj):
    return {"text": "Here you go:\n```json\n" + json.dumps(obj) + "\n```", "tool_calls": [], "usage": {"input": 100, "output": 50}, "stop": "end", "model": "m"}


class LearnCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.proj = projects.create(self.root, "Demo")["id"]
        self.rt = jobs.Runtime(self.root, after_job=lambda j, a: diary.reflect(self.root, j, a), after_feedback=lambda j: diary.learn_from_feedback(self.root, j))

    def agent(self, **kw):
        return agents.create(self.root, {"name": "Bot", "mode": "auto", "diary_enabled": True, **kw})["id"]

    def job(self, script, aid, prompt="do it"):
        with mock.patch("agentlab.jobs.llm.chat", script):
            j = self.rt.start(self.proj, aid, prompt)
            end = time.time() + 20
            while self.rt.get(j["id"])["status"] not in jobs.FINAL and time.time() < end:
                time.sleep(0.02)
        time.sleep(0.2)  # after_job runs just after the final status is written
        return self.rt.get(j["id"])


class DiaryTests(LearnCase):
    def test_rule_based_entry_turns_failures_into_lessons_and_memories(self):
        aid = self.agent()
        j = self.job(Script([R(calls=[("read_file", {"path": "nope.txt"}), ("read_file", {"path": "/etc/passwd"})]), R("gave up")]), aid, "summarise nope.txt")
        e = diary.entries(self.root, aid)[0]
        self.assertEqual((e["source"], e["outcome"], e["job"], e["mood"]), ("rule", "succeeded", j["id"], "proud"))
        self.assertIn("summarise nope.txt", e["body"])
        self.assertIn("2 tool errors", e["lessons"][0])
        self.assertEqual(e["metrics"]["tool_errors"], 2)
        self.assertEqual([f["fact"] for f in memory.list_facts(self.root, aid)], e["lessons"])
        self.assertEqual(memory.list_facts(self.root, aid)[0]["source"], "diary")

    def test_model_written_entry_is_parsed_costed_and_falls_back_cleanly(self):
        aid = self.agent(provider=REAL)
        entry = {"title": "Fixed the bug", "body": "I traced it to a typo.", "lessons": ["Grep before editing", "Run tests twice", "Keep diffs small", "extra ignored"], "mood": "proud"}
        self.job(Router(Script([R("done")]), fenced(entry)), aid)
        e = diary.entries(self.root, aid)[0]
        self.assertEqual((e["source"], e["title"], len(e["lessons"])), ("auto", "Fixed the bug", 3))
        kinds = {k["key"]: k for k in usage.summary(self.root)["by_kind"]}
        self.assertEqual((kinds["diary"]["input"], kinds["diary"]["output"], kinds["job"]["calls"]), (100, 50, 1))
        self.assertAlmostEqual(kinds["diary"]["cost"], 0.00015, places=6)  # priced like any other call
        for bad in (llm.LLMError("down"), {"text": "I refuse to use JSON", "tool_calls": [], "usage": {"input": 1, "output": 1}, "stop": "end"}):
            self.job(Router(Script([R("done again")]), bad), aid)
            self.assertEqual(diary.entries(self.root, aid)[0]["source"], "rule")  # the diary never has gaps

    def test_user_notes_deletion_and_feedback(self):
        aid = self.agent()
        j = self.job(Script([R("done")]), aid, "write the report")
        note = diary.add_note(self.root, aid, "Remember I prefer short reports")
        self.assertEqual(note["source"], "user")
        with self.assertRaises(ValueError):
            diary.add_note(self.root, aid, "  ")
        self.rt.feedback(j["id"], -1, "Too long, use bullet points")
        entries = diary.entries(self.root, aid)
        self.assertIn("feedback", {e["source"] for e in entries})
        fact = next(f for f in memory.list_facts(self.root, aid) if f["source"] == "feedback")
        self.assertEqual((fact["fact"], fact["importance"]), ("User feedback: Too long, use bullet points", 3))
        self.assertEqual(self.rt.get(j["id"])["feedback"]["rating"], -1)
        for bad in (0, 2, "up"):
            with self.assertRaises(jobs.JobError):
                self.rt.feedback(j["id"], bad)
        self.assertTrue(diary.delete_entry(self.root, aid, note["id"]))
        self.assertFalse(diary.delete_entry(self.root, aid, note["id"]))
        self.assertNotIn("user", {e["source"] for e in diary.entries(self.root, aid)})

    def test_diary_off_means_no_entries(self):
        aid = self.agent(diary_enabled=False)
        rt = jobs.Runtime(self.root, after_job=lambda j, a: diary.reflect(self.root, j, a) if a["diary_enabled"] else None)
        with mock.patch("agentlab.jobs.llm.chat", Script([R("ok")])):
            j = rt.start(self.proj, aid, "x")
            time.sleep(0.8)
        self.assertEqual(diary.entries(self.root, aid), [])


class EvolutionTests(LearnCase):
    def seed(self, aid, lessons=("Check paths before reading files", "Break big goals into smaller steps", "Prefer bullet points")):
        for l in lessons:
            diary.add_entry(self.root, aid, title="t", body="b", lessons=[l], source="auto", outcome="succeeded")

    def test_rule_based_proposal_accept_reject_rollback_and_versions(self):
        aid = self.agent(instructions="Be helpful.")
        with self.assertRaises(evolution.EvolutionError):
            evolution.propose(self.root, aid)  # nothing learned yet
        self.seed(aid)
        p = evolution.propose(self.root, aid)
        self.assertEqual((p["status"], p["source"], p["base_version"], p["lessons_used"]), ("pending", "rule", 1, 3))
        self.assertTrue(p["instructions"].startswith("Be helpful.\n\n## Lessons learned"))
        self.assertEqual(agents.get(self.root, aid)["version"], 1)  # proposing changes nothing
        agent = evolution.accept(self.root, aid, p["id"])
        self.assertEqual((agent["version"], agent["instructions"] == p["instructions"]), (2, True))
        self.assertEqual(agent["history"][-1]["reason"], f"evolution {p['id']}")
        with self.assertRaises(evolution.EvolutionError):
            evolution.accept(self.root, aid, p["id"])
        with self.assertRaises(evolution.EvolutionError):
            evolution.propose(self.root, aid)  # the same lessons add nothing new
        self.seed(aid, ("Always run the tests",))
        p2 = evolution.propose(self.root, aid)
        self.assertEqual(p2["instructions"].count("## Lessons learned"), 1)  # the section is replaced, not duplicated
        self.assertEqual(evolution.reject(self.root, aid, p2["id"])["status"], "rejected")
        self.assertEqual(agents.get(self.root, aid)["version"], 2)
        back = evolution.rollback(self.root, aid, 1)
        self.assertEqual((back["instructions"], back["version"]), ("Be helpful.", 3))
        with self.assertRaises(evolution.EvolutionError):
            evolution.rollback(self.root, aid, 99)
        self.assertEqual([x["status"] for x in evolution.proposals(self.root, aid)], ["rejected", "accepted"])

    def test_model_written_proposal_is_validated(self):
        aid = self.agent(provider=REAL, instructions="Old rules.")
        self.seed(aid)
        good = {"instructions": "Old rules. Verify paths first.", "rationale": "path errors", "changes": ["+ verify paths"]}
        with mock.patch("agentlab.evolution.llm.chat", return_value=fenced(good)):
            p = evolution.propose(self.root, aid)
        self.assertEqual((p["source"], p["instructions"], p["changes"]), ("llm", "Old rules. Verify paths first.", ["+ verify paths"]))
        self.assertEqual(next(k for k in usage.summary(self.root)["by_kind"] if k["key"] == "evolution")["input"], 100)
        evolution.reject(self.root, aid, p["id"])
        for bad in ({"instructions": "Old rules."}, {"instructions": ""}, {"instructions": "x" * 20001}, {"nope": 1}):
            with mock.patch("agentlab.evolution.llm.chat", return_value=fenced(bad)):
                self.assertEqual(evolution.propose(self.root, aid)["source"], "rule")  # unusable model output -> rule based
            evolution.reject(self.root, aid, evolution.proposals(self.root, aid)[0]["id"])

    def test_auto_suggest_never_applies_and_waits_for_enough_lessons(self):
        aid = self.agent(evolve="suggest", instructions="Base.")
        agent = agents.get(self.root, aid)
        self.seed(aid, ("one", "two"))
        self.assertIsNone(evolution.maybe_auto_propose(self.root, agent))
        self.seed(aid, ("three",))
        p = evolution.maybe_auto_propose(self.root, agent)
        self.assertEqual(p["status"], "pending")
        self.assertEqual(agents.get(self.root, aid)["version"], 1)
        self.assertIsNone(evolution.maybe_auto_propose(self.root, agent))  # one pending proposal at a time
        self.assertIsNone(evolution.maybe_auto_propose(self.root, agents.get(self.root, self.agent(evolve="off"))))


class StatsTests(LearnCase):
    def test_per_agent_analytics_health_and_version_comparison(self):
        aid = self.agent(provider={"type": "mock"})
        self.job(Script([R(calls=[("list_files", {}), ("read_file", {"path": "missing.txt"})], usage_=(100, 50)), R("ok", usage_=(100, 50))]), aid, "first")
        self.job(Script([llm.LLMError("boom")]), aid, "second")
        agents.update(self.root, aid, {"instructions": "Now with new instructions"}, reason="manual")
        j3 = self.job(Script([R(calls=[("list_files", {})], usage_=(80, 20)), R("fine", usage_=(80, 20))]), aid, "third")
        self.rt.feedback(j3["id"], 1, "great")
        s = agent_stats.stats(self.root, aid)
        self.assertEqual(s["jobs"]["total"], 3)
        self.assertEqual(s["jobs"]["by_status"], {"succeeded": 2, "failed": 1})
        self.assertEqual(s["jobs"]["success_rate"], 0.667)
        self.assertEqual((s["totals"]["input_tokens"], s["totals"]["output_tokens"], s["totals"]["tool_errors"]), (360, 140, 1))
        self.assertEqual(s["tool_usage"], [{"tool": "list_files", "count": 2}, {"tool": "read_file", "count": 1}])
        self.assertEqual(s["top_errors"][0]["count"], 1)
        self.assertEqual([(v["version"], v["jobs"], v["succeeded"]) for v in s["versions"]], [(1, 2, 1), (2, 1, 1)])
        self.assertEqual(next(v for v in s["versions"] if v["version"] == 2)["up"], 1)
        self.assertEqual((s["feedback"], s["version"]), ({"up": 1, "down": 0}, 2))
        self.assertEqual(s["efficiency"]["tokens_per_success"], round(500 / 2))
        self.assertEqual(s["health"]["level"], "warn")
        self.assertEqual(s["health"]["reasons"][0], {"code": "failures", "n": 1})
        self.assertGreaterEqual(s["knowledge"]["diary_entries"], 4)
        self.assertEqual(sum(d["jobs"] for d in s["by_day"]), 3)
        self.assertEqual(agent_stats.stats(self.root, self.agent())["health"]["level"], "idle")


if __name__ == "__main__":
    unittest.main()
