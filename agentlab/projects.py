"""Projects: a named workspace with a space quota. Agents work inside it; nothing outside is reachable.

Layout: .agentlab/projects/<id>/{project.json, workspace/, home/}
"""

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .hooks import data_dir

KINDS = ("blank", "website", "game", "model", "infra", "import")
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_READ, MAX_WRITE = 1_000_000, 5_000_000
DEFAULT_QUOTA_MB, MAX_QUOTA_MB = 500, 50_000


class ProjectError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _root(project):
    return data_dir(project) / "projects"


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "project"
    return s


def create(project, name, description="", quota_mb=DEFAULT_QUOTA_MB, kind="blank"):
    name = str(name or "").strip()
    if not name or len(name) > 80:
        raise ProjectError("name is required (max 80 characters)")
    if kind not in KINDS:
        raise ProjectError(f"kind must be one of {list(KINDS)}")
    try:
        quota_mb = int(quota_mb)
    except (TypeError, ValueError):
        raise ProjectError("quota_mb must be a number")
    if not 1 <= quota_mb <= MAX_QUOTA_MB:
        raise ProjectError(f"quota_mb must be between 1 and {MAX_QUOTA_MB}")
    pid = f"{_slug(name)}-{uuid.uuid4().hex[:4]}"
    rec = {"id": pid, "name": name, "description": str(description)[:500], "quota_mb": quota_mb, "kind": kind, "created": time.time(), "agent": None}
    d = _root(project) / pid
    (d / "workspace").mkdir(parents=True)
    (d / "project.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _dir(project, pid):
    if not PROJECT_ID.match(pid or ""):
        raise ProjectError("invalid project id")
    d = _root(project) / pid
    if not (d / "project.json").exists():
        raise ProjectError("project not found", 404)
    return d


def get(project, pid):
    return json.loads((_dir(project, pid) / "project.json").read_text(encoding="utf-8"))


def update(project, pid, **fields):
    rec = get(project, pid)
    if "name" in fields and fields["name"]:
        rec["name"] = str(fields["name"]).strip()[:80]
    if "description" in fields:
        rec["description"] = str(fields["description"])[:500]
    if "agent" in fields:
        rec["agent"] = fields["agent"]
    if fields.get("quota_mb") is not None:
        q = int(fields["quota_mb"])
        if not 1 <= q <= MAX_QUOTA_MB:
            raise ProjectError(f"quota_mb must be between 1 and {MAX_QUOTA_MB}")
        rec["quota_mb"] = q
    (_dir(project, pid) / "project.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def workspace(project, pid):
    return _dir(project, pid) / "workspace"


def home(project, pid):
    return _dir(project, pid) / "home"


def list_projects(project):
    out = []
    for p in sorted(_root(project).glob("*/project.json")) if _root(project).exists() else []:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            out.append({**rec, **usage(project, rec["id"])})
        except (ValueError, ProjectError):
            pass
    return sorted(out, key=lambda r: r["created"], reverse=True)


def delete(project, pid):
    shutil.rmtree(_dir(project, pid))


def usage(project, pid):
    """Bytes and file count used in the workspace (symlinks are not followed or counted)."""
    ws = workspace(project, pid)
    used = files = 0
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for f in filenames:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                used += os.path.getsize(p)
                files += 1
    quota = get(project, pid)["quota_mb"] * 1_000_000
    return {"used_bytes": used, "files": files, "quota_bytes": quota, "free_bytes": max(0, quota - used)}


def resolve(project, pid, rel):
    """Absolute path inside the workspace for `rel`, or ProjectError. Rejects traversal, absolute paths and symlinks."""
    ws = workspace(project, pid)
    rel = str(rel or "").strip()
    if "\x00" in rel or rel.startswith(("/", "~")) or re.search(r"(^|/)\.\.(/|$)", rel):
        raise ProjectError("path must be relative and stay inside the project")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    cur = ws
    for part in parts:  # every existing component must be a real file/dir, never a symlink
        cur = cur / part
        if cur.is_symlink():
            raise ProjectError("symlinks are not allowed inside a project")
    real = os.path.realpath(cur)
    if real != os.path.realpath(ws) and not real.startswith(os.path.realpath(ws) + os.sep):
        raise ProjectError("path escapes the project")
    return cur


def list_dir(project, pid, rel=""):
    d = resolve(project, pid, rel)
    if not d.is_dir():
        raise ProjectError("not a directory", 404)
    out = []
    for e in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if e.is_symlink():
            continue
        out.append({"name": e.name, "dir": e.is_dir(), "size": 0 if e.is_dir() else e.stat().st_size})
    return out


def tree(project, pid, limit=500):
    """Flat sorted list of relative file paths (for the UI and for agents)."""
    ws = workspace(project, pid)
    out = []
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)) and d not in (".git", "node_modules"))
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            if not os.path.islink(full):
                out.append(os.path.relpath(full, ws).replace(os.sep, "/"))
        if len(out) >= limit:
            break
    return out[:limit]


def read_file(project, pid, rel, max_bytes=MAX_READ):
    p = resolve(project, pid, rel)
    if not p.is_file():
        raise ProjectError("file not found", 404)
    raw = p.read_bytes()[: max_bytes + 1]
    binary = b"\x00" in raw[:4096]
    return {"path": rel, "binary": binary, "truncated": len(raw) > max_bytes, "content": "" if binary else raw[:max_bytes].decode("utf-8", "replace")}


def write_file(project, pid, rel, content, append=False):
    """Write text. Enforces the project's space quota; raises ProjectError when full."""
    data = str(content).encode("utf-8")
    if len(data) > MAX_WRITE:
        raise ProjectError("file content too large for one write (5 MB)")
    p = resolve(project, pid, rel)
    if not [x for x in str(rel).split("/") if x not in ("", ".")]:
        raise ProjectError("a file path is required")
    if p.is_dir():
        raise ProjectError("path is a directory")
    old = 0 if append or not p.exists() else p.stat().st_size
    if len(data) - old > usage(project, pid)["free_bytes"]:
        raise ProjectError("project space quota exceeded", 413)
    p.parent.mkdir(parents=True, exist_ok=True)
    resolve(project, pid, str(Path(rel).parent))  # re-check after creating parents
    with open(p, "ab" if append else "wb") as f:
        f.write(data)
    return {"path": rel, "bytes": len(data)}


def delete_path(project, pid, rel):
    p = resolve(project, pid, rel)
    if not [x for x in str(rel).split("/") if x not in ("", ".")]:
        raise ProjectError("refusing to delete the workspace root")
    if not p.exists():
        raise ProjectError("not found", 404)
    shutil.rmtree(p) if p.is_dir() else p.unlink()
