"""Agent chat rooms: several agents (any models) talk like people and cooperate on a goal.

How a turn works: the next speaker is the agent @mentioned in the last message, otherwise the next one in the
rotation (nobody speaks twice in a row). Each agent sees the conversation with names, its own instructions and the
room goal. In "task" mode the room stops when an agent says it is DONE, and the last message is the result; the
manager (if the room has one) speaks first and last. Rooms only talk: no tools run, so nothing changes on disk
until you turn the result into a job.
"""

import json
import re
import threading
import time
import uuid

from . import agents, llm, usage
from .hooks import data_dir

MODES = ("chat", "task")
MAX_AGENTS, MAX_MESSAGES = 8, 400
DONE = re.compile(r"(^|\n)\s*DONE\b", re.I)
_lock = threading.RLock()
_running = {}


class RoomError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _path(root, rid):
    if not re.fullmatch(r"[a-f0-9]{8}", rid or ""):
        raise RoomError("room not found", 404)
    return data_dir(root) / "rooms" / f"{rid}.json"


def _save(root, room):
    p = _path(root, room["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(room), encoding="utf-8")
    tmp.replace(p)


def get(root, rid):
    p = _path(root, rid)
    if not p.exists():
        raise RoomError("room not found", 404)
    room = json.loads(p.read_text(encoding="utf-8"))
    room["running"] = rid in _running
    return room


def list_rooms(root):
    d = data_dir(root) / "rooms"
    out = []
    for p in d.glob("*.json") if d.exists() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": r["id"], "name": r["name"], "mode": r["mode"], "agents": r["agents"], "messages": len(r["messages"]), "status": r["status"], "updated": r["updated"]})
        except ValueError:
            pass
    return sorted(out, key=lambda r: -r["updated"])


def create(root, spec):
    ids = spec.get("agents") or []
    if not isinstance(ids, list) or not 2 <= len(ids) <= MAX_AGENTS or len(set(ids)) != len(ids):
        raise RoomError(f"a room needs 2-{MAX_AGENTS} different agents")
    for a in ids:
        agents.get(root, a)
    mode = spec.get("mode", "chat")
    if mode not in MODES:
        raise RoomError("mode must be chat or task")
    manager = spec.get("manager")
    if manager and manager not in ids:
        raise RoomError("the manager must be one of the room's agents")
    try:
        max_turns = max(1, min(int(spec.get("max_turns") or 12), 60))
    except (TypeError, ValueError):
        raise RoomError("max_turns must be a number")
    room = {"id": uuid.uuid4().hex[:8], "name": str(spec.get("name") or "Room").strip()[:80], "mode": mode, "goal": str(spec.get("goal") or "")[:4000], "agents": ids,
            "manager": manager, "max_turns": max_turns, "messages": [], "status": "idle", "result": None, "created": time.time(), "updated": time.time(),
            "metrics": {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}}
    _save(root, room)
    return room


def delete(root, rid):
    stop(root, rid)
    _path(root, rid).unlink(missing_ok=True)


def post(root, rid, text, author="you"):
    text = str(text or "").strip()
    if not text or len(text) > 20000:
        raise RoomError("a message is required (max 20,000 characters)")
    with _lock:
        room = get(root, rid)
        room["messages"].append({"id": uuid.uuid4().hex[:8], "from": author, "name": "You" if author == "you" else author, "text": text, "ts": time.time()})
        room["messages"] = room["messages"][-MAX_MESSAGES:]
        if room["status"] == "done":
            room.update(status="idle", result=None)
        room["updated"] = time.time()
        _save(root, room)
    return room


def _names(root, room):
    out = {}
    for a in room["agents"]:
        try:
            out[a] = agents.get(root, a)
        except agents.AgentError:
            pass
    return out


def next_speaker(room, members):
    """@mention wins; otherwise rotate, never the same speaker twice in a row; the manager opens a task room."""
    msgs = room["messages"]
    last = msgs[-1] if msgs else None
    ids = [a for a in room["agents"] if a in members]
    if not ids:
        raise RoomError("none of this room's agents exist any more")
    if last:
        for a in ids:
            if a != last["from"] and re.search(r"@" + re.escape(members[a]["name"]) + r"\b", last["text"], re.I):
                return a
    agent_turns = [m["from"] for m in msgs if m["from"] in members]
    if not agent_turns:
        return room["manager"] if room["manager"] in ids else ids[0]
    i = ids.index(agent_turns[-1]) if agent_turns[-1] in ids else -1
    nxt = ids[(i + 1) % len(ids)]
    return nxt if not last or nxt != last["from"] else ids[(i + 2) % len(ids)]


def _system(room, agent, members):
    others = ", ".join(f"{m['name']}" + (f" ({m['role']})" if m.get("role") else "") for a, m in members.items() if a != agent["id"])
    parts = [f"You are {agent['name']}" + (f", {agent['role']}." if agent.get("role") else "."), agent["instructions"].strip(),
             f"You are in a group conversation with the user and these colleagues: {others}. Talk naturally and briefly, like a person in a team chat. "
             "Build on what others said, disagree when you have a reason, and ask a colleague directly with @Name when you need them. Do not repeat yourself or speak for others."]
    if room["goal"]:
        parts.append(f"The goal of this room: {room['goal']}")
    if room["mode"] == "task":
        parts.append("You are cooperating on the goal. Split the work, contribute your part concretely, and when the team has a complete answer, "
                     "write it in full and end with a line containing only DONE." + (" You are the manager: open by proposing a plan and who does what, and close with the final answer." if room["manager"] == agent["id"] else ""))
    if agent["preferences"].get("language"):
        parts.append(f"Reply in {agent['preferences']['language']}.")
    return "\n\n".join(p for p in parts if p)


def _transcript(room, agent_id):
    """Own messages are the assistant's; everyone else's are user turns labelled with their name."""
    out = []
    for m in room["messages"][-60:]:
        role = "assistant" if m["from"] == agent_id else "user"
        text = m["text"] if role == "assistant" else f"[{m['name']}]: {m['text']}"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "[Room]: the conversation starts."})
    if out[-1]["role"] != "user":
        out.append({"role": "user", "content": "[Room]: continue."})
    return out


