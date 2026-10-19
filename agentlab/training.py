"""Training: build specialised models from a project's dataset, with live loss curves and overfit protection.

Trainers
  classifier  built in, pure Python, runs anywhere: a text classifier (logistic regression on hashed words and word
              pairs). Datasets: CSV or JSONL with a text column and a label column. Good for routing, tagging,
              sentiment, intent ... in seconds, on CPU. The result can be edited afterwards (teach corrections,
              rename or drop labels).
  lora        fine-tune a Hugging Face causal language model with LoRA. agentlab writes the training script and
              runs it in the project's Python environment (needs torch, transformers, peft, datasets there).
              Datasets: JSONL with {"prompt","completion"}, {"messages"} or {"text"}.

Overfit protection (both trainers): a held-out validation split, early stopping when validation loss stops
improving (patience, min_delta), the best checkpoint is kept rather than the last one, weight decay, and plain
warnings (tiny dataset, train/validation gap, perfect training accuracy with weak validation, imbalance).
"""

import csv
import hashlib
import io
import json
import math
import random
import re
import shutil
import sys
import threading
import time
import uuid

from . import envs, project_env, projects
from .hooks import data_dir

TRAINERS = ("classifier", "lora")
DIM = 4096
WORD = re.compile(r"\w+", re.U)
_lock = threading.RLock()
_cancel = {}


class TrainError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- datasets
def load_dataset(root, project_id, path, text_field=None, label_field=None, limit=200_000):
    """-> (rows as dicts, detected format). Reads CSV, TSV, JSON (list) or JSONL from the project."""
    f = projects.read_file(root, project_id, path, max_bytes=200_000_000)
    if f["binary"]:
        raise TrainError("the dataset must be a text file (CSV, TSV, JSON or JSONL)")
    raw = f["content"]
    rows = []
    if path.endswith((".jsonl", ".ndjson")):
        for i, line in enumerate(raw.splitlines()):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    raise TrainError(f"line {i + 1} is not valid JSON")
        fmt = "jsonl"
    elif path.endswith(".json"):
        try:
            rows = json.loads(raw)
        except ValueError:
            raise TrainError("the file is not valid JSON")
        if not isinstance(rows, list):
            raise TrainError("a JSON dataset must be a list of objects")
        fmt = "json"
    elif path.endswith((".csv", ".tsv")):
        rows = list(csv.DictReader(io.StringIO(raw), delimiter="\t" if path.endswith(".tsv") else ","))
        fmt = "csv"
    else:
        raise TrainError("use a .csv, .tsv, .json or .jsonl file")
    rows = [r for r in rows if isinstance(r, dict)][:limit]
    if not rows:
        raise TrainError("the dataset is empty")
    return rows, fmt


def _pick(rows, wanted, candidates):
    keys = list(rows[0].keys())
    if wanted:
        if wanted not in keys:
            raise TrainError(f"column '{wanted}' not found; columns: {keys}")
        return wanted
    return next((k for k in candidates if k in keys), None)


def inspect(root, project_id, path):
    """A quick look at a dataset: size, columns, suggested trainer, label balance."""
    rows, fmt = load_dataset(root, project_id, path)
    keys = list(rows[0].keys())
    text = _pick(rows, None, ("text", "sentence", "review", "message", "input", "question", "content"))
    label = _pick(rows, None, ("label", "labels", "category", "class", "intent", "sentiment", "target"))
    out = {"rows": len(rows), "format": fmt, "columns": keys, "sample": rows[:3], "text_field": text, "label_field": label}
    if text and label:
        counts = {}
        for r in rows:
            counts[str(r.get(label))] = counts.get(str(r.get(label)), 0) + 1
        out.update(suggested="classifier", labels=dict(sorted(counts.items(), key=lambda kv: -kv[1])[:50]))
    elif any(k in keys for k in ("prompt", "messages", "completion")) or text:
        out["suggested"] = "lora"
    else:
        out["suggested"] = None
    return out


def split(rows, val_fraction, seed):
    rows = list(rows)
    random.Random(seed).shuffle(rows)
    n_val = max(1, int(round(len(rows) * val_fraction))) if val_fraction > 0 and len(rows) >= 5 else 0
    return rows[n_val:], rows[:n_val]


