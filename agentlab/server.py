"""Local HTTP backend + web UI. Binds to localhost only; JSON in, JSON out."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import regress
from .hooks import data_dir
from .recorder import append_event, fork_run, load_run, summarize_run
from .sandbox import ATTACKS, parse_policy, simulate
from .schema import KINDS
from .tripwires import Tripwires

MAX_BODY = 5_000_000
RUN_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def make_server(project=".", port=8765):
    project = Path(project).resolve()
    runs_dir = data_dir(project) / "runs"
    policy_file = data_dir(project) / "policy.txt"

    def run_path(run_id):
        if not RUN_ID.match(run_id or ""):
            raise ApiError("invalid run id")
        path = runs_dir / f"{run_id}.jsonl"
        if not path.exists():
            raise ApiError("run not found", 404)
        return path

    def policy_from(body):
        try:
            return parse_policy(str(body.get("rules", "")))
        except ValueError as e:
            raise ApiError(str(e))

    # ---- route handlers: (body or None) -> JSON-able ----
    def list_runs(_):
        files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if runs_dir.exists() else []
        return {"runs": [summarize_run(p) for p in files]}

    def get_run(run_id):
        return {"id": run_id, "events": load_run(run_path(run_id))}

    def fork(run_id, body):
        src = run_path(run_id)
        step = body.get("step")
        if not isinstance(step, int) or step < 0:
            raise ApiError("step must be a non-negative integer")
        n = 1
        while (runs_dir / f"{run_id}-fork{n}.jsonl").exists():
            n += 1
        new = f"{run_id}-fork{n}"
        fork_run(src, step, runs_dir / f"{new}.jsonl")
        return {"id": new}

    def ingest(body):
        kind, run = body.get("kind"), body.get("run")
        if kind not in KINDS:
            raise ApiError(f"kind must be one of {list(KINDS)}")
        if not RUN_ID.match(run or ""):
            raise ApiError("invalid run id")
        fields = {"tool": str(body["tool"])[:100]} if body.get("tool") else {}
        step = append_event(runs_dir / f"{run}.jsonl", kind, str(body.get("content", ""))[:20000], **fields)
        return {"step": step}

    def get_policy(_):
        return {"rules": policy_file.read_text(encoding="utf-8") if policy_file.exists() else ""}

    def save_policy(body):
        policy_from(body)  # validate
        policy_file.parent.mkdir(parents=True, exist_ok=True)
        policy_file.write_text(str(body.get("rules", "")), encoding="utf-8")
        return {"saved": True}

    def sim(body):
        rows = simulate(policy_from(body), ATTACKS)
        return {"results": [{"action": a, "target": t, "allowed": ok, "reason": why} for a, t, ok, why in rows]}

    def check(body):
        ok, why = policy_from(body).check(str(body.get("action", "")), str(body.get("target", "")))
        return {"allowed": ok, "reason": why}

    def settings(body):
        return policy_from(body).to_claude_settings()

    def compare(body):
        base, cand = body.get("baseline"), body.get("candidate")
        if not isinstance(base, dict) or not isinstance(cand, dict):
            raise ApiError("baseline and candidate must be objects: {case: {output: ...}}")
        try:
            findings = regress.compare_runs(base, cand, float(body.get("threshold", 0.8)))
        except (KeyError, TypeError):
            raise ApiError("every case needs an 'output' string")
        return {"findings": findings, "regressed": regress.has_regressions(findings)}

    def _tripwires(body):
        seed = str(body.get("seed", "")).strip()
        if not seed:
            raise ApiError("seed is required")
        t = Tripwires(seed)
        t.make(max(1, min(int(body.get("n", 3)), 10)))
        return t

    def plant(body):
        t = _tripwires(body)
        return {"document": t.plant(str(body.get("document", ""))),
                "questions": [{"question": q, "expected": c["value"]} for c, q in t.questions()]}

    def audit(body):
        t = _tripwires(body)
        answer = str(body.get("answer", ""))
        return {"report": t.audit(answer), "healthy": t.verdict(answer, expect_all=bool(body.get("expect_all", False)))}

    routes = {
        ("GET", "/api/health"): lambda b: {"ok": True, "project": str(project)},
        ("GET", "/api/runs"): list_runs,
        ("POST", "/api/events"): ingest,
        ("GET", "/api/policy"): get_policy,
        ("POST", "/api/policy"): save_policy,
        ("POST", "/api/policy/simulate"): sim,
        ("POST", "/api/policy/check"): check,
        ("POST", "/api/policy/settings"): settings,
        ("POST", "/api/regress"): compare,
        ("POST", "/api/tripwires/plant"): plant,
        ("POST", "/api/tripwires/audit"): audit,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "agentlab"

        def log_message(self, *args):
            pass

        def _send(self, code, payload, ctype="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method):
            try:
                if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "[::1]"):
                    raise ApiError("forbidden host", 403)
                path = self.path.split("?")[0]
                if method == "GET" and path in ("/", "/index.html"):
                    return self._send(200, resources.files("agentlab").joinpath("ui/index.html").read_bytes(), "text/html; charset=utf-8")
                body = None
                if method == "POST":
                    if "application/json" not in self.headers.get("Content-Type", ""):
                        raise ApiError("Content-Type must be application/json", 415)
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > MAX_BODY:
                        raise ApiError("body too large", 413)
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        raise ApiError("invalid JSON")
                    if not isinstance(body, dict):
                        raise ApiError("body must be a JSON object")
                m = re.match(r"^/api/runs/([^/]+)(/fork)?$", path)
                if m and method == "GET" and not m.group(2):
                    return self._send(200, get_run(m.group(1)))
                if m and method == "POST" and m.group(2):
                    return self._send(200, fork(m.group(1), body))
                fn = routes.get((method, path))
                if not fn:
                    raise ApiError("not found", 404)
                self._send(200, fn(body))
            except ApiError as e:
                self._send(e.code, {"error": str(e)})
            except Exception:
                self._send(500, {"error": "internal error"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_in_thread(project=".", port=0):
    srv = make_server(project, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
