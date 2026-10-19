"""Private agents and models: a password is needed to use, view or change them.

The password is stored only as a scrypt hash. Unlocking gives a short-lived token (kept in memory, so a server
restart locks everything again). What this protects: the agentlab API and UI. What it does not: the files on
disk are not encrypted, so anyone with access to this machine's .agentlab folder can still read them.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from .hooks import data_dir

TTL = 30 * 60
MIN_LEN = 8
_tokens, _fails = {}, {}
_lock = threading.Lock()


class PrivacyError(ValueError):
    def __init__(self, message, code=403):
        super().__init__(message)
        self.code = code


def _path(root):
    return data_dir(root) / "private.json"


def _load(root):
    p = _path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(root, d):
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    os.chmod(p, 0o600)


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex()


def is_private(root, agent_id):
    return agent_id in _load(root)


def private_ids(root):
    return set(_load(root))


def set_password(root, agent_id, password, current=None):
    """Make an agent private, or change its password (the current one is needed to change it)."""
    if len(str(password or "")) < MIN_LEN:
        raise PrivacyError(f"the password needs at least {MIN_LEN} characters", 400)
    d = _load(root)
    if agent_id in d:
        _check(root, agent_id, current)
    salt = secrets.token_hex(16)
    d[agent_id] = {"salt": salt, "hash": _hash(password, salt), "set": time.time()}
    _save(root, d)
    lock(agent_id)


def remove_password(root, agent_id, password):
    _check(root, agent_id, password)
    d = _load(root)
    d.pop(agent_id, None)
    _save(root, d)
    lock(agent_id)


def _check(root, agent_id, password):
    rec = _load(root).get(agent_id)
    if not rec:
        raise PrivacyError("this agent is not private", 400)
    with _lock:
        f = _fails.get(agent_id, {"n": 0, "until": 0})
        if time.time() < f["until"]:
            raise PrivacyError(f"too many wrong passwords; try again in {int(f['until'] - time.time()) + 1} s", 429)
    if not hmac.compare_digest(_hash(str(password or ""), rec["salt"]), rec["hash"]):
        with _lock:
            f["n"] += 1
            if f["n"] >= 5:
                f.update(until=time.time() + min(300, 10 * 2 ** (f["n"] - 5)))
            _fails[agent_id] = f
        raise PrivacyError("wrong password")
    with _lock:
        _fails.pop(agent_id, None)


def unlock(root, agent_id, password):
    """-> {token, expires}. Send the token as X-Agent-Unlock to use the agent."""
    _check(root, agent_id, password)
    tok = secrets.token_urlsafe(24)
    with _lock:
        _tokens[tok] = {"agent": agent_id, "expires": time.time() + TTL}
    return {"token": tok, "expires": _tokens[tok]["expires"]}


def lock(agent_id=None, token=None):
    with _lock:
        for t in [t for t, v in _tokens.items() if t == token or (agent_id and v["agent"] == agent_id)]:
            _tokens.pop(t, None)


def unlocked(root, agent_id, tokens):
    """True if the agent is public, or one of `tokens` unlocks it."""
    if not is_private(root, agent_id):
        return True
    now = time.time()
    with _lock:
        return any((v := _tokens.get(t)) and v["agent"] == agent_id and v["expires"] > now for t in tokens or [])


def require(root, agent_id, tokens):
    if not unlocked(root, agent_id, tokens):
        raise PrivacyError("this agent is private; unlock it with its password first")


def redact_agent(agent):
    """What a locked private agent shows: its name and that it is private, nothing else."""
    return {"id": agent["id"], "name": agent["name"], "private": True, "locked": True, "role": "", "provider": {"type": "hidden", "model": "hidden"}}
