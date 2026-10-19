"""API keys for the local server. Only hashes are stored; the key is shown once at creation."""

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from .hooks import data_dir


def _path(project):
    return data_dir(project) / "keys.json"


def _load(project):
    p = _path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _save(project, keys):
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    os.chmod(p, 0o600)


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def public(rec):
    return {k: rec[k] for k in ("id", "label", "prefix", "created")}


def list_keys(project):
    return [public(k) for k in _load(project)]


def any_keys(project):
    return bool(_load(project))


def create(project, label):
    key = "alab_" + secrets.token_urlsafe(24)
    rec = {"id": uuid.uuid4().hex[:8], "label": (label or "untitled")[:60], "prefix": key[:9],
           "created": time.time(), "hash": _hash(key)}
    _save(project, _load(project) + [rec])
    return public(rec), key


def revoke(project, key_id):
    keys = _load(project)
    kept = [k for k in keys if k["id"] != key_id]
    _save(project, kept)
    return len(kept) != len(keys)


def verify(project, key):
    h = _hash(key or "")
    return any(hmac.compare_digest(h, k["hash"]) for k in _load(project))
