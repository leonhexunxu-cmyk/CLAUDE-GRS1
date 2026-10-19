"""Dream mode: while you sleep, an agent quietly thinks.

Inside a sleep window you set, the agent reviews its diary and memories, tidies what it remembers, finds themes,
and writes ideas and a plan for tomorrow into a dream journal. It never runs tools, never touches project files and
never changes its own instructions: anything it wants to change becomes a proposal you approve. A token budget caps
each night, and you get a morning summary.
"""

import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import agents, diary, evolution, llm, memory, notifications, usage
from .hooks import data_dir

MIN_CALL_BUDGET = 800


def _path(root, agent_id):
    return data_dir(root) / "agents" / agent_id / "dreams.jsonl"


def journal(root, agent_id, limit=30):
    p = _path(root, agent_id)
    items = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        try:
            items.append(json.loads(line))
        except ValueError:
            pass
    return sorted(items, key=lambda d: -d["ts"])[:limit]


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def window_night(cfg, now):
    """The 'night' (date the window started, in the agent's time zone) if `now` is inside the sleep window, else None."""
    local = datetime.fromtimestamp(now, timezone(timedelta(minutes=cfg["tz_minutes"])))
    t, start, end = local.hour * 60 + local.minute, _minutes(cfg["start"]), _minutes(cfg["end"])
    if start == end:
        return None
    if start < end:
        return local.strftime("%Y-%m-%d") if start <= t < end else None
    if t >= start:
        return local.strftime("%Y-%m-%d")  # before midnight: tonight
    if t < end:
        return (local - timedelta(days=1)).strftime("%Y-%m-%d")  # after midnight: the night that began yesterday
    return None


def _words(text):
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def consolidate(root, agent_id, threshold=0.8):
    """Merge near-duplicate memories, keeping the most important wording. Returns how many were merged."""
    facts = memory.list_facts(root, agent_id)
    keep, merged = [], 0
    for f in sorted(facts, key=lambda f: (-f["importance"], -f["ts"])):
        w = _words(f["fact"])
        dup = next((k for k in keep if w and _words(k["fact"]) and len(w & _words(k["fact"])) / len(w | _words(k["fact"])) >= threshold), None)
        if dup:
            memory.forget(root, agent_id, f["id"])
            merged += 1
        else:
            keep.append(f)
    return merged


def _material(root, agent):
    entries = [e for e in diary.entries(root, agent["id"], 40)]
    return {"diary": [{"title": e["title"], "body": e["body"][:300], "lessons": e["lessons"], "outcome": e.get("outcome"), "mood": e["mood"]} for e in entries[:20]],
            "memories": [f["fact"] for f in memory.list_facts(root, agent["id"])[:40]],
            "recent_dreams": [d["insights"][:2] for d in journal(root, agent["id"], 3)]}


def _by_rule(material):
    lessons = [l for e in material["diary"] for l in e["lessons"]]
    words = {}
    for l in lessons:
        for w in _words(l):
            words[w] = words.get(w, 0) + 1
    themes = [w for w, c in sorted(words.items(), key=lambda x: -x[1]) if c >= 2][:5]
    failed = [e["title"] for e in material["diary"] if e.get("outcome") in ("failed", "stopped")][:3]
    return {"themes": themes, "insights": [f"Lessons keep mentioning “{t}”." for t in themes[:3]] or ["Not enough experience yet to see patterns."],
            "ideas": [], "worries": [f"Unfinished or failed: {t}" for t in failed], "plan_for_tomorrow": [f"Revisit: {t}" for t in failed], "memory_updates": []}


