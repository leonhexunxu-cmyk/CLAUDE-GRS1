import tempfile
import unittest

from agentlab import agents, evals, evolution


def bot(rule):
    """A fake model whose answer depends on its instructions (so better instructions really score better)."""
    def chat(provider, system, msgs, tools, **kw):
        return {"text": rule(system, msgs[-1]["content"]), "tool_calls": [], "usage": {"input": 5, "output": 5}, "stop": "end", "model": "m"}
    return chat


class EvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.agent = agents.create(self.root, {"name": "Sup", "instructions": "Help customers.", "provider": {"type": "mock"}})
        self.aid = self.agent["id"]

    def test_checks(self):
        self.assertTrue(evals.check("Hello World", {"kind": "contains", "value": "world"}))
        self.assertFalse(evals.check("Hello World", {"kind": "contains", "value": "world", "case_sensitive": True}))
        self.assertTrue(evals.check("ok", {"kind": "not_contains", "value": "sorry"}))
        self.assertTrue(evals.check("order #123", {"kind": "regex", "value": r"#\d+"}))
        self.assertTrue(evals.check('sure: {"a": 1}', {"kind": "json"}))
        self.assertFalse(evals.check("one two three", {"kind": "max_words", "value": 2}))
        for bad in ([], [{"kind": "nope"}], [{"kind": "regex", "value": "("}], [{"kind": "contains", "value": ""}]):
            with self.assertRaises(evals.EvalError):
                evals.add_case(self.root, self.aid, "x", bad)

    def test_suite_and_gated_accept(self):
        evals.add_case(self.root, self.aid, "Where is my order?", [{"kind": "contains", "value": "order number"}])
        evals.add_case(self.root, self.aid, "Hi", [{"kind": "max_words", "value": 20}])
        rule = lambda system, q: ("Please share your order number." if "order number" in system and "order" in q.lower() else "Hello! How can I help?")
        r = evals.run_suite(self.root, self.agent, bot(rule))
        self.assertEqual((r["passed"], r["total"]), (1, 2))
        evolution._save(self.root, self.aid, [{"id": "p1", "status": "pending", "ts": 1, "instructions": "Help customers. Always ask for the order number.", "base_version": 1, "rationale": "", "changes": [], "source": "rule", "lessons_used": 1}])
        res = evals.auto_improve(self.root, self.aid, bot(rule))
        self.assertEqual(res["verdict"], "accepted")
        self.assertIn("order number", agents.get(self.root, self.aid)["instructions"])
        self.assertEqual(evals.history(self.root, self.aid)[0]["after"], 1.0)

    def test_rejects_regressions_and_no_gain(self):
        evals.add_case(self.root, self.aid, "Hi", [{"kind": "max_words", "value": 5}])
        rule = lambda system, q: ("word " * 30 if "verbose" in system else "Hello!")
        evolution._save(self.root, self.aid, [{"id": "p2", "status": "pending", "ts": 1, "instructions": "Be very verbose.", "base_version": 1, "rationale": "", "changes": [], "source": "rule", "lessons_used": 1}])
        res = evals.auto_improve(self.root, self.aid, bot(rule))
        self.assertEqual(res["verdict"], "rejected")
        self.assertIn("breaks", res["reason"])
        self.assertEqual(agents.get(self.root, self.aid)["instructions"], "Help customers.")
        self.assertEqual(evolution.proposals(self.root, self.aid)[0]["status"], "rejected")
        evolution._save(self.root, self.aid, evolution.proposals(self.root, self.aid) + [{"id": "p3", "status": "pending", "ts": 2, "instructions": "Help customers kindly.", "base_version": 1, "rationale": "", "changes": [], "source": "rule", "lessons_used": 1}])
        self.assertIn("no improvement", evals.auto_improve(self.root, self.aid, bot(rule))["reason"])

    def test_needs_cases(self):
        with self.assertRaises(evals.EvalError):
            evals.auto_improve(self.root, self.aid)


if __name__ == "__main__":
    unittest.main()
