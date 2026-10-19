"""Embedding models: turn text into vectors for search.

  builtin  - a hashing embedder (words and word pairs hashed into 512 dimensions). No download, no network, fast,
             good for keyword-like search; it does not understand synonyms.
  openai   - any OpenAI-compatible /embeddings endpoint: OpenAI, Ollama (/v1), LM Studio, vLLM, llama.cpp
             (--embedding), Hugging Face router, Mistral, ...
"""

import hashlib
import math
import re

from . import llm, vault

DIM = 512
PRESETS = {
    "builtin": {"type": "builtin", "model": "hashing-512", "label": "Built-in (offline)"},
    "openai-small": {"type": "openai", "base_url": "https://api.openai.com/v1", "model": "text-embedding-3-small", "key": "openai", "label": "OpenAI text-embedding-3-small"},
    "ollama-nomic": {"type": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "nomic-embed-text", "key": "", "label": "Ollama nomic-embed-text"},
    "lmstudio": {"type": "openai", "base_url": "http://127.0.0.1:1234/v1", "model": "text-embedding-nomic-embed-text-v1.5", "key": "", "label": "LM Studio"},
    "mistral": {"type": "openai", "base_url": "https://api.mistral.ai/v1", "model": "mistral-embed", "key": "mistral", "label": "Mistral embed"},
}
WORD = re.compile(r"\w+", re.U)


class EmbeddingError(ValueError):
    pass


def validate(spec):
    spec = dict(spec or PRESETS["builtin"])
    if spec.get("preset"):
        spec = {**PRESETS.get(spec["preset"], {}), **{k: v for k, v in spec.items() if k != "preset"}}
    if spec.get("type") not in ("builtin", "openai"):
        raise EmbeddingError("embedding type must be builtin or openai")
    if spec["type"] == "openai":
        if not str(spec.get("base_url", "")).startswith(("http://", "https://")) or not spec.get("model"):
            raise EmbeddingError("an embedding endpoint needs a base_url and a model")
        if spec.get("key") and not vault.NAME.match(spec["key"]):
            raise EmbeddingError("key must be the name of a saved key")
    return {k: spec.get(k) for k in ("type", "base_url", "model", "key", "label") if spec.get(k) is not None}


def _builtin(text):
    v = [0.0] * DIM
    words = [w.lower() for w in WORD.findall(text)]
    for feat in words + [a + " " + b for a, b in zip(words, words[1:])]:
        h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
        v[h % DIM] += 1.0 if (h >> 63) else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(spec, texts, project=".", batch=64):
    """-> list of vectors (unit length)."""
    spec = validate(spec)
    if spec["type"] == "builtin":
        return [_builtin(t) for t in texts]
    key = vault.get_secret(project, spec.get("key"))
    if spec.get("key") and not key:
        raise EmbeddingError(f"no API key named '{spec['key']}'")
    out = []
    for i in range(0, len(texts), batch):
        try:
            data = llm._post(spec["base_url"].rstrip("/") + "/embeddings", {"Authorization": f"Bearer {key}"} if key else {}, {"model": spec["model"], "input": texts[i:i + batch]}, 60, 2, 1.0)
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))]
        except llm.LLMError as e:
            raise EmbeddingError(vault.redact_value(str(e), project)) from None
        except (KeyError, TypeError):
            raise EmbeddingError("the server's reply did not look like an embeddings response") from None
        for v in vecs:
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
    return out


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))
