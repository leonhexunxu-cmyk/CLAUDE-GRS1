"""Doc builder: README, API reference, user guide, changelog and contributing guide, from templates filled with
what agentlab finds in the project (or from an OpenAPI spec). Writes Markdown and a matching HTML page into docs/.
An agent can then polish the drafts ("Improve with AI" starts a job with the generated files as a brief).
"""

import html
import json
import re
import time

from . import analyze, apigen, projects

TEMPLATES = {
    "readme": "README with overview, install, usage, project layout and license",
    "api": "API reference: from an OpenAPI file in the project, the agentlab API, or the project's public Python functions",
    "guide": "User guide: getting started, tasks, troubleshooting, FAQ",
    "changelog": "CHANGELOG in Keep a Changelog format",
    "contributing": "CONTRIBUTING: setup, tests, style, pull requests",
}


class DocError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _install(info):
    k = info["kind"]
    if k == "python-package":
        return "```bash\npip install .\n```"
    if k == "python":
        reqs = sorted({analyze.pip_name(m) for m in info["third_party_imports"]})
        return "```bash\npython3 -m venv .venv && . .venv/bin/activate\n" + (f"pip install {' '.join(reqs)}\n" if reqs else "") + "```"
    if k == "node":
        return "```bash\nnpm install\n```"
    if k == "docker":
        return "```bash\ndocker compose up --build\n```"
    if k == "worker":
        return "```bash\nnpx wrangler deploy\n```"
    if k == "static-site":
        return "No build step: open `index.html` in a browser, or serve the folder:\n\n```bash\npython3 -m http.server\n```"
    return "Describe how to install it."


def _usage(info):
    if info["entry_points"]:
        return "\n".join(f"```bash\npython3 {e}\n```" for e in info["entry_points"][:3])
    scripts = (info["package_json"] or {}).get("scripts") or {}
    if scripts:
        return "```bash\n" + "\n".join(f"npm run {s}" for s in list(scripts)[:5]) + "\n```"
    if info["public"]:
        f = info["public"][0]
        mod = f["file"].removesuffix(".py").replace("/", ".")
        return f"```python\nfrom {mod} import {f['name']}\n```"
    return "Describe how to use it."


def readme(info, description=""):
    tree = "\n".join(f"- `{k}`: {v} file{'s' if v != 1 else ''}" for k, v in info["languages"].items() if k)
    return f"""# {info['name']}

{description or 'One or two sentences on what this project does and who it is for.'}

## Install

{_install(info)}

## Usage

{_usage(info)}

## Project layout

{info['files']} files ({info['bytes'] / 1000:.0f} kB).

{tree}

## License

{'See LICENSE.' if info['has']['LICENSE'] else 'Add a LICENSE file (MIT, Apache-2.0, ...).'}
"""


def api_from_openapi(spec):
    out = [f"# {spec.get('info', {}).get('title', 'API')} reference", "", spec.get("info", {}).get("description", ""), ""]
    for path, ops in sorted((spec.get("paths") or {}).items()):
        for method, op in ops.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            out += [f"## `{method.upper()} {path}`", "", op.get("summary", ""), ""]
            params = op.get("parameters") or []
            if params:
                out += ["| Parameter | In | Required |", "|---|---|---|"] + [f"| `{p.get('name')}` | {p.get('in')} | {'yes' if p.get('required') else 'no'} |" for p in params] + [""]
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            props = body.get("properties") or {}
            if props:
                req = set(body.get("required") or [])
                out += ["| Field | Type | Required |", "|---|---|---|"] + [f"| `{k}` | {v.get('type', 'any')} | {'yes' if k in req else 'no'} |" for k, v in props.items()] + [""]
    return "\n".join(out)


def api_from_python(info):
    out = [f"# {info['name']} API reference", ""]
    for f in info["public"]:
        sig = f"{f['name']}({', '.join(a for a in f['args'] if a != 'self')})" if f["kind"] == "function" else f"class {f['name']}"
        out += [f"## `{sig}`", f"*{f['file']}*", "", f["doc"] or "No docstring yet.", ""]
    return "\n".join(out) if info["public"] else out[0] + "\n\nNo public functions or classes found."


