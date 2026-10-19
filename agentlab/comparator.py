"""Model comparator: ask two (or more) of your models the same thing and see how they actually differ.

For every model it records the answer, tokens in and out, cost, latency, tokens per second and length, runs each
one several times when you ask for it (so a slow first call does not decide the winner), and compares the answers
with each other. Comparisons are saved so you can look back at what a model used to answer.

What it does not do: judge which answer is *better*. That needs a judge model and is a separate, opt-in step
(`judge`), and even then the verdict is one model's opinion, labelled as such.
"""

import json
import re
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import llm, playground, usage
from .hooks import data_dir

MAX_TARGETS, MAX_REPEATS, KEEP = 8, 5, 100
WORD = re.compile(r"\w+", re.U)


class CompareError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _file(root):
    return data_dir(root) / "comparisons.json"


def _load(root):
    p = _file(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save_all(root, items):
    p = _file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items[-KEEP:]), encoding="utf-8")


def _labels(targets):
    out = []
    for t in targets:
        base = str(t.get("label") or t.get("agent_id") or (t.get("provider") or {}).get("model") or "model")[:60]
        label, n = base, 2
        while label in out:
            label, n = f"{base} #{n}", n + 1
        out.append(label)
    return out


def _overlap(a, b):
    """Jaccard overlap of the words in two answers: 1.0 means the same vocabulary, 0 means nothing shared."""
    return round(len(a & b) / len(a | b), 3) if (a | b) else 0.0


