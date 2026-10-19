"""Per-agent analytics: how an agent performs, what it costs, where it struggles, and whether evolving helped."""

import json
import time
from collections import Counter

from . import agents, diary, memory, usage
from .hooks import data_dir
from .jobs import FINAL
from .recorder import load_run


def _health(jobs_, st):
    reasons, level = [], "ok"

    def flag(lv, code, **kw):
        nonlocal level
        reasons.append({"code": code, **kw})
        level = "bad" if (lv == "bad" or level == "bad") else "warn"

    done = [j for j in jobs_ if j["status"] in FINAL]
    if not jobs_:
        return "idle", [{"code": "quiet"}]
    failed = sum(1 for j in done if j["status"] == "failed")
    if done and failed / len(done) >= 0.4 and len(done) >= 3:
        flag("bad", "failures", n=failed)
    elif failed:
        flag("warn", "failures", n=failed)
    stopped = sum(1 for j in done if j["status"] == "stopped")
    if stopped:
        flag("warn", "budget_stops", n=stopped)
    if st["tool_calls"] >= 5 and st["tool_errors"] / st["tool_calls"] >= 0.25:
        flag("warn", "tool_errors", pct=round(100 * st["tool_errors"] / st["tool_calls"]))
    if st["approvals_asked"] >= 3 and st["approvals_denied"] / st["approvals_asked"] >= 0.5:
        flag("warn", "denials", n=st["approvals_denied"])
    return level, reasons or [{"code": "ok"}]


def stats(root, agent_id, days=30, now=None):
    now = now or time.time()
    agent = agents.get(root, agent_id)
    since = now - days * 86400
    jobs_dir = data_dir(root) / "jobs"  # read-only: jobs are plain files, no runtime needed
    jobs_ = []
    for p in jobs_dir.glob("*/job.json") if jobs_dir.exists() else []:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if j["agent"] == agent_id and j["created"] >= since:
            jobs_.append(j)
    jobs_.sort(key=lambda j: j["created"])
    done = [j for j in jobs_ if j["status"] in FINAL]
    ok = [j for j in done if j["status"] == "succeeded"]
    sums = Counter()
    for j in jobs_:
        for k, v in j["metrics"].items():
            sums[k] += v
    tools, errors = Counter(), Counter()
    for j in jobs_[-60:]:
        run = data_dir(root) / "runs" / f"job-{j['id']}.jsonl"
        if run.exists():
            for e in load_run(run):
                if e["kind"] == "tool_call":
                    tools[e.get("tool", "?")] += 1
                elif e["kind"] == "tool_result" and str(e["content"]).startswith("ERROR"):
                    errors[str(e["content"])[7:70]] += 1
    tokens = sums["input_tokens"] + sums["output_tokens"]
    by_day = {}
    for j in jobs_:
        d = usage.day_of(j["created"])
        b = by_day.setdefault(d, {"date": d, "jobs": 0, "succeeded": 0, "tokens": 0})
        b["jobs"] += 1
        b["succeeded"] += j["status"] == "succeeded"
        b["tokens"] += j["metrics"]["input_tokens"] + j["metrics"]["output_tokens"]
    versions = {}
    for j in done:
        v = versions.setdefault(j.get("agent_version", 1), {"version": j.get("agent_version", 1), "jobs": 0, "succeeded": 0, "tokens": 0, "up": 0, "down": 0})
        v["jobs"] += 1
        v["succeeded"] += j["status"] == "succeeded"
        v["tokens"] += j["metrics"]["input_tokens"] + j["metrics"]["output_tokens"]
        fb = (j.get("feedback") or {}).get("rating")
        v["up"] += fb == 1
        v["down"] += fb == -1
    for v in versions.values():
        v["success_rate"] = round(v["succeeded"] / v["jobs"], 3)
        v["avg_tokens"] = round(v["tokens"] / v["jobs"])
    counts = Counter(j["status"] for j in jobs_)
    st = {k: sums[k] for k in ("tool_calls", "tool_errors", "approvals_asked", "approvals_denied", "files_written", "llm_calls")}
    level, reasons = _health(jobs_, st)
    n = max(len(done), 1)
    return {
        "agent": agent_id, "days": days, "version": agent["version"],
        "jobs": {"total": len(jobs_), "by_status": dict(counts), "success_rate": round(len(ok) / len(done), 3) if done else None,
                 "running": sum(1 for j in jobs_ if j["status"] not in FINAL)},
        "efficiency": {"avg_steps": round(sums["llm_calls"] / n, 1), "avg_tokens": round(tokens / n), "tokens_per_success": round(tokens / len(ok)) if ok else None,
                       "avg_duration_s": round(sum(j["metrics"]["duration_s"] for j in done) / n, 1), "avg_cost": round(sums["cost"] / n, 4)},
        "totals": {"input_tokens": sums["input_tokens"], "output_tokens": sums["output_tokens"], "cost": round(sums["cost"], 4), **st},
        "tool_usage": [{"tool": t, "count": c} for t, c in tools.most_common(8)],
        "top_errors": [{"error": e, "count": c} for e, c in errors.most_common(5)],
        "by_day": sorted(by_day.values(), key=lambda b: b["date"]),
        "versions": sorted(versions.values(), key=lambda v: v["version"]),
        "feedback": {"up": sum(1 for j in jobs_ if (j.get("feedback") or {}).get("rating") == 1), "down": sum(1 for j in jobs_ if (j.get("feedback") or {}).get("rating") == -1)},
        "knowledge": {"memories": len(memory.list_facts(root, agent_id)), "diary_entries": len(diary.entries(root, agent_id, 10_000))},
        "health": {"level": level, "reasons": reasons},
        "last_active": max((j["created"] for j in jobs_), default=None),
    }
