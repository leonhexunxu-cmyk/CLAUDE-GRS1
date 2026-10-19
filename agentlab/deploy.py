"""One-click deploy: serve a project on localhost, or publish it to Cloudflare (Pages for sites, Workers for workers).

localhost   a static file server for the project's workspace (or a sub-folder), bound to 127.0.0.1 only.
cloudflare  runs `npx wrangler pages deploy <dir>` (or `wrangler deploy` for a Worker project) with the
            cloudflare_api_token and cloudflare_account keys from the vault. Needs Node.js. The token is passed
            in the environment of that one process, never on the command line or in logs.
"""

import functools
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import projects, vault
from .hooks import data_dir

_servers = {}
_lock = threading.Lock()


class DeployError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def _folder(root, project_id, sub=""):
    ws = projects.workspace(root, project_id)
    d = projects.resolve(root, project_id, sub) if sub else ws
    if not d.is_dir():
        raise DeployError("that folder does not exist in the project", 404)
    return d


def _history(root):
    p = data_dir(root) / "deploys.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _record(root, rec):
    p = data_dir(root) / "deploys.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps((_history(root) + [rec])[-200:]), encoding="utf-8")


def serve_local(root, project_id, sub="", port=0):
    """Start (or return) a localhost server for the project. -> {url, port}."""
    d = _folder(root, project_id, sub)
    with _lock:
        cur = _servers.get(project_id)
        if cur:
            return cur["info"]
        try:
            port = int(port or 0)
        except (TypeError, ValueError):
            raise DeployError("port must be a number")
        if port and not 1024 <= port <= 65535:
            raise DeployError("port must be between 1024 and 65535")
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(d)))
        except OSError as e:
            raise DeployError(f"could not open port {port}: {e.strerror}")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        info = {"project": project_id, "target": "localhost", "url": f"http://127.0.0.1:{srv.server_port}/", "port": srv.server_port, "folder": sub or ".", "started": time.time()}
        _servers[project_id] = {"srv": srv, "info": info}
    _record(root, {"id": uuid.uuid4().hex[:8], **info, "status": "live"})
    return info


def stop_local(project_id):
    with _lock:
        cur = _servers.pop(project_id, None)
    if not cur:
        raise DeployError("this project is not being served", 404)
    cur["srv"].shutdown()
    cur["srv"].server_close()


def local_servers():
    with _lock:
        return [v["info"] for v in _servers.values()]


def _detect(d):
    if (d / "wrangler.toml").exists() and re.search(r"(?m)^main\s*=", (d / "wrangler.toml").read_text(encoding="utf-8", errors="replace")):
        return "worker"
    return "pages"


def cloudflare_command(kind, name, folder):
    if kind == "worker":
        return ["npx", "--yes", "wrangler@3", "deploy"]
    return ["npx", "--yes", "wrangler@3", "pages", "deploy", folder, "--project-name", name, "--commit-dirty=true"]


def deploy_cloudflare(root, project_id, sub="", name=None, runner=None):
    """Publish to Cloudflare. -> {url, output}. Blocks until wrangler finishes (call it from a background thread)."""
    d = _folder(root, project_id, sub)
    token, account = vault.get_secret(root, "cloudflare_api_token"), vault.get_secret(root, "cloudflare_account")
    if not token or not account:
        raise DeployError("save cloudflare_api_token and cloudflare_account under Providers first")
    if not runner and not shutil.which("npx"):
        raise DeployError("Node.js (npx) is needed to deploy to Cloudflare")
    p = projects.get(root, project_id)
    name = re.sub(r"[^a-z0-9-]+", "-", (name or p["name"]).lower()).strip("-")[:58] or "agentlab-site"
    kind = _detect(d)
    cmd = cloudflare_command(kind, name, ".")
    env = {"PATH": __import__("os").environ.get("PATH", "/usr/bin:/bin"), "HOME": str(projects.home(root, project_id)), "CLOUDFLARE_API_TOKEN": token, "CLOUDFLARE_ACCOUNT_ID": account, "CI": "1"}
    projects.home(root, project_id).mkdir(parents=True, exist_ok=True)
    try:
        if runner:
            code, out = runner(cmd, str(d), env)
        else:
            r = subprocess.run(cmd, cwd=str(d), env=env, capture_output=True, text=True, timeout=600)
            code, out = r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        raise DeployError("wrangler took too long (10 minutes)")
    out = vault.redact_value(out, root).replace(token, "[redacted]")
    urls = re.findall(r"https://[\w.-]+\.(?:pages\.dev|workers\.dev)\S*", out)
    rec = {"id": uuid.uuid4().hex[:8], "project": project_id, "target": f"cloudflare-{kind}", "url": urls[-1] if urls else None, "folder": sub or ".", "started": time.time(),
           "status": "live" if code == 0 else "failed"}
    _record(root, rec)
    if code != 0:
        raise DeployError(f"wrangler failed: {out[-800:]}", 502)
    return {**rec, "output": out[-3000:]}


def history(root, project_id=None):
    return [h for h in reversed(_history(root)) if project_id in (None, h["project"])]
