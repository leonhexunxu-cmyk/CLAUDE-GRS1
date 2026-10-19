"""Packager and installer, with compatibility and optimisation checks.

check()    compatibility: which Python versions can parse the code (3.8-3.13), imports that are not declared as
           dependencies, platform-only modules, missing pieces (README, LICENSE, tests, .gitignore), Node engines;
           optimisation: big files and images, unminified bundles, duplicates, caches and secrets that should not ship.
package()  a clean .zip in dist/ (junk, caches and secret-looking files left out), plus install.sh and install.ps1
           that set the project up on another machine, and a requirements.txt when one is missing.
"""

import ast
import hashlib
import io
import re
import time
import warnings
import zipfile

from . import analyze, projects

PY_VERSIONS = ((3, 8), (3, 9), (3, 10), (3, 11), (3, 12), (3, 13))
SECRET_FILE = re.compile(r"(^|/)(\.env(\..*)?|id_rsa|id_ed25519|.*\.pem|.*\.key|secrets?\.json|credentials(\.json)?)$", re.I)
JUNK = re.compile(r"(^|/)(\.DS_Store|Thumbs\.db|.*\.pyc|.*\.log|.*~)$")
IMG = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


class PackError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _issue(level, area, text, path=None, fix=None):
    return {"level": level, "area": area, "text": text, "path": path, "fix": fix}


def python_versions(sources):
    """-> {version: [files that fail to parse]} for every tested version."""
    out = {}
    for v in PY_VERSIONS:
        bad = []
        for path, src in sources:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ast.parse(src, feature_version=v)
            except SyntaxError:
                bad.append(path)
        out[f"{v[0]}.{v[1]}"] = bad
    return out


def check(root, pid):
    info = analyze.project_info(root, pid)
    fs = analyze.files(root, pid)
    ws = projects.workspace(root, pid)
    comp, opt = [], []
    py = [(f["path"], s) for f in fs if f["path"].endswith(".py") and (s := analyze.read(f)) is not None]
    versions = python_versions(py) if py else {}
    ok_versions = [v for v, bad in versions.items() if not bad]
    if py:
        if not ok_versions:
            comp.append(_issue("error", "python", "Some files do not parse on any tested Python version (a syntax error).", (versions["3.13"] or [None])[0]))
        elif ok_versions[0] != "3.8":
            first_bad = versions[f"{PY_VERSIONS[0][0]}.{PY_VERSIONS[0][1]}"]
            comp.append(_issue("info", "python", f"Needs Python {ok_versions[0]} or newer (newer syntax, e.g. in {first_bad[0] if first_bad else 'the code'}).", fix=f"declare requires-python = \">={ok_versions[0]}\""))
    declared = set(info["declared"])
    for mod in info["third_party_imports"]:
        name = analyze.pip_name(mod)
        if name not in declared and mod.lower() not in declared:
            comp.append(_issue("warn", "dependencies", f"'{mod}' is imported but not declared as a dependency.", info["imports_by_module"][mod][0], f"add {name} to requirements.txt"))
    used = {analyze.pip_name(m) for m in info["third_party_imports"]} | {m.lower() for m in info["third_party_imports"]}
    for d in sorted(declared - used):
        if info["kind"].startswith("python"):
            opt.append(_issue("info", "dependencies", f"'{d}' is declared but never imported (unused, or used only by a plugin/CLI).", fix="remove it if it is not needed"))
    for mod, plat in analyze.PLATFORM_ONLY.items():
        if mod in info["imports_by_module"]:
            comp.append(_issue("warn", "platform", f"'{mod}' only exists on {plat}: the project will not run elsewhere unless the import is guarded.", info["imports_by_module"][mod][0]))
    engines = (info["package_json"] or {}).get("engines", {}).get("node")
    if info["kind"] == "node" and not engines:
        comp.append(_issue("info", "node", "package.json has no engines.node field, so the supported Node version is unknown.", "package.json", 'add "engines": {"node": ">=18"}'))
    for k, why in (("README.md", "people need to know what it is and how to install it"), ("LICENSE", "others cannot legally reuse it"), (".gitignore", "caches and secrets may get committed")):
        if not info["has"][k]:
            comp.append(_issue("info", "project", f"No {k}: {why}.", fix="the doc builder can write a README" if k == "README.md" else None))
    if not info["has"]["tests"] and info["kind"] in ("python", "python-package", "node"):
        comp.append(_issue("info", "project", "No tests found, so nothing checks that the package works after install."))
    hashes = {}
    for f in fs:
        p, size = f["path"], f["size"]
        if SECRET_FILE.search(p):
            opt.append(_issue("error", "security", "Looks like a secret (keys, .env). It is left out of packages; never ship or commit it.", p))
        elif JUNK.search(p):
            opt.append(_issue("info", "size", "Junk file (cache, log, editor backup) is left out of packages.", p))
        if size > 5_000_000:
            opt.append(_issue("warn", "size", f"Large file ({size / 1e6:.1f} MB). Consider Git LFS, a download step or removing it.", p))
        if p.lower().endswith(IMG) and size > 500_000:
            opt.append(_issue("warn", "size", f"Image is {size / 1e3:.0f} kB; compress it or use WebP/AVIF for the web.", p))
        if p.endswith((".js", ".css")) and size > 100_000 and not re.search(r"\.min\.(js|css)$", p):
            src = analyze.read(f) or ""
            lines = src.count("\n") + 1
            if len(src) / lines < 200:
                opt.append(_issue("info", "size", f"{size / 1e3:.0f} kB of unminified {p.rsplit('.', 1)[1].upper()}; minify it for production.", p))
        if size and size < 50_000_000:
            h = hashlib.sha256(f["abs"].read_bytes()).hexdigest()
            if h in hashes and size > 1000:
                opt.append(_issue("info", "size", f"Duplicate of {hashes[h]}.", p))
            hashes.setdefault(h, p)
    for d in ("node_modules", "__pycache__", ".venv", "venv"):
        if (ws / d).exists():
            opt.append(_issue("info", "size", f"{d}/ is in the project; it is left out of packages and should be in .gitignore.", d))
    score = max(0, 100 - sum({"error": 25, "warn": 8, "info": 2}[i["level"]] for i in comp + opt))
    return {"project": info["name"], "kind": info["kind"], "python": {"tested": versions, "works_on": ok_versions}, "compatibility": comp, "optimisation": opt, "score": score,
            "summary": {"files": info["files"], "bytes": info["bytes"], "errors": sum(i["level"] == "error" for i in comp + opt), "warnings": sum(i["level"] == "warn" for i in comp + opt)}}


