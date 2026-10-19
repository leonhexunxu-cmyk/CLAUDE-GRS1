"""Models dashboard: every model agentlab knows about, what uses it, how it performs and what it costs."""

from pathlib import Path

from . import agents, hardware, model_tests, projects, runners, tokenizers, usage


def overview(root, days=30, now=None):
    hw = hardware.info()
    use = {m["key"]: m for m in usage.summary(root, days, now)["by_model"]}
    lat = {}
    for e in usage._entries(root, (now or __import__("time").time()) - days * 86400):
        b = lat.setdefault(f"{e['provider']}:{e['model']}", [])
        if e["ms"]:
            b.append(e["ms"])
    tests = model_tests.last_reports(root)
    models = {}
    for a in agents.list_agents(root):
        p = a["provider"]
        key = model_tests.key_of(p)
        m = models.setdefault(key, {"key": key, "model": p["model"], "type": p["type"], "label": p.get("label") or p["type"], "host": p.get("base_url") or p.get("url") or "", "tool_mode": p.get("tool_mode"),
                                    "tokenizer": tokenizers.describe(root, p.get("tokenizer"))["label"], "compute": p.get("compute"), "agents": [], "context_tokens": 0})
        m["agents"].append({"id": a["id"], "name": a["name"]})
        m["context_tokens"] = max(m["context_tokens"], a["limits"].get("context_tokens", 0))
    for key, m in models.items():
        u = use.get(f"{m['type']}:{m['model']}", {"calls": 0, "input": 0, "output": 0, "cost": 0})
        samples = sorted(lat.get(f"{m['type']}:{m['model']}", []))
        m["usage"] = {"calls": u["calls"], "input_tokens": u["input"], "output_tokens": u["output"], "cost": u["cost"],
                      "p50_ms": samples[len(samples) // 2] if samples else None, "p95_ms": samples[int(len(samples) * 0.95) - 1] if len(samples) >= 20 else (samples[-1] if samples else None)}
        t = tests.get(key)
        m["test"] = {"passed": t["summary"]["passed"], "total": t["summary"]["total"], "tool_mode": t["recommendation"]["tool_mode"], "ts": t["ts"]} if t else None
    local = []
    for p in projects.list_projects(root):
        for f in projects.tree(root, p["id"], 500):
            if f.lower().endswith((".gguf", ".safetensors", ".bin")) and "tokenizer" not in f:
                size = (projects.workspace(root, p["id"]) / f).stat().st_size
                local.append({"project": p["id"], "project_name": p["name"], "path": f, "size_bytes": size, "format": f.rsplit(".", 1)[-1],
                              "fit": hardware.check_fit(size, 4096, "auto", hw) if size > 1_000_000 else None})
    return {"hardware": hw, "models": sorted(models.values(), key=lambda m: -m["usage"]["calls"]), "runners": runners.list_runners(root), "weights": local,
            "totals": {"models": len(models), "calls": sum(m["usage"]["calls"] for m in models.values()), "cost": round(sum(m["usage"]["cost"] for m in models.values()), 4)}}