def guide(info):
    return f"""# {info['name']} user guide

## Getting started

{_install(info)}

Then:

{_usage(info)}

## Common tasks

### First task
Step-by-step instructions.

### Second task
Step-by-step instructions.

## Troubleshooting

| Problem | What to do |
|---|---|
| It does not start | Check the install step and the version requirements. |
| Something else | Describe the fix. |

## FAQ

**Question?**
Answer.
"""


def changelog(info):
    return f"""# Changelog

All notable changes to {info['name']} are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- 

## [0.1.0] - {time.strftime('%Y-%m-%d')}

### Added
- First release.
"""


def contributing(info):
    test = "pytest -q" if info["kind"].startswith("python") else "npm test" if info["kind"] == "node" else "describe how to run the tests"
    return f"""# Contributing to {info['name']}

1. Fork and clone the repository.
2. Set up: see the Install section of the README.
3. Run the tests: `{test}`.
4. Keep changes small and focused, with a test for every fix.
5. Open a pull request that says what changed and why.
"""


def md_to_html(md, title="Docs"):
    """A small Markdown renderer (headings, lists, code, tables, bold/italic/code/links) for the HTML copy."""
    esc = html.escape
    def inline(s):
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + esc("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,6}) (.*)", ln)
        if m:
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
        elif ln.startswith("|") and i + 1 < len(lines) and re.match(r"\|[-| :]+\|", lines[i + 1]):
            head = [c.strip() for c in ln.strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip("|").split("|")])
                j += 1
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table>")
            i = j
            continue
        elif re.match(r"\s*([-*]|\d+\.) ", ln):
            tag = "ol" if re.match(r"\s*\d+\.", ln) else "ul"
            items, j = [], i
            while j < len(lines) and re.match(r"\s*([-*]|\d+\.) ", lines[j]):
                items.append(re.sub(r"\s*([-*]|\d+\.) ", "", lines[j], 1))
                j += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            i = j
            continue
        elif ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    css = ("body{max-width:820px;margin:40px auto;padding:0 20px;font:16px/1.6 -apple-system,system-ui,sans-serif;color:#1d1d1f}"
           "pre{background:#f5f5f7;padding:14px;border-radius:12px;overflow:auto}code{font:13px ui-monospace,Menlo,monospace}"
           "table{border-collapse:collapse}td,th{border-bottom:1px solid #ddd;padding:6px 10px;text-align:left}"
           "@media(prefers-color-scheme:dark){body{background:#000;color:#f5f5f7}pre{background:#1c1c1e}}")
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style>" + "\n".join(out)


def build(root, pid, kinds=None, description="", openapi_path=None, include_agentlab_api=False, write=True):
    """Generate the chosen documents. -> {files: {path: markdown}}."""
    kinds = kinds or ["readme"]
    bad = [k for k in kinds if k not in TEMPLATES]
    if bad:
        raise DocError(f"unknown templates: {bad}; choose from {list(TEMPLATES)}")
    info = analyze.project_info(root, pid)
    out = {}
    for k in kinds:
        if k == "readme":
            out["README.md"] = readme(info, description)
        elif k == "api":
            if openapi_path:
                try:
                    spec = json.loads(projects.read_file(root, pid, openapi_path)["content"])
                except ValueError:
                    raise DocError("the OpenAPI file must be JSON")
                out["docs/API.md"] = api_from_openapi(spec)
            elif include_agentlab_api:
                out["docs/API.md"] = api_from_openapi(apigen.openapi("current"))
            else:
                out["docs/API.md"] = api_from_python(info)
        elif k == "guide":
            out["docs/GUIDE.md"] = guide(info)
        elif k == "changelog":
            out["CHANGELOG.md"] = changelog(info)
        elif k == "contributing":
            out["CONTRIBUTING.md"] = contributing(info)
    if write:
        for path, md in out.items():
            projects.write_file(root, pid, path, md)
            name = path.rsplit("/", 1)[-1].removesuffix(".md").lower()
            projects.write_file(root, pid, f"docs/html/{name}.html", md_to_html(md, f"{info['name']} · {name}"))
    return {"files": out, "info": {k: info[k] for k in ("kind", "name", "files", "languages")}}


def improve_with_ai(root, pid, runtime, agent_id, paths):
    brief = ("Improve these generated documentation drafts using what you can read in the project. Replace every placeholder sentence with real, "
             "accurate content (read the code, don't invent features), keep the structure, and keep the docs/html copies in sync if you change a file.\n\nFiles: " + ", ".join(paths))
    return runtime.start(pid, agent_id, brief)
