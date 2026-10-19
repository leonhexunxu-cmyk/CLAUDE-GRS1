"""Token counting: pick a preset estimate, or load a real tokenizer file (no libraries needed).

Presets are quick estimates. A `tokenizer.json` from Hugging Face (byte-level BPE: GPT-2 style models and many others)
gives exact token counts for those models, computed here in pure Python. Counts are used for budgets, context limits
and the usage numbers of models whose API does not report tokens.
"""

import json
import math
import re
from pathlib import Path

from . import projects

PRESETS = {
    "chars4": "Estimate: about 4 characters per token (English prose)",
    "chars3": "Estimate: about 3 characters per token (code and dense text)",
    "words": "Estimate: about 1.3 tokens per word",
    "cjk": "Estimate: one token per Chinese/Japanese/Korean character",
}
DEFAULT = {"kind": "preset", "preset": "chars4"}
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_PRETOKEN = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d| ?[^\W\d_]+| ?\d+| ?(?:[^\s\w]|_)+|\s+(?!\S)|\s+")
_cache = {}


class TokenizerError(ValueError):
    pass


def estimate(text, preset="chars4"):
    text = str(text or "")
    if not text:
        return 0
    if preset == "chars3":
        return math.ceil(len(text) / 3)
    if preset == "words":
        return math.ceil(len(text.split()) * 1.3)
    if preset == "cjk":
        cjk = len(_CJK.findall(text))
        return cjk + math.ceil((len(text) - cjk) / 4)
    return math.ceil(len(text) / 4)


def _bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(0xA1, 0xAC + 1)) + list(range(0xAE, 0xFF + 1))
    cs, n = bs[:], 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


_B2U = _bytes_to_unicode()


class ByteLevelBPE:
    """Byte-level BPE from a Hugging Face tokenizer.json. Exact for GPT-2-style tokenizers; close for similar ones."""

    def __init__(self, data):
        model = data.get("model", {})
        if model.get("type", "BPE") != "BPE" or "vocab" not in model or "merges" not in model:
            raise TokenizerError("only byte-level BPE tokenizer.json files are supported (this one is a different kind)")
        pre = json.dumps(data.get("pre_tokenizer"))
        if "ByteLevel" not in pre and "Split" not in pre:
            raise TokenizerError("this BPE tokenizer is not byte-level; use a preset estimate instead")
        self.vocab = model["vocab"]
        merges = [m.split(" ") if isinstance(m, str) else list(m) for m in model["merges"]]
        self.ranks = {(a, b): i for i, (a, b) in enumerate(merges)}
        self.unk = self.vocab.get(model.get("unk_token") or "", None)
        self.special = {t["content"]: t["id"] for t in data.get("added_tokens", []) if t.get("special")}
        self._words = {}

    def _bpe(self, token):
        if token in self._words:
            return self._words[token]
        word = list(token)
        while len(word) > 1:
            pairs = [(self.ranks.get((word[i], word[i + 1]), None), i) for i in range(len(word) - 1)]
            pairs = [p for p in pairs if p[0] is not None]
            if not pairs:
                break
            _, i = min(pairs)
            a, b = word[i], word[i + 1]
            merged, j = [], 0
            while j < len(word):
                if j < len(word) - 1 and word[j] == a and word[j + 1] == b:
                    merged.append(a + b)
                    j += 2
                else:
                    merged.append(word[j])
                    j += 1
            word = merged
        if len(self._words) < 50000:
            self._words[token] = word
        return word

    def encode(self, text):
        ids = []
        parts = re.split("(" + "|".join(re.escape(s) for s in self.special) + ")", text) if self.special else [text]
        for part in parts:
            if part in self.special:
                ids.append(self.special[part])
                continue
            for tok in _PRETOKEN.findall(part):
                mapped = "".join(_B2U[b] for b in tok.encode("utf-8"))
                for piece in self._bpe(mapped):
                    i = self.vocab.get(piece, self.unk)
                    ids.append(i if i is not None else 0)
        return ids

    def count(self, text):
        return len(self.encode(text))


def validate_spec(spec):
    """None -> default preset. Raises ValueError for anything malformed."""
    if spec in (None, {}, ""):
        return dict(DEFAULT)
    if not isinstance(spec, dict) or spec.get("kind") not in ("preset", "file"):
        raise ValueError("tokenizer must be {kind: preset|file, ...}")
    if spec["kind"] == "preset":
        if spec.get("preset") not in PRESETS:
            raise ValueError(f"tokenizer preset must be one of {sorted(PRESETS)}")
        return {"kind": "preset", "preset": spec["preset"]}
    if not spec.get("project") or not spec.get("path"):
        raise ValueError("a tokenizer file needs project and path")
    return {"kind": "file", "project": str(spec["project"]), "path": str(spec["path"])}


def load(root, spec):
    """A callable text -> token count for the spec (falls back to the default estimate if a file cannot be used)."""
    spec = validate_spec(spec)
    if spec["kind"] == "preset":
        return lambda text, p=spec["preset"]: estimate(text, p)
    try:
        path = projects.resolve(root, spec["project"], spec["path"])
        key = (str(path), path.stat().st_mtime_ns)
        if key not in _cache:
            _cache[key] = ByteLevelBPE(json.loads(path.read_text(encoding="utf-8")))
        return _cache[key].count
    except (projects.ProjectError, OSError, ValueError, TokenizerError, KeyError):
        return lambda text: estimate(text, "chars4")


def count(text, spec=None, root="."):
    return load(root, spec)(str(text or ""))


def describe(root, spec):
    """For the UI: what this spec resolves to, and whether a file was usable."""
    spec = validate_spec(spec)
    if spec["kind"] == "preset":
        return {**spec, "label": PRESETS[spec["preset"]], "exact": False, "ok": True}
    try:
        path = projects.resolve(root, spec["project"], spec["path"])
        tok = ByteLevelBPE(json.loads(Path(path).read_text(encoding="utf-8")))
        return {**spec, "label": f"Exact byte-level BPE ({len(tok.vocab):,} tokens)", "exact": True, "ok": True}
    except (projects.ProjectError, OSError, ValueError, TokenizerError) as e:
        return {**spec, "label": str(e), "exact": False, "ok": False}
