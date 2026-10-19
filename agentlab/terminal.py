"""Built-in terminal: a real shell (PTY) in a project's workspace, streamed to the browser.

The shell runs as you, in the project folder, with the project's environment (variables, Python venv) and a
clean HOME. When the project's network policy is "off" and the machine supports it, the shell has no network.
This is your own terminal, not an agent's: commands are not blocked, but obviously destructive lines are
flagged in the output so a slip is visible. Sessions end after 30 idle minutes or when the server stops.
"""

import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import threading
import time
import uuid

from . import danger, envs, project_env, projects

IDLE = 30 * 60
MAX_BUFFER = 2_000_000
MAX_SESSIONS = 8
_sessions = {}
_lock = threading.Lock()


class TerminalError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class Session:
    def __init__(self, root, pid, cols=100, rows=30):
        self.id, self.project, self.created, self.last = uuid.uuid4().hex[:10], pid, time.time(), time.time()
        ws = projects.workspace(root, pid)
        home = projects.home(root, pid)
        home.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home), "TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8"),
               "PS1": r"\[\e[1;34m\]\W\[\e[0m\] $ ", "AGENTLAB_PROJECT": pid, **project_env.command_env(root, pid)}
        cfg = project_env.get(root, pid)
        self.offline = cfg.get("network") == "off" and envs._unshare_prefix() is not None
        shell = os.environ.get("SHELL") if os.environ.get("SHELL", "").endswith(("bash", "zsh", "sh")) else "/bin/bash"
        argv = ([*envs._unshare_prefix()] if self.offline else []) + [shell if os.path.exists(shell) else "/bin/sh", "-i"]
        self.pid_, self.fd = pty.fork()
        if self.pid_ == 0:  # child
            try:
                os.chdir(ws)
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        self.resize(cols, rows)
        self.buf, self.base, self.alive, self.line = bytearray(), 0, True, ""
        self.cond = threading.Condition()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while self.alive:
            try:
                r, _, _ = select.select([self.fd], [], [], 1.0)
                if not r:
                    continue
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self.alive = False
            with self.cond:
                self.buf += data
                if len(self.buf) > MAX_BUFFER:  # keep the tail; readers far behind skip ahead
                    cut = len(self.buf) - MAX_BUFFER
                    del self.buf[:cut]
                    self.base += cut
                self.cond.notify_all()
        try:
            os.waitpid(self.pid_, os.WNOHANG)
        except ChildProcessError:
            pass

    def write(self, text):
        self.last = time.time()
        warn = self._watch(text)
        os.write(self.fd, text.encode("utf-8", "replace"))
        return warn

    def _watch(self, text):
        """Flag (not block) obviously destructive lines as they are entered."""
        warn = None
        for ch in text:
            if ch in "\r\n":
                level, risks = danger.decide(self.line, "/", "standard")
                if level == "block":
                    warn = "; ".join(r["reason"] for r in risks)[:200]
                self.line = ""
            elif ch in "\x7f\b":
                self.line = self.line[:-1]
            elif ch == "\x03":
                self.line = ""
            elif ch.isprintable():
                self.line = (self.line + ch)[-2000:]
        return warn

    def read(self, offset, wait=20.0):
        """-> (bytes since offset, new offset, alive). Long-polls up to `wait` seconds for new output."""
        self.last = time.time()
        end = time.time() + wait
        with self.cond:
            while self.alive and self.base + len(self.buf) <= offset and time.time() < end:
                self.cond.wait(max(0.0, end - time.time()))
            start = max(offset, self.base) - self.base
            return bytes(self.buf[start:]), self.base + len(self.buf), self.alive

    def resize(self, cols, rows):
        cols, rows = max(20, min(int(cols), 400)), max(5, min(int(rows), 200))
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self):
        self.alive = False
        for sig in (signal.SIGHUP, signal.SIGKILL):
            try:
                os.kill(self.pid_, sig)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid_, 0)
        except ChildProcessError:
            pass


def _reap():
    now = time.time()
    with _lock:
        stale = [s for s in _sessions.values() if now - s.last > IDLE or not s.alive]
        for s in stale:
            _sessions.pop(s.id, None)
    for s in stale:
        s.close()


def open_session(root, pid, cols=100, rows=30):
    projects.get(root, pid)
    _reap()
    with _lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise TerminalError(f"at most {MAX_SESSIONS} terminals can be open; close one first", 429)
    s = Session(root, pid, cols, rows)
    with _lock:
        _sessions[s.id] = s
    return info(s.id)


def _get(sid):
    s = _sessions.get(sid or "")
    if not s:
        raise TerminalError("terminal not found (it may have timed out)", 404)
    return s


def info(sid):
    s = _get(sid)
    return {"id": s.id, "project": s.project, "alive": s.alive, "offline": s.offline, "created": s.created}


def list_sessions():
    _reap()
    return [info(sid) for sid in list(_sessions)]


def write(sid, text):
    if not isinstance(text, str) or len(text) > 100_000:
        raise TerminalError("input must be text (max 100,000 characters)")
    return {"warning": _get(sid).write(text)}


def read(sid, offset=0, wait=20.0):
    data, off, alive = _get(sid).read(int(offset), min(float(wait), 25.0))
    return {"data": data.decode("utf-8", "replace"), "offset": off, "alive": alive}


def resize(sid, cols, rows):
    _get(sid).resize(cols, rows)


def close(sid):
    s = _get(sid)
    with _lock:
        _sessions.pop(sid, None)
    s.close()


def close_all():
    for sid in list(_sessions):
        try:
            close(sid)
        except TerminalError:
            pass


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78]")


def strip_ansi(text):
    return ANSI.sub("", text)
