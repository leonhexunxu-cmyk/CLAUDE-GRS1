"""Code pads: small saved scratch files kept with the project."""

import json
import re
import time
import uuid

from .hooks import data_dir

LANGUAGES = ("javascript", "typescript", "python", "shell", "json", "text")
PAD_ID = re.compile(r"^[a-f0-9]{8}$")
MAX_CONTENT = 1_000_000


def _dir(project):
    return data_dir(project) / "pads"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def list_pads(project):
    d = _dir(project)
    pads = [_read(p) for p in d.glob("*.json")] if d.exists() else []
    return sorted(({k: p[k] for k in ("id", "name", "language", "updated")} for p in pads),
                  key=lambda p: p["updated"], reverse=True)


def get(project, pad_id):
    p = _dir(project) / f"{pad_id}.json"
    return _read(p) if PAD_ID.match(pad_id or "") and p.exists() else None


def save(project, pad_id, name, language, content):
    """Create (pad_id falsy) or update a pad. Raises ValueError on bad input."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {list(LANGUAGES)}")
    if len(content) > MAX_CONTENT:
        raise ValueError("pad is too large")
    if pad_id and not get(project, pad_id):
        raise ValueError("pad not found")
    pad = {"id": pad_id or uuid.uuid4().hex[:8], "name": (name or "Untitled")[:100],
           "language": language, "content": content, "updated": time.time()}
    _dir(project).mkdir(parents=True, exist_ok=True)
    (_dir(project) / f"{pad['id']}.json").write_text(json.dumps(pad), encoding="utf-8")
    return pad


def delete(project, pad_id):
    p = _dir(project) / f"{pad_id}.json"
    if PAD_ID.match(pad_id or "") and p.exists():
        p.unlink()
        return True
    return False
