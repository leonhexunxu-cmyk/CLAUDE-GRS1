"""Agent Flight Recorder: log every agent step to JSONL, replay it, fork from any step."""

import json
import time
import uuid
from pathlib import Path

from .schema import KINDS, SCHEMA_VERSION, redact

try:
    import fcntl
except ImportError:  # Windows: best effort without locking
    fcntl = None


def append_event(path, kind, content, run_id=None, meta=None, **fields):
    """Append one event to a run file. Safe across processes (hooks run as separate processes).

    Step numbers continue from the file's last event; a `start` event is written first if the file is new.
    Credentials in `content` and string fields are redacted. `kind=None` only creates the start event.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = run_id or path.stem
    with open(path, "a+", encoding="utf-8") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = None
        for line in f:
            if line.strip():
                last = line
        step = json.loads(last)["step"] if last else -1
        out = []
        if step < 0:
            out.append({"step": 0, "kind": "start", "content": "", "meta": meta or {}})
            step = 0
        if kind is not None:
            out.append({"step": step + 1, "kind": kind, "content": content, **fields})
        for e in out:
            e.update(v=SCHEMA_VERSION, run_id=run_id, ts=time.time())
            for k, val in e.items():
                if isinstance(val, str):
                    e[k] = redact(val)
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        f.flush()
    return out[-1]["step"]


class Recorder:
    """Append-only JSONL log of an agent run.

    with Recorder("runs/a.jsonl") as rec:
        rec.log("prompt", "Fix the bug")
        rec.log("tool_call", "grep TODO", tool="bash")
    """

    def __init__(self, path, run_id=None, meta=None):
        self.path = Path(path)
        self.run_id = run_id or uuid.uuid4().hex[:8]
        append_event(self.path, None, "", run_id=self.run_id, meta=meta)
        self.step = len(load_run(self.path)) - 1

    def log(self, kind, content, **fields):
        self.step = append_event(self.path, kind, content, run_id=self.run_id, **fields)
        return self.step

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.log("error", f"{exc_type.__name__}: {exc}")
        return False


def load_run(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def replay(path, upto=None, width=100):
    """Return a human-readable timeline, optionally stopping at step `upto`."""
    events = load_run(path)
    if not events:
        return ""
    t0 = events[0]["ts"]
    lines = []
    for e in events:
        if upto is not None and e["step"] > upto:
            break
        text = " ".join(str(e["content"]).split())
        if len(text) > width:
            text = text[: width - 1] + "…"
        extra = f" [{e['tool']}]" if "tool" in e else ""
        lines.append(f"{e['step']:>3} +{e['ts'] - t0:6.2f}s {e['kind']:<11}{extra} {text}")
    return "\n".join(lines)


def fork_run(path, step, new_path):
    """Copy a run up to and including `step` into a new file, with a fresh run id.

    The caller resumes the agent from that state and keeps recording with
    Recorder(new_path, run_id=...) to explore a different path.
    """
    events = [e for e in load_run(path) if e["step"] <= step]
    if not events:
        raise ValueError("source run is empty")
    parent = events[0]["run_id"]
    new_id = uuid.uuid4().hex[:8]
    Path(new_path).parent.mkdir(parents=True, exist_ok=True)
    with open(new_path, "w", encoding="utf-8") as f:
        for e in events:
            e = dict(e, run_id=new_id)
            if e["step"] == 0:
                e["meta"] = dict(e.get("meta", {}), forked_from=parent, forked_at=step)
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return new_id


def summarize_run(path):
    """One-line facts about a run for listings."""
    events = load_run(path)
    if not events:
        return {"id": Path(path).stem, "events": 0}
    meta = events[0].get("meta", {})
    blocked = sum(1 for e in events if e["kind"] == "decision" and str(e["content"]).startswith("BLOCKED"))
    return {
        "id": Path(path).stem,
        "events": len(events) - 1,
        "started": events[0]["ts"],
        "last": events[-1]["ts"],
        "blocked": blocked,
        "errors": sum(1 for e in events if e["kind"] == "error"),
        "forked_from": meta.get("forked_from"),
        "source": meta.get("source"),
        "title": next((str(e["content"])[:80] for e in events if e["kind"] == "prompt"), ""),
    }
