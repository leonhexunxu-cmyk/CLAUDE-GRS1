"""Usage log and insights: every model call is recorded (tokens, cost estimate, latency) for budgets and charts."""

import json
import time
from collections import defaultdict
from datetime import datetime, timezone

from .hooks import data_dir


def _path(project):
    return data_dir(project) / "usage.jsonl"


def record(project, *, agent, provider, model, input_tokens, output_tokens, cost=0.0, ms=0, job=None, room=None, project_id=None, ok=True, ts=None):
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": ts or time.time(), "agent": agent, "provider": provider, "model": model, "in": int(input_tokens), "out": int(output_tokens),
            "cost": float(cost), "ms": int(ms), "job": job, "room": room, "project": project_id, "ok": bool(ok)}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def _entries(project, since=0):
    p = _path(project)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e["ts"] >= since:
            yield e


def day_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def tokens_today(project, agent_id, now=None):
    """Tokens (input + output) this agent used since 00:00 UTC today."""
    today = day_of(now or time.time())
    return sum(e["in"] + e["out"] for e in _entries(project) if e["agent"] == agent_id and day_of(e["ts"]) == today)


def summary(project, days=14, now=None):
    now = now or time.time()
    since = now - days * 86400
    tot = {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "errors": 0}
    by = {k: defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cost": 0.0}) for k in ("day", "agent", "model", "project")}
    for e in _entries(project, since):
        tot["calls"] += 1
        tot["input"] += e["in"]
        tot["output"] += e["out"]
        tot["cost"] += e["cost"]
        tot["errors"] += 0 if e["ok"] else 1
        for k, key in (("day", day_of(e["ts"])), ("agent", e["agent"]), ("model", f"{e['provider']}:{e['model']}"), ("project", e["project"] or "-")):
            b = by[k][key]
            b["calls"] += 1
            b["input"] += e["in"]
            b["output"] += e["out"]
            b["cost"] += e["cost"]
    days_list = [day_of(now - i * 86400) for i in range(days - 1, -1, -1)]
    return {"days": days, "totals": {**tot, "cost": round(tot["cost"], 4)},
            "by_day": [{"date": d, **by["day"].get(d, {"calls": 0, "input": 0, "output": 0, "cost": 0.0})} for d in days_list],
            **{f"by_{k}": sorted(({"key": key, **{**v, "cost": round(v["cost"], 4)}} for key, v in by[k].items()), key=lambda r: -(r["input"] + r["output"]))
               for k in ("agent", "model", "project")}}
