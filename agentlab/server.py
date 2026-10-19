"""Local HTTP backend + web UI. Binds to localhost only; JSON in, JSON out."""

import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import __version__, apigen, apikeys, connect as connect_mod, envs, insights, notify, pads, regress
from .hooks import data_dir
from .recorder import append_event, fork_run, load_run, summarize_run
from .sandbox import ATTACKS, parse_policy, simulate
from .schema import KINDS
from .tripwires import Tripwires

MAX_BODY = 5_000_000
RUN_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
KEY_ID = re.compile(r"^[a-f0-9]{8}$")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "frame-src 'self'; frame-ancestors 'none'")
# The code pad runner executes user code inside a sandboxed iframe + worker with no network.
RUNNER_CSP = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; worker-src blob:; frame-ancestors 'self'"
STATIC = {"style.css": "text/css", "app.js": "text/javascript", "i18n.json": "application/json",
          "runner.html": "text/html"}


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def make_server(project=".", port=8765):
    project = Path(project).resolve()
    runs_dir = data_dir(project) / "runs"
    policy_file = data_dir(project) / "policy.txt"
    manager = envs.Manager(project, on_finish=lambda rec: notify.fire(
        project, "task_finished", f"agentlab task “{rec['name']}” {rec['status']}" + (f" ({sum(rec['changes'].values())} file changes to review)" if rec.get("changes") and sum(rec["changes"].values()) else ""),
        {"task": rec["id"], "status": rec["status"], "exit_code": rec["exit_code"]}))
    ui_token = secrets.token_urlsafe(16)  # injected into the page we serve; lets the UI through key checks

    def ui_file(name):
        return resources.files("agentlab").joinpath("ui", name).read_bytes()

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

    # ---- handlers: (body, *path_args) -> JSON-able ----
    def list_runs(_):
        files = sorted(runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if runs_dir.exists() else []
        return {"runs": [summarize_run(p) for p in files]}

    def get_run(_, run_id):
        return {"id": run_id, "events": load_run(run_path(run_id))}

    def fork(body, run_id):
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
        policy_from(body)
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

    def agents(_):
        return {"agents": connect_mod.status(project)}

    def task_call(fn):
        """Run a task-manager call, turning its errors into API errors."""
        try:
            return fn()
        except envs.TaskError as e:
            raise ApiError(str(e), 404 if "not found" in str(e) else 400)

    def list_tasks(_):
        return {"tasks": manager.list()}

    def create_task(body):
        return task_call(lambda: manager.create(body))

    def get_task(_, task_id):
        return task_call(lambda: {**manager.get(task_id), "log": manager.log_tail(task_id)})

    def cancel_task(_, task_id):
        task_call(lambda: manager.cancel(task_id))
        return {"cancelling": True}

    def task_changes(_, task_id):
        return task_call(lambda: {"changes": manager.changes(task_id)})

    def apply_task(body, task_id):
        paths = body.get("paths")
        if paths is not None and not (isinstance(paths, list) and all(isinstance(p, str) for p in paths)):
            raise ApiError("paths must be a list of strings")
        return task_call(lambda: manager.apply(task_id, paths))

    def discard_task(_, task_id):
        return task_call(lambda: manager.discard(task_id))

    def list_connectors(_):
        return {"webhooks": notify.list_webhooks(project), "events": list(notify.EVENTS)}

    def save_connectors(body):
        try:
            return {"webhooks": notify.save_webhooks(project, body.get("webhooks", []))}
        except ValueError as e:
            raise ApiError(str(e))

    def test_connector(body):
        hid = str(body.get("id", ""))
        if not any(h["id"] == hid for h in notify.list_webhooks(project)):
            raise ApiError("webhook not found", 404)
        return {"results": notify.fire(project, "test", "agentlab test notification", {"test": True}, only_id=hid)}

    def list_pads(_):
        return {"pads": pads.list_pads(project)}

    def get_pad(_, pad_id):
        pad = pads.get(project, pad_id)
        if not pad:
            raise ApiError("pad not found", 404)
        return pad

    def save_pad(body):
        try:
            return pads.save(project, body.get("id"), str(body.get("name", "")), str(body.get("language", "")),
                             str(body.get("content", "")))
        except ValueError as e:
            raise ApiError(str(e))

    def delete_pad(_, pad_id):
        if not pads.delete(project, pad_id):
            raise ApiError("pad not found", 404)
        return {"deleted": True}

    def list_keys(_):
        return {"keys": apikeys.list_keys(project)}

    def create_key(body):
        rec, key = apikeys.create(project, str(body.get("label", "")))
        return {**rec, "key": key}

    def revoke_key(_, key_id):
        if not KEY_ID.match(key_id) or not apikeys.revoke(project, key_id):
            raise ApiError("key not found", 404)
        return {"revoked": True}

    def generate(body):
        try:
            code = apigen.generate(str(body.get("language", "")), str(body.get("base_url") or "http://127.0.0.1:8765"))
        except ValueError as e:
            raise ApiError(str(e))
        return {"language": body["language"], "filename": apigen.FILENAMES[body["language"]], "code": code}

    exact = {
        ("GET", "/api/health"): lambda b: {"ok": True, "project": str(project), "version": __version__},
        ("GET", "/api/openapi.json"): lambda b: apigen.openapi(__version__),
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
        ("GET", "/api/agents"): agents,
        ("GET", "/api/insights"): lambda b: insights.compute(project),
        ("GET", "/api/isolation"): lambda b: envs.capabilities(),
        ("GET", "/api/tasks"): list_tasks,
        ("POST", "/api/tasks"): create_task,
        ("GET", "/api/connectors"): list_connectors,
        ("POST", "/api/connectors"): save_connectors,
        ("POST", "/api/connectors/test"): test_connector,
        ("GET", "/api/pads"): list_pads,
        ("POST", "/api/pads"): save_pad,
        ("GET", "/api/keys"): list_keys,
        ("POST", "/api/keys"): create_key,
        ("POST", "/api/generate"): generate,
    }
    patterned = [
        ("GET", re.compile(r"^/api/runs/([^/]+)$"), get_run),
        ("POST", re.compile(r"^/api/runs/([^/]+)/fork$"), fork),
        ("GET", re.compile(r"^/api/tasks/([^/]+)$"), get_task),
        ("POST", re.compile(r"^/api/tasks/([^/]+)/cancel$"), cancel_task),
        ("GET", re.compile(r"^/api/tasks/([^/]+)/changes$"), task_changes),
        ("POST", re.compile(r"^/api/tasks/([^/]+)/apply$"), apply_task),
        ("POST", re.compile(r"^/api/tasks/([^/]+)/discard$"), discard_task),
        ("GET", re.compile(r"^/api/pads/([^/]+)$"), get_pad),
        ("POST", re.compile(r"^/api/pads/([^/]+)/delete$"), delete_pad),
        ("POST", re.compile(r"^/api/keys/([^/]+)/revoke$"), revoke_key),
    ]

    class Handler(BaseHTTPRequestHandler):
        server_version = "agentlab"

        def log_message(self, *args):
            pass

        def _send(self, code, payload, ctype="application/json", csp=CSP):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, path):
            if path == "/api/health" or not apikeys.any_keys(project):
                return True
            if self.headers.get("X-AgentLab-UI") == ui_token:
                return True
            auth = self.headers.get("Authorization", "")
            return auth.startswith("Bearer ") and apikeys.verify(project, auth[7:])

        def _handle(self, method):
            try:
                if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "[::1]"):
                    raise ApiError("forbidden host", 403)
                path = self.path.split("?")[0]
                if method == "GET" and path in ("/", "/index.html"):
                    html = ui_file("index.html").replace(b"__UI_TOKEN__", ui_token.encode())
                    return self._send(200, html, "text/html; charset=utf-8")
                if method == "GET" and path.startswith("/static/") and path[8:] in STATIC:
                    name = path[8:]
                    return self._send(200, ui_file(name), STATIC[name] + "; charset=utf-8",
                                      RUNNER_CSP if name == "runner.html" else CSP)
                if not path.startswith("/api/"):
                    raise ApiError("not found", 404)
                if not self._authorized(path):
                    raise ApiError("API key required: send 'Authorization: Bearer <key>'", 401)
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
                fn = exact.get((method, path))
                if fn:
                    return self._send(200, fn(body))
                for m, rx, fn in patterned:
                    hit = rx.match(path)
                    if m == method and hit:
                        return self._send(200, fn(body, *hit.groups()))
                raise ApiError("not found", 404)
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
