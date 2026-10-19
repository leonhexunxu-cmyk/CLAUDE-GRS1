"""Evolution: agents propose improvements to their own instructions, and you decide.

Proposals are built from diary lessons and your feedback. Nothing is ever applied automatically: you accept or
reject each proposal, every accepted change is a new instruction version, and you can roll back to any version.
Version performance (success rate, tokens, feedback) shows whether evolving actually helped.
"""

import json
import time
import uuid

from . import agents, diary, llm, usage
from .hooks import data_dir

SECTION = "## Lessons learned"
MAX_LESSONS, AUTO_AFTER = 10, 3


class EvolutionError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _path(root, agent_id):
    return data_dir(root) / "agents" / agent_id / "evolution.json"


def proposals(root, agent_id):
    p = _path(root, agent_id)
    return sorted(json.loads(p.read_text(encoding="utf-8")) if p.exists() else [], key=lambda x: -x["ts"])


def _save(root, agent_id, items):
    p = _path(root, agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items), encoding="utf-8")


def collect_lessons(root, agent_id, limit=30):
    seen, out = set(), []
    for e in diary.entries(root, agent_id, limit):
        for l in e["lessons"]:
            if l.lower() not in seen:
                seen.add(l.lower())
                out.append({"lesson": l, "source": e["source"], "outcome": e.get("outcome")})
    return out


def _by_rule(instructions, lessons):
    base = instructions.split(SECTION)[0].rstrip()
    items = [x["lesson"] for x in lessons][:MAX_LESSONS]
    text = (base + "\n\n" if base else "") + SECTION + " (added by evolution; edit freely)\n" + "\n".join(f"- {l}" for l in items)
    return {"instructions": text, "rationale": f"Added {len(items)} lessons from the diary and your feedback.", "changes": [f"+ {l}" for l in items]}


def propose(root, agent_id):
    agent = agents.get(root, agent_id)
    lessons = collect_lessons(root, agent_id)
    if not lessons:
        raise EvolutionError("nothing to learn from yet: run a few jobs (with the diary on) or leave feedback first")
    result, source = None, "rule"
    if agent["provider"]["type"] != "mock":
        system = ("You improve an AI agent's instructions. Keep what works, fold in the lessons, stay concise and concrete, do not add rules that contradict the original. "
                  'Reply with ONLY JSON: {"instructions": "<full new instructions>", "rationale": "<why>", "changes": ["<short bullet>", ...]}')
        user = json.dumps({"current_instructions": agent["instructions"], "lessons": [x["lesson"] for x in lessons][:20]})
        t0 = time.time()
        try:
            reply = llm.chat(agent["provider"], system, [{"role": "user", "content": user}], max_tokens=2000, project=root, retries=1)
            usage.record(root, agent=agent_id, provider=agent["provider"]["type"], model=reply.get("model") or agent["provider"]["model"], input_tokens=reply["usage"]["input"],
                         output_tokens=reply["usage"]["output"], cost=llm.cost(agent["provider"], reply["usage"]), ms=(time.time() - t0) * 1000, kind="evolution")
            parsed = llm.extract_json(reply["text"])
            if parsed and isinstance(parsed.get("instructions"), str) and 0 < len(parsed["instructions"]) <= 20000 and parsed["instructions"].strip() != agent["instructions"].strip():
                result, source = parsed, "llm"
        except llm.LLMError:
            pass
    result = result or _by_rule(agent["instructions"], lessons)
    if result["instructions"].strip() == agent["instructions"].strip():
        raise EvolutionError("the instructions already include these lessons")
    prop = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "status": "pending", "base_version": agent["version"], "instructions": result["instructions"],
            "rationale": str(result.get("rationale", ""))[:600], "changes": [str(c)[:200] for c in result.get("changes", [])][:12], "source": source,
            "lessons_used": len(lessons)}
    _save(root, agent_id, proposals(root, agent_id) + [prop])
    return prop


def _find(root, agent_id, pid):
    items = proposals(root, agent_id)
    for p in items:
        if p["id"] == pid:
            return items, p
    raise EvolutionError("proposal not found", 404)


def accept(root, agent_id, pid):
    items, prop = _find(root, agent_id, pid)
    if prop["status"] != "pending":
        raise EvolutionError("proposal was already decided")
    agent = agents.update(root, agent_id, {"instructions": prop["instructions"]}, reason=f"evolution {pid}")
    prop.update(status="accepted", decided=time.time(), version=agent["version"])
    _save(root, agent_id, items)
    return agent


def reject(root, agent_id, pid):
    items, prop = _find(root, agent_id, pid)
    if prop["status"] != "pending":
        raise EvolutionError("proposal was already decided")
    prop.update(status="rejected", decided=time.time())
    _save(root, agent_id, items)
    return prop


def rollback(root, agent_id, version):
    agent = agents.get(root, agent_id)
    old = next((h for h in agent["history"] if h["version"] == version), None)
    if not old:
        raise EvolutionError("that version is not in the history (only the last 50 are kept)", 404)
    return agents.update(root, agent_id, {"instructions": old["instructions"]}, reason=f"rollback to v{version}")


def maybe_auto_propose(root, agent):
    """After a job: if the agent opted in (evolve=suggest) and enough new lessons piled up, queue ONE pending proposal."""
    if agent["evolve"] != "suggest":
        return None
    items = proposals(root, agent["id"])
    if any(p["status"] == "pending" for p in items):
        return None
    since = items[0]["ts"] if items else 0
    fresh = [e for e in diary.entries(root, agent["id"], 200) if e["ts"] > since and e["lessons"]]
    if len(fresh) < AUTO_AFTER:
        return None
    try:
        return propose(root, agent["id"])
    except EvolutionError:
        return None
