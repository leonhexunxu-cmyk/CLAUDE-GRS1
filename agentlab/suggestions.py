"""AI suggestions: specific, explainable next steps drawn from your own data. Rule-based: no model calls, no cost.

Each suggestion says why it appears and offers one action. Dismissed suggestions are snoozed for a week.
"""

import json
import time

from . import accounts, agent_stats, agents, evolution, memory, notify, projects, runners, usage
from .hooks import data_dir
from .scheduler import Scheduler  # noqa: F401  (schedules are read from their file below)

SNOOZE_DAYS = 7


def _dismissed(root):
    p = data_dir(root) / "suggestions.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def dismiss(root, key):
    d = _dismissed(root)
    d[key] = time.time()
    p = data_dir(root) / "suggestions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")


def _s(key, level, title, text, page, label, ident=None):
    return {"key": key, "level": level, "title": title, "text": text, "action": {"page": page, "label": label, "id": ident}}


def compute(root, now=None):
    now = now or time.time()
    out = []
    ags = agents.list_agents(root)
    projs = projects.list_projects(root)
    if not ags:
        out.append(_s("first-agent", "tip", "Create your first agent", "Agents are models with instructions, permissions and a diary. Start with the demo model, or connect an API key or your own weights.", "agents", "Create an agent"))
    for a in ags:
        try:
            st = agent_stats.stats(root, a["id"], 30, now)
        except Exception:
            continue
        jobs_ = st["jobs"]
        done = sum(v for k, v in jobs_["by_status"].items() if k in ("succeeded", "failed", "stopped", "cancelled"))
        if done >= 3 and jobs_["success_rate"] is not None and jobs_["success_rate"] < 0.5:
            top = st["top_errors"][0]["error"] if st["top_errors"] else "see the job traces"
            out.append(_s(f"fails:{a['id']}", "warn", f"{a['name']} fails more than half its jobs", f"{jobs_['by_status'].get('failed', 0)} failures in the last 30 days. Most common error: {top}. Try clearer instructions, a stronger model or a higher step limit.", "agents", "Open agent", a["id"]))
        if jobs_["by_status"].get("stopped", 0) >= 2:
            out.append(_s(f"budget:{a['id']}", "tip", f"{a['name']} keeps hitting its limits", "Jobs were stopped by a step or token budget. Raise the limit, or split big goals into smaller jobs.", "agents", "Adjust limits", a["id"]))
        if jobs_["total"] >= 3 and not a["diary_enabled"]:
            out.append(_s(f"diary:{a['id']}", "tip", f"Turn on the diary for {a['name']}", "With the diary on, the agent reflects after each job and saves lessons it can reuse.", "agents", "Open agent", a["id"]))
        pending = [p for p in evolution.proposals(root, a["id"]) if p["status"] == "pending"]
        if pending:
            out.append(_s(f"proposal:{a['id']}", "info", f"{a['name']} has an improvement proposal", f"Based on {pending[0]['lessons_used']} lessons. Nothing changes until you accept.", "agents", "Review proposal", a["id"]))
        elif a["evolve"] == "off" and len(evolution.collect_lessons(root, a["id"])) >= 3:
            out.append(_s(f"evolve:{a['id']}", "tip", f"{a['name']} has learned several lessons", "Ask it for an improvement proposal to fold them into its instructions.", "agents", "Open agent", a["id"]))
        if a["mode"] == "auto" and a["authorizations"].get("network", "inherit") in ("inherit", "allow"):
            out.append(_s(f"auto-net:{a['id']}", "tip", f"{a['name']} runs fully automatic with network access", "It can run commands and reach the internet without asking. Consider ‘ask only for risky actions’, or deny network.", "agents", "Review permissions", a["id"]))
        src = a.get("source", {})
        if src.get("runner"):
            try:
                if runners.get(root, src["runner"])["status"] == "stopped":
                    out.append(_s(f"runner:{a['id']}", "warn", f"The local model for {a['name']} is stopped", "Start its runner before giving it work.", "agents", "Open agent", a["id"]))
            except runners.RunnerError:
                pass
    for p in projs:
        if p["quota_bytes"] and p["used_bytes"] / p["quota_bytes"] >= 0.8:
            out.append(_s(f"quota:{p['id']}", "warn", f"{p['name']} is {round(100 * p['used_bytes'] / p['quota_bytes'])}% full", "Agents will start failing to write files. Raise the space quota or delete what you do not need.", "projects", "Open project", p["id"]))
        data_files = [f for f in projects.tree(root, p["id"], 200) if f.endswith((".csv", ".jsonl", ".parquet")) or "/train" in f]
        if data_files and not p.get("ai"):
            out.append(_s(f"dataset:{p['id']}", "tip", f"{p['name']} has a dataset", f"Found {data_files[0]}. Pick a model and press Train to build a specialised model from it.", "projects", "Open project", p["id"]))
    acc = accounts.status(root)
    if projs and not any(v["connected"] for v in acc.values()):
        out.append(_s("connect-accounts", "tip", "Connect GitHub or Hugging Face", "Import repositories, datasets and model weights straight into a project.", "connectors", "Connect"))
    u = usage.summary(root, 8, now)
    days = u["by_day"]
    today, prior = days[-1], days[:-1]
    avg = sum(d["input"] + d["output"] for d in prior) / max(len(prior), 1)
    if avg > 0 and today["input"] + today["output"] > 2.5 * avg and today["input"] + today["output"] > 20000:
        out.append(_s("spike", "warn", "Token usage is far above your recent average", f"{today['input'] + today['output']:,} tokens today versus about {int(avg):,} per day. Check the Usage page for the agent responsible.", "usage", "See usage"))
    if usage.summary(root, 30, now)["totals"]["calls"] > 20 and not notify.has_webhooks(root):
        out.append(_s("webhook", "tip", "Get alerts outside the browser", "Add a webhook (Slack, Discord, ...) to be told when an agent needs approval or a job finishes.", "connectors", "Add a webhook"))
    sch = data_dir(root) / "schedules.json"
    for s in json.loads(sch.read_text(encoding="utf-8")) if sch.exists() else []:
        if not s["enabled"] and "paused" in s.get("note", ""):
            out.append(_s(f"sched:{s['id']}", "warn", f"Schedule “{s['name']}” was paused", s["note"] + ". Fix the cause, then re-enable it.", "schedules", "Open schedules", s["id"]))
    snoozed = {k for k, ts in _dismissed(root).items() if now - ts < SNOOZE_DAYS * 86400}
    order = {"warn": 0, "info": 1, "tip": 2}
    return sorted((s for s in out if s["key"] not in snoozed), key=lambda s: order[s["level"]])
