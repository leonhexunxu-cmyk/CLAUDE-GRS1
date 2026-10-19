"""One chat interface (with tool calling) over Anthropic, OpenAI-compatible servers and a scripted mock.

Neutral message format used everywhere in agentlab:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Tools: [{"name", "description", "parameters": <JSON schema>}]
Reply: {"text", "tool_calls", "usage": {"input", "output"}, "stop": "end"|"tool"|"length", "model"}
"""

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
import urllib.error
import urllib.request

from . import vault

PRESETS = {
    "anthropic": {"type": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "key": "anthropic", "label": "Anthropic"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "model": "", "key": "openai", "max_tokens_param": "max_completion_tokens", "label": "OpenAI"},
    "openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "", "key": "openrouter", "label": "OpenRouter (hundreds of models)"},
    "gemini": {"type": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "", "key": "gemini", "label": "Google Gemini (OpenAI-compatible endpoint)"},
    "mistral": {"type": "openai", "base_url": "https://api.mistral.ai/v1", "model": "", "key": "mistral", "label": "Mistral"},
    "groq": {"type": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "", "key": "groq", "label": "Groq"},
    "together": {"type": "openai", "base_url": "https://api.together.xyz/v1", "model": "", "key": "together", "label": "Together AI"},
    "fireworks": {"type": "openai", "base_url": "https://api.fireworks.ai/inference/v1", "model": "", "key": "fireworks", "label": "Fireworks AI"},
    "deepseek": {"type": "openai", "base_url": "https://api.deepseek.com", "model": "", "key": "deepseek", "label": "DeepSeek"},
    "xai": {"type": "openai", "base_url": "https://api.x.ai/v1", "model": "", "key": "xai", "label": "xAI"},
    "perplexity": {"type": "openai", "base_url": "https://api.perplexity.ai", "model": "", "key": "perplexity", "label": "Perplexity"},
    "huggingface": {"type": "openai", "base_url": "https://router.huggingface.co/v1", "model": "", "key": "hf_token", "label": "Hugging Face Inference Providers"},
    "http": {"type": "http", "url": "", "model": "custom", "key": "", "label": "Any HTTP API (map request and response yourself)"},
    "cloudflare": {"type": "openai", "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "key": "cloudflare_token", "label": "Cloudflare Workers AI"},
    "ollama": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "key": "", "label": "Ollama (local)"},
    "lmstudio": {"type": "openai", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "key": "", "label": "LM Studio (local)"},
    "llamacpp": {"type": "openai", "base_url": "http://127.0.0.1:8080/v1", "model": "local", "key": "", "label": "llama.cpp server (local)"},
    "vllm": {"type": "openai", "base_url": "http://127.0.0.1:8000/v1", "model": "local", "key": "", "label": "vLLM (local)"},
    "custom": {"type": "openai", "base_url": "", "model": "", "key": "", "label": "Custom OpenAI-compatible endpoint"},
    "openclaw": {"type": "openai", "base_url": "http://127.0.0.1:18789/v1", "model": "openclaw/default", "key": "openclaw_token", "label": "OpenClaw agent (via its Gateway)", "external": True},
    "openclaw_cli": {"type": "cli", "command": ["openclaw", "agent", "--agent", "{openclaw_agent}", "--message-file", "{message_file}"], "model": "openclaw", "label": "OpenClaw agent (via its CLI)", "external": True},
    "mock": {"type": "mock", "base_url": "", "model": "demo", "key": "", "label": "Demo model (no network, scripted)"},
}
TYPES = ("anthropic", "openai", "cli", "http", "mock")


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
    out["tool_mode"] = p.get("tool_mode") or ("prompt" if out["type"] in ("cli", "http") else "native")
    if out["tool_mode"] not in ("native", "prompt"):
        raise ValueError("provider.tool_mode must be native or prompt")
    if out["type"] == "http":
        url = str(p.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("provider.url must be an http(s) URL")
        body = p.get("body", {"prompt": "{prompt}"})
        headers = p.get("headers", {})
        if not isinstance(body, (dict, list)) or not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
            raise ValueError("provider.body must be a JSON object/array and provider.headers an object of strings")
        out.update(url=url, method=str(p.get("method", "POST")).upper(), headers=headers, body=body, response_path=str(p.get("response_path", "")),
                   usage_in_path=str(p.get("usage_in_path", "")), usage_out_path=str(p.get("usage_out_path", "")), timeout=max(1, min(int(p.get("timeout") or 120), 3600)))
        if out["method"] not in ("POST", "PUT"):
            raise ValueError("provider.method must be POST or PUT")
        out["model"] = out["model"] or "custom"
    elif out["type"] == "cli":
        cmd = p.get("command")
        if not isinstance(cmd, list) or not 1 <= len(cmd) <= 20 or not all(isinstance(c, str) and 0 < len(c) <= 500 for c in cmd):
            raise ValueError("provider.command must be a list of 1-20 strings")
        if not any("{message}" in c or "{message_file}" in c for c in cmd):
            raise ValueError("provider.command must contain {message} or {message_file}")
        if any("{openclaw_agent}" in c for c in cmd):
            raise ValueError("choose the OpenClaw agent id first (provider.command still has {openclaw_agent})")
        out["command"] = cmd
        out["timeout"] = max(1, min(int(p.get("timeout") or 600), 3600))
        out["model"] = out["model"] or "cli"
    elif out["type"] != "mock":
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


def _transcript(system, messages):
    parts = [f"[System]\n{system}"] if system else []
    for m in messages:
        who = {"user": "User", "assistant": "Assistant", "tool": "Tool result"}[m["role"]]
        parts.append(f"[{who}]\n{m['content']}")
    return "\n\n".join(parts)


def _cli(provider, system, messages):
    """An external agent run as a command (e.g. `openclaw agent`): the conversation goes in, its stdout comes back."""
    text = _transcript(system, messages)
    fd, path = tempfile.mkstemp(prefix="agentlab-msg-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        argv = [c.replace("{message_file}", path).replace("{message}", text) for c in provider["command"]]  # a list, never a shell string
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=provider["timeout"], stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise LLMError(f"command not found: {argv[0]} (is it installed and on your PATH?)") from None
        except subprocess.TimeoutExpired:
            raise LLMError(f"{argv[0]} did not finish within {provider['timeout']} seconds", None, True) from None
    finally:
        os.unlink(path)
    if r.returncode != 0:
        raise LLMError(f"{argv[0]} exited with {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    out = r.stdout.strip()
    return {"text": out, "tool_calls": [], "usage": {"input": _tokens(text), "output": _tokens(out)}, "stop": "end", "model": provider["model"]}


def _fill(node, values):
    """Substitute {placeholders} inside strings of a JSON-like template (values keep their type if a string is exactly one placeholder)."""
    if isinstance(node, str):
        if node in values and not isinstance(values[node], str):
            return values[node]
        for k, v in values.items():
            node = node.replace(k, str(v))
        return node
    if isinstance(node, list):
        return [_fill(x, values) for x in node]
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    return node


def _dig(obj, path):
    """data['choices'][0]['message']['content'] <- 'choices.0.message.content'. Returns None if it is not there."""
    for part in [p for p in str(path).split(".") if p]:
        try:
            obj = obj[int(part)] if isinstance(obj, list) else obj[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return obj


def _http(provider, system, messages, max_tokens, project, retries, backoff):
    """Any HTTP model API: you describe the request template and where the answer is in the response."""
    key = vault.get_secret(project, provider.get("key"))
    if provider.get("key") and not key:
        raise LLMError(f"no API key named '{provider['key']}': add it under Providers")
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    values = {"{prompt}": _transcript(system, messages), "{system}": system or "", "{user}": last_user, "{max_tokens}": max_tokens, "{model}": provider["model"], "{key}": key or ""}
    body = _fill(provider["body"], values)
    headers = _fill(provider["headers"], values)
    data = json.dumps(body).encode()
    raw = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(provider["url"], data=data, method=provider["method"], headers={"Content-Type": "application/json", "User-Agent": "agentlab", **headers})
        try:
            with urllib.request.urlopen(req, timeout=provider["timeout"]) as r:
                raw = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise LLMError(vault.redact_value(f"{e.code}: {e.read().decode('utf-8', 'replace')[:500]}", project), e.code, e.code in (429, 500, 502, 503, 504)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise LLMError(f"could not reach {provider['url']}: {getattr(e, 'reason', e)}", None, True) from None
    if provider["response_path"]:
        try:
            text = _dig(json.loads(raw), provider["response_path"])
        except ValueError:
            raise LLMError("the server did not return JSON, but a response path is set") from None
        if text is None:
            raise LLMError(f"nothing at response path '{provider['response_path']}' in the reply")
        text = text if isinstance(text, str) else json.dumps(text)
    else:
        text = raw.strip()
    usage = {"input": _tokens(values["{prompt}"]), "output": _tokens(text)}
    try:
        parsed = json.loads(raw)
        for k, path in (("input", provider["usage_in_path"]), ("output", provider["usage_out_path"])):
            v = _dig(parsed, path) if path else None
            if isinstance(v, (int, float)):
                usage[k] = int(v)
    except ValueError:
        pass
    return {"text": text, "tool_calls": [], "usage": usage, "stop": "end", "model": provider["model"]}


TOOL_PROTOCOL = ("You can use tools. To use ONE tool, reply with ONLY a JSON object, nothing else: {{\"tool\": \"<name>\", \"arguments\": {{...}}}}\n"
                 "After the tool runs you will get its result and can continue. When you have the final answer, reply in plain text with no JSON.\n\nTools:\n{tools}")


def _prompt_tools(system, messages, tools):
    """Text-only tool calling for models without native function calling."""
    listing = "\n".join(f"- {t['name']}: {t['description']} Arguments (JSON schema): {json.dumps(t['parameters'])}" for t in tools)
    sys2 = ((system + "\n\n") if system else "") + TOOL_PROTOCOL.format(tools=listing)
    out = []
    for m in messages:
        if m["role"] == "assistant":
            calls = "\n".join(json.dumps({"tool": c["name"], "arguments": c["arguments"]}) for c in m.get("tool_calls", []))
            out.append({"role": "assistant", "content": (m.get("content") or "") + (("\n" if m.get("content") else "") + calls)})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": f"Tool result ({m['name']}):\n{m['content']}"})
        else:
            out.append(m)
    return sys2, out


def _parse_tool_text(reply):
    obj = extract_json(reply["text"])
    if obj and isinstance(obj.get("tool"), str) and isinstance(obj.get("arguments", {}), dict):
        before = re.sub(r"```(?:json)?\s*$", "", reply["text"].split("{", 1)[0].rstrip()).strip()  # prose before the JSON, minus a code-fence opener
        reply = dict(reply, tool_calls=[{"id": "p" + uuid.uuid4().hex[:8], "name": obj["tool"], "arguments": obj.get("arguments", {})}], stop="tool", text=before)
    return reply


def chat(provider, system, messages, tools=None, max_tokens=1024, temperature=None, project=".", timeout=120, retries=2, backoff=1.0):
    """Send one request; returns a Reply dict. Raises LLMError. Works with any model: native tool calling where the
    API has it, a text protocol where it does not."""
    if provider["type"] == "mock":
        return _mock(provider, messages, tools)
    if tools and provider.get("tool_mode", "native") == "prompt":
        sys2, msgs2 = _prompt_tools(system, messages, tools)
        return _parse_tool_text(_dispatch(provider, sys2, msgs2, None, max_tokens, temperature, project, timeout, retries, backoff))
    return _dispatch(provider, system, messages, tools, max_tokens, temperature, project, timeout, retries, backoff)


def _dispatch(provider, system, messages, tools, max_tokens, temperature, project, timeout, retries, backoff):
    if provider["type"] == "cli":
        return _cli(provider, system, messages)
    if provider["type"] == "http":
        return _http(provider, system, messages, max_tokens, project, retries, backoff)
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
