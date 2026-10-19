"""Claude Code hook adapter: record every event and enforce the project's sandbox policy."""

import json
import os
import re
from pathlib import Path

from .recorder import append_event
from .sandbox import parse_policy

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def data_dir(project):
    return Path(os.environ.get("AGENTLAB_DIR") or Path(project) / ".agentlab")


def load_policy(project):
    path = data_dir(project) / "policy.txt"
    return parse_policy(path.read_text(encoding="utf-8")) if path.exists() else None


def summarize(tool, inp):
    inp = inp or {}
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "path"):
        if inp.get(key):
            return str(inp[key])
    return json.dumps(inp, ensure_ascii=False)[:300]


def process(event, project):
    """Handle one hook payload. Returns (exit_code, message); exit code 2 blocks a tool call."""
    project = Path(event.get("cwd") or project)
    name = event.get("hook_event_name", "")
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(event.get("session_id") or "session"))[:100]
    run = data_dir(project) / "runs" / f"{session}.jsonl"
    tool, inp = event.get("tool_name", ""), event.get("tool_input") or {}

    if name == "SessionStart":
        append_event(run, None, "", meta={"source": "claude-code", "cwd": str(project)})
    elif name == "UserPromptSubmit":
        append_event(run, "prompt", event.get("prompt", ""))
    elif name == "PreToolUse":
        step = append_event(run, "tool_call", summarize(tool, inp), tool=tool)
        policy = load_policy(project)
        if policy:
            ok, why = policy.check_tool(tool, inp, str(project))
            if not ok:
                append_event(run, "decision", f"BLOCKED {tool}: {why}", tool=tool, blocks_step=step)
                return 2, f"agentlab sandbox blocked this call: {why}"
    elif name == "PostToolUse":
        kind = "file_edit" if tool in EDIT_TOOLS else "tool_result"
        content = summarize(tool, inp) if kind == "file_edit" else str(event.get("tool_response", ""))[:2000]
        append_event(run, kind, content, tool=tool)
    elif name == "Stop":
        append_event(run, "output", "(turn ended)")
    return 0, ""