# ---------------------------------------------------------------- overfit guard
class OverfitGuard:
    """Early stopping on validation loss, with the best epoch remembered."""

    def __init__(self, patience=3, min_delta=0.001, enabled=True):
        self.patience, self.min_delta, self.enabled = patience, min_delta, enabled
        self.best, self.best_epoch, self.bad = math.inf, None, 0

    def update(self, epoch, val_loss):
        """-> (is_best, should_stop)."""
        if val_loss is None:
            return True, False
        if val_loss < self.best - self.min_delta:
            self.best, self.best_epoch, self.bad = val_loss, epoch, 0
            return True, False
        self.bad += 1
        return False, self.enabled and self.bad >= self.patience


def warnings(n_train, n_val, history, labels=None):
    w = []
    if n_train < 50:
        w.append(f"Only {n_train} training examples: the model will memorise rather than learn. Aim for a few hundred or more.")
    if n_val == 0:
        w.append("No validation split: overfitting cannot be detected. Keep at least 10% aside.")
    elif n_val < 20:
        w.append(f"The validation set has only {n_val} examples, so its numbers are noisy.")
    if len(history) >= 3 and history[-1].get("val_loss") is not None:
        best = min(h["val_loss"] for h in history if h.get("val_loss") is not None)
        last = history[-1]
        if last["val_loss"] > best * 1.1 and last["train_loss"] < history[0]["train_loss"] * 0.7:
            w.append("Validation loss is rising while training loss keeps falling: that is overfitting. The best epoch was kept.")
        if last.get("train_acc") == 1.0 and last.get("val_acc") is not None and last["val_acc"] < 0.8:
            w.append("100% training accuracy but much lower on validation: the model memorised the training set. Add data or raise weight decay.")
    if labels:
        top, total = max(labels.values()), sum(labels.values())
        if total and top / total > 0.8:
            w.append(f"One label is {round(100 * top / total)}% of the data; accuracy will look good even if the model only guesses it.")
    return w


# ---------------------------------------------------------------- built-in classifier
def _features(text):
    words = [w.lower() for w in WORD.findall(str(text))]
    feats = {}
    for f in words + [a + " " + b for a, b in zip(words, words[1:])]:
        i = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=4).digest(), "big") % DIM
        feats[i] = feats.get(i, 0.0) + 1.0
    n = math.sqrt(sum(v * v for v in feats.values())) or 1.0
    return {i: v / n for i, v in feats.items()}


def _softmax(z):
    m = max(z)
    e = [math.exp(x - m) for x in z]
    s = sum(e)
    return [x / s for x in e]


class Classifier:
    def __init__(self, labels, W=None, b=None):
        self.labels = list(labels)
        self.W = W or [[0.0] * DIM for _ in self.labels]
        self.b = b or [0.0] * len(self.labels)

    def probs(self, feats):
        return _softmax([self.b[k] + sum(self.W[k][i] * v for i, v in feats.items()) for k in range(len(self.labels))])

    def sgd(self, feats, y, lr, wd):
        p = self.probs(feats)
        for k in range(len(self.labels)):
            g = p[k] - (1.0 if k == y else 0.0)
            row = self.W[k]
            for i, v in feats.items():
                row[i] -= lr * (g * v + wd * row[i])
            self.b[k] -= lr * g
        return -math.log(max(p[y], 1e-12))

    def evaluate(self, data):
        if not data:
            return None, None
        loss = correct = 0.0
        for feats, y in data:
            p = self.probs(feats)
            loss += -math.log(max(p[y], 1e-12))
            correct += max(range(len(p)), key=p.__getitem__) == y
        return loss / len(data), correct / len(data)

    def predict(self, text, top=3):
        p = self.probs(_features(text))
        order = sorted(range(len(p)), key=lambda k: -p[k])[:top]
        return [{"label": self.labels[k], "p": round(p[k], 4)} for k in order]

    def to_json(self):
        sparse = [{str(i): round(v, 6) for i, v in enumerate(row) if abs(v) > 1e-7} for row in self.W]
        return {"kind": "classifier", "labels": self.labels, "W": sparse, "b": self.b, "dim": DIM}

    @classmethod
    def from_json(cls, d):
        W = [[0.0] * DIM for _ in d["labels"]]
        for k, row in enumerate(d["W"]):
            for i, v in row.items():
                W[k][int(i)] = v
        return cls(d["labels"], W, list(d["b"]))

    def copy(self):
        return Classifier(self.labels, [row[:] for row in self.W], self.b[:])


