"""Webhook connectors: tell Slack/Discord/anything when an agent is blocked, a task finishes or something errors.

The payload carries both `text` (Slack-style) and `content` (Discord-style) so common incoming webhooks work as-is.
"""

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

from .hooks import data_dir

EVENTS = ("blocked", "task_finished", "error")
_ID = re.compile(r"^[a-f0-9]{8}$")


def _path(project):
    return data_dir(project) / "connectors.json"


def _load(project):
    p = _path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"webhooks": []}


def public(hook):
    return {"id": hook["id"], "name": hook["name"], "url": hook["url"], "events": hook["events"], "has_secret": bool(hook.get("secret"))}


def list_webhooks(project):
    return [public(h) for h in _load(project)["webhooks"]]


def has_webhooks(project):
    try:
        return bool(_load(project)["webhooks"])
    except (OSError, ValueError):
        return False


def save_webhooks(project, hooks):
    """Replace the list. A hook with an existing id keeps its secret unless a new `secret` key is sent."""
    old = {h["id"]: h for h in _load(project)["webhooks"]}
    out = []
    if not isinstance(hooks, list) or len(hooks) > 10:
        raise ValueError("webhooks must be a list of up to 10 items")
    for h in hooks:
        url = str(h.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 500:
            raise ValueError("each webhook needs an http(s) url")
        events = [e for e in h.get("events", []) if e in EVENTS]
        if not events:
            raise ValueError(f"each webhook needs at least one event of {list(EVENTS)}")
        hid = h.get("id") if _ID.match(str(h.get("id", ""))) else uuid.uuid4().hex[:8]
        secret = h["secret"] if "secret" in h else old.get(hid, {}).get("secret", "")
        out.append({"id": hid, "name": str(h.get("name") or parsed.hostname)[:60], "url": url, "events": events, "secret": str(secret)[:200]})
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"webhooks": out}, indent=2), encoding="utf-8")
    import os
    os.chmod(p, 0o600)
    return [public(h) for h in out]


def _post(hook, event, text, data):
    body = json.dumps({"event": event, "text": text, "content": text, "data": data, "timestamp": time.time()}).encode()
    headers = {"Content-Type": "application/json", "X-AgentLab-Event": event, "User-Agent": "agentlab"}
    if hook.get("secret"):
        headers["X-AgentLab-Signature"] = "sha256=" + hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    try:
        with urllib.request.urlopen(urllib.request.Request(hook["url"], data=body, headers=headers, method="POST"), timeout=3) as r:
            return {"id": hook["id"], "ok": 200 <= r.status < 300, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"id": hook["id"], "ok": False, "status": e.code}
    except Exception as e:
        return {"id": hook["id"], "ok": False, "error": type(e).__name__}


def fire(project, event, text, data=None, only_id=None):
    """Send `event` to every subscribed webhook (or one, for tests). Never raises."""
    try:
        hooks = [h for h in _load(project)["webhooks"] if (h["id"] == only_id if only_id else event in h["events"])]
    except (OSError, ValueError):
        return []
    return [_post(h, event, text, data or {}) for h in hooks]
