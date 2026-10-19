"""MCP server (stdio, JSON-RPC 2.0, newline-delimited) exposing agentlab to any MCP-capable agent.

Note: MCP lets an agent *call* agentlab. It cannot force an agent to record itself or obey a verdict,
so use the hook (Claude Code) or the proxy for enforcement and this for cooperative agents.
"""

import json
import sys
import uuid
from pathlib import Path

from . import __version__
from .hooks import data_dir, load_policy
from .recorder import append_event
from .schema import KINDS
from .tripwires import Tripwires

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
_STR = {"type": "string"}

TOOLS = [
    {"name": "check_policy",
     "description": "Ask the project's sandbox policy whether an action is allowed BEFORE doing it. "
                    "Returns ALLOWED or BLOCKED with the reason. Respect a BLOCKED answer.",
     "inputSchema": {"type": "object", "required": ["action", "target"], "properties": {
         "action": {"type": "string", "enum": ["read", "write", "run", "network"]},
         "target": {**_STR, "description": "File path, shell command, or hostname"}}}},
    {"name": "record_note",
     "description": "Record a step or decision in the project's flight recorder so humans can replay this run.",
     "inputSchema": {"type": "object", "required": ["content"], "properties": {
         "content": _STR, "kind": {"type": "string", "enum": list(KINDS), "default": "decision"},
         "run": {**_STR, "description": "Run id; defaults to one id per server session"}}}},
    {"name": "plant_canaries",
     "description": "Insert verifiable made-up facts into a document. Returns the document and the questions to ask later.",
     "inputSchema": {"type": "object", "required": ["seed", "document"], "properties": {
         "seed": _STR, "document": _STR, "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3}}}},
    {"name": "audit_answer",
     "description": "Check an answer against the canaries for a seed. Flags contradicted or unsupported facts.",
     "inputSchema": {"type": "object", "required": ["seed", "answer"], "properties": {
         "seed": _STR, "answer": _STR, "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
         "expect_all": {"type": "boolean", "default": False}}}},
]


class Server:
    def __init__(self, project="."):
        self.project = Path(project).resolve()
        self.run = f"mcp-{uuid.uuid4().hex[:8]}"

    # ---- tools: arguments -> text (raise ValueError for a tool-level error) ----
    def _check_policy(self, a):
        policy = load_policy(self.project)
        if policy is None:
            return "ALLOWED: no policy configured (create .agentlab/policy.txt)"
        action = a.get("action")
        if action not in ("read", "write", "run", "network"):
            raise ValueError("action must be read, write, run or network")
        ok, why = policy.check(action, str(a.get("target", "")))
        return f"{'ALLOWED' if ok else 'BLOCKED'}: {why}"

    def _record_note(self, a):
        kind = a.get("kind", "decision")
        run = str(a.get("run") or self.run)
        if not run.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError("run may contain only letters, digits, - _ .")
        step = append_event(data_dir(self.project) / "runs" / f"{run}.jsonl", kind,
                            str(a.get("content", ""))[:20000], tool="mcp")
        return f"recorded step {step} in run {run}"

    def _tripwires(self, a):
        seed = str(a.get("seed", "")).strip()
        if not seed:
            raise ValueError("seed is required")
        t = Tripwires(seed)
        t.make(max(1, min(int(a.get("n", 3)), 10)))
        return t

    def _plant(self, a):
        t = self._tripwires(a)
        qs = "\n".join(f"- {q} -> expect {c['value']}" for c, q in t.questions())
        return f"{t.plant(str(a.get('document', '')))}\n\n---\nQuestions to ask later:\n{qs}"

    def _audit(self, a):
        t = self._tripwires(a)
        answer = str(a.get("answer", ""))
        lines = [f"- {r['subject']}: {r['status']}" for r in t.audit(answer)]
        verdict = "looks healthy" if t.verdict(answer, bool(a.get("expect_all"))) else "possible hallucination"
        return f"{verdict}\n" + "\n".join(lines)

    # ---- protocol ----
    def handle(self, msg):
        """Return a response dict, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if "id" not in msg:
            return None  # notification (e.g. notifications/initialized)
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _ok(mid, {"protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                             "capabilities": {"tools": {}},
                             "serverInfo": {"name": "agentlab", "version": __version__}})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            fn = {"check_policy": self._check_policy, "record_note": self._record_note,
                  "plant_canaries": self._plant, "audit_answer": self._audit}.get(params.get("name"))
            if fn is None:
                return _error(mid, -32602, f"unknown tool: {params.get('name')}")
            try:
                text, bad = fn(params.get("arguments") or {}), False
            except (ValueError, TypeError) as e:
                text, bad = str(e), True
            return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": bad})
        return _error(mid, -32601, f"method not found: {method}")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(project=".", stdin=None, stdout=None):
    """Read one JSON message per line from stdin, write responses to stdout (nothing else goes there)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    srv = Server(project)
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = _error(None, -32700, "parse error")
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in map(srv.handle, batch) if r is not None]
            out = replies if isinstance(msg, list) and replies else (replies[0] if replies else None)
        if out is not None:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()
