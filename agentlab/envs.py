"""Dedicated environments: run a command in a disposable copy of the project, then review and apply the changes.

Isolation (be honest about it):
  * files      - the task works in its own copy; your project changes only when you apply a reviewed diff
  * environment - a scrubbed environment (only PATH/LANG/TERM + variables you list), its own HOME, wall-clock
                  timeout, CPU limit, process-group kill
  * network    - blocked with `unshare` when the machine allows it (otherwise the task refuses to start)
  * docker     - optional backend with --network none, memory/cpu/pids limits
This is not a security boundary against hostile code. Use the docker backend for that.
"""

import difflib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .hooks import data_dir
from .recorder import append_event

SECRET_NAMES = re.compile(r"^(\.env($|\.)|id_(rsa|dsa|ecdsa|ed25519)|.*\.(pem|key|p12|pfx)$|\.netrc$|\.npmrc$|\.pypirc$)")
IGNORE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}
PROTECTED = (".agentlab", ".claude", ".kimi-code", ".git", ".mcp.json", ".github/workflows")
MAX_COPY_BYTES, MAX_COPY_FILES = 300_000_000, 50_000
MAX_LOG = 2_000_000
STATUSES = ("queued", "running", "succeeded", "failed", "timeout", "cancelled", "interrupted")
TASK_ID = re.compile(r"^[a-f0-9]{10}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,199}$")

_caps = {}


class TaskError(ValueError):
    pass


def _unshare_prefix():
    """Argv prefix that gives a command its own empty network namespace, or None if this machine can't."""
    if "unshare" in _caps:
        return _caps["unshare"]
    prefix = None
    if shutil.which("unshare"):
        for flags in (["--user", f"--map-user={os.getuid()}", f"--map-group={os.getgid()}", "--net"], ["-r", "-n"]):
            try:
                if subprocess.run(["unshare", *flags, "--", "true"], capture_output=True, timeout=10).returncode == 0:
                    prefix = ["unshare", *flags, "--"]
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
    _caps["unshare"] = prefix
    return prefix


def _docker_ready():
    if "docker" not in _caps:
        ok = False
        if shutil.which("docker"):
            try:
                ok = subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        _caps["docker"] = ok
    return _caps["docker"]


