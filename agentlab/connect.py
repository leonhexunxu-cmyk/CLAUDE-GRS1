"""One place to connect, check and disconnect agents: Claude Code, Kimi Code CLI and OpenClaw."""

import json
import os
import shlex
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from .hooks import data_dir
from .install import EVENTS, base_command, default_command, install_claude_code

MARK_BEGIN, MARK_END = "# >>> agentlab >>>", "# <<< agentlab <<<"
NAMES = {"claude-code": "Claude Code", "kimi": "Kimi Code", "openclaw": "OpenClaw"}


def _kimi_home():
    return Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")


def _openclaw_home():
    return Path.home() / ".openclaw"


def _plugin_dir(project):
    return data_dir(project) / "openclaw-plugin"


def _mcp_entry(project):
    exe, lead = base_command()
    return {"command": exe, "args": [*lead, "mcp", "--project", str(Path(project).resolve())]}


def _merge_mcp_json(path, project):
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg.setdefault("mcpServers", {})["agentlab"] = _mcp_entry(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path


def _drop_mcp_json(path):
    if not path.exists():
        return False
    cfg = json.loads(path.read_text(encoding="utf-8"))
    removed = cfg.get("mcpServers", {}).pop("agentlab", None) is not None
    if removed:
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return removed


# ---- Kimi Code CLI: user-level [[hooks]] in config.toml (appended as text, so we never rewrite your file) ----
def _kimi_block(command):
    # Kimi only accepts event, matcher, command, timeout in each [[hooks]]; extra fields break config loading.
    parts = [MARK_BEGIN]
    for event, matcher in EVENTS.items():
        parts += ["[[hooks]]", f"event = {json.dumps(event)}", f"command = {json.dumps(command)}", "timeout = 10", ""]
    return "\n".join(parts + [MARK_END]) + "\n"


def _connect_kimi(project, mcp, command):
    cfg = _kimi_home() / "config.toml"
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    notes = []
    if MARK_BEGIN not in text:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text + ("\n" if text and not text.endswith("\n") else "") + _kimi_block(command), encoding="utf-8")
        notes.append(f"added agentlab hooks to {cfg}")
    else:
        notes.append(f"hooks already present in {cfg}")
    if mcp:
        notes.append(f"registered MCP server in {_merge_mcp_json(Path(project) / '.kimi-code' / 'mcp.json', project)}")
    return notes


def _disconnect_kimi(project):
    cfg = _kimi_home() / "config.toml"
    notes = []
    if cfg.exists() and MARK_BEGIN in (text := cfg.read_text(encoding="utf-8")):
        head, _, rest = text.partition(MARK_BEGIN)
        cfg.write_text((head.rstrip("\n") + "\n" + rest.partition(MARK_END)[2].lstrip("\n")).lstrip("\n"), encoding="utf-8")
        notes.append(f"removed agentlab hooks from {cfg}")
    if _drop_mcp_json(Path(project) / ".kimi-code" / "mcp.json"):
        notes.append("removed MCP server from .kimi-code/mcp.json")
    return notes


# ---- OpenClaw: a generated plugin (before_tool_call) installed with `openclaw plugins install` ----
def _connect_openclaw(project, mcp, command):
    d = _plugin_dir(project)
    (d / "dist").mkdir(parents=True, exist_ok=True)
    js = resources.files("agentlab").joinpath("assets/openclaw_plugin.js").read_text(encoding="utf-8")
    js = js.replace("__HOOK_COMMAND__", json.dumps(shlex.split(command))).replace("__PROJECT__", json.dumps(str(Path(project).resolve())))
    (d / "dist" / "index.js").write_text(js, encoding="utf-8")
    (d / "package.json").write_text(json.dumps({
        "name": "agentlab", "version": "0.1.0", "type": "module", "files": ["dist", "openclaw.plugin.json"],
        "peerDependencies": {"openclaw": ">=2026.5.17"}, "openclaw": {"extensions": ["./dist/index.js"]}}, indent=2) + "\n", encoding="utf-8")
    (d / "openclaw.plugin.json").write_text(json.dumps({
        "id": "agentlab", "name": "agentlab", "description": "Records agent activity and enforces the agentlab sandbox policy.",
        "version": "0.1.0", "activation": {"onStartup": True},
        "configSchema": {"type": "object", "additionalProperties": False, "properties": {}}}, indent=2) + "\n", encoding="utf-8")
    notes = [f"generated plugin in {d}"]
    if shutil.which("openclaw"):
        r = subprocess.run(["openclaw", "plugins", "install", str(d)], capture_output=True, text=True, timeout=120)
        notes.append("installed into OpenClaw" if r.returncode == 0 else f"`openclaw plugins install` failed: {(r.stderr or r.stdout).strip()[:300]}")
    else:
        notes.append(f"next: openclaw plugins install {d}")
    if mcp:
        snippet = json.dumps({"mcp": {"servers": {"agentlab": _mcp_entry(project)}}}, indent=2)
        notes.append(f"optional MCP: add this to {_openclaw_home() / 'openclaw.json'} (or run `openclaw configure`):\n{snippet}")
    return notes


