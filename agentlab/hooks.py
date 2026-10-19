"""Claude Code hook adapter: record every event and enforce the project's sandbox policy."""

import json
import os
import re
from pathlib import Path

from .adapters import normalize
from .recorder import append_event
from .sandbox import parse_policy

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def data_dir(project):
    return Path(os.environ.get("AGENTLAB_DIR") or Path(project) / ".agentlab")


def find_project(cwd):
    """Nearest ancestor of cwd that has opted in by having a .agentlab directory (or None).

    User-level agent hooks (Kimi Code) fire in every repository; this keeps agentlab silent in the ones
    you have not connected.
    """
    if os.environ.get("AGENTLAB_DIR"):
        return Path(cwd)
    cwd = Path(cwd).resolve()
    return next((p for p in (cwd, *cwd.parents) if (p / ".agentlab").is_dir()), None)


def load_policy(project):
    path = data_dir(project) / "policy.txt"
    return parse_policy(path.read_text(encoding="utf-8")) if path.exists() else None


def summarize(tool, inp):
    tool, inp = normalize(tool, inp)
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "path"):
        if inp.get(key):
            return str(inp[key])
    return json.dumps(inp, ensure_ascii=False)[:300]


def _get(event, *keys, default=""):
    """First present key: agents name the same field differently."""
    return next((event[k] for k in keys if event.get(k) not in (None, "")), default)


def process(event, project):
    """Handle one hook payload from any agent. Returns (exit_code, message); exit code 2 blocks a tool call."""
    cwd = Path(event.get("cwd") or project)
    root = find_project(cwd)
    if root is None:
        return 0, ""
    name = event.get("hook_event_name", "")
    source = str(event.get("client_type") or "claude-code")
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(event.get("session_id") or "session"))[:100]
    run = data_dir(root) / "runs" / f"{session}.jsonl"
    tool = str(_get(event, "tool_name", "toolName", "tool"))
    inp = _get(event, "tool_input", "toolInput", "params", default={}) or {}

    if name == "SessionStart":
        append_event(run, None, "", meta={"source": source, "cwd": str(cwd)})
    elif name == "UserPromptSubmit":
        append_event(run, "prompt", str(_get(event, "prompt", "user_prompt", "message")), meta={"source": source})
    elif name == "PreToolUse":
        step = append_event(run, "tool_call", summarize(tool, inp), meta={"source": source}, tool=tool)
        policy = load_policy(root)
        if policy:
            ok, why = policy.check_tool(tool, inp, str(cwd), str(root))
            if not ok:
                append_event(run, "decision", f"BLOCKED {tool}: {why}", tool=tool, blocks_step=step)
                return 2, f"agentlab sandbox blocked this call: {why}"
    elif name == "PostToolUse":
        canon, _ = normalize(tool, inp)
        kind = "file_edit" if canon in EDIT_TOOLS else "tool_result"
        content = summarize(tool, inp) if kind == "file_edit" else str(_get(event, "tool_response", "tool_output", "output"))[:2000]
        append_event(run, kind, content, meta={"source": source}, tool=tool)
    elif name == "Stop":
        append_event(run, "output", "(turn ended)", meta={"source": source})
    return 0, ""
