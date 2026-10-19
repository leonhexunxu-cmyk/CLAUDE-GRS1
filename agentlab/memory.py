"""Agent memory: short facts an agent (or you) wants remembered, retrieved into the prompt by relevance."""

import json
import re
import time
import uuid

from .hooks import data_dir

MAX_FACTS, MAX_FACT_CHARS = 500, 600
_WORD = re.compile(r"[a-z0-9]{3,}")


def _path(project, agent_id):
    return data_dir(project) / "agents" / agent_id / "memory.json"


def _load(project, agent_id):
    p = _path(project, agent_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(project, agent_id, facts):
    p = _path(project, agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts), encoding="utf-8")


def add(project, agent_id, fact, tags=(), importance=1, source="agent"):
    fact = " ".join(str(fact).split())[:MAX_FACT_CHARS]
    if not fact:
        raise ValueError("fact is empty")
    facts = _load(project, agent_id)
    for f in facts:
        if f["fact"].lower() == fact.lower():  # don't store the same fact twice, just refresh it
            f["ts"] = time.time()
            _save(project, agent_id, facts)
            return f
    rec = {"id": uuid.uuid4().hex[:8], "fact": fact, "tags": [str(t)[:30] for t in tags][:8], "importance": max(1, min(int(importance), 5)), "ts": time.time(), "source": source}
    facts.append(rec)
    if len(facts) > MAX_FACTS:  # forget the oldest, least important facts first
        facts.sort(key=lambda f: (f["importance"], f["ts"]))
        facts = facts[len(facts) - MAX_FACTS:]
    _save(project, agent_id, facts)
    return rec


def list_facts(project, agent_id):
    return sorted(_load(project, agent_id), key=lambda f: -f["ts"])


def forget(project, agent_id, mem_id):
    facts = _load(project, agent_id)
    kept = [f for f in facts if f["id"] != mem_id]
    _save(project, agent_id, kept)
    return len(kept) != len(facts)


def search(project, agent_id, query, k=5):
    """Facts ranked by word overlap with the query, then importance, then recency."""
    q = set(_WORD.findall(str(query).lower()))
    scored = []
    for f in _load(project, agent_id):
        words = set(_WORD.findall(f["fact"].lower())) | {t.lower() for t in f["tags"]}
        overlap = len(q & words)
        if overlap or not q:
            scored.append((overlap, f["importance"], f["ts"], f))
    scored.sort(key=lambda t: t[:3], reverse=True)
    return [t[3] for t in scored[:k]]


def as_prompt(project, agent_id, query, k=6):
    facts = search(project, agent_id, query, k)
    return "\n".join(f"- {f['fact']}" for f in facts)