def capabilities():
    return {"network_isolation": _unshare_prefix() is not None, "docker": _docker_ready()}


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root, include_git=False):
    """Yield (relative_posix_path, absolute_path) for regular files, skipping ignored dirs, symlinks and secret-looking files."""
    root = str(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        skip = set(IGNORE_DIRS) | ({".agentlab"} if rel_dir == "." else set()) | (set() if include_git else {".git"})
        dirnames[:] = [d for d in dirnames if d not in skip and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if SECRET_NAMES.match(name) or os.path.islink(full) or not os.path.isfile(full):
                continue  # secret-looking files are never copied into an environment
            yield os.path.normpath(os.path.join(rel_dir, name)).replace(os.sep, "/"), full


def _snapshot(root, include_git=False):
    return {rel: _sha1(full) for rel, full in _files(root, include_git)}


def is_protected(rel):
    return any(rel == p or rel.startswith(p + "/") for p in PROTECTED)


class Manager:
    """Creates, runs and reviews tasks for one project. Runs tasks on background threads."""

    def __init__(self, project, on_finish=None):
        self.project = Path(project).resolve()
        self.dir = data_dir(self.project) / "tasks"
        self.on_finish = on_finish
        self._procs = {}
        self._cancel_flags = set()
        self._lock = threading.Lock()
        self._mark_interrupted()

    # ---- storage ----
    def _tdir(self, task_id):
        if not TASK_ID.match(task_id or ""):
            raise TaskError("invalid task id")
        return self.dir / task_id

    def _save(self, rec):
        d = self.dir / rec["id"]
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "task.json.tmp"
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, d / "task.json")

    def get(self, task_id):
        p = self._tdir(task_id) / "task.json"
        if not p.exists():
            raise TaskError("task not found")
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self):
        recs = []
        for p in self.dir.glob("*/task.json") if self.dir.exists() else []:
            try:
                recs.append(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                pass
        return sorted(recs, key=lambda r: r["created"], reverse=True)

    def log_tail(self, task_id, limit=20000):
        p = self._tdir(task_id) / "output.log"
        if not p.exists():
            return ""
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            return f.read().decode("utf-8", "replace")

    def _mark_interrupted(self):
        for rec in self.list():
            if rec["status"] in ("queued", "running"):
                rec.update(status="interrupted", ended=time.time(), error="server stopped while the task was running")
                self._save(rec)

    # ---- create + run ----
    def create(self, spec):
        command = str(spec.get("command", "")).strip()
        if not command or len(command) > 10_000:
            raise TaskError("command is required (max 10,000 characters)")
        backend = spec.get("backend", "local")
        if backend not in ("local", "docker"):
            raise TaskError("backend must be local or docker")
        network = bool(spec.get("network", False))
        if backend == "local" and not network and _unshare_prefix() is None:
            raise TaskError("network isolation is not available on this machine; allow network or use the docker backend")
        if backend == "docker" and not _docker_ready():
            raise TaskError("docker is not available (is the daemon running?)")
        image = str(spec.get("image") or "python:3.12-slim")
        if not IMAGE.match(image):
            raise TaskError("invalid image name")
        try:
            timeout = max(1, min(int(spec.get("timeout", 600)), 3600))
        except (TypeError, ValueError):
            raise TaskError("timeout must be a number of seconds")
        pass_env = list(spec.get("pass_env") or [])
        if len(pass_env) > 20 or not all(isinstance(n, str) and ENV_NAME.match(n) for n in pass_env):
            raise TaskError("pass_env must be up to 20 valid variable names")
        files = spec.get("files") or {}
        if not isinstance(files, dict) or len(files) > 20 or not all(
                isinstance(k, str) and FILE_NAME.match(k) and isinstance(v, str) and len(v) <= 1_000_000 for k, v in files.items()):
            raise TaskError("files must map simple file names to text (max 20 files, 1 MB each)")
        rec = {"id": uuid.uuid4().hex[:10], "name": str(spec.get("name") or command)[:80], "command": command,
               "backend": backend, "network": network, "timeout": timeout, "image": image, "pass_env": pass_env,
               "include_git": bool(spec.get("include_git", False)), "status": "queued", "created": time.time(),
               "started": None, "ended": None, "exit_code": None, "error": None, "changes": None,
               "applied": False, "discarded": False}
        self._save(rec)
        threading.Thread(target=self._run, args=(rec, files), daemon=True).start()
        return rec

    def _copy_project(self, work, include_git):
        total = count = 0
        for rel, full in _files(self.project, include_git):
            total += os.path.getsize(full)
            count += 1
            if total > MAX_COPY_BYTES or count > MAX_COPY_FILES:
                raise TaskError("project is too large to copy into an environment (limit 300 MB / 50,000 files)")
        for rel, full in _files(self.project, include_git):
            dest = work / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)

    def _env(self, rec, home):
        keep = {k: os.environ[k] for k in ("PATH", "LANG", "TERM", "TZ") if k in os.environ}
        keep.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        keep.update({k: os.environ[k] for k in rec["pass_env"] if k in os.environ})
        keep.update(HOME=str(home), TMPDIR=str(home / "tmp"), AGENTLAB_DIR=str(data_dir(self.project)),
                    AGENTLAB_TASK=rec["id"], CI="1")
        return keep

    def _argv(self, rec, work, env):
        if rec["backend"] == "docker":
            argv = ["docker", "run", "--rm", "--network", "bridge" if rec["network"] else "none", "--memory", "2g",
                    "--cpus", "2", "--pids-limit", "512", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{work}:/work", "-w", "/work", "-e", "HOME=/tmp", "-e", f"AGENTLAB_TASK={rec['id']}"]
            for name in rec["pass_env"]:
                if name in env:
                    argv += ["-e", name]
            return [*argv, rec["image"], "sh", "-c", rec["command"]]
        # ulimit: no core dumps, CPU seconds capped at twice the wall-clock timeout
        script = 'ulimit -c 0; ulimit -t "$1"; shift; exec /bin/sh -c "$1"'
        argv = ["/bin/sh", "-c", script, "sh", str(rec["timeout"] * 2 + 5), rec["command"]]
        return ([] if rec["network"] else _unshare_prefix()) + argv

    def _run(self, rec, files):
        tdir = self.dir / rec["id"]
        run_file = data_dir(self.project) / "runs" / f"task-{rec['id']}.jsonl"
        try:
            rec.update(status="running", started=time.time())
            self._save(rec)
            work, home = tdir / "work", tdir / "home"
            work.mkdir(parents=True, exist_ok=True)
            (home / "tmp").mkdir(parents=True, exist_ok=True)
            self._copy_project(work, rec["include_git"])
            for name, content in files.items():
                (work / name).write_text(content, encoding="utf-8")
            (tdir / "snapshot.json").write_text(json.dumps(_snapshot(work, rec["include_git"])), encoding="utf-8")
            append_event(run_file, "prompt", rec["command"], meta={"source": "task", "task": rec["id"]})
            env = self._env(rec, home)
            with open(tdir / "output.log", "wb") as log:
                proc = subprocess.Popen(self._argv(rec, work, env), cwd=work, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
                with self._lock:
                    self._procs[rec["id"]] = proc
                timed_out = threading.Event()
                timer = threading.Timer(rec["timeout"], lambda: (timed_out.set(), self._kill(proc)))
                timer.start()
                written = 0
                for chunk in iter(lambda: proc.stdout.read(4096), b""):
                    if written < MAX_LOG:
                        log.write(chunk[: MAX_LOG - written])
                        log.flush()
                        written += len(chunk)
                proc.wait()
                timer.cancel()
            rec["exit_code"] = proc.returncode
            cancelled = self._cancelled(rec["id"])
            rec["status"] = "cancelled" if cancelled else "timeout" if timed_out.is_set() else "succeeded" if proc.returncode == 0 else "failed"
        except TaskError as e:
            rec.update(status="failed", error=str(e))
        except Exception as e:  # never leave a task stuck in "running"
            rec.update(status="failed", error=f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._procs.pop(rec["id"], None)
        rec["ended"] = time.time()
        try:
            ch = self._diff(rec["id"], with_text=False)
            rec["changes"] = {k: sum(1 for c in ch if c["status"] == k) for k in ("added", "modified", "deleted")}
        except Exception:
            rec["changes"] = None
        self._save(rec)
        try:
            tail = self.log_tail(rec["id"], 1500)
            append_event(run_file, "output" if rec["status"] == "succeeded" else "error",
                         f"{rec['status']} (exit {rec['exit_code']})\n{tail}".strip())
        except Exception:
            pass
        if self.on_finish:
            try:
                self.on_finish(rec)
            except Exception:
                pass

    def _cancelled(self, task_id):
        return task_id in self._cancel_flags

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def cancel(self, task_id):
        rec = self.get(task_id)
        with self._lock:
            proc = self._procs.get(task_id)
        if not proc:
            raise TaskError("task is not running")
        self._cancel_flags.add(task_id)
        self._kill(proc)
        return rec

    # ---- review ----
    def _diff(self, task_id, with_text=True):
        tdir = self._tdir(task_id)
        work = tdir / "work"
        if not work.exists():
            return []
        rec = self.get(task_id)
        base = json.loads((tdir / "snapshot.json").read_text(encoding="utf-8")) if (tdir / "snapshot.json").exists() else {}
        now = {rel: full for rel, full in _files(work, rec["include_git"])}
        out = []
        for rel in sorted(set(base) | set(now)):
            if rel in now and rel not in base:
                status = "added"
            elif rel not in now:
                status = "deleted"
            elif _sha1(now[rel]) != base[rel]:
                status = "modified"
            else:
                continue
            item = {"path": rel, "status": status, "protected": is_protected(rel)}
            if with_text and len(out) < 50:
                item["diff"] = self._text_diff(rel, now.get(rel), status)
            out.append(item)
        return out

    def _text_diff(self, rel, new_path, status):
        old_path = self.project / rel
        try:
            old = old_path.read_text(encoding="utf-8").splitlines() if status != "added" and old_path.is_file() else []
            new = Path(new_path).read_text(encoding="utf-8").splitlines() if new_path else []
        except (UnicodeDecodeError, OSError):
            return None  # binary or unreadable
        if len(old) > 5000 or len(new) > 5000:
            return None
        return "\n".join(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", lineterm="", n=2))[:200_000]

    def changes(self, task_id):
        return self._diff(task_id)

    def apply(self, task_id, paths=None):
        """Copy reviewed changes into the project. Refuses protected paths and files edited since the task began."""
        rec = self.get(task_id)
        if rec["status"] in ("queued", "running"):
            raise TaskError("task is still running")
        if rec["discarded"]:
            raise TaskError("task environment was discarded")
        tdir = self._tdir(task_id)
        base = json.loads((tdir / "snapshot.json").read_text(encoding="utf-8"))
        root = os.path.realpath(self.project)
        applied, skipped = [], []
        for ch in self._diff(task_id, with_text=False):
            rel = ch["path"]
            if paths is not None and rel not in paths:
                continue
            target = os.path.realpath(os.path.join(root, rel))
            if ch["protected"]:
                skipped.append({"path": rel, "reason": "protected"})
            elif not target.startswith(root + os.sep):
                skipped.append({"path": rel, "reason": "outside project"})
            elif (_sha1(target) if os.path.isfile(target) else None) != base.get(rel):
                skipped.append({"path": rel, "reason": "changed in project since the task started"})
            elif ch["status"] == "deleted":
                os.unlink(target)
                applied.append(rel)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(tdir / "work" / rel, target)
                applied.append(rel)
        if applied:
            rec["applied"] = True
            self._save(rec)
        return {"applied": applied, "skipped": skipped}

    def discard(self, task_id):
        rec = self.get(task_id)
        if rec["status"] in ("queued", "running"):
            raise TaskError("cancel the task first")
        for sub in ("work", "home"):
            shutil.rmtree(self._tdir(task_id) / sub, ignore_errors=True)
        rec["discarded"] = True
        self._save(rec)
        return rec


def run_command(cwd, command, *, network=False, timeout=60, env=None, free_bytes=None, max_output=100_000, home=None, cancel=None):
    """Run a shell command in `cwd` (an agent's workspace) with the same isolation as tasks.

    Scrubbed environment (PATH/LANG/TERM/TZ + `env`), own HOME, wall-clock timeout, CPU limit, per-file size cap
    from `free_bytes` (a soft disk quota), process-group kill, network blocked with `unshare` unless allowed.
    Returns {"exit_code", "output", "timed_out", "truncated", "duration"}. Raises TaskError if isolation was
    requested but is unavailable.
    """
    if not network and _unshare_prefix() is None:
        raise TaskError("network isolation is not available on this machine; allow network for this project")
    base = {k: os.environ[k] for k in ("PATH", "LANG", "TERM", "TZ") if k in os.environ}
    base.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    if home:
        Path(home).mkdir(parents=True, exist_ok=True)
        base.update(HOME=str(home), TMPDIR=str(home))
    base.update(env or {})
    base["CI"] = "1"
    fsize = f"ulimit -f {max(1, int(free_bytes) // 1024)}; " if free_bytes is not None else ""
    script = f'ulimit -c 0; {fsize}ulimit -t "$1"; shift; exec /bin/sh -c "$1"'
    argv = ([] if network else _unshare_prefix()) + ["/bin/sh", "-c", script, "sh", str(int(timeout) * 2 + 5), command]
    start = time.time()
    proc = subprocess.Popen(argv, cwd=cwd, env=base, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    timed_out = threading.Event()

    def kill():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    timer = threading.Timer(timeout, lambda: (timed_out.set(), kill()))
    timer.start()
    if cancel is not None:  # a watcher so cancelling stops even a silent, long-running command
        threading.Thread(target=lambda: (cancel.wait(timeout + 5), cancel.is_set() and kill()), daemon=True).start()
    chunks, size, truncated = [], 0, False
    try:
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            if size < max_output:
                chunks.append(chunk[: max_output - size])
            else:
                truncated = True
            size += len(chunk)
        proc.wait()
    finally:
        timer.cancel()
        kill()  # make sure no stragglers outlive the command
    return {"exit_code": proc.returncode, "output": b"".join(chunks).decode("utf-8", "replace"), "timed_out": timed_out.is_set(),
            "truncated": truncated or size > max_output, "duration": round(time.time() - start, 2)}
