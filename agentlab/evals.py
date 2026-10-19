"""Evaluation cases and gated auto-improvement.

Each agent can have test cases: a prompt plus checks on the answer (contains, does not contain, regex, exact,
valid JSON, max words). Auto-improvement takes an improvement proposal (from the agent's lessons), runs the
cases with the current and the proposed instructions, and accepts it only when the score goes up (or stays
equal, if allowed) and no case that passed before now fails. Otherwise the proposal is rejected with the reason.
Cases are answered as a chat (no tools run), so they test what the agent says and decides, not side effects.
"""

import json
import re
import threading
import time
import uuid

from . import agents, evolution, library, llm, usage
from .hooks import data_dir

CHECKS = ("contains", "not_contains", "regex", "equals", "json", "max_words")
_lock = threading.Lock()


class EvalError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _path(root, agent_id, kind="cases"):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,47}", agent_id or ""):
        raise EvalError("invalid agent id")
    return data_dir(root) / "evals" / f"{agent_id}.{kind}.json"


def _load(root, agent_id, kind="cases"):
    p = _path(root, agent_id, kind)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _store(root, agent_id, items, kind="cases"):
    p = _path(root, agent_id, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items), encoding="utf-8")


def _validate_check(c):
    if not isinstance(c, dict) or c.get("kind") not in CHECKS:
        raise EvalError(f"each check needs a kind among {list(CHECKS)}")
    v = c.get("value")
    if c["kind"] == "regex":
        try:
            re.compile(str(v))
        except re.error as e:
            raise EvalError(f"bad regex: {e}")
    if c["kind"] == "max_words":
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise EvalError("max_words needs a number")
    if c["kind"] in ("contains", "not_contains", "equals") and not str(v or ""):
        raise EvalError(f"{c['kind']} needs a value")
    return {"kind": c["kind"], "value": v if c["kind"] != "json" else None, "case_sensitive": bool(c.get("case_sensitive"))}


def cases(root, agent_id):
    return _load(root, agent_id)


def add_case(root, agent_id, prompt, checks, name=""):
    agents.get(root, agent_id)
    prompt = str(prompt or "").strip()
    if not prompt:
        raise EvalError("a prompt is required")
    if not isinstance(checks, list) or not checks:
        raise EvalError("add at least one check")
    case = {"id": uuid.uuid4().hex[:8], "name": str(name or prompt[:40])[:80], "prompt": prompt[:8000], "checks": [_validate_check(c) for c in checks][:10]}
    with _lock:
        _store(root, agent_id, _load(root, agent_id) + [case])
    return case


def delete_case(root, agent_id, cid):
    with _lock:
        items = _load(root, agent_id)
        kept = [c for c in items if c["id"] != cid]
        if len(kept) == len(items):
            raise EvalError("case not found", 404)
        _store(root, agent_id, kept)


def check(answer, c):
    a = str(answer)
    v = c.get("value")
    fold = (lambda s: s) if c.get("case_sensitive") else (lambda s: s.lower())
    if c["kind"] == "contains":
        return fold(str(v)) in fold(a)
    if c["kind"] == "not_contains":
        return fold(str(v)) not in fold(a)
    if c["kind"] == "equals":
        return fold(a.strip()) == fold(str(v).strip())
    if c["kind"] == "regex":
        return bool(re.search(str(v), a, 0 if c.get("case_sensitive") else re.I))
    if c["kind"] == "json":
        return llm.extract_json(a) is not None
    if c["kind"] == "max_words":
        return len(a.split()) <= int(v)
    return False


def run_suite(root, agent, chat_fn=None, instructions=None):
    """Answer every case with `agent` (optionally with other instructions). -> {score, passed, total, cases: [...]}."""
    chat_fn = chat_fn or llm.chat
    items = _load(root, agent["id"])
    if not items:
        raise EvalError("this agent has no evaluation cases yet")
    a = {**agent, "instructions": instructions if instructions is not None else agent["instructions"]}
    system = agents.system_prompt(a, "Evaluation", skills=library.skills_prompt(root, agent.get("skills", [])))
    out, passed, total = [], 0, 0
    for c in items:
        t0 = time.time()
        try:
            r = chat_fn(agent["provider"], system, [{"role": "user", "content": c["prompt"]}], None, max_tokens=agent["limits"]["max_output_tokens"],
                        temperature=0.0, project=root, retries=1)
            answer, err = r["text"], None
            usage.record(root, agent=agent["id"], provider=agent["provider"]["type"], model=r.get("model") or agent["provider"]["model"], input_tokens=r["usage"]["input"],
                         output_tokens=r["usage"]["output"], cost=llm.cost(agent["provider"], r["usage"]), ms=(time.time() - t0) * 1000, kind="eval")
        except llm.LLMError as e:
            answer, err = "", str(e)[:200]
        results = [{"kind": k["kind"], "value": k["value"], "ok": bool(not err and check(answer, k))} for k in c["checks"]]
        ok = sum(r["ok"] for r in results)
        passed += ok
        total += len(results)
        out.append({"id": c["id"], "name": c["name"], "answer": answer[:2000], "error": err, "checks": results, "passed": ok == len(results)})
    return {"score": round(passed / total, 4) if total else 0.0, "passed": passed, "total": total, "cases": out}


def auto_improve(root, agent_id, chat_fn=None, allow_equal=False, propose_fn=None):
    """Propose (or take the pending proposal), evaluate both versions, accept only on a real, regression-free gain."""
    agent = agents.get(root, agent_id)
    if not _load(root, agent_id):
        raise EvalError("add evaluation cases first: auto-improvement only accepts changes it can measure")
    pending = [p for p in evolution.proposals(root, agent_id) if p["status"] == "pending"]
    prop = pending[-1] if pending else (propose_fn or evolution.propose)(root, agent_id)
    before = run_suite(root, agent, chat_fn)
    after = run_suite(root, agent, chat_fn, prop["instructions"])
    was = {c["id"]: c["passed"] for c in before["cases"]}
    regressions = [c["name"] for c in after["cases"] if was.get(c["id"]) and not c["passed"]]
    gain = after["score"] - before["score"]
    if regressions:
        verdict, reason = "rejected", f"it breaks cases that passed before: {', '.join(regressions[:5])}"
    elif gain > 0 or (allow_equal and gain == 0):
        verdict, reason = "accepted", f"score {before['score']:.0%} → {after['score']:.0%} with no regressions"
    else:
        verdict, reason = "rejected", f"no improvement (score {before['score']:.0%} → {after['score']:.0%})"
    if verdict == "accepted":
        evolution.accept(root, agent_id, prop["id"])
    else:
        evolution.reject(root, agent_id, prop["id"])
    rec = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "proposal": prop["id"], "verdict": verdict, "reason": reason, "before": before["score"], "after": after["score"],
           "regressions": regressions, "version": agents.get(root, agent_id)["version"]}
    with _lock:
        _store(root, agent_id, (_load(root, agent_id, "runs") + [rec])[-100:], "runs")
    return {**rec, "details": {"before": before, "after": after}}


def history(root, agent_id):
    return list(reversed(_load(root, agent_id, "runs")))
