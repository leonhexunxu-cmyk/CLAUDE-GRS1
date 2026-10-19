"""Project environments: the boundary an agent's commands run inside.

Python packages live in a per-project venv (outside the workspace, so they don't use its quota); non-secret
variables and explicitly shared keys are passed to commands; and `network: "off"` makes the project hard-offline
no matter what an agent is authorised to do.
"""

import json
import re
import shlex
import threading
import time

from . import envs, projects, vault
from .hooks import data_dir

PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+(,(==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+)*)?$")
VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESERVED = {"PATH", "HOME", "TMPDIR", "VIRTUAL_ENV", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP", "SHELL", "IFS", "BASH_ENV", "ENV"}
DEFAULTS = {"python": {"enabled": False, "packages": []}, "vars": {}, "secrets": [], "network": "allowed", "timeout": 60}


class EnvError(ValueError):
    pass


def _dir(root, pid):
    return projects.workspace(root, pid).parent


def get(root, pid):
    p = _dir(root, pid) / "environment.json"
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {**DEFAULTS, **cfg, "python": {**DEFAULTS["python"], **cfg.get("python", {})}}


def validate(spec):
    py = spec.get("python", {})
    pkgs = [str(x).strip() for x in py.get("packages", [])]
    bad = [x for x in pkgs if not PKG.match(x)]
    if bad or len(pkgs) > 60:
        raise EnvError(f"invalid package spec (use names with optional versions, like 'requests>=2'): {bad[:3]}")
    variables = spec.get("vars", {})
    if not isinstance(variables, dict) or len(variables) > 50:
        raise EnvError("vars must be an object with up to 50 entries")
    for k, v in variables.items():
        if not VAR.match(k) or k.upper() in RESERVED or not isinstance(v, str) or len(v) > 2000:
            raise EnvError(f"invalid variable {k!r} (reserved names such as PATH and HOME cannot be set)")
    secrets = [str(s) for s in spec.get("secrets", [])]
    if any(not vault.NAME.match(s) for s in secrets) or len(secrets) > 20:
        raise EnvError("secrets must be names of stored keys")
    network = spec.get("network", "allowed")
    if network not in ("allowed", "off"):
        raise EnvError("network must be 'allowed' or 'off'")
    try:
        timeout = int(spec.get("timeout", 60))
    except (TypeError, ValueError):
        raise EnvError("timeout must be a number of seconds")
    if not 1 <= timeout <= 600:
        raise EnvError("timeout must be between 1 and 600 seconds")
    return {"python": {"enabled": bool(py.get("enabled", False)), "packages": pkgs}, "vars": variables, "secrets": secrets, "network": network, "timeout": timeout}


def save(root, pid, spec):
    cfg = validate({**get(root, pid), **spec, "python": {**get(root, pid)["python"], **spec.get("python", {})}})
    (_dir(root, pid) / "environment.json").write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def venv_dir(root, pid):
    return _dir(root, pid) / "venv"


def command_env(root, pid):
    """Environment variables for an agent's commands in this project."""
    cfg = get(root, pid)
    env = dict(cfg["vars"])
    for name in cfg["secrets"]:  # explicit opt-in: these keys are visible to commands in this project
        value = vault.get_secret(root, name)
        if value:
            env[vault.ENV_FALLBACK.get(name, name.upper())] = value
    v = venv_dir(root, pid)
    if cfg["python"]["enabled"] and (v / "bin" / "python").exists():
        import os
        env["VIRTUAL_ENV"] = str(v)
        env["PATH"] = f"{v / 'bin'}:{os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    return env


def _setup_file(root, pid):
    return _dir(root, pid) / "env-setup.json"


def setup_status(root, pid):
    p = _setup_file(root, pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"status": "none"}


def setup_python(root, pid, wait=False):
    """Create the venv and install packages, in the background (network is needed for pip)."""
    cfg = get(root, pid)
    if not cfg["python"]["enabled"]:
        raise EnvError("turn on the Python environment first")
    if setup_status(root, pid).get("status") == "running":
        raise EnvError("setup is already running")
    pkgs = cfg["python"]["packages"]
    command = "python3 -m venv venv && " + (f"venv/bin/pip install --no-input --disable-pip-version-check {' '.join(shlex.quote(p) for p in pkgs)}" if pkgs else "venv/bin/python -V")
    rec = {"status": "running", "started": time.time(), "ended": None, "log": "", "packages": pkgs}
    _setup_file(root, pid).write_text(json.dumps(rec), encoding="utf-8")

    def work():
        try:
            r = envs.run_command(_dir(root, pid), command, network=True, timeout=900, home=projects.home(root, pid), max_output=50_000)
            rec.update(status="succeeded" if r["exit_code"] == 0 else "failed", log=r["output"][-4000:])
        except Exception as e:
            rec.update(status="failed", log=f"{type(e).__name__}: {e}")
        rec["ended"] = time.time()
        _setup_file(root, pid).write_text(json.dumps(rec), encoding="utf-8")

    t = threading.Thread(target=work, daemon=True)
    t.start()
    if wait:
        t.join()
    return rec
