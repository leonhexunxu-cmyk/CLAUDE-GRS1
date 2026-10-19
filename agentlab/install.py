"""Install agentlab hooks into a project's .claude/settings.json (idempotent, merges)."""

import json
import shutil
import sys
from pathlib import Path

EVENTS = {"SessionStart": False, "UserPromptSubmit": False, "PreToolUse": True,
          "PostToolUse": True, "Stop": False}  # event -> takes a matcher


def base_command():
    """(executable, leading args) that launch agentlab, preferring the installed script."""
    return ("agentlab", []) if shutil.which("agentlab") else (sys.executable, ["-m", "agentlab"])


def default_command():
    exe, args = base_command()
    return " ".join([exe, *args, "hook"])


def install_claude_code(project, command=None, policy_text=None, with_permissions=False):
    """Merge hooks into <project>/.claude/settings.json. Returns the path written."""
    project = Path(project)
    command = command or default_command()
    path = project / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event, matcher in EVENTS.items():
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == command for g in groups for h in g.get("hooks", [])):
            continue
        group = {"hooks": [{"type": "command", "command": command}]}
        if matcher:
            group["matcher"] = "*"
        groups.append(group)
    if policy_text is not None:
        from .sandbox import parse_policy
        policy = parse_policy(policy_text)  # validate before writing anything
        (project / ".agentlab").mkdir(exist_ok=True)
        (project / ".agentlab" / "policy.txt").write_text(policy_text, encoding="utf-8")
        if with_permissions:
            perms = settings.setdefault("permissions", {})
            gen = policy.to_claude_settings()["permissions"]
            for key in ("allow", "deny"):
                perms[key] = sorted(set(perms.get(key, [])) | set(gen[key]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


def install_mcp_config(project, command=None, args=None):
    """Merge an agentlab entry into <project>/.mcp.json (Claude Code project-scoped MCP servers)."""
    path = Path(project) / ".mcp.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    exe, lead = (command, args or []) if command else base_command()
    cfg.setdefault("mcpServers", {})["agentlab"] = {"command": exe, "args": [*lead, "mcp", "--project", "."]}
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path
