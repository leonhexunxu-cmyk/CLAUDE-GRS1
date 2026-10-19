"""Commands: shortcuts you create. A prompt command expands into a message ("/review src/app.py"); a shell command runs a
fixed command in the project's sandbox. Parameters are filled in order; extras go to {args}.
"""

import json
import re
import shlex

from . import danger, envs, project_env, projects
from .hooks import data_dir

NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
BUILTIN = {
    "review": ("Review the recent changes in this project. List problems by severity with file and line, then suggest fixes. {args}", "Review changes"),
    "explain": ("Explain {args} in simple terms, with one short example.", "Explain something"),
    "summarize": ("Summarize {args} in five bullet points, keeping the key numbers and names.", "Summarize"),
    "fix": ("Find and fix the problem: {args}. Reproduce it first, then fix the cause and show how you checked.", "Fix a problem"),
    "test": ("Write tests for {args} that cover normal use and edge cases, then run them and report the result.", "Write tests"),
    "plan": ("Make a short, ordered plan (3-8 steps, each checkable) for: {args}", "Plan work"),
    "docs": ("Write clear documentation for {args}: purpose, quick start with an example, and reference.", "Write docs"),
    "translate": ("Translate the following into {lang}, keeping formatting and placeholders: {args}", "Translate"),
    "commit": ("Write a conventional commit message for the current changes. {args}", "Commit message"),
}
BUILTIN_PARAMS = {"translate": ["lang"]}


class CommandError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _dir(root):
    return data_dir(root) / "commands"


def _custom(root):
    out = []
    for p in sorted(_dir(root).glob("*.json")) if _dir(root).exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return out


def list_commands(root):
    b = [{"name": n, "kind": "prompt", "description": d, "template": t, "params": BUILTIN_PARAMS.get(n, []), "builtin": True} for n, (t, d) in BUILTIN.items()]
    return b + [{**c, "builtin": False} for c in _custom(root)]


def save(root, spec):
    name = str(spec.get("name", "")).strip().lstrip("/").lower()
    kind = spec.get("kind", "prompt")
    template = str(spec.get("template", "")).strip()
    if not NAME.match(name):
        raise CommandError("name must be lowercase letters, digits and dashes, like daily-report")
    if name in BUILTIN or any(c["name"] == name for c in _custom(root)):
        raise CommandError(f"a command called /{name} already exists")
    if kind not in ("prompt", "shell"):
        raise CommandError("kind must be prompt or shell")
    if not template or len(template) > 4000:
        raise CommandError("a template is required (max 4,000 characters)")
    params = [str(p) for p in spec.get("params", [])]
    if len(params) > 8 or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,20}", p) and p != "args" for p in params):
        raise CommandError("params must be up to 8 lowercase names (not 'args')")
    used = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", template)) - {"args"}
    if used - set(params):
        raise CommandError(f"the template uses undeclared parameters: {sorted(used - set(params))}")
    if kind == "shell" and danger.classify(re.sub(r"\{[a-z0-9_]+\}", "x", template))["level"] == "block":
        raise CommandError("that command is blocked for safety")
    rec = {"name": name, "kind": kind, "description": str(spec.get("description", ""))[:120], "template": template, "params": params,
           "network": bool(spec.get("network", False)) and kind == "shell"}
    _dir(root).mkdir(parents=True, exist_ok=True)
    (_dir(root) / f"{name}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def delete(root, name):
    p = _dir(root) / f"{name}.json"
    if not NAME.match(name) or not p.exists():
        raise CommandError("command not found (built-in commands cannot be deleted)", 404)
    p.unlink()


def get(root, name):
    for c in list_commands(root):
        if c["name"] == name:
            return c
    raise CommandError(f"unknown command /{name}", 404)


def parse(line):
    line = str(line or "").strip()
    if not line.startswith("/"):
        raise CommandError("a command starts with /")
    try:
        parts = shlex.split(line[1:])
    except ValueError:
        parts = line[1:].split()
    if not parts:
        raise CommandError("type a command name after /")
    return parts[0].lower(), parts[1:]


def _fill(cmd, args, quote=False):
    params = cmd["params"]
    if len(args) < len(params):
        raise CommandError(f"/{cmd['name']} needs: {' '.join('<%s>' % p for p in params)}" + (" [more text]" if "{args}" in cmd["template"] else ""))
    values = {p: args[i] for i, p in enumerate(params)}
    rest = " ".join(args[len(params):])
    q = (lambda v: shlex.quote(v) if v else "''") if quote else (lambda v: v)
    values = {k: q(v) for k, v in values.items()}
    values["args"] = q(rest) if quote else rest
    return re.sub(r"\{([a-z][a-z0-9_]*)\}", lambda m: values.get(m.group(1), m.group(0)), cmd["template"]).strip()


def expand(root, line):
    """'/review src/app.py' -> {'kind': 'prompt', 'text': ...} or {'kind': 'shell', 'command': ...}."""
    name, args = parse(line)
    cmd = get(root, name)
    return {"name": name, "kind": cmd["kind"], **({"text": _fill(cmd, args)} if cmd["kind"] == "prompt" else {"command": _fill(cmd, args, quote=True), "network": cmd["network"]})}


def run_shell(root, project_id, line, cancel=None, timeout=60):
    """Run a user-created shell command in the project's sandbox (you wrote it, so no approval; dangerous ones are still blocked)."""
    e = expand(root, line)
    if e["kind"] != "shell":
        raise CommandError(f"/{e['name']} is a prompt command; send it to an agent instead")
    ws = projects.workspace(root, project_id)
    decision, reasons = danger.decide(e["command"], str(ws), "standard")
    if decision == "block":
        raise CommandError("blocked for safety: " + "; ".join(x["reason"] for x in reasons))
    cfg = project_env.get(root, project_id)
    if e["network"] and cfg["network"] == "off":
        raise CommandError("this project's environment is offline")
    u = projects.usage(root, project_id)
    res = envs.run_command(ws, e["command"], network=e["network"], env=project_env.command_env(root, project_id), timeout=min(timeout, cfg["timeout"]), free_bytes=u["free_bytes"],
                           home=projects.home(root, project_id), cancel=cancel)
    return {**res, "command": e["command"], "warnings": [x["reason"] for x in reasons if x["level"] in ("warn", "confirm")]}
