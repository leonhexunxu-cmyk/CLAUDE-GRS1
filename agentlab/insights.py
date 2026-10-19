"""Agent health insights, computed from what agents actually did (recorded runs, tasks, hook errors)."""

import time
from collections import Counter

from .connect import status as connect_status
from .envs import Manager
from .hooks import data_dir
from .recorder import load_run

SOURCE_OF = {"claude-code": "claude-code", "kimi": "kimi_code_cli", "openclaw": "openclaw"}
NAMES = {"claude-code": "Claude Code", "kimi_code_cli": "Kimi Code", "openclaw": "OpenClaw", "other": "Other"}
DAY, ACTIVE_S, STALLED_S = 86400, 120, 300


def hook_errors(project, since):
    p = data_dir(project) / "hook-errors.log"
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            n += float(line.split("\t", 1)[0]) >= since
        except ValueError:
            pass
    return n


def _agent(rec, stats):
    """Turn raw counts into a level and a list of reasons (codes the UI translates)."""
    reasons = []
    level = "ok"

    def flag(lv, code, **kw):
        nonlocal level
        reasons.append({"code": code, **kw})
        level = "bad" if lv == "bad" or level == "bad" else "warn"

    steps, calls = stats["steps"], stats["tool_calls"]
    if stats["errors"]:
        flag("bad" if stats["errors"] / max(steps, 1) >= 0.1 else "warn", "errors", n=stats["errors"])
    if calls >= 5 and stats["blocked"] / calls >= 0.3:
        flag("warn", "blocks", pct=round(100 * stats["blocked"] / calls))
    if stats["stalled_min"]:
        flag("warn", "stalled", n=stats["stalled_min"])
    if rec["connected"] and stats["hook_errors"]:
        flag("warn", "hook_errors", n=stats["hook_errors"])
    if not reasons:
        if steps == 0:
            return "idle", [{"code": "quiet"}]
        reasons.append({"code": "ok"})
    return level, reasons


def compute(project, now=None):
    now = now or time.time()
    since = now - DAY
    runs_dir = data_dir(project) / "runs"
    per = {}
    hourly = [0] * 24
    tools = Counter()
    recent = []
    files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:300] if runs_dir.exists() else []
    for f in files:
        if f.stat().st_mtime < now - 7 * DAY:
            break
        try:
            events = load_run(f)
        except ValueError:
            continue
        if not events:
            continue
        src = (events[0].get("meta") or {}).get("source") or "other"
        if src == "task":
            continue
        src = src if src in NAMES else "other"
        s = per.setdefault(src, {"sessions": 0, "steps": 0, "tool_calls": 0, "blocked": 0, "errors": 0, "last": 0, "active": 0, "stalled_min": 0})
        recent_events = [e for e in events[1:] if e["ts"] >= since]
        last = events[-1]
        s["last"] = max(s["last"], last["ts"])
        if recent_events:
            s["sessions"] += 1
        for e in recent_events:
            s["steps"] += 1
            hourly[min(23, int((now - e["ts"]) // 3600))] += 1
            if e["kind"] == "tool_call":
                s["tool_calls"] += 1
                tools[e.get("tool") or "?"] += 1
            elif e["kind"] == "decision" and str(e["content"]).startswith("BLOCKED"):
                s["blocked"] += 1
            elif e["kind"] == "error":
                s["errors"] += 1
        idle = now - last["ts"]
        if len(events) > 1 and last["kind"] != "output" and idle < ACTIVE_S:
            s["active"] += 1
        elif last["kind"] == "tool_call" and STALLED_S <= idle < 3600:
            s["stalled_min"] = max(s["stalled_min"], int(idle // 60))
        if len(recent) < 8 and len(events) > 1:
            recent.append({"id": f.stem, "title": next((str(e["content"])[:70] for e in events if e["kind"] == "prompt"), f.stem),
                           "source": src, "last": last["ts"], "blocked": sum(1 for e in events if e["kind"] == "decision" and str(e["content"]).startswith("BLOCKED"))})
    herr = hook_errors(project, since)
    agents = []
    seen = set()
    for rec in connect_status(project):
        src = SOURCE_OF[rec["id"]]
        seen.add(src)
        stats = {**per.get(src, {"sessions": 0, "steps": 0, "tool_calls": 0, "blocked": 0, "errors": 0, "last": 0, "active": 0, "stalled_min": 0}), "hook_errors": herr}
        if not (rec["connected"] or rec["detected"] or stats["steps"]):
            continue
        level, reasons = _agent(rec, stats)
        if not rec["connected"] and stats["steps"] == 0:
            level, reasons = "off", [{"code": "not_connected"}]
        agents.append({"id": rec["id"], "name": rec["name"], "connected": rec["connected"], "detected": rec["detected"],
                       "level": level, "reasons": reasons, "stats": stats})
    for src, stats in per.items():
        if src not in seen:
            stats = {**stats, "hook_errors": 0}
            level, reasons = _agent({"connected": False}, stats)
            agents.append({"id": src, "name": NAMES[src], "connected": False, "detected": False, "level": level, "reasons": reasons, "stats": stats})
    mgr = Manager(project)
    task_list = [t for t in mgr.list() if t["created"] >= since]
    totals = {k: sum(a["stats"][k] for a in agents) for k in ("sessions", "steps", "tool_calls", "blocked", "errors")}
    return {
        "generated": now,
        "totals": {**totals, "active": sum(a["stats"]["active"] for a in agents), "hook_errors": herr},
        "agents": agents,
        "hourly": hourly[::-1],  # oldest first, last bucket = the current hour
        "tools": [{"tool": t, "count": c} for t, c in tools.most_common(6)],
        "recent": recent,
        "tasks": {"running": sum(t["status"] in ("queued", "running") for t in task_list),
                  "succeeded": sum(t["status"] == "succeeded" for t in task_list),
                  "failed": sum(t["status"] in ("failed", "timeout") for t in task_list),
                  "pending_review": sum(1 for t in task_list if t["changes"] and sum(t["changes"].values()) and not t["applied"] and not t["discarded"])},
    }
