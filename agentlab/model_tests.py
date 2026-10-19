"""Model tests: a short battery that tells you what a model can do before you give it work.

Checks connectivity, instruction following, simple reasoning, JSON output, and tool calling both natively and with the
text protocol, then recommends a tool mode. Each test is a tiny request, so a full run costs a few hundred tokens.
"""

import json
import time

from . import agents, hooks, llm, usage

ADD = {"name": "add", "description": "Add two integers.", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}


def _file(root):
    return hooks.data_dir(root) / "model-tests.json"


def key_of(provider):
    return f"{provider['type']}|{provider.get('base_url') or provider.get('url') or ''}|{provider['model']}"


def _run(root, provider, user, tools=None, tool_mode=None, chat_fn=llm.chat, max_tokens=80):
    p = dict(provider, tool_mode=tool_mode) if tool_mode else provider
    t0 = time.time()
    r = chat_fn(p, "", [{"role": "user", "content": user}], tools, max_tokens=max_tokens, project=root, retries=0, timeout=60)
    ms = int((time.time() - t0) * 1000)
    usage.record(root, agent="-", provider=provider["type"], model=r.get("model") or provider["model"], input_tokens=r["usage"]["input"], output_tokens=r["usage"]["output"], cost=llm.cost(provider, r["usage"]), ms=ms, kind="test")
    return r, ms


def _tests():
    def connect(root, p, f):
        r, ms = _run(root, p, "Reply with the single word: ok", chat_fn=f)
        return bool(r["text"].strip()), ms, r["usage"], f"replied “{r['text'].strip()[:40]}”"

    def follow(root, p, f):
        r, ms = _run(root, p, "Reply with exactly the word PINEAPPLE in capital letters and nothing else.", chat_fn=f)
        ok = "PINEAPPLE" in r["text"].upper() and len(r["text"].strip()) <= 24
        return ok, ms, r["usage"], f"replied “{r['text'].strip()[:60]}”"

    def reasoning(root, p, f):
        r, ms = _run(root, p, "What is 12 times 12? Answer with the number only.", chat_fn=f)
        return "144" in r["text"], ms, r["usage"], f"replied “{r['text'].strip()[:40]}”"

    def json_out(root, p, f):
        r, ms = _run(root, p, 'Reply with ONLY a JSON object of the form {"sum": N} where N is 17 plus 25.', chat_fn=f, max_tokens=60)
        obj = llm.extract_json(r["text"])
        return bool(obj) and obj.get("sum") == 42, ms, r["usage"], f"replied “{r['text'].strip()[:60]}”"

    def tools(mode):
        def run(root, p, f):
            if mode == "native" and p["type"] in ("cli", "http"):
                return None, 0, {"input": 0, "output": 0}, "this kind of model has no native tool calling"
            r, ms = _run(root, p, "Use the add tool to add 19 and 23.", tools=[ADD], tool_mode=mode, chat_fn=f, max_tokens=120)
            call = (r["tool_calls"] or [None])[0]
            ok = bool(call) and call["name"] == "add" and sum(int(v) for v in call["arguments"].values() if str(v).lstrip("-").isdigit()) == 42
            return ok, ms, r["usage"], (f"called {call['name']}({json.dumps(call['arguments'])})" if call else f"no tool call; said “{r['text'].strip()[:50]}”")
        return run
    return [("connect", "Connects and answers", connect), ("follow", "Follows a precise instruction", follow), ("reasoning", "Simple arithmetic", reasoning),
            ("json", "Returns clean JSON", json_out), ("tools_native", "Native tool calling", tools("native")), ("tools_text", "Text-protocol tool calling", tools("prompt"))]


def run_battery(root, provider, chat_fn=None, save=True):
    chat_fn = chat_fn or llm.chat
    provider = llm.validate_provider(provider)
    results, failed_connect = [], False
    for tid, name, fn in _tests():
        if failed_connect:
            results.append({"id": tid, "name": name, "status": "skip", "ms": 0, "tokens": 0, "detail": "skipped because the model did not connect"})
            continue
        try:
            ok, ms, u, detail = fn(root, provider, chat_fn)
            status = "skip" if ok is None else "pass" if ok else "fail"
            results.append({"id": tid, "name": name, "status": status, "ms": ms, "tokens": u["input"] + u["output"], "detail": detail})
        except llm.LLMError as e:
            results.append({"id": tid, "name": name, "status": "error", "ms": 0, "tokens": 0, "detail": str(e)[:200]})
            failed_connect = failed_connect or tid == "connect"
    st = {r["id"]: r["status"] for r in results}
    notes = []
    if st["connect"] != "pass":
        tool_mode = "none"
        notes.append("The model did not answer. Check the URL, model name and key.")
    elif st["tools_native"] == "pass":
        tool_mode = "native"
        notes.append("Use native tool calling: it is the most reliable.")
    elif st["tools_text"] == "pass":
        tool_mode = "prompt"
        notes.append("Native tool calling did not work, but the text protocol does: set tool mode to ‘prompt’.")
    else:
        tool_mode = "none"
        notes.append("This model could not call tools in either mode. Use it as a chat or external agent, or try a larger model.")
    if st["follow"] == "fail":
        notes.append("It struggles with precise instructions; keep prompts short and explicit.")
    if st["json"] == "fail":
        notes.append("It does not return clean JSON; features that rely on JSON (diary, dreams, evolution) will use their built-in fallbacks.")
    passed = sum(1 for r in results if r["status"] == "pass")
    ran = [r for r in results if r["status"] in ("pass", "fail")]
    report = {"key": key_of(provider), "model": provider["model"], "ts": time.time(), "tests": results,
              "summary": {"passed": passed, "total": sum(1 for r in results if r["status"] != "skip"), "avg_ms": int(sum(r["ms"] for r in ran) / len(ran)) if ran else None, "tokens": sum(r["tokens"] for r in results)},
              "recommendation": {"tool_mode": tool_mode, "notes": notes}}
    if save:
        data = json.loads(_file(root).read_text(encoding="utf-8")) if _file(root).exists() else {}
        data[report["key"]] = report
        _file(root).parent.mkdir(parents=True, exist_ok=True)
        _file(root).write_text(json.dumps(data), encoding="utf-8")
    return report


def last_reports(root):
    p = _file(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
