"""Parallel batches: the same task for several agents at once (compare), or many tasks at once (fan-out).

compare  - every agent gets the same prompt; with isolate=True each works on its own copy of the project, so the
           results can be compared side by side without the agents overwriting each other.
fan-out  - one agent, many prompts (one job each), all in the same project or each in its own copy.
Jobs still respect the runtime's parallel limit; extra jobs wait in the queue.
"""

import json
import threading
import time
import uuid

from . import projects
from .hooks import data_dir

MAX_ITEMS = 20
_lock = threading.Lock()


class BatchError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _path(root):
    return data_dir(root) / "batches.json"


def _load(root):
    p = _path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(root, items):
    _path(root).parent.mkdir(parents=True, exist_ok=True)
    _path(root).write_text(json.dumps(items[-200:]), encoding="utf-8")


def start(root, runtime, spec):
    """spec: {project, kind: compare|fanout, agents: [...] | agent, prompt | prompts: [...], isolate: bool, name}."""
    kind = spec.get("kind", "compare")
    project = projects.get(root, spec.get("project"))
    if kind == "compare":
        ids, prompt = spec.get("agents") or [], str(spec.get("prompt") or "").strip()
        if not isinstance(ids, list) or not 2 <= len(ids) <= MAX_ITEMS:
            raise BatchError(f"compare needs 2-{MAX_ITEMS} agents")
        if not prompt:
            raise BatchError("a prompt is required")
        items = [(a, prompt) for a in ids]
    elif kind == "fanout":
        prompts = [str(p).strip() for p in spec.get("prompts") or [] if str(p).strip()]
        if not spec.get("agent") or not 1 <= len(prompts) <= MAX_ITEMS:
            raise BatchError(f"fan-out needs an agent and 1-{MAX_ITEMS} prompts")
        items = [(spec["agent"], p) for p in prompts]
    else:
        raise BatchError("kind must be compare or fanout")
    isolate = bool(spec.get("isolate", kind == "compare"))
    bid = uuid.uuid4().hex[:8]
    entries = []
    for i, (agent_id, prompt) in enumerate(items):
        pid = project["id"]
        if isolate:
            pid = projects.clone(root, project["id"], f"{project['name']} · {kind} {i + 1}"[:80])["id"]
            projects.update(root, pid, batch=bid)
        try:
            job = runtime.start(pid, agent_id, prompt, overrides=spec.get("overrides"))
            entries.append({"job": job["id"], "agent": agent_id, "project": pid, "prompt": prompt[:200]})
        except Exception as e:
            if isolate:
                projects.delete(root, pid)
            entries.append({"job": None, "agent": agent_id, "project": None, "prompt": prompt[:200], "error": str(e)[:200]})
    rec = {"id": bid, "name": str(spec.get("name") or f"{kind} · {items[0][1][:40]}")[:80], "kind": kind, "project": project["id"], "isolate": isolate, "created": time.time(), "items": entries}
    with _lock:
        _save(root, _load(root) + [rec])
    return status(root, runtime, bid)


def status(root, runtime, bid):
    rec = next((b for b in _load(root) if b["id"] == bid), None)
    if not rec:
        raise BatchError("batch not found", 404)
    rows, done = [], 0
    for e in rec["items"]:
        row = dict(e)
        if e["job"]:
            try:
                j = runtime.get(e["job"])
                m = j["metrics"]
                row.update(status=j["status"], agent_name=j["agent_name"], percent=j["progress"]["percent"], summary=j["summary"], error=j["error"],
                           tokens=m["input_tokens"] + m["output_tokens"], cost=m["cost"], duration_s=m["duration_s"], tool_calls=m["tool_calls"], files_written=m["files_written"])
            except Exception:
                row["status"] = "missing"
        else:
            row["status"] = "failed"
        done += row["status"] in ("succeeded", "failed", "cancelled", "stopped", "missing")
        rows.append(row)
    ok = [r for r in rows if r["status"] == "succeeded"]
    best = lambda key: min(ok, key=lambda r: r[key])["job"] if ok else None
    return {**{k: rec[k] for k in ("id", "name", "kind", "project", "isolate", "created")}, "items": rows,
            "progress": {"done": done, "total": len(rows), "percent": round(100 * done / len(rows)) if rows else 100},
            "fastest": best("duration_s"), "cheapest": best("tokens"), "finished": done == len(rows)}


def list_batches(root, runtime):
    return [status(root, runtime, b["id"]) for b in reversed(_load(root))][:50]


def cancel(root, runtime, bid):
    st = status(root, runtime, bid)
    for r in st["items"]:
        if r["job"] and r["status"] in ("queued", "running", "waiting_approval"):
            try:
                runtime.cancel(r["job"])
            except Exception:
                pass
    return status(root, runtime, bid)