def dream(root, agent_id, now=None, force=False, chat=llm.chat):
    """Run one night's dream for an agent. Returns the journal entry, or None if it is not time / already done."""
    now = now or time.time()
    agent = agents.get(root, agent_id)
    cfg = agent["dream"]
    night = window_night(cfg, now)
    if not force and (not cfg["enabled"] or night is None):
        return None
    night = night or datetime.fromtimestamp(now, timezone(timedelta(minutes=cfg["tz_minutes"]))).strftime("%Y-%m-%d")
    if not force and any(d["night"] == night for d in journal(root, agent_id, 10)):
        return None  # one dream per night
    merged = consolidate(root, agent_id)
    material = _material(root, agent)
    result, source, spent = {"themes": [], "insights": [], "ideas": [], "worries": [], "plan_for_tomorrow": [], "memory_updates": []}, "rule", 0
    cap = agent["limits"].get("daily_token_cap")
    if agent["provider"]["type"] != "mock" and (material["diary"] or material["memories"]):
        for cycle in range(cfg["cycles"]):
            remaining = cfg["token_budget"] - spent
            if remaining < MIN_CALL_BUDGET or (cap and usage.tokens_today(root, agent_id, now) >= cap):
                break
            system = (f"You are {agent['name']}. It is night and you are dreaming: thinking quietly, with no tools and no one waiting. Review your recent diary and what you remember. "
                      "Find themes, notice mistakes you keep making, and imagine better ways of working. "
                      'Reply with ONLY JSON: {"themes": [str], "insights": [str], "ideas": [str], "worries": [str], "plan_for_tomorrow": [str], "memory_updates": [{"fact": str, "importance": 1-5}]}. Keep every list short.')
            user = json.dumps({**material, "so_far": {k: result[k] for k in ("themes", "insights", "ideas")}, "cycle": cycle + 1})
            t0 = time.time()
            try:
                reply = chat(agent["provider"], system, [{"role": "user", "content": user}], None, max_tokens=min(1500, remaining), project=root, retries=1)
            except llm.LLMError:
                break
            tokens = reply["usage"]["input"] + reply["usage"]["output"]
            spent += tokens
            usage.record(root, agent=agent_id, provider=agent["provider"]["type"], model=reply.get("model") or agent["provider"]["model"], input_tokens=reply["usage"]["input"],
                         output_tokens=reply["usage"]["output"], cost=llm.cost(agent["provider"], reply["usage"]), ms=(time.time() - t0) * 1000, ts=now, kind="dream")
            parsed = llm.extract_json(reply["text"])
            if not parsed:
                break
            before = sum(len(result[k]) for k in ("insights", "ideas"))
            for k in result:
                vals = parsed.get(k) or []
                if isinstance(vals, list):
                    result[k] += [v for v in vals if v not in result[k]][:6]
            source = "llm"
            if sum(len(result[k]) for k in ("insights", "ideas")) == before:
                break  # nothing new this pass: stop early to save tokens
    if source == "rule":
        result = _by_rule(material)
    added = 0
    if agent["memory_enabled"]:
        for u in result["memory_updates"][:5]:
            if isinstance(u, dict) and u.get("fact"):
                memory.add(root, agent_id, str(u["fact"]), tags=["dream"], importance=int(u.get("importance", 2) or 2), source="dream")
                added += 1
    entry = {"id": uuid.uuid4().hex[:8], "ts": now, "night": night, "source": source, "tokens": spent, "merged": merged, "memory_added": added,
             **{k: [str(x)[:300] for x in result[k]][:8] for k in ("themes", "insights", "ideas", "worries", "plan_for_tomorrow")}}
    p = _path(root, agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    proposal = None
    if agent["evolve"] == "suggest":
        proposal = evolution.maybe_auto_propose(root, agent)
    bits = [f"{len(entry['insights'])} insights", f"{len(entry['ideas'])} ideas"] + ([f"{merged} memories tidied"] if merged else []) + (["an improvement proposal is waiting"] if proposal else [])
    notifications.emit(root, "system", f"{agent['name']} dreamed last night", ", ".join(bits) + ". Open the dream journal to read it.", "info", {"page": "agents", "id": agent_id}, dedupe_key=f"dream:{agent_id}")
    return entry


class Dreamer:
    """Checks every few minutes whether any agent should be dreaming right now."""

    def __init__(self, root, on_event=None, interval=300, clock=time.time):
        self.root, self.on_event, self.interval, self.clock = root, on_event, interval, clock
        self._busy = set()
        self._stop = __import__("threading").Event()
        self._thread = None

    def tick(self, now=None):
        done = []
        for a in agents.list_agents(self.root):
            if a["dream"]["enabled"] and a["id"] not in self._busy:
                self._busy.add(a["id"])
                try:
                    e = dream(self.root, a["id"], now or self.clock())
                    if e:
                        done.append(a["id"])
                        if self.on_event:
                            self.on_event("dream_finished", f"{a['name']} finished dreaming", {"agent": a["id"]})
                except Exception:
                    pass
                finally:
                    self._busy.discard(a["id"])
        return done

    def start(self):
        import threading
        if self._thread:
            return
        def loop():
            while not self._stop.wait(self.interval):
                self.tick()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
