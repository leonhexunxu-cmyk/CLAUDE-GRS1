import json
import tempfile
import unittest
from pathlib import Path

from agentlab import Recorder, load_run, replay, fork_run, compare_runs, run_suite, parse_policy, Tripwires
from agentlab.sandbox import simulate
from agentlab.regress import has_regressions


class RecorderTests(unittest.TestCase):
    def test_record_replay_fork(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            with Recorder(p) as r:
                r.log("prompt", "fix bug")
                r.log("tool_call", "grep TODO", tool="bash")
                r.log("output", "done")
            self.assertEqual([e["step"] for e in load_run(p)], [0, 1, 2, 3])
            self.assertIn("grep TODO", replay(p))
            self.assertNotIn("done", replay(p, upto=2))
            q = Path(d) / "b.jsonl"
            fork_run(p, 2, q)
            forked = load_run(q)
            self.assertEqual(len(forked), 3)
            self.assertEqual(forked[0]["meta"]["forked_at"], 2)

    def test_error_is_logged_and_bad_kind_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.jsonl"
            with self.assertRaises(RuntimeError):
                with Recorder(p) as r:
                    with self.assertRaises(ValueError):
                        r.log("nonsense", "x")
                    raise RuntimeError("boom")
            self.assertEqual(load_run(p)[-1]["kind"], "error")


class RegressTests(unittest.TestCase):
    def test_detects_drift_and_constraints(self):
        cases = [{"name": "greet", "input": "hi", "must_contain": ["hello"]}]
        base = run_suite(lambda s: "Hello there, how can I help?", cases)
        same = run_suite(lambda s: "hello there,  how can I help?", cases)
        drift = run_suite(lambda s: "Absolutely not. Go away.", cases)
        self.assertFalse(has_regressions(compare_runs(base, same)))
        f = compare_runs(base, drift)
        self.assertEqual(f[0]["status"], "fail")  # missing 'hello'


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.p = parse_policy("""
            allow read src/**, tests/
            allow write src/
            allow run pytest, git status
            allow network api.github.com
            never write src/secrets
            never read .env
        """)

    def test_allow_and_deny(self):
        c = self.p.check
        self.assertTrue(c("read", "src/app/main.py")[0])
        self.assertTrue(c("run", "pytest -q")[0])
        self.assertTrue(c("network", "api.github.com")[0])
        self.assertFalse(c("write", "src/secrets/key.txt")[0])
        self.assertFalse(c("read", ".env")[0])
        self.assertFalse(c("run", "pytester")[0])  # prefix must be a whole word
        self.assertFalse(c("network", "evilapi.github.com.attacker.io")[0])

    def test_attacks_blocked_and_json(self):
        self.assertFalse(any(ok for _, _, ok, _ in simulate(self.p)))
        s = json.loads(self.p.to_json())
        self.assertIn("Bash(pytest:*)", s["permissions"]["allow"])
        self.assertIn("Read(.env)", s["permissions"]["deny"])

    def test_bad_rule(self):
        with self.assertRaises(ValueError):
            parse_policy("please be nice")


class TripwireTests(unittest.TestCase):
    def test_plant_and_audit(self):
        t = Tripwires("s3cret"); t.make(2)
        doc = t.plant("Para one.\n\nPara two.\n\nPara three.")
        for c in t.canaries:
            self.assertIn(c["sentence"], doc)
        good = " ".join(f"{c['subject']} is {c['value']}." for c in t.canaries)
        self.assertTrue(t.verdict(good))
        bad = f"{t.canaries[0]['subject']} is 123456-Z."
        self.assertEqual(t.audit(bad)[0]["status"], "contradicted")
        self.assertFalse(t.verdict(bad))
        self.assertEqual(Tripwires.loads(t.dumps()).canaries, t.canaries)
        self.assertEqual(Tripwires("s3cret").make(2), t.canaries)  # deterministic


if __name__ == "__main__":
    unittest.main()