def run(root, spec, chat_fn=None, workers=4):
    """spec: {targets: [{agent_id|provider, label}], prompt, system?, max_tokens?, temperature?, repeats?, save?}."""
    targets = spec.get("targets")
    if not isinstance(targets, list) or not 2 <= len(targets) <= MAX_TARGETS:
        raise CompareError(f"pick 2 to {MAX_TARGETS} models to compare")
    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        raise CompareError("write the question you want to ask them")
    try:
        repeats = max(1, min(int(spec.get("repeats") or 1), MAX_REPEATS))
    except (TypeError, ValueError):
        raise CompareError("repeats must be a number")
    base = {k: spec[k] for k in ("system", "max_tokens", "temperature") if spec.get(k) not in (None, "")}
    base["messages"] = [{"role": "user", "content": prompt}]
    base["widgets"] = bool(spec.get("widgets", False))
    labels = _labels(targets)

    def one(pair):
        t, label = pair
        who = {k: t[k] for k in ("agent_id", "provider") if k in t}
        runs, first, error = [], None, None
        for _ in range(repeats):
            t0 = time.time()
            try:
                r = playground.chat(root, {**base, **who}, chat_fn)
            except playground.PlaygroundError as e:
                error = str(e)
                break
            r["insights"]["latency_ms"] = r["insights"].get("latency_ms") or max(1, int((time.time() - t0) * 1000))
            runs.append(r)
            first = first or r
        if not runs:
            return {"label": label, "ok": False, "error": error or "no answer"}
        ins = [r["insights"] for r in runs]
        lat = sorted(i["latency_ms"] for i in ins)
        out_tokens = statistics.fmean(i["output_tokens"] for i in ins)
        cost = statistics.fmean(i["cost"] for i in ins)
        return {
            "label": label, "ok": True, "text": first["text"], "widgets": first.get("widgets", []),
            "model": first["model"], "provider": first["provider"], "finish": first["finish"],
            "runs": len(runs),
            "metrics": {
                "input_tokens": round(statistics.fmean(i["input_tokens"] for i in ins)),
                "output_tokens": round(out_tokens),
                "total_tokens": round(statistics.fmean(i["input_tokens"] + i["output_tokens"] for i in ins)),
                "cost": round(cost, 6),
                "cost_per_1k_output": round(cost / max(out_tokens, 1) * 1000, 6),
                "latency_ms": lat[len(lat) // 2],
                "latency_min_ms": lat[0], "latency_max_ms": lat[-1],
                "latency_spread_ms": lat[-1] - lat[0],
                "tokens_per_second": round(out_tokens / max(lat[len(lat) // 2] / 1000, 1e-6), 1),
                "words": len(first["text"].split()), "characters": len(first["text"]),
                "context_percent": first["insights"]["context"].get("percent"),
                "tokenizer": first["insights"].get("tokenizer"),
            },
        }

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as ex:
        results = list(ex.map(one, zip(targets, labels)))

    ok = [r for r in results if r["ok"]]
    bags = {r["label"]: set(w.lower() for w in WORD.findall(r["text"])) for r in ok}
    for r in ok:
        others = [k for k in bags if k != r["label"]]
        r["metrics"]["agreement"] = round(statistics.fmean(_overlap(bags[r["label"]], bags[k]) for k in others), 3) if others else None
    pairs = [{"a": a, "b": b, "overlap": _overlap(bags[a], bags[b])}
             for i, a in enumerate(bags) for b in list(bags)[i + 1:]]
    pick = lambda key, reverse=False: (max if reverse else min)(ok, key=lambda r: r["metrics"][key])["label"] if ok else None
    totals = {
        "tokens": sum(r["metrics"]["total_tokens"] for r in ok),
        "cost": round(sum(r["metrics"]["cost"] for r in ok), 6),
        "calls": sum(r["runs"] for r in ok),
    }
    winners = {
        "fastest": pick("latency_ms"),
        "cheapest": pick("cost") if any(r["metrics"]["cost"] for r in ok) else None,
        "fewest_tokens": pick("total_tokens"),
        "most_detailed": pick("words", True),
        "most_concise": pick("words"),
        "steadiest": pick("latency_spread_ms") if repeats > 1 else None,
    }
    rec = {
        "id": uuid.uuid4().hex[:8], "ts": time.time(), "prompt": prompt, "repeats": repeats,
        "system": base.get("system", ""), "results": results, "pairs": pairs, "winners": winners, "totals": totals,
        "note": "Winners are measurements, not a judgement of answer quality. Read the answers.",
    }
    if spec.get("save", True):
        _save_all(root, _load(root) + [{**rec, "results": [{k: v for k, v in r.items() if k != "widgets"} for r in rec["results"]]}])
    return rec


def history(root, limit=30):
    out = []
    for c in reversed(_load(root)):
        out.append({"id": c["id"], "ts": c["ts"], "prompt": c["prompt"][:160], "repeats": c.get("repeats", 1),
                    "labels": [r["label"] for r in c["results"]], "winners": c.get("winners", {}), "totals": c.get("totals", {})})
    return out[:limit]


def get(root, cid):
    c = next((x for x in _load(root) if x["id"] == cid), None)
    if not c:
        raise CompareError("comparison not found", 404)
    return c


def delete(root, cid):
    items = _load(root)
    kept = [x for x in items if x["id"] != cid]
    if len(kept) == len(items):
        raise CompareError("comparison not found", 404)
    _save_all(root, kept)


JUDGE_SYSTEM = ("You compare answers from different AI models to the same question. Be strict and concrete. "
                'Reply with ONLY JSON: {"ranking": ["<label best>", ...], "scores": [{"label": str, "score": 1-10, "why": "<one sentence>"}], '
                '"summary": "<two sentences on how they differ>"}')


def judge(root, comparison, provider, chat_fn=None):
    """Optional: ask one model to rank the answers. It is one model's opinion and is labelled as such."""
    ok = [r for r in comparison["results"] if r["ok"]]
    if len(ok) < 2:
        raise CompareError("need at least two answers to judge")
    chat_fn = chat_fn or llm.chat
    try:
        provider = llm.validate_provider(provider)
    except ValueError as e:
        raise CompareError(str(e))
    body = {"question": comparison["prompt"], "answers": [{"label": r["label"], "answer": r["text"][:4000]} for r in ok]}
    t0 = time.time()
    try:
        r = chat_fn(provider, JUDGE_SYSTEM, [{"role": "user", "content": json.dumps(body)}], None, max_tokens=900, temperature=0.0, project=root, retries=1)
    except llm.LLMError as e:
        raise CompareError(f"the judge could not answer: {e}", 502)
    usage.record(root, agent="-", provider=provider["type"], model=r.get("model") or provider["model"], input_tokens=r["usage"]["input"],
                 output_tokens=r["usage"]["output"], cost=llm.cost(provider, r["usage"]), ms=(time.time() - t0) * 1000, kind="compare")
    parsed = llm.extract_json(r["text"]) or {}
    labels = {x["label"] for x in ok}
    ranking = [l for l in (parsed.get("ranking") or []) if l in labels]
    scores = [{"label": s.get("label"), "score": s.get("score"), "why": str(s.get("why", ""))[:300]}
              for s in (parsed.get("scores") or []) if isinstance(s, dict) and s.get("label") in labels]
    if not ranking and not scores:
        raise CompareError("the judge did not return a usable ranking (try a stronger model)")
    return {"by": provider.get("label") or provider["model"], "ranking": ranking, "scores": scores,
            "summary": str(parsed.get("summary", ""))[:600], "caveat": "One model's opinion, not a measurement."}
