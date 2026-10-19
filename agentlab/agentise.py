"""Agentise: turn a model into an agent, from a hosted API (your key) or from your own weights."""

import time

from . import agents, library, llm, runners, vault


class AgentiseError(ValueError):
    pass


def test_provider(root, provider):
    """One tiny request to prove the provider, model and key work. Returns {ok, ms, reply | error}."""
    t0 = time.time()
    try:
        r = llm.chat(llm.validate_provider(provider), "", [{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=16, project=root, retries=0, timeout=30)
        return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": r["text"][:80], "tokens": r["usage"]["input"] + r["usage"]["output"]}
    except (llm.LLMError, ValueError) as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": str(e)[:300]}


def _agent_fields(spec):
    """Agent settings from a request; a `role_preset` (manager, developer, ...) fills the gaps, explicit values win."""
    try:
        spec = library.apply_role({k: v for k, v in spec.items() if k != "role_preset"}, spec.get("role_preset"))
    except library.LibraryError as e:
        raise AgentiseError(str(e))
    return {k: spec[k] for k in ("name", "role", "avatar", "instructions", "preferences", "mode", "authorizations", "limits", "memory_enabled", "diary_enabled", "evolve", "skills", "plugins", "protection") if k in spec}


def from_api(root, spec):
    """spec: preset (anthropic/openai/openrouter/cloudflare/ollama/...), model, optional base_url, api_key, name, ..."""
    preset = spec.get("preset", "custom")
    if preset not in llm.PRESETS:
        raise AgentiseError(f"preset must be one of {sorted(llm.PRESETS)}")
    provider = {**llm.PRESETS[preset], **{k: spec[k] for k in ("base_url", "model", "key", "price_in", "price_out", "account_id", "command", "timeout") if spec.get(k) not in (None, "")}}
    external = bool(provider.pop("external", False))
    if spec.get("no_auth"):  # e.g. an OpenClaw Gateway or local server running with auth switched off
        provider["key"] = ""
    if provider.get("type") == "cli" and spec.get("openclaw_agent"):
        agent_id = str(spec["openclaw_agent"])
        if not agent_id.replace("-", "").replace("_", "").isalnum():
            raise AgentiseError("the OpenClaw agent id may only contain letters, digits, - and _")
        provider["command"] = [c.replace("{openclaw_agent}", agent_id) for c in provider["command"]]
    try:
        provider = llm.validate_provider(provider)
    except ValueError as e:
        raise AgentiseError(str(e))
    if spec.get("api_key"):
        key_name = provider["key"] or preset
        try:
            vault.set_secret(root, key_name, spec["api_key"])
        except ValueError as e:
            raise AgentiseError(str(e))
        provider["key"] = key_name
    if not spec.get("skip_test"):
        result = test_provider(root, provider)
        if not result["ok"]:
            raise AgentiseError(f"the model did not answer: {result['error']}")
    fields = _agent_fields({"name": spec.get("name") or provider["model"], **spec})
    try:
        return agents.create(root, {**fields, "provider": provider, "native_tools": not external, "source": {"kind": "api", "preset": preset, **({"external": True} if external else {})}})
    except agents.AgentError as e:
        raise AgentiseError(str(e))


def from_weights(root, spec, binary=None):
    """spec: project_id, path (a .gguf in the project), runtime (llama-server | ollama), name, ctx, ..."""
    runtime = spec.get("runtime", "llama-server")
    name = spec.get("name") or spec["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    try:
        if runtime == "ollama":
            provider, source = runners.ollama_create(root, spec["project_id"], spec["path"], name, binary=binary, device=spec.get("device", "auto"), threads=spec.get("threads")), {"kind": "weights", "runtime": "ollama", "path": spec["path"]}
            runner = None
        elif runtime == "llama-server":
            runner = runners.start(root, spec["project_id"], spec["path"], name=name, ctx=spec.get("ctx", 4096), binary=binary, device=spec.get("device", "auto"), threads=spec.get("threads"))
            provider, source = runners.provider_for(runner), {"kind": "weights", "runtime": "llama-server", "path": spec["path"], "runner": runner["id"]}
            if spec.get("wait"):
                runners.wait_ready(root, runner, timeout=float(spec["wait"]))
        else:
            raise AgentiseError("runtime must be llama-server or ollama")
        provider = {**provider, "compute": {"device": spec.get("device", "auto"), "threads": spec.get("threads")}, **({"tokenizer": spec["tokenizer"]} if spec.get("tokenizer") else {})}
        agent = agents.create(root, {**_agent_fields({"name": name, **spec}), "provider": provider, "source": source})
    except (runners.RunnerError, agents.AgentError) as e:
        raise AgentiseError(str(e))
    return {"agent": agent, "runner": runner}
