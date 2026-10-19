"""Agent diary: a short first-person reflection after each job, plus your own notes and feedback.

The diary is where learning happens: lessons from entries are saved to the agent's memory, and evolution
uses them to propose better instructions. Reflections use the agent's own model (a small, separate call);
if that fails the entry is written by simple rules so the diary never has gaps.
"""

import json
import time
import uuid

from . import agents, llm, memory, usage
from .hooks import data_dir
from .recorder import load_run

MOODS = ("proud", "neutral", "frustrated", "curious")


def _path(root, agent_id):
    return data_dir(root) / "agents" / agent_id / "diary.jsonl"


def entries(root, agent_id, limit=50):
    p = _path(root, agent_id)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return sorted(out, key=lambda e: -e["ts"])[:limit]


def add_entry(root, agent_id, *, title, body, lessons=(), mood="neutral", job=None, project=None, outcome=None, source="auto", metrics=None):
    entry = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "title": str(title)[:120], "body": str(body)[:2000],
             "lessons": [str(x)[:200] for x in lessons][:3], "mood": mood if mood in MOODS else "neutral", "job": job, "project": project,
             "outcome": outcome, "source": source, "metrics": metrics or {}}
    p = _path(root, agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def add_note(root, agent_id, text, title=""):
    text = str(text or "").strip()
    if not text:
        raise ValueError("note is empty")
    return add_entry(root, agent_id, title=title or "A note from you", body=text, source="user")


def delete_entry(root, agent_id, entry_id):
    p = _path(root, agent_id)
    items = [e for e in reversed(entries(root, agent_id, 10_000))]
    kept = [e for e in items if e["id"] != entry_id]
    if len(kept) == len(items):
        return False
    p.write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    return True


def digest(root, job):
    """The facts of a job, small enough to hand to a model (or to write an entry from by rule)."""
    errors, tools = [], {}
    run = data_dir(root) / "runs" / f"job-{job['id']}.jsonl"
    if run.exists():
        for e in load_run(run):
            if e["kind"] == "tool_call":
                tools[e.get("tool", "?")] = tools.get(e.get("tool", "?"), 0) + 1
            elif e["kind"] == "tool_result" and str(e["content"]).startswith("ERROR"):
                errors.append(str(e["content"])[:160])
    return {"task": job["prompt"][:500], "status": job["status"], "result": (job.get("summary") or job.get("error") or job.get("stop_reason") or "")[:500],
            "tools": tools, "errors": errors[:3], "metrics": job["metrics"], "feedback": job.get("feedback")}


def _by_rule(d):
    m = d["metrics"]
    lessons = []
    if m["tool_errors"]:
        lessons.append(f"Check paths and arguments before calling tools ({m['tool_errors']} tool errors last time).")
    if d["status"] == "stopped":
        lessons.append(f"Break big goals into smaller steps ({d['result'] or 'hit a limit'}).")
    if m["approvals_denied"]:
        lessons.append("When an action is denied, propose a different approach instead of retrying it.")
    if d["status"] == "failed" and d["result"]:
        lessons.append(f"Failure to avoid: {d['result'][:120]}")
    mood = {"succeeded": "proud", "failed": "frustrated", "stopped": "frustrated"}.get(d["status"], "neutral")
    body = (f"I worked on: {d['task'][:160]}. It {d['status']} after {m['llm_calls']} model calls and {m['tool_calls']} tool calls"
            f" ({m['input_tokens'] + m['output_tokens']} tokens).")
    if d["errors"]:
        body += f" Things that went wrong: {'; '.join(d['errors'][:2])}"
    return {"title": f"{d['status'].capitalize()}: {d['task'][:60]}", "body": body, "lessons": lessons, "mood": mood}


def reflect(root, job, agent):
    """Write (and store) the diary entry for a finished job. Returns the entry."""
    d = digest(root, job)
    result, source = None, "rule"
    if agent["provider"]["type"] != "mock":
        system = (f"You are {agent['name']}. Write a short private diary entry about the task you just worked on, in first person. "
                  'Reply with ONLY JSON: {"title": str, "body": "2-4 sentences", "lessons": [up to 3 short, actionable lessons], "mood": "proud|neutral|frustrated|curious"}')
        t0 = time.time()
        try:
            reply = llm.chat(agent["provider"], system, [{"role": "user", "content": json.dumps(d)}], max_tokens=400, project=root, retries=1)
            usage.record(root, agent=agent["id"], provider=agent["provider"]["type"], model=reply.get("model") or agent["provider"]["model"], input_tokens=reply["usage"]["input"],
                         output_tokens=reply["usage"]["output"], cost=llm.cost(agent["provider"], reply["usage"]), ms=(time.time() - t0) * 1000, job=job["id"], project_id=job["project"], kind="diary")
            parsed = llm.extract_json(reply["text"])
            if parsed and parsed.get("body"):
                result, source = parsed, "auto"
        except llm.LLMError:
            pass
    result = result or _by_rule(d)
    entry = add_entry(root, agent["id"], title=result.get("title") or f"{d['status'].capitalize()}", body=result["body"], lessons=result.get("lessons") or [],
                      mood=result.get("mood", "neutral"), job=job["id"], project=job["project"], outcome=d["status"], source=source, metrics=job["metrics"])
    if agent["memory_enabled"]:
        for lesson in entry["lessons"]:
            memory.add(root, agent["id"], lesson, tags=["lesson"], importance=2, source="diary")
    return entry


def learn_from_feedback(root, job):
    """Your thumbs up/down (with a note) becomes a diary entry and, if there is a note, a memory."""
    fb = job.get("feedback")
    if not fb:
        return None
    agent = agents.get(root, job["agent"])
    note = fb["note"].strip()
    entry = add_entry(root, agent["id"], title=("👍 " if fb["rating"] > 0 else "👎 ") + job["prompt"][:60], body=note or ("The user liked this result." if fb["rating"] > 0 else "The user was not happy with this result."),
                      lessons=[f"User feedback: {note}"] if note else [], mood="proud" if fb["rating"] > 0 else "frustrated", job=job["id"], project=job["project"],
                      outcome=job["status"], source="feedback")
    if note and agent["memory_enabled"]:
        memory.add(root, agent["id"], f"User feedback: {note}", tags=["feedback"], importance=3, source="feedback")
    return entry
