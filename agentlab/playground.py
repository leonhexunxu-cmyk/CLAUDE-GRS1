"""Playground: talk to any model or agent directly, with attached files, widgets, and per-reply insights."""

import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import agents, library, llm, tokenizers, usage, widgets

MAX_MESSAGES_CHARS, MAX_ATTACH_TOTAL, MAX_ATTACH_ONE = 400_000, 300_000, 100_000


class PlaygroundError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _attachments_text(attachments):
    if not attachments:
        return ""
    if not isinstance(attachments, list) or len(attachments) > 10:
        raise PlaygroundError("attach at most 10 files")
    total, parts = 0, []
    for a in attachments:
        name, content = str(a.get("name", "file"))[:100], a.get("content")
        if not isinstance(content, str):
            raise PlaygroundError(f"{name}: only text files can be attached")
        if len(content) > MAX_ATTACH_ONE:
            raise PlaygroundError(f"{name} is too large to attach ({len(content):,} characters; max {MAX_ATTACH_ONE:,})")
        total += len(content)
        if total > MAX_ATTACH_TOTAL:
            raise PlaygroundError("attachments are too large in total")
        parts.append(f"[Attached file: {name}]\n````\n{content}\n````")
    return "\n\n".join(parts)


def _resolve(root, spec):
    """-> (provider, agent or None)."""
    if spec.get("agent_id"):
        try:
            a = agents.get(root, spec["agent_id"])
        except agents.AgentError as e:
            raise PlaygroundError(str(e), e.code)
        return a["provider"], a
    try:
        return llm.validate_provider(spec.get("provider") or llm.PRESETS["mock"]), None
    except ValueError as e:
        raise PlaygroundError(str(e))


def _offline_reply(last_user):
    w = widgets.generate_offline(last_user)
    if w:
        return f"Here is a {w['title'].lower()} widget (built from a template, because this is the demo model).\n\n```widget-html\n{w['html']}\n```"
    return "I'm the demo model, so I can only build template widgets here (a calculator, timer, to-do list, counter, dice, colour picker, unit converter or pomodoro). Connect a real model to chat freely."


def chat(root, spec, chat_fn=None):
    """One reply. spec: agent_id | provider, system, messages, temperature, max_tokens, attachments, widgets (bool)."""
    chat_fn = chat_fn or llm.chat
    provider, agent = _resolve(root, spec)
    msgs = spec.get("messages")
    if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) for m in msgs):
        raise PlaygroundError("messages must be a non-empty list of {role: user|assistant, content: text}")
    if msgs[-1]["role"] != "user":
        raise PlaygroundError("the last message must be from the user")
    if sum(len(m["content"]) for m in msgs) > MAX_MESSAGES_CHARS:
        raise PlaygroundError("the conversation is too long; start a new one")
    msgs = [dict(m) for m in msgs]
    extra = _attachments_text(spec.get("attachments"))
    if extra:
        msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + extra
    use_widgets = spec.get("widgets", True)
    if agent:
        system = agents.system_prompt(agent, "Playground", skills=library.skills_prompt(root, agent.get("skills", [])))
        system = (spec["system"] + "\n\n" + system) if spec.get("system") else system
    else:
        system = str(spec.get("system", ""))
    if use_widgets:
        system = (system + "\n\n" if system else "") + widgets.PROTOCOL
    try:
        max_tokens = max(16, min(int(spec.get("max_tokens") or (agent["limits"]["max_output_tokens"] if agent else 1024)), 32768))
        temperature = spec.get("temperature", agent["limits"]["temperature"] if agent else None)
        temperature = None if temperature in (None, "") else max(0.0, min(float(temperature), 2.0))
    except (TypeError, ValueError):
        raise PlaygroundError("max_tokens and temperature must be numbers")
    t0 = time.time()
    try:
        if provider["type"] == "mock" and not provider.get("script"):
            text = _offline_reply(next((m["content"] for m in reversed(msgs) if m["role"] == "user"), ""))
            reply = {"text": text, "tool_calls": [], "usage": {"input": tokenizers.count(system + "".join(m["content"] for m in msgs), provider.get("tokenizer"), root), "output": tokenizers.count(text, provider.get("tokenizer"), root)}, "stop": "end", "model": "demo"}
        else:
            reply = chat_fn(provider, system, msgs, None, max_tokens=max_tokens, temperature=temperature, project=root, retries=1)
    except llm.LLMError as e:
        usage.record(root, agent=(agent or {}).get("id", "-"), provider=provider["type"], model=provider["model"], input_tokens=0, output_tokens=0, ms=(time.time() - t0) * 1000, kind="playground", ok=False)
        raise PlaygroundError(str(e), 502)
    ms = max(1, int((time.time() - t0) * 1000))
    u = reply["usage"]
    cost = llm.cost(provider, u)
    usage.record(root, agent=(agent or {}).get("id", "-"), provider=provider["type"], model=reply.get("model") or provider["model"], input_tokens=u["input"], output_tokens=u["output"], cost=cost, ms=ms, kind="playground")
    clean, found, errors = widgets.extract(reply["text"]) if use_widgets else (reply["text"], [], [])
    window = (agent["limits"].get("context_tokens", 0) if agent else 0) or None
    return {"text": clean, "widgets": found, "widget_errors": errors, "model": reply.get("model") or provider["model"], "provider": provider.get("label") or provider["type"],
            "finish": reply["stop"], "insights": {"input_tokens": u["input"], "output_tokens": u["output"], "latency_ms": ms, "tokens_per_second": round(u["output"] / (ms / 1000), 1), "cost": cost,
                                                  "context": {"used": u["input"] + u["output"], "window": window, "percent": round(100 * (u["input"] + u["output"]) / window, 1) if window else None},
                                                  "tokenizer": tokenizers.describe(root, provider.get("tokenizer"))["label"]}}


def compare(root, spec, chat_fn=None, workers=4):
    """The same conversation on several models at once. spec.targets: [{agent_id | provider, label?}, ...]."""
    targets = spec.get("targets")
    if not isinstance(targets, list) or not 2 <= len(targets) <= 8:
        raise PlaygroundError("compare needs 2-8 targets")
    base = {k: v for k, v in spec.items() if k not in ("targets", "agent_id", "provider")}

    labels = []
    for t in targets:  # labels identify results (fastest, cheapest), so they must be unique
        base_label = str(t.get("label") or t.get("agent_id") or (t.get("provider") or {}).get("model") or "model")[:60]
        label, n = base_label, 2
        while label in labels:
            label, n = f"{base_label} #{n}", n + 1
        labels.append(label)

    def one(pair):
        t, label = pair
        try:
            return {"label": label, "ok": True, **chat(root, {**base, **{k: t[k] for k in ("agent_id", "provider") if k in t}}, chat_fn)}
        except PlaygroundError as e:
            return {"label": label, "ok": False, "error": str(e)}
    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as ex:
        results = list(ex.map(one, zip(targets, labels)))
    ok = [r for r in results if r["ok"]]
    words = {r["label"]: set(re.findall(r"\w+", r["text"].lower())) for r in ok}
    for r in ok:
        others = [w for k, w in words.items() if k != r["label"]]
        r["agreement"] = round(sum(len(words[r["label"]] & w) / max(1, len(words[r["label"]] | w)) for w in others) / len(others), 2) if others else None
        r["words"] = len(r["text"].split())
    return {"results": results, "fastest": min(ok, key=lambda r: r["insights"]["latency_ms"])["label"] if ok else None,
            "cheapest": min(ok, key=lambda r: r["insights"]["cost"])["label"] if ok and any(r["insights"]["cost"] for r in ok) else None}
