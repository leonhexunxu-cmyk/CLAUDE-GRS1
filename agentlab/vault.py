"""Key vault: API keys and tokens kept in .agentlab/secrets.json (mode 600).

Values are stored in plain text on your disk (like a .env file) and are never returned by the API; only a
name and the last four characters are. Environment variables are used as a fallback.
"""

import json
import os
import re

from .hooks import data_dir

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ENV_FALLBACK = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
                "cloudflare_token": "CLOUDFLARE_API_TOKEN", "cloudflare_account": "CLOUDFLARE_ACCOUNT_ID",
                "hf_token": "HF_TOKEN", "github_token": "GITHUB_TOKEN", "openclaw_token": "OPENCLAW_GATEWAY_TOKEN"}


def _path(project):
    return data_dir(project) / "secrets.json"


def _load(project):
    p = _path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def set_secret(project, name, value):
    if not NAME.match(name or ""):
        raise ValueError("name must be lowercase letters, digits and underscores")
    value = str(value).strip()
    if not value or len(value) > 4096:
        raise ValueError("value is required (max 4096 characters)")
    data = _load(project)
    data[name] = value
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(p, 0o600)


def delete_secret(project, name):
    data = _load(project)
    if data.pop(name, None) is None:
        return False
    _path(project).write_text(json.dumps(data), encoding="utf-8")
    return True


def get_secret(project, name):
    """The stored value, else the conventional environment variable, else None."""
    if not name:
        return None
    return _load(project).get(name) or os.environ.get(ENV_FALLBACK.get(name, "")) or None


def list_secrets(project):
    stored = _load(project)
    names = sorted(set(stored) | {n for n, env in ENV_FALLBACK.items() if os.environ.get(env)})
    return [{"name": n, "hint": ("…" + stored[n][-4:]) if n in stored and len(stored[n]) > 8 else "", "source": "vault" if n in stored else "env"}
            for n in names]


def redact_value(text, project):
    """Remove any stored secret value from text (defence in depth for logs and error messages)."""
    for v in _load(project).values():
        if len(v) >= 8:
            text = text.replace(v, "[REDACTED]")
    return text
