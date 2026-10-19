"""File uploads into a project (model weights, datasets, documents), streamed to disk with quota and progress."""

import hashlib
import os
import threading
import time
import uuid

from . import projects

MAX_UPLOAD = 50_000_000_000
_progress = {}
_lock = threading.Lock()


class UploadError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def receive(root, pid, rel, stream, length, upload_id=None, chunk=1 << 20):
    """Write `length` bytes from `stream` to the project file `rel`. Checks the quota first, writes to a temporary
    file and moves it into place only when complete. -> {path, bytes, sha256}."""
    try:
        length = int(length)
    except (TypeError, ValueError):
        raise UploadError("Content-Length is required", 411)
    if not 0 < length <= MAX_UPLOAD:
        raise UploadError("empty or too large upload")
    dest = projects.resolve(root, pid, rel)
    existing = dest.stat().st_size if dest.is_file() else 0
    if length - existing > projects.usage(root, pid)["free_bytes"]:
        raise UploadError(f"not enough space in the project ({length / 1e6:.1f} MB needed); raise its quota", 413)
    uid = upload_id or uuid.uuid4().hex[:10]
    tmp = dest.parent / f".{dest.name}.{uid}.part"
    dest.parent.mkdir(parents=True, exist_ok=True)
    h, got, t0 = hashlib.sha256(), 0, time.time()
    with _lock:
        _progress[uid] = {"id": uid, "path": rel, "bytes": 0, "total": length, "percent": 0, "status": "receiving", "started": t0}
    try:
        with open(tmp, "wb") as f:
            while got < length:
                data = stream.read(min(chunk, length - got))
                if not data:
                    raise UploadError("the upload was interrupted")
                f.write(data)
                h.update(data)
                got += len(data)
                with _lock:
                    _progress[uid].update(bytes=got, percent=round(100 * got / length, 1), rate=round(got / max(time.time() - t0, 1e-3)))
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        with _lock:
            _progress[uid]["status"] = "failed"
        raise
    with _lock:
        _progress[uid].update(status="done", percent=100)
    return {"id": uid, "path": rel, "bytes": got, "sha256": h.hexdigest()}


def progress(uid):
    with _lock:
        p = _progress.get(uid)
    if not p:
        raise UploadError("upload not found", 404)
    return dict(p)
