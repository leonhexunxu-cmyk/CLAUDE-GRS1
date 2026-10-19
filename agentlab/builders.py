"""Builders: start a website, game, model or infrastructure project from a template, then let an agent build on it.

Templates are complete and work offline as they are (open index.html, or deploy them). "Build with AI" starts a
job in the new project with your description, so an agent customises the template for you.
"""

import json
from pathlib import Path

from . import projects

TEMPLATE_DIR = Path(__file__).parent / "templates"
KIND_OF = {"website": "website", "game": "game", "model": "model", "infra": "infra"}


def _load():
    return json.loads((TEMPLATE_DIR / "catalog.json").read_text(encoding="utf-8"))


def catalog(kind=None):
    """-> [{id, kind, name, description, files: [paths], tags}] (file contents are not included)."""
    out = []
    for tid, t in _load().items():
        if kind in (None, t["kind"]):
            out.append({"id": tid, "kind": t["kind"], "name": t["name"], "description": t["description"], "tags": t.get("tags", []), "files": sorted(t["files"]), "deploy": t.get("deploy", "static")})
    return out


def preview(tid):
    t = _load().get(tid)
    if not t:
        raise projects.ProjectError("template not found", 404)
    return {"id": tid, **{k: t[k] for k in ("kind", "name", "description")}, "files": t["files"]}


def create(root, tid, name=None, quota_mb=200, runtime=None, agent_id=None, prompt=None):
    """New project from a template. With runtime + agent_id + prompt, an agent then customises it."""
    t = _load().get(tid)
    if not t:
        raise projects.ProjectError("template not found", 404)
    p = projects.create(root, name or t["name"], t["description"], quota_mb, KIND_OF[t["kind"]])
    for rel, content in t["files"].items():
        projects.write_file(root, p["id"], rel, content)
    projects.update(root, p["id"], template=tid)
    job = None
    if runtime and agent_id and prompt:
        brief = (f"This project was created from the '{t['name']}' template ({t['description']}). Files: {', '.join(sorted(t['files']))}.\n"
                 f"Customise it for this request, keeping it working and self-contained (no external downloads unless asked):\n\n{prompt}")
        job = runtime.start(p["id"], agent_id, brief)
    return {"project": projects.get(root, p["id"]), "job": job}
