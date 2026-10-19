"""Agents: a model plus instructions, preferences, a control mode, authorizations and limits."""

import json
import re
import shutil
import time
import uuid

from . import llm
from .hooks import data_dir

CATEGORIES = ("read", "write", "run", "network", "environment")
# Control modes: what happens when the agent wants to use a tool of each category.
MODES = {
    "ask": {"label": "Ask before every action", "read": "ask", "write": "ask", "run": "ask", "network": "ask", "environment": "ask"},
    "ask_risky": {"label": "Ask only for risky actions", "read": "allow", "write": "allow", "run": "ask", "network": "ask", "environment": "ask"},
    "auto": {"label": "Full auto", "read": "allow", "write": "allow", "run": "allow", "network": "allow", "environment": "allow"},
}
DECISIONS = ("inherit", "allow", "ask", "deny")
STYLES = ("concise", "balanced", "detailed")
AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
LIMIT_RANGES = {"max_output_tokens": (16, 32768, 1024), "max_steps": (1, 200, 25), "token_budget": (1000, 10_000_000, 200_000), "step_timeout": (1, 600, 60), "context_tokens": (0, 2_000_000, 0)}

ROLE_TEMPLATES = {
    "coder": {"name": "Coder", "role": "Writes and fixes code", "avatar": "💻",
              "instructions": "You are a careful senior engineer. Read the existing files before changing them, make small focused edits, run the code or tests to check your work, and explain what you changed."},
    "researcher": {"name": "Researcher", "role": "Finds and summarises information", "avatar": "🔎",
                   "instructions": "You research questions thoroughly, cite where facts came from, separate what you verified from what you assume, and write concise notes into the project."},
    "planner": {"name": "Planner", "role": "Breaks goals into steps", "avatar": "🗺️",
                "instructions": "You turn vague goals into a short, ordered plan with clear owners and checks. You keep the plan in PLAN.md and update it as work progresses."},
    "reviewer": {"name": "Reviewer", "role": "Reviews work critically", "avatar": "🧐",
                 "instructions": "You review other people's work for correctness, risks and missing cases. Be specific and kind; point to exact files and lines; suggest fixes."},
    "designer": {"name": "Designer", "role": "Designs interfaces and visuals", "avatar": "🎨",
                 "instructions": "You design clean, accessible interfaces. Prefer simple layouts, readable type and good contrast. Produce working HTML/CSS."},
    "tester": {"name": "Tester", "role": "Tries to break things", "avatar": "🧪",
               "instructions": "You write and run tests, look for edge cases and failure modes, and report reproducible bugs with the exact steps."},
}


class AgentError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _root(project):
    return data_dir(project) / "agents"


def effective(agent, category, overrides=None):
    """allow | ask | deny for a tool category: per-job override, else the agent's authorization, else its mode."""
    if overrides and overrides.get(category) in ("allow", "ask", "deny"):
        return overrides[category]
    own = agent["authorizations"].get(category, "inherit")
    return own if own != "inherit" else MODES[agent["mode"]][category]


def validate(spec, existing=None):
    base = existing or {}
    get = lambda k, d=None: spec[k] if k in spec else base.get(k, d)
    name = str(get("name", "")).strip()
    if not name or len(name) > 60:
        raise AgentError("name is required (max 60 characters)")
    mode = get("mode", "ask_risky")
    if mode not in MODES:
        raise AgentError(f"mode must be one of {list(MODES)}")
    auth = {c: "inherit" for c in CATEGORIES}
    auth.update(get("authorizations", {}) or {})
    if set(auth) - set(CATEGORIES) or any(v not in DECISIONS for v in auth.values()):
        raise AgentError(f"authorizations must map {list(CATEGORIES)} to one of {list(DECISIONS)}")
    prefs = {"style": "balanced", "language": "", **(get("preferences", {}) or {})}
    if prefs["style"] not in STYLES:
        raise AgentError(f"preferences.style must be one of {list(STYLES)}")
    prefs["language"] = str(prefs["language"])[:40]
    limits = {}
    given = {**{k: v[2] for k, v in LIMIT_RANGES.items()}, **(base.get("limits") or {}), **(spec.get("limits") or {})}
    for k, (lo, hi, _) in LIMIT_RANGES.items():
        try:
            limits[k] = int(given[k])
        except (TypeError, ValueError):
            raise AgentError(f"limits.{k} must be a number")
        if not lo <= limits[k] <= hi:
            raise AgentError(f"limits.{k} must be between {lo} and {hi}")
    temp = given.get("temperature")
    limits["temperature"] = None if temp in (None, "") else float(temp)
    if limits["temperature"] is not None and not 0 <= limits["temperature"] <= 2:
        raise AgentError("limits.temperature must be between 0 and 2")
    cap = given.get("daily_token_cap")
    limits["daily_token_cap"] = None if cap in (None, "", 0) else int(cap)
    instructions = str(get("instructions", ""))
    if len(instructions) > 20000:
        raise AgentError("instructions are too long (max 20,000 characters)")
    protection = {"dangerous_commands": "standard", **(get("protection", {}) or {})}
    if protection["dangerous_commands"] not in ("standard", "strict", "off"):
        raise AgentError("protection.dangerous_commands must be standard, strict or off")
    dream = {"enabled": False, "start": "02:00", "end": "05:00", "tz_minutes": 0, "token_budget": 20000, "cycles": 2, **(get("dream", {}) or {})}
    for k in ("start", "end"):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(dream[k])):
            raise AgentError(f"dream.{k} must look like 02:30")
    try:
        dream.update(tz_minutes=int(dream["tz_minutes"]), token_budget=int(dream["token_budget"]), cycles=int(dream["cycles"]), enabled=bool(dream["enabled"]))
    except (TypeError, ValueError):
        raise AgentError("dream settings must be numbers")
    if not -840 <= dream["tz_minutes"] <= 840 or not 1000 <= dream["token_budget"] <= 1_000_000 or not 1 <= dream["cycles"] <= 5:
        raise AgentError("dream: tz_minutes -840..840, token_budget 1000..1,000,000, cycles 1..5")
    evolve = get("evolve", "off")
    if evolve not in ("off", "suggest"):
        raise AgentError("evolve must be off or suggest")
    try:
        provider = llm.validate_provider(get("provider", llm.PRESETS["mock"]))
    except ValueError as e:
        raise AgentError(str(e))
    return {"name": name, "role": str(get("role", ""))[:200], "avatar": str(get("avatar", "🤖"))[:8] or "🤖", "provider": provider,
            "instructions": instructions, "preferences": prefs, "mode": mode, "authorizations": auth, "limits": limits,
            "skills": [str(x) for x in get("skills", [])][:20], "plugins": [str(x) for x in get("plugins", [])][:20], "knowledge": [str(x) for x in get("knowledge", []) if re.fullmatch(r"[a-f0-9]{8}", str(x))][:10], "dream": {k: dream[k] for k in ("enabled", "start", "end", "tz_minutes", "token_budget", "cycles")}, "environment_control": bool(get("environment_control", False)), "native_tools": bool(get("native_tools", True)), "protection": {"dangerous_commands": protection["dangerous_commands"]}, "memory_enabled": bool(get("memory_enabled", True)), "diary_enabled": bool(get("diary_enabled", False)), "evolve": evolve,
            "source": get("source", {"kind": "manual"})}