INSTALL_SH = """#!/usr/bin/env sh
# Installer for {name}, generated by agentlab. Run: sh install.sh
set -eu
cd "$(dirname "$0")"
{body}
echo "{name} is installed."
"""
INSTALL_PS1 = """# Installer for {name}, generated by agentlab. Run: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
{body}
Write-Host "{name} is installed."
"""


def _installers(info, reqs):
    k = info["kind"]
    if k in ("python", "python-package"):
        minv = "3.8"
        sh = ("command -v python3 >/dev/null || { echo 'Python 3 is required'; exit 1; }\npython3 -m venv .venv\n. .venv/bin/activate\npython -m pip install --upgrade pip\n" +
              ("pip install .\n" if k == "python-package" else ("pip install -r requirements.txt\n" if reqs else "")))
        ps = ("python -m venv .venv\n.\\.venv\\Scripts\\Activate.ps1\npython -m pip install --upgrade pip\n" + ("pip install .\n" if k == "python-package" else ("pip install -r requirements.txt\n" if reqs else "")))
        return sh, ps
    if k == "node":
        return "command -v npm >/dev/null || { echo 'Node.js is required'; exit 1; }\nnpm ci || npm install\n", "npm ci; if ($LASTEXITCODE) { npm install }\n"
    if k == "docker":
        return "docker compose up -d --build\n", "docker compose up -d --build\n"
    if k == "static-site":
        return 'echo "Open index.html, or run: python3 -m http.server"\n', 'Write-Host "Open index.html, or run: python -m http.server"\n'
    return 'echo "Nothing to install."\n', 'Write-Host "Nothing to install."\n'


def package(root, pid, version="0.1.0", include_installers=True):
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-]?[\w.]+)?", str(version)):
        raise PackError("version must look like 1.2.3")
    info = analyze.project_info(root, pid)
    report = check(root, pid)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", info["name"]).strip("-").lower() or "package"
    added = {}
    reqs = None
    if info["kind"] == "python" and not info["has"]["requirements.txt"] and info["third_party_imports"]:
        reqs = "\n".join(sorted({analyze.pip_name(m) for m in info["third_party_imports"]})) + "\n"
        added["requirements.txt"] = reqs
    reqs = reqs or (info["has"]["requirements.txt"] and "yes")
    if include_installers:
        sh, ps = _installers(info, reqs)
        added["install.sh"] = INSTALL_SH.format(name=info["name"], body=sh.rstrip())
        added["install.ps1"] = INSTALL_PS1.format(name=info["name"], body=ps.rstrip())
    buf, skipped, count = io.BytesIO(), [], 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in analyze.files(root, pid):
            p = f["path"]
            if p.startswith("dist/") or SECRET_FILE.search(p) or JUNK.search(p):
                skipped.append(p)
                continue
            if p in added:
                continue
            z.write(f["abs"], f"{safe}-{version}/{p}")
            count += 1
        for p, content in added.items():
            info_z = zipfile.ZipInfo(f"{safe}-{version}/{p}", time.localtime()[:6])
            info_z.external_attr = (0o755 if p.endswith(".sh") else 0o644) << 16
            z.writestr(info_z, content)
            count += 1
    data = buf.getvalue()
    free = projects.usage(root, pid)["free_bytes"]
    if len(data) > free:
        raise PackError("the package does not fit in the project's remaining space")
    out = projects.resolve(root, pid, f"dist/{safe}-{version}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"path": f"dist/{safe}-{version}.zip", "bytes": len(data), "files": count, "skipped": skipped, "added": sorted(added), "check": {"score": report["score"], **report["summary"]}}