def step(root, rid, chat_fn=None, speaker=None):
    """One agent speaks. Returns the new message (or None if the room is finished)."""
    chat_fn = chat_fn or llm.chat
    room = get(root, rid)
    if room["status"] == "done":
        return None
    members = _names(root, room)
    sid = speaker or next_speaker(room, members)
    if sid not in members:
        raise RoomError("that agent is not in this room")
    agent = members[sid]
    t0 = time.time()
    try:
        r = chat_fn(agent["provider"], _system(room, agent, members), _transcript(room, sid), None,
                    max_tokens=min(agent["limits"]["max_output_tokens"], 2048), temperature=agent["limits"]["temperature"], project=root, retries=1)
    except llm.LLMError as e:
        usage.record(root, agent=sid, provider=agent["provider"]["type"], model=agent["provider"]["model"], input_tokens=0, output_tokens=0, ms=(time.time() - t0) * 1000, room=rid, kind="room", ok=False)
        raise RoomError(f"{agent['name']} could not answer: {e}", 502)
    text = re.sub(r"^\s*\[" + re.escape(agent["name"]) + r"\]:\s*", "", r["text"].strip()) or "…"
    cost = llm.cost(agent["provider"], r["usage"])
    usage.record(root, agent=sid, provider=agent["provider"]["type"], model=r.get("model") or agent["provider"]["model"], input_tokens=r["usage"]["input"], output_tokens=r["usage"]["output"],
                 cost=cost, ms=(time.time() - t0) * 1000, room=rid, kind="room")
    with _lock:
        room = get(root, rid)
        done = room["mode"] == "task" and bool(DONE.search(text))
        clean = DONE.sub("", text).strip() if done else text
        msg = {"id": uuid.uuid4().hex[:8], "from": sid, "name": agent["name"], "text": clean, "ts": time.time(), "tokens": r["usage"]["input"] + r["usage"]["output"]}
        room["messages"] = (room["messages"] + [msg])[-MAX_MESSAGES:]
        m = room["metrics"]
        m.update(turns=m["turns"] + 1, input_tokens=m["input_tokens"] + r["usage"]["input"], output_tokens=m["output_tokens"] + r["usage"]["output"], cost=round(m["cost"] + cost, 6))
        if done:
            room.update(status="done", result={"text": clean, "by": agent["name"], "ts": time.time()})
        room["updated"] = time.time()
        _save(root, room)
    return msg


def run(root, rid, turns=None, chat_fn=None, background=True, on_done=None):
    """Let the agents talk for up to `turns` turns (default: the room's max_turns), in the background."""
    room = get(root, rid)
    turns = max(1, min(int(turns or room["max_turns"]), 60))
    if rid in _running:
        raise RoomError("the room is already talking")
    stop_ev = threading.Event()
    _running[rid] = stop_ev

    def go():
        try:
            for _ in range(turns):
                if stop_ev.is_set() or step(root, rid, chat_fn) is None or get(root, rid)["status"] == "done":
                    break
        except RoomError as e:
            with _lock:
                r = get(root, rid)
                r["messages"].append({"id": uuid.uuid4().hex[:8], "from": "system", "name": "agentlab", "text": str(e), "ts": time.time()})
                _save(root, r)
        finally:
            _running.pop(rid, None)
            if on_done:
                try:
                    on_done(get(root, rid))
                except Exception:
                    pass
    if background:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()
    return get(root, rid)


def stop(root, rid):
    ev = _running.get(rid)
    if ev:
        ev.set()
    return bool(ev)
