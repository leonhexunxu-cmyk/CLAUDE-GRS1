"""One chat interface (with tool calling) over Anthropic, OpenAI-compatible servers and a scripted mock.

Neutral message format used everywhere in agentlab:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Tools: [{"name", "description", "parameters": <JSON schema>}]
Reply: {"text", "tool_calls", "usage": {"input", "output"}, "stop": "end"|"tool"|"length", "model"}
"""

import json
import time
import urllib.error
import urllib.request

from . import vault

PRESETS = {
    "anthropic": {"type": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "key": "anthropic", "label": "Anthropic"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5", "key": "openai", "max_tokens_param": "max_completion_tokens", "label": "OpenAI"},
    "openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "anthropic/claude-sonnet-5-5", "key": "openrouter", "label": "OpenRouter"},
    "cloudflare": {"type": "openai", "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "key": "cloudflare_token", "label": "Cloudflare Workers AI"},
    "ollama": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "key": "", "label": "Ollama (local)"},
    "lmstudio": {"type": "openai", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "key": "", "label": "LM Studio (local)"},
    "llamacpp": {"type": "openai", "base_url": "http://127.0.0.1:8080/v1", "model": "local", "key": "", "label": "llama.cpp server (local)"},
    "vllm": {"type": "openai", "base_url": "http://127.0.0.1:8000/v1", "model": "local", "key": "", "label": "vLLM (local)"},
    "custom": {"type": "openai", "base_url": "", "model": "", "key": "", "label": "Custom OpenAI-compatible endpoint"},
    "mock": {"type": "mock", "base_url": "", "model": "demo", "key": "", "label": "Demo model (no network, scripted)"},
}
TYPES = ("anthropic", "openai", "mock")


class LLMError(Exception):
    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def validate_provider(p):
    """Normalise and validate a provider dict. Raises ValueError."""
    if not isinstance(p, dict) or p.get("type") not in TYPES:
        raise ValueError(f"provider.type must be one of {list(TYPES)}")
    out = {"type": p["type"], "base_url": str(p.get("base_url", "")).strip().rstrip("/"), "model": str(p.get("model", "")).strip()[:200],
           "key": str(p.get("key", "") or ""), "max_tokens_param": p.get("max_tokens_param", "max_tokens"),
           "price_in": float(p.get("price_in") or 0), "price_out": float(p.get("price_out") or 0), "label": str(p.get("label", ""))[:60]}
    if out["max_tokens_param"] not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("invalid max_tokens_param")
    if out["type"] != "mock":
        if not out["base_url"].startswith(("http://", "https://")):
            raise ValueError("provider.base_url must be an http(s) URL")
        if not out["model"]:
            raise ValueError("provider.model is required")
    if out["key"] and not vault.NAME.match(out["key"]):
        raise ValueError("provider.key must be the name of a stored key")
    if p.get("script") is not None:
        out["script"] = p["script"]
    if p.get("account_id"):
        out["account_id"] = str(p["account_id"])
    return out


def extract_json(text):
    """First JSON object found in a model's reply (models often wrap it in prose or code fences), or None."""
    text = str(text or "")
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\" and not esc)
                if c == '"' and not esc:
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def cost(provider, usage):
    return round((usage["input"] * provider.get("price_in", 0) + usage["output"] * provider.get("price_out", 0)) / 1e6, 6)


# ---- wire formats ----
def _to_anthropic(messages):
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]} for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no output)"}]})
        else:  # tool result: consecutive results share one user message
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) and out[-1]["content"][-1].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _to_openai(system, messages):
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in m["tool_calls"]]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _post(url, headers, body, timeout, retries, backoff):
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "agentlab", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            retryable = e.code in (429, 500, 502, 503, 504, 529)
            if retryable and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise LLMError(f"{e.code}: {detail}", e.code, retryable) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise LLMError(f"could not reach {url}: {getattr(e, 'reason', e)}", None, True) from None
        except ValueError:
            raise LLMError("the server did not return valid JSON") from None


def _tokens(text):
    return max(1, len(text) // 4)


def _mock(provider, messages, tools):
    """Scripted replies (provider['script']) cycled per assistant turn; default is a harmless demo."""
    turn = sum(1 for m in messages if m["role"] == "assistant")
    script = provider.get("script")
    if script:
        r = script[min(turn, len(script) - 1)]
        calls = [{"id": f"m{turn}_{i}", "name": c["name"], "arguments": c.get("arguments", {})} for i, c in enumerate(r.get("tool_calls", []))]
        text = r.get("text", "")
    else:
        names = [t["name"] for t in tools or []]
        text = "This is the demo model. Add a provider (Anthropic, OpenAI, Ollama, ...) to run a real agent."
        calls = [{"id": f"m{turn}", "name": "finish", "arguments": {"summary": text}}] if "finish" in names and turn == 0 else []
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return {"text": text, "tool_calls": calls, "usage": {"input": _tokens(last) + 20, "output": _tokens(text) + 5}, "stop": "tool" if calls else "end", "model": "mock"}


def chat(provider, system, messages, tools=None, max_tokens=1024, temperature=None, project=".", timeout=120, retries=2, backoff=1.0):
    """Send one request; returns a Reply dict. Raises LLMError."""
    if provider["type"] == "mock":
        return _mock(provider, messages, tools)
    key = vault.get_secret(project, provider.get("key"))
    if provider.get("key") and not key:
        raise LLMError(f"no API key named '{provider['key']}': add it under Providers")
    base = provider["base_url"]
    if "{account_id}" in base:
        account = provider.get("account_id") or vault.get_secret(project, "cloudflare_account")
        if not account:
            raise LLMError("Cloudflare account id is missing: add 'cloudflare_account' under Providers")
        base = base.replace("{account_id}", account)
    try:
        if provider["type"] == "anthropic":
            body = {"model": provider["model"], "max_tokens": max_tokens, "messages": _to_anthropic(messages)}
            if system:
                body["system"] = system
            if tools:
                body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
            if temperature is not None:
                body["temperature"] = temperature
            data = _post(base + "/v1/messages", {"x-api-key": key or "", "anthropic-version": "2023-06-01"}, body, timeout, retries, backoff)
            blocks = data.get("content", [])
            calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})} for b in blocks if b.get("type") == "tool_use"]
            stop = data.get("stop_reason")
            return {"text": "".join(b.get("text", "") for b in blocks if b.get("type") == "text"), "tool_calls": calls,
                    "usage": {"input": data.get("usage", {}).get("input_tokens", 0), "output": data.get("usage", {}).get("output_tokens", 0)},
                    "stop": "tool" if calls else "length" if stop == "max_tokens" else "end", "model": data.get("model", provider["model"])}
        body = {"model": provider["model"], provider.get("max_tokens_param", "max_tokens"): max_tokens, "messages": _to_openai(system, messages)}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        data = _post(base + "/chat/completions", headers, body, timeout, retries, backoff)
        choice = data["choices"][0]
        msg = choice.get("message", {})
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {"_invalid_json": c["function"].get("arguments")}
            calls.append({"id": c.get("id") or f"call_{len(calls)}", "name": c["function"]["name"], "arguments": args if isinstance(args, dict) else {}})
        usage = data.get("usage") or {}
        return {"text": msg.get("content") or "", "tool_calls": calls,
                "usage": {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
                "stop": "tool" if calls else "length" if choice.get("finish_reason") == "length" else "end", "model": data.get("model", provider["model"])}
    except LLMError as e:
        raise LLMError(vault.redact_value(str(e), project), e.status, e.retryable) from None
    except (KeyError, IndexError, TypeError):
        raise LLMError("the server's reply did not look like a chat completion") from None
