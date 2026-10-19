"""Agent Flight Recorder: log every agent step to JSONL, replay it, fork from any step."""

import json
import time
import uuid
from pathlib import Path

KINDS = ("prompt", "tool_call", "tool_result", "file_edit", "decision", "output", "error")


class Recorder:
    """Append-only JSONL log of an agent run.

    with Recorder("runs/a.jsonl") as rec:
        rec.log("prompt", "Fix the bug")
        rec.log("tool_call", "grep TODO", tool="bash")
    """

    def __init__(self, path, run_id=None, meta=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.step = 0
        self._f = open(self.path, "a", encoding="utf-8")
        self._write({"step": 0, "kind": "start", "content": "", "meta": meta or {}})

    def _write(self, event):
        event.update(run_id=self.run_id, ts=time.time())
        self._f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._f.flush()

    def log(self, kind, content, **fields):
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")
        self.step += 1
        self._write({"step": self.step, "kind": kind, "content": content, **fields})
        return self.step

    def close(self):
        if not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.log("error", f"{exc_type.__name__}: {exc}")
        self.close()
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
