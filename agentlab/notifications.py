"""Notification feed: things that happened (or need you) with unread state and a link to where to act."""

import json
import threading
import time
import uuid

from .hooks import data_dir

KINDS = ("approval", "job", "import", "train", "proposal", "schedule", "health", "task", "system")
LEVELS = ("info", "success", "warn", "error")
MAX_KEEP = 400
_lock = threading.Lock()


def _path(root):
    return data_dir(root) / "notifications.jsonl"


def _read(root):
    p = _path(root)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _write(root, items):
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def emit(root, kind, title, text="", level="info", link=None, dedupe_key=None):
    """Add a notification. With dedupe_key, an unread one with the same key is refreshed instead of repeated."""
    if kind not in KINDS or level not in LEVELS:
        raise ValueError("unknown kind or level")
    with _lock:
        items = _read(root)
        if dedupe_key:
            for i in items:
                if i.get("key") == dedupe_key and not i["read"]:
                    i.update(ts=time.time(), title=title[:140], text=text[:400])
                    _write(root, items)
                    return i
        n = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "kind": kind, "level": level, "title": str(title)[:140], "text": str(text)[:400], "link": link or {}, "read": False, "key": dedupe_key}
        items.append(n)
        _write(root, items[-MAX_KEEP:])
        return n


def list_notifications(root, unread_only=False, limit=50):
    items = sorted(_read(root), key=lambda i: -i["ts"])
    return [i for i in items if not (unread_only and i["read"])][:limit]


def unread_count(root):
    return sum(1 for i in _read(root) if not i["read"])


def mark_read(root, ids=None):
    with _lock:
        items = _read(root)
        n = 0
        for i in items:
            if not i["read"] and (ids is None or i["id"] in ids):
                i["read"] = True
                n += 1
        _write(root, items)
        return n


def clear(root):
    with _lock:
        _write(root, [])
