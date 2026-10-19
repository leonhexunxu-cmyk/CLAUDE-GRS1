"""GitHub and Hugging Face connections: verify a token, remember who you are, search and list repos/models/datasets."""

import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from . import vault
from .hooks import data_dir

GITHUB_API = "https://api.github.com"
HF_API = "https://huggingface.co"
OPENAI_API = "https://api.openai.com/v1"
ANTHROPIC_API = "https://api.anthropic.com"
SERVICES = {"github": "github_token", "huggingface": "hf_token", "openai": "openai", "anthropic": "anthropic"}


class AccountError(ValueError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _get_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "agentlab", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        hint = {401: "the token was rejected", 403: "access denied (check the token's permissions)", 404: "not found", 429: "rate limited, try again later"}.get(e.code, f"HTTP {e.code}")
        raise AccountError(hint, e.code) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise AccountError(f"could not reach the service: {getattr(e, 'reason', e)}") from None
    except ValueError:
        raise AccountError("the service returned something unexpected") from None


def _gh_headers(token):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    return {**h, "Authorization": f"Bearer {token}"} if token else h


def _path(root):
    return data_dir(root) / "accounts.json"


def _identities(root):
    p = _path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def connect(root, service, token):
    """Verify the token with the service, then store it (vault, mode 600) and who it belongs to."""
    if service not in SERVICES:
        raise AccountError(f"service must be one of {list(SERVICES)}")
    token = str(token or "").strip()
    if not token:
        raise AccountError("a token is required")
    if service == "github":
        data = _get_json(f"{GITHUB_API}/user", _gh_headers(token))
        who = {"name": data.get("login", ""), "display": data.get("name") or ""}
    elif service == "huggingface":
        data = _get_json(f"{HF_API}/api/whoami-v2", {"Authorization": f"Bearer {token}"})
        who = {"name": data.get("name", ""), "display": data.get("fullname") or data.get("type", "")}
    else:  # OpenAI / Anthropic keys do not say who they belong to; a successful model listing proves they work
        models = _list(service, token)
        who = {"name": f"{service} key", "display": f"{len(models)} models available"}
    if not who["name"]:
        raise AccountError("the service did not say who this token belongs to")
    vault.set_secret(root, SERVICES[service], token)
    ids = _identities(root)
    ids[service] = {**who, "verified": time.time()}
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ids), encoding="utf-8")
    return {"service": service, **who}


def disconnect(root, service):
    if service not in SERVICES:
        raise AccountError(f"service must be one of {list(SERVICES)}")
    vault.delete_secret(root, SERVICES[service])
    ids = _identities(root)
    ids.pop(service, None)
    _path(root).write_text(json.dumps(ids), encoding="utf-8")


def status(root):
    ids = _identities(root)
    out = {}
    for service, secret in SERVICES.items():
        have = vault.get_secret(root, secret)
        out[service] = {"connected": bool(have), "identity": ids.get(service) if have else None,
                        "hint": ("…" + have[-4:]) if have and len(have) > 8 else ""}
    return out


def github_repos(root, query=None, limit=30):
    token = vault.get_secret(root, "github_token")
    if query:
        data = _get_json(f"{GITHUB_API}/search/repositories?q={quote(query)}&per_page={limit}", _gh_headers(token))
        items = data.get("items", [])
    else:
        if not token:
            raise AccountError("connect GitHub first to list your repositories, or type a search")
        items = _get_json(f"{GITHUB_API}/user/repos?sort=updated&per_page={limit}", _gh_headers(token))
    return [{"full_name": r["full_name"], "description": (r.get("description") or "")[:160], "private": bool(r.get("private")), "stars": r.get("stargazers_count", 0),
             "default_branch": r.get("default_branch", "main"), "updated": r.get("updated_at", "")} for r in items]


def hf_search(root, kind, query, limit=20):
    if kind not in ("models", "datasets"):
        raise AccountError("kind must be models or datasets")
    token = vault.get_secret(root, "hf_token")
    url = f"{HF_API}/api/{kind}?search={quote(query or '')}&limit={limit}&sort=downloads&direction=-1"
    items = _get_json(url, {"Authorization": f"Bearer {token}"} if token else None)
    return [{"id": r.get("id") or r.get("modelId"), "downloads": r.get("downloads", 0), "likes": r.get("likes", 0), "task": r.get("pipeline_tag") or "",
             "tags": (r.get("tags") or [])[:6], "private": bool(r.get("private")), "gated": bool(r.get("gated"))} for r in items]


def _list(service, token, base=None):
    if service == "anthropic":
        data = _get_json(f"{(base or ANTHROPIC_API).rstrip('/')}/v1/models?limit=100", {"x-api-key": token, "anthropic-version": "2023-06-01"})
        return [{"id": m["id"], "name": m.get("display_name") or m["id"], "created": m.get("created_at", "")} for m in data.get("data", [])]
    data = _get_json(f"{(base or OPENAI_API).rstrip('/')}/models", {"Authorization": f"Bearer {token}"} if token else None)
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    return sorted(({"id": m.get("id") or m.get("name"), "name": m.get("id") or m.get("name"), "owned_by": m.get("owned_by", "")} for m in items if isinstance(m, dict) and (m.get("id") or m.get("name"))), key=lambda m: m["id"])


def list_models(root, provider):
    """Models a provider offers, so you can pick the model name instead of typing it. Works for Anthropic and
    any OpenAI-compatible server (OpenAI, OpenRouter, Groq, Mistral, Ollama, LM Studio, vLLM, llama.cpp ...)."""
    kind = provider.get("type")
    if kind not in ("anthropic", "openai"):
        raise AccountError("model listing works for anthropic and OpenAI-compatible providers")
    token = vault.get_secret(root, provider.get("key")) if provider.get("key") else None
    if provider.get("key") and not token:
        raise AccountError(f"no API key named '{provider['key']}'")
    base = provider.get("base_url")
    if kind == "anthropic":
        return _list("anthropic", token, base)
    if not str(base or "").startswith(("http://", "https://")):
        raise AccountError("a base_url is needed")
    if "{account_id}" in base:
        raise AccountError("Cloudflare Workers AI does not list models here; see developers.cloudflare.com/workers-ai/models")
    return _list("openai", token, base)
