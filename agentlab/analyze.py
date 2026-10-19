"""Project analysis shared by the doc builder and the packager: what kind of project this is and what it uses."""

import ast
import json
import re
import sys

from . import projects

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".training", "dist", "build", ".mypy_cache", ".pytest_cache"}
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
IMPORT_TO_PIP = {"cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn", "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
                 "dateutil": "python-dateutil", "jwt": "pyjwt", "Crypto": "pycryptodome", "skimage": "scikit-image", "attr": "attrs", "google.protobuf": "protobuf"}
PLATFORM_ONLY = {"winreg": "Windows", "msvcrt": "Windows", "_winapi": "Windows", "fcntl": "Unix", "termios": "Unix", "pty": "Unix", "grp": "Unix", "pwd": "Unix", "resource": "Unix"}


def files(root, pid, limit=5000):
    ws = projects.workspace(root, pid)
    out = []
    for p in sorted(ws.rglob("*")):
        rel = p.relative_to(ws)
        if any(part in SKIP_DIRS for part in rel.parts) or p.is_symlink() or not p.is_file():
            continue
        out.append({"path": rel.as_posix(), "size": p.stat().st_size, "abs": p})
        if len(out) >= limit:
            break
    return out


def read(f, max_bytes=2_000_000):
    try:
        return f["abs"].read_bytes()[:max_bytes].decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _requirements(text):
    out = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            out.add(re.split(r"[<>=!~\[; ]", line, 1)[0].lower().replace("_", "-"))
    return out


def project_info(root, pid):
    fs = files(root, pid)
    paths = {f["path"] for f in fs}
    by = {f["path"]: f for f in fs}
    langs = {}
    for f in fs:
        ext = f["path"].rsplit(".", 1)[-1].lower() if "." in f["path"] else ""
        langs[ext] = langs.get(ext, 0) + 1
    kind = ("python-package" if "pyproject.toml" in paths or "setup.py" in paths else "node" if "package.json" in paths else
            "worker" if "wrangler.toml" in paths else "docker" if "Dockerfile" in paths else "static-site" if "index.html" in paths else
            "python" if langs.get("py") else "other")
    imports, declared, defs, entry = {}, set(), [], []
    for f in fs:
        if f["path"].endswith(".py"):
            src = read(f)
            if src is None:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        imports.setdefault(a.name.split(".")[0], set()).add(f["path"])
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    imports.setdefault(n.module.split(".")[0], set()).add(f["path"])
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                    defs.append({"file": f["path"], "name": n.name, "kind": "class" if isinstance(n, ast.ClassDef) else "function", "doc": (ast.get_docstring(n) or "").split("\n")[0][:200],
                                 "args": [a.arg for a in n.args.args] if not isinstance(n, ast.ClassDef) else []})
            if "__main__" in src:
                entry.append(f["path"])
    if "requirements.txt" in paths:
        declared |= _requirements(read(by["requirements.txt"]) or "")
    if "pyproject.toml" in paths:
        txt = read(by["pyproject.toml"]) or ""
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", txt, re.S)
        if m:
            declared |= _requirements("\n".join(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))))
    local = {p.split("/")[0].removesuffix(".py") for p in paths if p.endswith(".py")} | {p.split("/")[0] for p in paths if "/" in p}
    third = sorted(m for m in imports if m not in STDLIB and m not in local)
    pkg = {}
    if "package.json" in paths:
        try:
            pkg = json.loads(read(by["package.json"]) or "{}")
        except ValueError:
            pkg = {}
    name = None
    if "pyproject.toml" in paths:
        m = re.search(r'(?m)^name\s*=\s*["\']([^"\']+)', read(by["pyproject.toml"]) or "")
        name = m.group(1) if m else None
    name = name or pkg.get("name") or projects.get(root, pid)["name"]
    return {"kind": kind, "name": name, "files": len(fs), "bytes": sum(f["size"] for f in fs), "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])[:8]),
            "third_party_imports": third, "imports_by_module": {k: sorted(v) for k, v in imports.items()}, "declared": sorted(declared), "public": defs[:300],
            "entry_points": entry, "package_json": pkg, "has": {k: k in paths for k in ("README.md", "LICENSE", "requirements.txt", "pyproject.toml", "package.json", "Dockerfile", "tests", ".gitignore")} | {"tests": any(p.startswith("tests/") or "/test_" in p or p.startswith("test_") for p in paths)}}


def pip_name(module):
    return IMPORT_TO_PIP.get(module, module).lower().replace("_", "-")
