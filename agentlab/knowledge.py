"""Knowledge bases: text, files and web pages an agent can search while it works.

Add sources (pasted text, project files, web pages or a small same-site crawl); they are split into chunks and
embedded with the knowledge base's embedding model. Agents linked to a knowledge base get a search_knowledge tool
and the best matching passages for their task. The crawler only fetches public pages, honours robots.txt,
stays on the starting site and waits between requests.
"""

import json
import re
import threading
import time
import urllib.robotparser
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlparse

from . import embeddings, projects
from .hooks import data_dir

CHUNK, OVERLAP = 900, 150
MAX_CHUNKS = 20_000
_lock = threading.RLock()


class KnowledgeError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _dir(root, kid=None):
    d = data_dir(root) / "knowledge"
    if kid is None:
        return d
    if not re.fullmatch(r"[a-f0-9]{8}", kid or ""):
        raise KnowledgeError("knowledge base not found", 404)
    return d / kid


def _meta(root, kid):
    p = _dir(root, kid) / "kb.json"
    if not p.exists():
        raise KnowledgeError("knowledge base not found", 404)
    return json.loads(p.read_text(encoding="utf-8"))


def _write_meta(root, kb):
    (_dir(root, kb["id"]) / "kb.json").write_text(json.dumps(kb), encoding="utf-8")


def create(root, name, embedding=None, description=""):
    name = str(name or "").strip()[:80]
    if not name:
        raise KnowledgeError("a name is required")
    kb = {"id": uuid.uuid4().hex[:8], "name": name, "description": str(description)[:500], "embedding": embeddings.validate(embedding), "sources": [], "chunks": 0, "created": time.time()}
    _dir(root, kb["id"]).mkdir(parents=True)
    _write_meta(root, kb)
    return kb


def get(root, kid):
    return _meta(root, kid)


def list_kbs(root):
    out = []
    for p in _dir(root).glob("*/kb.json") if _dir(root).exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return sorted(out, key=lambda k: -k["created"])


def delete(root, kid):
    import shutil
    _meta(root, kid)
    shutil.rmtree(_dir(root, kid))


def chunk(text, size=CHUNK, overlap=OVERLAP):
    """Split on paragraph boundaries where possible, into chunks of about `size` characters."""
    text = re.sub(r"\n{3,}", "\n\n", str(text)).strip()
    out, cur = [], ""
    for para in re.split(r"\n\n", text):
        while len(para) > size:
            cut = para.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut].strip())
            para = para[max(0, cut - overlap):]
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur)
            cur = cur[-overlap:] + "\n\n" + para if overlap else para
        else:
            cur = (cur + "\n\n" + para) if cur else para
    if cur.strip():
        out.append(cur.strip())
    return [c for c in out if c.strip()]


def add_text(root, kid, text, title="", source=None, project="."):
    kb = _meta(root, kid)
    pieces = chunk(text)
    if not pieces:
        raise KnowledgeError("there is no text to add")
    if kb["chunks"] + len(pieces) > MAX_CHUNKS:
        raise KnowledgeError(f"a knowledge base holds at most {MAX_CHUNKS:,} chunks")
    vecs = embeddings.embed(kb["embedding"], pieces, root)
    sid = uuid.uuid4().hex[:8]
    with _lock:
        with open(_dir(root, kid) / "chunks.jsonl", "a", encoding="utf-8") as f:
            for i, (t, v) in enumerate(zip(pieces, vecs)):
                f.write(json.dumps({"source": sid, "i": i, "text": t, "v": [round(x, 5) for x in v]}) + "\n")
        kb = _meta(root, kid)
        kb["sources"].append({"id": sid, "title": str(title or (source or {}).get("url") or text[:50])[:200], "chunks": len(pieces), "chars": len(text), "added": time.time(), **(source or {"kind": "text"})})
        kb["chunks"] += len(pieces)
        _write_meta(root, kb)
    return kb["sources"][-1]


def add_project_file(root, kid, project_id, rel):
    f = projects.read_file(root, project_id, rel)
    if f["binary"]:
        raise KnowledgeError("only text files can be added")
    return add_text(root, kid, f["content"], rel, {"kind": "file", "project": project_id, "path": rel})


