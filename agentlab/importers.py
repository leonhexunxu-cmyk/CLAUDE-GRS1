"""Import code and data into a project: GitHub repositories (git clone) and Hugging Face models/datasets (file download).

Imports run in the background with a progress bar, respect the project's space quota, never put tokens on command
lines, verify LFS checksums, and strip credentials when a download redirects to another host.
"""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from . import accounts, projects, vault
from .hooks import data_dir

GH_SLUG = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})$")
GH_URL = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?(?:/tree/([A-Za-z0-9._/-]{1,100}))?/?$")
HF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")
REV = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
DEFAULT_PATTERNS = {"models": ["*.json", "*.md", "*.txt", "tokenizer*", "*.model"], "datasets": ["*.json", "*.jsonl", "*.md", "*.txt", "*.csv"]}
DEFAULT_MAX_BYTES = 1_000_000_000
FINAL = ("succeeded", "failed", "cancelled")
ALLOW_FILE_URLS = False  # tests only


class ImportFailed(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def parse_github(text):
    """'owner/repo', a github.com URL or a /tree/<branch> URL -> (owner, repo, ref or None)."""
    text = str(text or "").strip()
    m = GH_SLUG.match(text)
    if m:
        return m.group(1), m.group(2).removesuffix(".git"), None
    m = GH_URL.match(text)
    if not m:
        raise ImportFailed("use owner/repo or a https://github.com/owner/repo URL")
    return m.group(1), m.group(2), m.group(3)


class _StripAuthOnCrossHost(urllib.request.HTTPRedirectHandler):
    """Hugging Face redirects downloads to a CDN; never send the token to a different host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.request.urlparse(newurl).netloc != urllib.request.urlparse(req.full_url).netloc:
            new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
        return new


def _open(url, token=None, timeout=30):
    headers = {"User-Agent": "agentlab"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.build_opener(_StripAuthOnCrossHost).open(urllib.request.Request(url, headers=headers), timeout=timeout)


def hf_tree(root, repo_type, repo_id, revision="main"):
    """Files in a Hugging Face repo: [{path, size, sha256 or None}]."""
    if repo_type not in ("models", "datasets") or not HF_ID.match(repo_id or "") or not REV.match(revision or "") or ".." in revision:
        raise ImportFailed("invalid repository or revision")
    token = vault.get_secret(root, "hf_token")
    url = f"{accounts.HF_API}/api/{repo_type}/{repo_id}/tree/{quote(revision, safe='/')}?recursive=true"
    try:
        with _open(url, token) as r:
            items = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ImportFailed({401: "this repository needs a token with access: connect Hugging Face first", 403: "access denied (gated or private): accept the licence on the website and connect a token",
                            404: "repository or revision not found"}.get(e.code, f"Hugging Face returned HTTP {e.code}"), e.code) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise ImportFailed(f"could not reach Hugging Face: {getattr(e, 'reason', e)}") from None
    return [{"path": i["path"], "size": i.get("size", 0), "sha256": (i.get("lfs") or {}).get("oid")} for i in items if i.get("type") == "file"]


def _safe_rel(path):
    if not path or path.startswith("/") or "\x00" in path or any(p in ("..", "") for p in path.split("/")):
        raise ImportFailed(f"unsafe file path in repository: {path!r}")
    return path


class Importer:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.dir = data_dir(self.root) / "imports"
        self._cancel = {}
        self._lock = threading.Lock()
        for r in self.list():
            if r["status"] in ("queued", "running"):
                r.update(status="failed", error="server stopped during the import", ended=time.time())
                self._save(r)

    # ---- storage ----
    def _save(self, rec):
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{rec['id']}.json.tmp"
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            tmp.replace(self.dir / f"{rec['id']}.json")

    def get(self, import_id):
        p = self.dir / f"{import_id}.json"
        if not import_id.isalnum() or not p.exists():
            raise ImportFailed("import not found", 404)
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self, project_id=None):
        out = [json.loads(p.read_text(encoding="utf-8")) for p in self.dir.glob("*.json")] if self.dir.exists() else []
        return sorted((r for r in out if project_id in (None, r["project"])), key=lambda r: -r["created"])

    def cancel(self, import_id):
        rec = self.get(import_id)
        if rec["status"] in FINAL:
            raise ImportFailed("import already finished")
        self._cancel.setdefault(import_id, threading.Event()).set()
        return rec

    def _new(self, kind, project_id, source, dest):
        projects.get(self.root, project_id)
        rec = {"id": uuid.uuid4().hex[:10], "kind": kind, "project": project_id, "source": source, "dest": dest, "status": "queued", "created": time.time(), "ended": None,
               "error": None, "progress": {"done": 0, "total": 0, "files_done": 0, "files_total": 0, "current": ""}}
        self._save(rec)
        self._cancel[rec["id"]] = threading.Event()
        return rec

    # ---- GitHub ----
    def start_github(self, project_id, repo, ref=None, dest=None, clone_url=None):
        owner, name, url_ref = parse_github(repo)
        ref = ref or url_ref
        if ref and (not REV.match(ref) or ".." in ref or ref.startswith("-")):
            raise ImportFailed("invalid branch or tag")
        dest = (dest or name).strip("/")
        projects.resolve(self.root, project_id, dest)
        if (projects.workspace(self.root, project_id) / dest).exists():
            raise ImportFailed(f"'{dest}' already exists in the project")
        url = clone_url if (clone_url and ALLOW_FILE_URLS) else f"https://github.com/{owner}/{name}.git"
        rec = self._new("github", project_id, f"{owner}/{name}" + (f"@{ref}" if ref else ""), dest)
        threading.Thread(target=self._run_clone, args=(rec, url, ref), daemon=True).start()
        return rec

    def _run_clone(self, rec, url, ref):
        cancel = self._cancel[rec["id"]]
        ws = projects.workspace(self.root, rec["project"])
        target = ws / rec["dest"]
        askpass = None
        try:
            rec["status"] = "running"
            self._save(rec)
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
            token = vault.get_secret(self.root, "github_token")
            if token:  # via GIT_ASKPASS so the token never appears on a command line or in .git/config
                fd, askpass = tempfile.mkstemp(prefix="agentlab-askpass-")
                with os.fdopen(fd, "w") as f:
                    f.write("#!/bin/sh\ncase \"$1\" in *sername*) echo x-access-token;; *) echo \"$AGENTLAB_GIT_TOKEN\";; esac\n")
                os.chmod(askpass, stat.S_IRWXU)
                env.update(GIT_ASKPASS=askpass, AGENTLAB_GIT_TOKEN=token)
            argv = ["git", "clone", "--depth", "1", "--single-branch"] + (["--branch", ref] if ref else []) + ["--", url, str(target)]
            proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            out = b""
            while proc.poll() is None:
                if cancel.is_set():
                    proc.kill()
                    break
                time.sleep(0.1)
            out = proc.stdout.read()
            if cancel.is_set():
                rec["status"] = "cancelled"
            elif proc.returncode != 0:
                msg = vault.redact_value(out.decode("utf-8", "replace").strip().splitlines()[-1] if out.strip() else "git clone failed", self.root)
                raise ImportFailed(msg[:300])
            else:
                u = projects.usage(self.root, rec["project"])
                rec["progress"].update(done=u["used_bytes"], total=u["used_bytes"], files_done=u["files"], files_total=u["files"])
                if u["used_bytes"] > u["quota_bytes"]:
                    raise ImportFailed("the repository does not fit in this project's space quota; raise the quota and try again")
                rec["status"] = "succeeded"
        except ImportFailed as e:
            rec.update(status="failed", error=str(e))
        except Exception as e:
            rec.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
        finally:
            if askpass:
                os.unlink(askpass)
            if rec["status"] != "succeeded":
                shutil.rmtree(target, ignore_errors=True)
            rec["ended"] = time.time()
            self._save(rec)

    # ---- Hugging Face ----
    def start_hf(self, project_id, repo_type, repo_id, revision="main", files=None, patterns=None, dest=None, max_bytes=None):
        tree = hf_tree(self.root, repo_type, repo_id, revision)
        by_path = {f["path"]: f for f in tree}
        if files:
            missing = [f for f in files if f not in by_path]
            if missing:
                raise ImportFailed(f"not in the repository: {missing[:3]}")
            chosen = [by_path[f] for f in files]
        else:
            pats = patterns or DEFAULT_PATTERNS[repo_type]
            chosen = [f for f in tree if any(fnmatch.fnmatch(f["path"], p) for p in pats)]
        if not chosen:
            raise ImportFailed("no files matched; pick files or patterns")
        for f in chosen:
            _safe_rel(f["path"])
        total = sum(f["size"] for f in chosen)
        cap = int(max_bytes or DEFAULT_MAX_BYTES)
        if total > cap:
            raise ImportFailed(f"{total} bytes selected, which is over the {cap} byte limit; choose fewer files or raise max_bytes")
        free = projects.usage(self.root, project_id)["free_bytes"]
        if total > free:
            raise ImportFailed(f"{total} bytes selected but the project has only {free} bytes free; raise its quota", 413)
        dest = (dest or repo_id.split("/")[-1]).strip("/")
        projects.resolve(self.root, project_id, dest)
        rec = self._new("huggingface", project_id, f"{repo_type}/{repo_id}@{revision}", dest)
        rec["progress"].update(total=total, files_total=len(chosen))
        self._save(rec)
        threading.Thread(target=self._run_hf, args=(rec, repo_type, repo_id, revision, chosen), daemon=True).start()
        return rec

    def _run_hf(self, rec, repo_type, repo_id, revision, files):
        cancel = self._cancel[rec["id"]]
        token = vault.get_secret(self.root, "hf_token")
        prefix = "datasets/" if repo_type == "datasets" else ""
        last_save = 0.0
        try:
            rec["status"] = "running"
            self._save(rec)
            for f in files:
                rel = f"{rec['dest']}/{f['path']}"
                target = projects.resolve(self.root, rec["project"], rel)
                if projects.usage(self.root, rec["project"])["free_bytes"] < f["size"]:
                    raise ImportFailed("the project ran out of space; raise its quota", 413)
                target.parent.mkdir(parents=True, exist_ok=True)
                projects.resolve(self.root, rec["project"], str(Path(rel).parent))
                rec["progress"]["current"] = f["path"]
                url = f"{accounts.HF_API}/{prefix}{repo_id}/resolve/{quote(revision, safe='/')}/{quote(f['path'])}"
                tmp = target.with_name(target.name + ".part")
                sha = hashlib.sha256()
                try:
                    with _open(url, token) as r, open(tmp, "wb") as out:
                        read = getattr(r, "read1", r.read)  # return what has arrived, so progress and Cancel stay responsive on slow links
                        for chunk in iter(lambda: read(1 << 16), b""):
                            if cancel.is_set():
                                raise InterruptedError
                            out.write(chunk)
                            sha.update(chunk)
                            rec["progress"]["done"] += len(chunk)
                            if time.time() - last_save > 0.3:
                                last_save = time.time()
                                self._save(rec)
                except urllib.error.HTTPError as e:
                    raise ImportFailed(f"{f['path']}: HTTP {e.code}" + (" (needs a token with access)" if e.code in (401, 403) else "")) from None
                if f["sha256"] and sha.hexdigest() != f["sha256"]:
                    raise ImportFailed(f"{f['path']}: checksum mismatch, the download was corrupted")
                tmp.replace(target)
                rec["progress"]["files_done"] += 1
            rec["status"] = "succeeded"
            rec["progress"]["current"] = ""
        except InterruptedError:
            rec["status"] = "cancelled"
        except ImportFailed as e:
            rec.update(status="failed", error=str(e))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            rec.update(status="failed", error=f"download failed: {getattr(e, 'reason', e)}"[:300])
        finally:
            if rec["status"] != "succeeded":
                for part in (projects.workspace(self.root, rec["project"]) / rec["dest"]).rglob("*.part"):
                    part.unlink(missing_ok=True)
            rec["ended"] = time.time()
            self._save(rec)
