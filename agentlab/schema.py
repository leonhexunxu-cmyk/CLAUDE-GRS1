"""Event schema (v1) and secret redaction shared by every adapter."""

import re

SCHEMA_VERSION = 1
KINDS = ("prompt", "tool_call", "tool_result", "file_edit", "decision", "output", "error")

_REDACTED = "[REDACTED]"
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                    r"|xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{30,})")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{16,}")
_ASSIGN = re.compile(r"(?i)\b((?:api[_-]?key|secret|token|password|passwd)[\"']?\s*[=:]\s*[\"']?)[^\s\"',;]{6,}")


def redact(text):
    """Mask credentials before they are written to disk. Heuristic, not a guarantee."""
    if not isinstance(text, str):
        return text
    text = _PEM.sub(_REDACTED, text)
    text = _TOKEN.sub(_REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + _REDACTED, text)
    return _ASSIGN.sub(lambda m: m.group(1) + _REDACTED, text)
