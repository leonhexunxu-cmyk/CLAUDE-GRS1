"""Local model runners: serve your own weights (GGUF) with llama.cpp's llama-server, or register them in Ollama.

A runner is just a local OpenAI-compatible server on 127.0.0.1, so any agent can use it as its provider.
agentlab does not bundle an inference engine: install llama.cpp (llama-server) or Ollama first.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import projects
from .hooks import data_dir

ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")
INSTALL_HINT = "install llama.cpp (https://github.com/ggml-org/llama.cpp, e.g. `brew install llama.cpp`) so `llama-server` is on your PATH"


class RunnerError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def available():
    return {"llama-server": shutil.which("llama-server"), "ollama": shutil.which("ollama")}


def check_gguf(path):
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise RunnerError("this is not a GGUF file. llama.cpp and Ollama need GGUF weights; convert safetensors with llama.cpp's convert_hf_to_gguf.py")


def _file(root):
    return data_dir(root) / "runners.json"


def _load(root):
    p = _file(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(root, data):
    p = _file(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:  # a zombie counts as dead
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def _health(port, timeout=1.5):
    for path in ("/health", "/v1/models"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
                return r.status == 200
        except urllib.error.HTTPError:
            return False  # reachable but still loading
        except (urllib.error.URLError, OSError):
            continue
    return False


def provider_for(rec):
    return {"type": "openai", "base_url": f"http://127.0.0.1:{rec['port']}/v1", "model": rec["alias"], "key": "", "label": f"{rec['name']} (local weights)"}


def start(root, project_id, rel_path, *, name=None, ctx=4096, port=None, binary=None):
    ws_file = projects.resolve(root, project_id, rel_path)
    if not ws_file.is_file():
        raise RunnerError("weights file not found in the project", 404)
    check_gguf(ws_file)
    binary = binary or shutil.which("llama-server")
    if not binary:
        raise RunnerError(f"llama-server was not found: {INSTALL_HINT}")
    name = (name or Path(rel_path).stem)[:60]
    alias = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")[:60] or "local"
    if not ALIAS.match(alias):
        raise RunnerError("could not make a valid model name")
    ctx = max(256, min(int(ctx), 131072))
    port = int(port or _free_port())
    rid = uuid.uuid4().hex[:8]
    log = data_dir(root) / "runners" / f"{rid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as lf:
        proc = subprocess.Popen([binary, "-m", str(ws_file), "--host", "127.0.0.1", "--port", str(port), "-c", str(ctx), "--alias", alias],
                                stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    rec = {"id": rid, "name": name, "alias": alias, "runtime": "llama-server", "project": project_id, "path": rel_path, "port": port, "pid": proc.pid,
           "ctx": ctx, "status": "starting", "started": time.time(), "log": str(log)}
    data = _load(root)
    data[rid] = rec
    _save(root, data)
    return rec


def refresh(root, rec):
    if rec["status"] == "stopped":
        return rec
    if not _alive(rec["pid"]):
        rec["status"] = "stopped"
    else:
        rec["status"] = "ready" if _health(rec["port"]) else "starting"
    data = _load(root)
    data[rec["id"]] = rec
    _save(root, data)
    return rec


def wait_ready(root, rec, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        rec = refresh(root, rec)
        if rec["status"] in ("ready", "stopped"):
            return rec
        time.sleep(0.25)
    return rec


def list_runners(root):
    return sorted((refresh(root, r) for r in _load(root).values()), key=lambda r: -r["started"])


def get(root, rid):
    rec = _load(root).get(rid)
    if not rec:
        raise RunnerError("runner not found", 404)
    return refresh(root, rec)


def stop(root, rid):
    rec = get(root, rid)
    if rec["status"] != "stopped":
        try:
            os.killpg(rec["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        end = time.time() + 5
        while _alive(rec["pid"]) and time.time() < end:
            time.sleep(0.05)
        if _alive(rec["pid"]):
            try:
                os.killpg(rec["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    rec["status"] = "stopped"
    data = _load(root)
    data[rid] = rec
    _save(root, data)
    return rec


def ollama_create(root, project_id, rel_path, name, binary=None):
    """Register a GGUF file with Ollama (`ollama create`) and return the provider for it."""
    ws_file = projects.resolve(root, project_id, rel_path)
    if not ws_file.is_file():
        raise RunnerError("weights file not found in the project", 404)
    check_gguf(ws_file)
    binary = binary or shutil.which("ollama")
    if not binary:
        raise RunnerError("ollama was not found: install it from https://ollama.com")
    name = re.sub(r"[^a-z0-9._-]+", "-", str(name).lower()).strip("-")[:60]
    if not ALIAS.match(name):
        raise RunnerError("invalid model name")
    modelfile = ws_file.parent / f"Modelfile.{name}"
    modelfile.write_text(f"FROM ./{ws_file.name}\n", encoding="utf-8")
    r = subprocess.run([binary, "create", name, "-f", str(modelfile)], cwd=ws_file.parent, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RunnerError(f"ollama create failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": name, "key": "", "label": f"{name} (Ollama)"}
