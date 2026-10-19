import json
import random
import tempfile
import time
import unittest

from agentlab import projects, training

POS = ["great", "love", "excellent", "wonderful", "happy", "fantastic", "amazing", "good"]
NEG = ["awful", "hate", "terrible", "bad", "sad", "horrible", "poor", "worst"]
FILL = ["the", "movie", "was", "really", "this", "food", "service", "it", "very", "and"]


def dataset(n, seed=1, noise=0.0):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        lab = rng.choice(["pos", "neg"])
        words = rng.sample(FILL, 4) + [rng.choice(POS if lab == "pos" else NEG)]
        rng.shuffle(words)
        if rng.random() < noise:
            lab = "neg" if lab == "pos" else "pos"
        rows.append({"text": " ".join(words), "label": lab})
    return rows


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.pid = projects.create(self.root, "ML")["id"]

    def write(self, name, rows):
        if name.endswith(".csv"):
            content = "text,label\n" + "".join(f"{r['text']},{r['label']}\n" for r in rows)
        else:
            content = "".join(json.dumps(r) + "\n" for r in rows)
        projects.write_file(self.root, self.pid, name, content)

    def test_inspect_and_dataset_errors(self):
        self.write("d.csv", dataset(30))
        info = training.inspect(self.root, self.pid, "d.csv")
        self.assertEqual((info["suggested"], info["rows"], info["text_field"]), ("classifier", 30, "text"))
        projects.write_file(self.root, self.pid, "x.txt", "hi")
        with self.assertRaises(training.TrainError):
            training.load_dataset(self.root, self.pid, "x.txt")
        projects.write_file(self.root, self.pid, "bad.jsonl", "{oops\n")
        with self.assertRaises(training.TrainError):
            training.load_dataset(self.root, self.pid, "bad.jsonl")

    def test_classifier_learns_and_predicts(self):
        self.write("d.jsonl", dataset(300))
        run = training.start(self.root, {"project": self.pid, "dataset": "d.jsonl", "options": {"epochs": 8}}, background=False)
        self.assertEqual(run["status"], "succeeded", run.get("error"))
        self.assertEqual(run["split"], {"train": 240, "val": 60})
        self.assertGreaterEqual(run["best"]["val_acc"], 0.95)
        h = run["history"]
        self.assertLess(h[-1]["train_loss"], h[0]["train_loss"])
        self.assertEqual(training.predict(self.root, run["id"], "what a wonderful film")[0]["label"], "pos")
        self.assertEqual(training.predict(self.root, run["id"], "horrible service")[0]["label"], "neg")

    def test_overfit_protection_stops_early_and_keeps_best(self):
        self.write("noisy.jsonl", dataset(60, seed=3, noise=0.45))
        run = training.start(self.root, {"project": self.pid, "dataset": "noisy.jsonl", "options": {"epochs": 60, "lr": 2.0, "weight_decay": 0, "patience": 2}}, background=False)
        self.assertTrue(run["stopped_early"])
        self.assertLess(len(run["history"]), 60)
        self.assertTrue(any("Stopped early" in w for w in run["warnings"]))
        self.assertEqual(run["model"]["epoch"], run["best"]["epoch"])
        off = training.start(self.root, {"project": self.pid, "dataset": "noisy.jsonl", "options": {"epochs": 6, "overfit_protection": False, "patience": 1}}, background=False)
        self.assertFalse(off["stopped_early"])
        self.assertEqual(len(off["history"]), 6)

    def test_guard_and_warnings(self):
        g = training.OverfitGuard(patience=2)
        self.assertEqual(g.update(1, 1.0), (True, False))
        self.assertEqual(g.update(2, 0.9), (True, False))
        self.assertEqual(g.update(3, 0.95), (False, False))
        self.assertEqual(g.update(4, 0.99), (False, True))
        self.assertEqual(g.best_epoch, 2)
        w = training.warnings(10, 0, [], {"a": 9, "b": 1})
        self.assertEqual(len(w), 3)

    def test_edit_teach_rename_drop_undo(self):
        self.write("d.jsonl", dataset(200))
        run = training.start(self.root, {"project": self.pid, "dataset": "d.jsonl", "options": {"epochs": 4}}, background=False)
        rid = run["id"]
        training.edit(self.root, rid, "teach", text="meh it was fine I guess", label="neutral")
        self.assertEqual(training.predict(self.root, rid, "meh it was fine I guess")[0]["label"], "neutral")
        training.edit(self.root, rid, "rename", old="pos", new="positive")
        self.assertIn("positive", training.load_model(self.root, rid)["labels"])
        training.edit(self.root, rid, "undo")
        self.assertIn("pos", training.load_model(self.root, rid)["labels"])
        training.edit(self.root, rid, "drop", label="neutral")
        with self.assertRaises(training.TrainError):
            training.edit(self.root, rid, "drop", label="pos")  # two must remain
        with self.assertRaises(training.TrainError):
            training.edit(self.root, rid, "explode")

    def test_project_ai_train_button(self):
        with self.assertRaises(training.TrainError):
            training.train_project(self.root, self.pid)
        self.write("d.csv", dataset(80))
        training.configure_project(self.root, self.pid, {"dataset": "d.csv", "options": {"epochs": 3}})
        self.assertEqual(projects.get(self.root, self.pid)["ai"]["dataset"], "d.csv")
        events = []
        run = training.train_project(self.root, self.pid, on_event=lambda *a: events.append(a))
        end = time.time() + 20
        while training.get(self.root, run["id"])["status"] == "running" and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.05)
        self.assertEqual(training.get(self.root, run["id"])["status"], "succeeded")
        self.assertEqual(events[0][0], "train_finished")
        self.assertEqual(len(training.list_runs(self.root, self.pid)), 1)

    def test_lora_runs_script_and_streams_metrics(self):
        self.write("chat.jsonl", [{"prompt": f"q{i}", "completion": f"a{i}"} for i in range(40)])
        with self.assertRaises(training.TrainError):
            training.start(self.root, {"project": self.pid, "dataset": "chat.jsonl", "trainer": "lora"}, background=False)
        ws = projects.workspace(self.root, self.pid)

        def fake_runner(cwd, command, **kw):
            cfg_path = command.split()[-1]
            cfg = json.loads((ws / cfg_path).read_text())
            self.assertEqual(len(cfg["train"]) + len(cfg["val"]), 40)
            self.assertTrue(cfg["overfit_protection"])
            with open(ws / cfg["metrics"], "a") as f:
                for ep, tl, vl in ((1, 2.0, 1.8), (2, 1.5, 1.6), (3, 1.0, 1.9)):
                    f.write(json.dumps({"kind": "step", "epoch": ep, "train_loss": tl}) + "\n")
                    f.write(json.dumps({"kind": "eval", "epoch": ep, "val_loss": vl}) + "\n")
                f.write(json.dumps({"kind": "done", "best_checkpoint": "out/checkpoint-1"}) + "\n")
            return {"exit_code": 0, "output": ""}
        rows, _ = training.load_dataset(self.root, self.pid, "chat.jsonl")
        run = {"id": "abcdef1234", "project": self.pid, "options": {**training.DEFAULTS, "base_model": "tiny/model", "epochs": 3}, "status": "running", "progress": {}, "name": "x"}
        training._cancel[run["id"]] = __import__("threading").Event()
        training._save(self.root, run)
        training._train_lora(self.root, run, rows, runner=fake_runner)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["best"]["epoch"], 2)
        self.assertEqual(run["best_checkpoint"], "out/checkpoint-1")
        self.assertTrue((ws / run["workdir"] / "train.py").exists())


if __name__ == "__main__":
    unittest.main()