# ---------------------------------------------------------------- runs
def _dir(root, rid=None):
    d = data_dir(root) / "training"
    if rid is None:
        return d
    if not re.fullmatch(r"[a-f0-9]{10}", rid or ""):
        raise TrainError("training run not found", 404)
    return d / rid


def _save(root, run):
    with _lock:
        d = _dir(root, run["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"run.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(run), encoding="utf-8")
        tmp.replace(d / "run.json")


def get(root, rid):
    p = _dir(root, rid) / "run.json"
    if not p.exists():
        raise TrainError("training run not found", 404)
    run = json.loads(p.read_text(encoding="utf-8"))
    m = _dir(root, rid) / "metrics.jsonl"
    run["history"] = []
    for line in m.read_text(encoding="utf-8").splitlines() if m.exists() else []:
        try:
            run["history"].append(json.loads(line))
        except ValueError:  # a line still being written
            pass
    return run


def list_runs(root, project_id=None):
    out = []
    for p in _dir(root).glob("*/run.json") if _dir(root).exists() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if project_id in (None, r["project"]):
            out.append(r)
    return sorted(out, key=lambda r: -r["created"])


def _metric(root, rid, entry):
    with open(_dir(root, rid) / "metrics.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


DEFAULTS = {"epochs": 20, "lr": 0.5, "weight_decay": 1e-4, "val_fraction": 0.2, "patience": 3, "min_delta": 0.001, "overfit_protection": True, "seed": 42,
            "base_model": "", "lora_r": 8, "max_length": 512, "batch_size": 4, "allow_network": False}


def start(root, spec, on_event=None, background=True):
    """spec: {project, dataset, trainer, name, text_field, label_field, options}."""
    trainer = spec.get("trainer") or "classifier"
    if trainer not in TRAINERS:
        raise TrainError(f"trainer must be one of {list(TRAINERS)}")
    pid = spec.get("project")
    projects.get(root, pid)
    opts = {**DEFAULTS, **{k: v for k, v in (spec.get("options") or {}).items() if k in DEFAULTS}}
    try:
        opts["epochs"] = max(1, min(int(opts["epochs"]), 500))
        opts["val_fraction"] = max(0.0, min(float(opts["val_fraction"]), 0.5))
        opts["patience"] = max(1, min(int(opts["patience"]), 50))
        opts["lr"], opts["weight_decay"], opts["min_delta"] = float(opts["lr"]), float(opts["weight_decay"]), float(opts["min_delta"])
    except (TypeError, ValueError):
        raise TrainError("training options must be numbers")
    rows, _ = load_dataset(root, pid, spec.get("dataset", ""))
    if trainer == "lora" and not re.fullmatch(r"[\w.\-]+(/[\w.\-]+)?", str(opts["base_model"] or "")):
        raise TrainError("lora needs a base_model (a Hugging Face id like Qwen/Qwen2.5-0.5B, or a folder in the project)")
    run = {"id": uuid.uuid4().hex[:10], "name": str(spec.get("name") or f"{trainer} · {spec['dataset']}")[:80], "project": pid, "dataset": spec["dataset"], "trainer": trainer,
           "options": opts, "text_field": spec.get("text_field"), "label_field": spec.get("label_field"), "status": "running", "created": time.time(), "ended": None,
           "progress": {"epoch": 0, "epochs": opts["epochs"], "percent": 0}, "best": None, "warnings": [], "error": None, "stopped_early": False, "examples": len(rows)}
    _save(root, run)
    _cancel[run["id"]] = threading.Event()
    target = _train_classifier if trainer == "classifier" else _train_lora

    def go():
        try:
            target(root, run, rows)
        except Exception as e:
            run.update(status="failed", error=f"{type(e).__name__}: {e}"[:500])
        run["ended"] = time.time()
        _save(root, run)
        _cancel.pop(run["id"], None)
        if on_event:
            try:
                on_event("train_finished", f"training “{run['name']}” {run['status']}", {"run": run["id"], "status": run["status"]})
            except Exception:
                pass
    if background:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()
    return get(root, run["id"])


def cancel(root, rid):
    get(root, rid)
    ev = _cancel.get(rid)
    if not ev:
        raise TrainError("this run is not running")
    ev.set()


def _train_classifier(root, run, rows):
    o = run["options"]
    tf = _pick(rows, run["text_field"], ("text", "sentence", "review", "message", "input", "question", "content"))
    lf = _pick(rows, run["label_field"], ("label", "labels", "category", "class", "intent", "sentiment", "target"))
    if not tf or not lf:
        raise TrainError("could not find the text and label columns; name them in text_field and label_field")
    rows = [r for r in rows if str(r.get(tf) or "").strip() and r.get(lf) not in (None, "")]
    labels = sorted({str(r[lf]) for r in rows})
    if len(labels) < 2:
        raise TrainError("a classifier needs at least two different labels")
    counts = {l: 0 for l in labels}
    for r in rows:
        counts[str(r[lf])] += 1
    train, val = split(rows, o["val_fraction"], o["seed"])
    enc = lambda rs: [(_features(r[tf]), labels.index(str(r[lf]))) for r in rs]
    tr, va = enc(train), enc(val)
    run.update(text_field=tf, label_field=lf, labels=counts, split={"train": len(tr), "val": len(va)})
    model, best_model = Classifier(labels), None
    guard = OverfitGuard(o["patience"], o["min_delta"], o["overfit_protection"])
    history, rng = [], random.Random(o["seed"])
    for epoch in range(1, o["epochs"] + 1):
        if _cancel[run["id"]].is_set():
            run["status"] = "cancelled"
            break
        rng.shuffle(tr)
        lr = o["lr"] / (1 + 0.1 * (epoch - 1))
        tl = sum(model.sgd(f, y, lr, o["weight_decay"]) for f, y in tr) / len(tr)
        _, ta = model.evaluate(tr)
        vl, vacc = model.evaluate(va)
        is_best, stop = guard.update(epoch, vl)
        if is_best:
            best_model = model.copy()
        entry = {"epoch": epoch, "train_loss": round(tl, 5), "val_loss": None if vl is None else round(vl, 5), "train_acc": round(ta, 4), "val_acc": None if vacc is None else round(vacc, 4), "best": is_best}
        history.append(entry)
        _metric(root, run["id"], entry)
        run["progress"] = {"epoch": epoch, "epochs": o["epochs"], "percent": round(100 * epoch / o["epochs"])}
        run["best"] = {"epoch": guard.best_epoch or epoch, "val_loss": None if guard.best == math.inf else round(guard.best, 5),
                       "val_acc": next((h["val_acc"] for h in history if h["epoch"] == (guard.best_epoch or epoch)), None)}
        run["warnings"] = warnings(len(tr), len(va), history, counts)
        _save(root, run)
        if stop:
            run.update(stopped_early=True)
            run["warnings"].insert(0, f"Stopped early at epoch {epoch}: validation loss had not improved for {o['patience']} epochs.")
            break
    if run["status"] == "running":
        run["status"] = "succeeded"
        run["progress"]["percent"] = 100
    final = best_model if (o["overfit_protection"] and best_model) else model
    (_dir(root, run["id"]) / "model.json").write_text(json.dumps({**final.to_json(), "name": run["name"], "text_field": tf}), encoding="utf-8")
    run["model"] = {"kind": "classifier", "labels": labels, "epoch": run["best"]["epoch"] if o["overfit_protection"] else run["progress"]["epoch"]}


LORA_SCRIPT = r'''
# Written by agentlab. Fine-tunes a causal language model with LoRA and reports metrics as JSON lines.
import json, math, sys
import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, DataCollatorForLanguageModeling, EarlyStoppingCallback, Trainer, TrainerCallback, TrainingArguments

cfg = json.load(open(sys.argv[1]))
out = open(cfg["metrics"], "a")
def emit(**k):
    out.write(json.dumps(k) + "\n"); out.flush()

def as_text(r):
    if "messages" in r:
        return "\n".join(f"{m['role']}: {m['content']}" for m in r["messages"])
    if "prompt" in r:
        return f"{r['prompt']}\n{r.get('completion', '')}"
    return str(r.get("text", ""))

tok = AutoTokenizer.from_pretrained(cfg["base_model"])
tok.pad_token = tok.pad_token or tok.eos_token
model = AutoModelForCausalLM.from_pretrained(cfg["base_model"])
model = get_peft_model(model, LoraConfig(r=cfg["lora_r"], lora_alpha=2 * cfg["lora_r"], lora_dropout=0.05, task_type="CAUSAL_LM"))
enc = lambda rows: Dataset.from_list([{"text": as_text(r)} for r in rows]).map(lambda b: tok(b["text"], truncation=True, max_length=cfg["max_length"]), batched=True, remove_columns=["text"])
train, val = enc(cfg["train"]), enc(cfg["val"]) if cfg["val"] else None

class Report(TrainerCallback):
    def on_log(self, args, state, control, logs=None, **kw):
        logs = logs or {}
        if "loss" in logs:
            emit(kind="step", step=state.global_step, epoch=state.epoch, train_loss=logs["loss"])
        if "eval_loss" in logs:
            emit(kind="eval", step=state.global_step, epoch=state.epoch, val_loss=logs["eval_loss"])

args = TrainingArguments(output_dir=cfg["output"], num_train_epochs=cfg["epochs"], per_device_train_batch_size=cfg["batch_size"], learning_rate=cfg["lr"],
                         weight_decay=cfg["weight_decay"], logging_steps=10, eval_strategy="epoch" if val else "no", save_strategy="epoch",
                         load_best_model_at_end=bool(val and cfg["overfit_protection"]), metric_for_best_model="eval_loss", greater_is_better=False,
                         save_total_limit=2, report_to=[], use_cpu=not torch.cuda.is_available())
cbs = [Report()] + ([EarlyStoppingCallback(cfg["patience"], cfg["min_delta"])] if val and cfg["overfit_protection"] else [])
t = Trainer(model=model, args=args, train_dataset=train, eval_dataset=val, data_collator=DataCollatorForLanguageModeling(tok, mlm=False), callbacks=cbs)
t.train()
t.save_model(cfg["output"] + "/best")
tok.save_pretrained(cfg["output"] + "/best")
emit(kind="done", best_checkpoint=t.state.best_model_checkpoint)
'''


def _train_lora(root, run, rows, runner=None):
    o = run["options"]
    train, val = split(rows, o["val_fraction"], o["seed"])
    ws = projects.workspace(root, run["project"])
    work = ws / ".training" / run["id"]
    work.mkdir(parents=True, exist_ok=True)
    (work / "train.py").write_text(LORA_SCRIPT, encoding="utf-8")
    metrics_raw = work / "metrics.jsonl"
    lr = o["lr"] if o["lr"] < 0.01 else 2e-4  # the classifier default is far too high for LoRA
    cfg = {**o, "lr": lr, "train": train, "val": val, "metrics": str(metrics_raw.relative_to(ws)), "output": str((work / "out").relative_to(ws))}
    (work / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    run.update(split={"train": len(train), "val": len(val)}, workdir=str(work.relative_to(ws)))
    run["warnings"] = warnings(len(train), len(val), [])
    _save(root, run)
    venv = project_env.venv_dir(root, run["project"]) / "bin" / "python"
    py = str(venv) if venv.exists() else "python3"
    rel = work.relative_to(ws)
    done = threading.Event()
    result = {}

    def proc():
        try:
            result["r"] = (runner or envs.run_command)(ws, f"{py} {rel}/train.py {rel}/config.json", network=bool(o["allow_network"]), timeout=6 * 3600,
                                                        env=project_env.command_env(root, run["project"]), home=projects.home(root, run["project"]), max_output=20_000)
        except Exception as e:
            result["e"] = e
        done.set()
    threading.Thread(target=proc, daemon=True).start()
    seen = 0
    while True:
        finished = done.wait(1.0)
        lines = metrics_raw.read_text(encoding="utf-8").splitlines() if metrics_raw.exists() else []
        for line in lines[seen:]:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") in ("step", "eval"):
                _metric(root, run["id"], {k: e.get(k) for k in ("epoch", "step", "train_loss", "val_loss")})
                ep = e.get("epoch") or 0
                run["progress"] = {"epoch": round(ep, 2), "epochs": o["epochs"], "percent": min(99, round(100 * ep / o["epochs"]))}
            if e.get("kind") == "done":
                run["best_checkpoint"] = e.get("best_checkpoint")
        seen = len(lines)
        hist = get(root, run["id"])["history"]
        evals = [h for h in hist if h.get("val_loss") is not None]
        if evals:
            b = min(evals, key=lambda h: h["val_loss"])
            run["best"] = {"epoch": b["epoch"], "val_loss": round(b["val_loss"], 5)}
        _save(root, run)
        if finished:
            break
        if _cancel[run["id"]].is_set():
            run["status"] = "cancelled"  # the process is left to its timeout; it only writes inside the workspace
            return
    if "e" in result:
        raise result["e"]
    r = result["r"]
    if r["exit_code"] != 0:
        hint = " (install torch, transformers, peft and datasets in the project's Python environment)" if "ModuleNotFoundError" in r["output"] else ""
        raise TrainError(f"training failed{hint}: {r['output'][-600:]}")
    run.update(status="succeeded", progress={**run["progress"], "percent": 100}, model={"kind": "lora", "path": f"{rel}/out/best", "base_model": o["base_model"]})


# ---------------------------------------------------------------- using and editing trained models
def load_model(root, rid):
    p = _dir(root, rid) / "model.json"
    if not p.exists():
        raise TrainError("this run has no built-in model (only classifier runs do)", 404)
    return json.loads(p.read_text(encoding="utf-8"))


def predict(root, rid, text, top=3):
    return Classifier.from_json(load_model(root, rid)).predict(text, top)


def edit(root, rid, action, **kw):
    """Edit a trained classifier: teach (text, label: a correction, learned on the spot), rename (old, new),
    drop (label), rename_model (name). The previous version is kept as model.prev.json for undo."""
    d = load_model(root, rid)
    m = Classifier.from_json(d)
    if action == "teach":
        text, label = str(kw.get("text") or ""), str(kw.get("label") or "")
        if not text.strip() or not label:
            raise TrainError("teach needs text and label")
        if label not in m.labels:
            m.labels.append(label)
            m.W.append([0.0] * DIM)
            m.b.append(0.0)
        f, y = _features(text), m.labels.index(label)
        for _ in range(30):
            m.sgd(f, y, 0.5, 0.0)
            if m.predict(text, 1)[0]["label"] == label and m.probs(f)[y] > 0.6:
                break
        d.setdefault("edits", []).append({"teach": text[:200], "label": label, "ts": time.time()})
    elif action == "rename":
        old, new = str(kw.get("old")), str(kw.get("new") or "").strip()
        if old not in m.labels or not new or new in m.labels:
            raise TrainError("rename needs an existing label and a new, unused name")
        m.labels[m.labels.index(old)] = new
    elif action == "drop":
        lab = str(kw.get("label"))
        if lab not in m.labels or len(m.labels) <= 2:
            raise TrainError("drop needs an existing label, and at least two must remain")
        k = m.labels.index(lab)
        for lst in (m.labels, m.W, m.b):
            lst.pop(k)
    elif action == "rename_model":
        d["name"] = str(kw.get("name") or d["name"])[:80]
    elif action == "undo":
        prev = _dir(root, rid) / "model.prev.json"
        if not prev.exists():
            raise TrainError("nothing to undo")
        shutil.copy(prev, _dir(root, rid) / "model.json")
        return load_model(root, rid)
    else:
        raise TrainError("action must be teach, rename, drop, rename_model or undo")
    shutil.copy(_dir(root, rid) / "model.json", _dir(root, rid) / "model.prev.json")
    new = {**d, **m.to_json()}
    (_dir(root, rid) / "model.json").write_text(json.dumps(new), encoding="utf-8")
    return {k: new[k] for k in ("name", "labels")} | {"edits": len(new.get("edits", []))}


# ---------------------------------------------------------------- project AI
def configure_project(root, project_id, spec):
    """Project AI: the model (an agent, or a base model to fine-tune), the dataset and trainer behind the Train button."""
    ai = {"agent": spec.get("agent"), "dataset": spec.get("dataset"), "trainer": spec.get("trainer") or "classifier", "options": {k: v for k, v in (spec.get("options") or {}).items() if k in DEFAULTS},
          "text_field": spec.get("text_field"), "label_field": spec.get("label_field")}
    if ai["trainer"] not in TRAINERS:
        raise TrainError(f"trainer must be one of {list(TRAINERS)}")
    if ai["dataset"]:
        load_dataset(root, project_id, ai["dataset"])
    if ai["agent"]:
        from . import agents
        agents.get(root, ai["agent"])
    p = projects.get(root, project_id)
    rec = projects._dir(root, project_id) / "project.json"
    p["ai"] = ai
    rec.write_text(json.dumps(p), encoding="utf-8")
    return ai


def train_project(root, project_id, on_event=None):
    """The Train button."""
    ai = projects.get(root, project_id).get("ai")
    if not ai or not ai.get("dataset"):
        raise TrainError("choose a dataset for this project first")
    return start(root, {"project": project_id, **ai, "name": f"{projects.get(root, project_id)['name']} model"}, on_event)