def remove_source(root, kid, sid):
    with _lock:
        kb = _meta(root, kid)
        if not any(s["id"] == sid for s in kb["sources"]):
            raise KnowledgeError("source not found", 404)
        p = _dir(root, kid) / "chunks.jsonl"
        kept = [l for l in p.read_text(encoding="utf-8").splitlines() if json.loads(l)["source"] != sid] if p.exists() else []
        p.write_text("".join(l + "\n" for l in kept), encoding="utf-8")
        kb["sources"] = [s for s in kb["sources"] if s["id"] != sid]
        kb["chunks"] = len(kept)
        _write_meta(root, kb)


def search(root, kid, query, k=5):
    kb = _meta(root, kid)
    q = embeddings.embed(kb["embedding"], [str(query)], root)[0]
    titles = {s["id"]: s["title"] for s in kb["sources"]}
    p = _dir(root, kid) / "chunks.jsonl"
    scored = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        c = json.loads(line)
        scored.append((embeddings.cosine(q, c["v"]), c))
    scored.sort(key=lambda x: -x[0])
    return [{"score": round(s, 3), "text": c["text"], "source": titles.get(c["source"], ""), "kb": kb["name"]} for s, c in scored[:max(1, min(int(k), 20))] if s > 0]


def search_many(root, kids, query, k=5):
    hits = []
    for kid in kids or []:
        try:
            hits += search(root, kid, query, k)
        except KnowledgeError:
            pass
    return sorted(hits, key=lambda h: -h["score"])[:k]


def as_prompt(root, kids, query, k=4, max_chars=4000):
    hits, out, used = search_many(root, kids, query, k), [], 0
    for h in hits:
        piece = f"[{h['source']}] {h['text']}"
        if used + len(piece) > max_chars:
            break
        out.append(piece)
        used += len(piece)
    return "\n\n".join(out)


# ---------------------------------------------------------------- web
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.title, self._skip, self._in_title = [], [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r" *\n *", "\n", text)).strip()
    return text, p.title.strip(), p.links


def _fetch(url):
    from .jobs import safe_fetch  # public-address check and redirect handling live there
    return safe_fetch(url, max_bytes=2_000_000)


def _robots(base, fetch):
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(fetch(urljoin(base, "/robots.txt")).splitlines())
    except ValueError:
        rp.parse([])  # no robots.txt: allowed
    return rp


def scrape(root, kid, url, max_pages=1, delay=1.0, fetch=None, sleep=time.sleep, progress=None):
    """Fetch a page (or crawl up to max_pages on the same site) into the knowledge base."""
    fetch = fetch or _fetch
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise KnowledgeError("give a full http(s) URL")
    max_pages = max(1, min(int(max_pages), 50))
    robots = _robots(url, fetch)
    queue, seen, added, skipped = [urldefrag(url)[0]], set(), [], []
    while queue and len(seen) < max_pages:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        if not robots.can_fetch("agentlab", page):
            skipped.append({"url": page, "reason": "robots.txt disallows it"})
            continue
        try:
            body = fetch(page)
        except ValueError as e:
            skipped.append({"url": page, "reason": str(e)[:120]})
            continue
        text, title, links = html_to_text(body) if "<" in body[:2000] else (body, "", [])
        if len(text) >= 80:
            added.append(add_text(root, kid, text, title or page, {"kind": "web", "url": page}))
        else:
            skipped.append({"url": page, "reason": "no readable text"})
        if progress:
            progress(len(seen), max_pages)
        for href in links:
            nxt = urldefrag(urljoin(page, href))[0]
            if urlparse(nxt).hostname == u.hostname and urlparse(nxt).scheme in ("http", "https") and nxt not in seen and not re.search(r"\.(png|jpe?g|gif|svg|pdf|zip|mp4|css|js)$", nxt, re.I):
                queue.append(nxt)
        if queue and len(seen) < max_pages:
            sleep(delay)
    return {"added": added, "skipped": skipped, "pages": len(seen)}