def _disconnect_openclaw(project):
    d = _plugin_dir(project)
    if d.exists():
        shutil.rmtree(d)
        return [f"removed {d}; also uninstall the plugin inside OpenClaw (see `openclaw plugins --help`)"]
    return []


# ---- Claude Code: project-level hooks + .mcp.json ----
def _connect_claude(project, mcp, command, policy_text=None):
    notes = [f"updated {install_claude_code(project, command, policy_text)}"]
    if mcp:
        notes.append(f"registered MCP server in {_merge_mcp_json(Path(project) / '.mcp.json', project)}")
    return notes


def _disconnect_claude(project):
    path = Path(project) / ".claude" / "settings.json"
    notes = []
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        for event, groups in list(cfg.get("hooks", {}).items()):
            for g in groups:
                g["hooks"] = [h for h in g.get("hooks", []) if " hook" not in h.get("command", "") or "agentlab" not in h.get("command", "")]
            cfg["hooks"][event] = [g for g in groups if g["hooks"]]
            if not cfg["hooks"][event]:
                del cfg["hooks"][event]
        if not cfg.get("hooks"):
            cfg.pop("hooks", None)
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        notes.append(f"removed agentlab hooks from {path}")
    if _drop_mcp_json(Path(project) / ".mcp.json"):
        notes.append("removed MCP server from .mcp.json")
    return notes


# ---- public API ----
def status(project):
    """[{id, name, detected, connected, command}] for every supported agent."""
    project = Path(project)
    cl = project / ".claude" / "settings.json"
    kimi_cfg = _kimi_home() / "config.toml"

    def has(path, needle):
        try:
            return needle in path.read_text(encoding="utf-8")
        except OSError:
            return False

    rows = {
        "claude-code": (bool(shutil.which("claude")) or (Path.home() / ".claude").exists() or cl.parent.exists(),
                        has(cl, "agentlab") and "hook" in cl.read_text(encoding="utf-8")),
        "kimi": (bool(shutil.which("kimi")) or _kimi_home().exists(), has(kimi_cfg, MARK_BEGIN)),
        "openclaw": (bool(shutil.which("openclaw")) or _openclaw_home().exists(), (_plugin_dir(project) / "openclaw.plugin.json").exists()),
    }
    return [{"id": a, "name": NAMES[a], "detected": d, "connected": c, "command": f"agentlab connect {a}"} for a, (d, c) in rows.items()]


def connect(agent, project, mcp=True, command=None, policy_text=None):
    """Connect one agent to a project. Returns human-readable notes."""
    if agent not in NAMES:
        raise ValueError(f"unknown agent {agent!r}; choose from {sorted(NAMES)}")
    project = Path(project).resolve()
    (project / ".agentlab").mkdir(exist_ok=True)  # opt this project in
    command = command or default_command()
    if agent == "claude-code":
        return _connect_claude(project, mcp, command, policy_text)
    if policy_text is not None:
        from .sandbox import parse_policy
        parse_policy(policy_text)
        (project / ".agentlab" / "policy.txt").write_text(policy_text, encoding="utf-8")
    return (_connect_kimi if agent == "kimi" else _connect_openclaw)(project, mcp, command)


def disconnect(agent, project):
    if agent not in NAMES:
        raise ValueError(f"unknown agent {agent!r}; choose from {sorted(NAMES)}")
    return {"claude-code": _disconnect_claude, "kimi": _disconnect_kimi, "openclaw": _disconnect_openclaw}[agent](Path(project).resolve())
