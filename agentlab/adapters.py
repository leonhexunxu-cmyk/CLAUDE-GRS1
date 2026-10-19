"""Normalise tool calls from different agents into one canonical shape (Claude Code's names)."""

_TOOLS = {
    "bash": "Bash", "shell": "Bash", "exec": "Bash", "run": "Bash", "runcommand": "Bash", "execute": "Bash",
    "read": "Read", "readfile": "Read", "readmediafile": "Read", "notebookread": "Read",
    "write": "Write", "writefile": "Write",
    "edit": "Edit", "multiedit": "MultiEdit", "notebookedit": "NotebookEdit",
    "grep": "Grep", "glob": "Glob",
    "webfetch": "WebFetch", "fetchurl": "WebFetch",
}
_PATH_KEYS = ("file_path", "path", "filePath", "file", "filename", "notebook_path")
_COMMAND_KEYS = ("command", "cmd", "script")
_URL_KEYS = ("url", "uri")


def _first(inp, keys):
    return next((inp[k] for k in keys if isinstance(inp.get(k), str) and inp[k]), None)


def normalize(tool, tool_input):
    """Return (canonical_tool, canonical_input). Unknown tools pass through unchanged.

    Handles name aliases (FetchURL, exec, read_file ...) and parameter aliases (path, cmd, uri ...).
    Known file/shell/web tools always get their canonical keys; a missing value stays missing so the
    policy can refuse to guess.
    """
    tool = tool or ""
    inp = dict(tool_input) if isinstance(tool_input, dict) else {}
    canon = _TOOLS.get(tool.lower().replace("_", "").replace("-", ""), tool)
    if canon in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep", "Glob"):
        path = _first(inp, _PATH_KEYS)
        if path is not None:
            inp["file_path"] = path
            inp.setdefault("path", path)
    elif canon == "Bash":
        cmd = _first(inp, _COMMAND_KEYS)
        if cmd is not None:
            inp["command"] = cmd
    elif canon == "WebFetch":
        url = _first(inp, _URL_KEYS)
        if url is not None:
            inp["url"] = url
    return canon, inp