def _public(rec):
    return rec


def create(project, spec):
    data = validate(spec)
    aid = f"{re.sub(r'[^a-z0-9]+', '-', data['name'].lower()).strip('-')[:24] or 'agent'}-{uuid.uuid4().hex[:4]}"
    rec = {"id": aid, **data, "version": 1, "history": [{"version": 1, "instructions": data["instructions"], "ts": time.time(), "reason": "created"}],
           "created": time.time(), "updated": time.time()}
    _write(project, rec)
    return rec


def _write(project, rec):
    d = _root(project) / rec["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.json").write_text(json.dumps(rec), encoding="utf-8")


def get(project, aid):
    if not AGENT_ID.match(aid or ""):
        raise AgentError("invalid agent id")
    p = _root(project) / aid / "agent.json"
    if not p.exists():
        raise AgentError("agent not found", 404)
    return json.loads(p.read_text(encoding="utf-8"))


def list_agents(project):
    out = []
    for p in _root(project).glob("*/agent.json") if _root(project).exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return sorted(out, key=lambda a: a["created"])


def update(project, aid, spec, reason="edited"):
    rec = get(project, aid)
    data = validate(spec, rec)
    if data["instructions"] != rec["instructions"]:
        rec["version"] += 1
        rec["history"] = (rec["history"] + [{"version": rec["version"], "instructions": data["instructions"], "ts": time.time(), "reason": reason}])[-50:]
    rec.update(data, updated=time.time())
    _write(project, rec)
    return rec


def delete(project, aid):
    get(project, aid)
    shutil.rmtree(_root(project) / aid)


def public_view(rec):
    """Agent without anything secret (providers only hold the *name* of a key, never the key)."""
    return {k: v for k, v in rec.items() if k != "history"} | {"history_len": len(rec.get("history", []))}


def system_prompt(agent, project_name, memories="", team="", skills=""):
    prefs = agent["preferences"]
    if not agent.get("native_tools", True):  # an external agent (e.g. OpenClaw) has its own tools; just brief it
        brief = f"You are working for the user on the project \"{project_name}\". {agent['instructions'].strip()}"
        return "\n\n".join(p for p in [brief, f"Reply in {prefs['language']}." if prefs["language"] else "", team, f"What you remember:\n{memories}" if memories else ""] if p)
    style = {"concise": "Be brief and to the point.", "balanced": "", "detailed": "Explain your reasoning and be thorough."}[prefs["style"]]
    parts = [f"You are {agent['name']}" + (f", {agent['role']}." if agent["role"] else "."), agent["instructions"].strip(), style,
             f"Reply in {prefs['language']}." if prefs["language"] else "",
             f"You work inside the project \"{project_name}\", in a private workspace. Use your tools to list, read, write and run things there; paths are relative to the workspace and you cannot reach anything outside it. "
             "Some actions may need the user's approval; if one is denied, adapt instead of repeating it. "
             "Report progress with update_progress, and when you are finished call finish with a short summary.",
             skills, team, f"What you remember:\n{memories}" if memories else ""]
    return "\n\n".join(p for p in parts if p)
