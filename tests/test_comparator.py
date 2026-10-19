import tempfile
import unittest

from agentlab import agents, comparator, llm


def reply(text, ms_tokens=(10, 5)):
    return {"text": text, "tool_calls": [], "usage": {"input": ms_tokens[0], "output": ms_tokens[1]}, "stop": "end", "model": "m"}


class Fake:
    """Answers per model name, with a controllable size."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, provider, system, msgs, tools=None, **kw):
        self.calls.append(provider["model"])
        text = self.answers[provider["model"]]
        return reply(text, (20, max(1, len(text.split()))))


class ComparatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        p = {**llm.PRESETS["mock"], "script": [1]}
        self.a = {"provider": {**p, "model": "alpha"}, "label": "Alpha"}
        self.b = {"provider": {**p, "model": "beta"}, "label": "Beta"}

    def test_compares_two_models_with_analytics(self):
        fake = Fake({"alpha": "Paris is the capital of France.", "beta": "The capital of France is Paris, a city on the Seine with about two million people."})
        c = comparator.run(self.root, {"targets": [self.a, self.b], "prompt": "What is the capital of France?"}, fake)
        self.assertEqual([r["label"] for r in c["results"]], ["Alpha", "Beta"])
        alpha, beta = c["results"]
        for m in (alpha["metrics"], beta["metrics"]):
            for key in ("input_tokens", "output_tokens", "total_tokens", "cost", "latency_ms", "tokens_per_second", "words", "agreement"):
                self.assertIn(key, m)
        self.assertLess(alpha["metrics"]["words"], beta["metrics"]["words"])
        self.assertEqual(c["winners"]["most_concise"], "Alpha")
        self.assertEqual(c["winners"]["most_detailed"], "Beta")
        self.assertEqual(c["winners"]["fewest_tokens"], "Alpha")
        self.assertEqual(c["totals"]["calls"], 2)
        self.assertEqual(len(c["pairs"]), 1)
        self.assertGreater(c["pairs"][0]["overlap"], 0)  # both mention Paris and France
        self.assertEqual(alpha["metrics"]["agreement"], c["pairs"][0]["overlap"])

    def test_repeats_give_median_and_spread(self):
        fake = Fake({"alpha": "one two", "beta": "three four"})
        c = comparator.run(self.root, {"targets": [self.a, self.b], "prompt": "hi", "repeats": 3}, fake)
        self.assertEqual(c["totals"]["calls"], 6)
        self.assertEqual([r["runs"] for r in c["results"]], [3, 3])
        m = c["results"][0]["metrics"]
        self.assertLessEqual(m["latency_min_ms"], m["latency_ms"])
        self.assertLessEqual(m["latency_ms"], m["latency_max_ms"])
        self.assertIsNotNone(c["winners"]["steadiest"])
        self.assertIsNone(comparator.run(self.root, {"targets": [self.a, self.b], "prompt": "hi"}, fake)["winners"]["steadiest"])

    def test_one_model_failing_does_not_stop_the_rest(self):
        def fake(provider, system, msgs, tools=None, **kw):
            if provider["model"] == "beta":
                raise llm.LLMError("502: upstream is down")
            return reply("fine")
        c = comparator.run(self.root, {"targets": [self.a, self.b], "prompt": "hi"}, fake)
        self.assertTrue(c["results"][0]["ok"])
        self.assertFalse(c["results"][1]["ok"])
        self.assertIn("upstream is down", c["results"][1]["error"])
        self.assertEqual(c["winners"]["fastest"], "Alpha")
        self.assertIsNone(c["results"][0]["metrics"]["agreement"])  # nobody to compare with

    def test_history_and_validation(self):
        fake = Fake({"alpha": "a", "beta": "b"})
        c = comparator.run(self.root, {"targets": [self.a, self.b], "prompt": "remember me"}, fake)
        h = comparator.history(self.root)
        self.assertEqual(h[0]["prompt"], "remember me")
        self.assertEqual(comparator.get(self.root, c["id"])["id"], c["id"])
        comparator.delete(self.root, c["id"])
        self.assertEqual(comparator.history(self.root), [])
        with self.assertRaises(comparator.CompareError):
            comparator.get(self.root, c["id"])
        for bad in ({"targets": [self.a], "prompt": "x"}, {"targets": [self.a, self.b], "prompt": "  "}, {"prompt": "x"}):
            with self.assertRaises(comparator.CompareError):
                comparator.run(self.root, bad, fake)

    def test_agents_can_be_compared_and_judged(self):
        aid = agents.create(self.root, {"name": "Ada", "instructions": "Be brief.", "provider": {"type": "mock", "model": "alpha", "script": [1]}})["id"]
        fake = Fake({"alpha": "short", "beta": "much longer answer here", "judge": '{"ranking": ["Ada", "Beta"], "scores": [{"label": "Ada", "score": 8, "why": "clear"}], "summary": "Ada is terser."}'})
        c = comparator.run(self.root, {"targets": [{"agent_id": aid, "label": "Ada"}, self.b], "prompt": "hi"}, fake)
        self.assertTrue(all(r["ok"] for r in c["results"]))
        v = comparator.judge(self.root, c, {**llm.PRESETS["mock"], "model": "judge", "script": [1]}, fake)
        self.assertEqual(v["ranking"], ["Ada", "Beta"])
        self.assertIn("opinion", v["caveat"])
        with self.assertRaises(comparator.CompareError):  # a judge that returns nothing usable
            comparator.judge(self.root, c, {**llm.PRESETS["mock"], "model": "alpha", "script": [1]}, fake)


if __name__ == "__main__":
    unittest.main()
