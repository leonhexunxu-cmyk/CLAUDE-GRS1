"""API description (single source of truth), OpenAPI export and client code generators."""

import re

LANGUAGES = ("python", "javascript", "typescript", "curl")
FILENAMES = {"python": "agentlab_client.py", "javascript": "agentlab-client.js",
             "typescript": "agentlab-client.ts", "curl": "agentlab.sh"}


def _e(name, method, path, summary, body=(), client=True):
    return {"name": name, "method": method, "path": path, "summary": summary, "body": list(body), "client": client}


_RULES = ("rules", "string", True)
SPEC = [
    _e("health", "GET", "/api/health", "Server status"),
    _e("list_runs", "GET", "/api/runs", "List recorded runs"),
    _e("get_run", "GET", "/api/runs/{run_id}", "Read one run's events"),
    _e("fork_run", "POST", "/api/runs/{run_id}/fork", "Fork a run from a step", [("step", "integer", True)]),
    _e("ingest_event", "POST", "/api/events", "Record an event from any agent",
       [("run", "string", True), ("kind", "string", True), ("content", "string", True), ("tool", "string", False)]),
    _e("get_policy", "GET", "/api/policy", "Read the project's sandbox rules"),
    _e("save_policy", "POST", "/api/policy", "Save the project's sandbox rules", [_RULES]),
    _e("simulate_policy", "POST", "/api/policy/simulate", "Test rules against common attacks", [_RULES]),
    _e("check_policy", "POST", "/api/policy/check", "Check one action against rules",
       [_RULES, ("action", "string", True), ("target", "string", True)]),
    _e("policy_settings", "POST", "/api/policy/settings", "Convert rules to Claude Code permissions", [_RULES]),
    _e("compare_runs", "POST", "/api/regress", "Compare candidate outputs with a baseline",
       [("baseline", "object", True), ("candidate", "object", True), ("threshold", "number", False)]),
    _e("plant_canaries", "POST", "/api/tripwires/plant", "Plant canary facts in a document",
       [("seed", "string", True), ("document", "string", True), ("n", "integer", False)]),
    _e("audit_answer", "POST", "/api/tripwires/audit", "Audit an answer against the canaries",
       [("seed", "string", True), ("answer", "string", True), ("n", "integer", False), ("expect_all", "boolean", False)]),
    _e("list_agents", "GET", "/api/agents", "Which agents are detected and connected"),
    _e("list_pads", "GET", "/api/pads", "List code pads"),
    _e("get_pad", "GET", "/api/pads/{pad_id}", "Read a code pad"),
    _e("save_pad", "POST", "/api/pads", "Create or update a code pad",
       [("name", "string", True), ("language", "string", True), ("content", "string", True), ("id", "string", False)]),
    _e("delete_pad", "POST", "/api/pads/{pad_id}/delete", "Delete a code pad"),
    _e("list_keys", "GET", "/api/keys", "List API keys", client=False),
    _e("create_key", "POST", "/api/keys", "Create an API key", [("label", "string", False)], client=False),
    _e("revoke_key", "POST", "/api/keys/{key_id}/revoke", "Revoke an API key", client=False),
    _e("generate_client", "POST", "/api/generate", "Generate a client library",
       [("language", "string", True), ("base_url", "string", False)], client=False),
    _e("openapi", "GET", "/api/openapi.json", "OpenAPI description of this API", client=False),
]

_BASE_OK = re.compile(r"^https?://[A-Za-z0-9._\-\[\]:]+(/[A-Za-z0-9._~/\-]*)?$")


def valid_base_url(url):
    return bool(_BASE_OK.match(url))


def _path_params(e):
    return re.findall(r"\{(\w+)\}", e["path"])


def _camel(name):
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def openapi(version, base="http://127.0.0.1:8765"):
    types = {"integer": "integer", "number": "number", "boolean": "boolean", "string": "string", "object": "object"}
    paths = {}
    for e in SPEC:
        op = {"operationId": e["name"], "summary": e["summary"], "responses": {"200": {"description": "OK"}}}
        if _path_params(e):
            op["parameters"] = [{"name": p, "in": "path", "required": True, "schema": {"type": "string"}}
                                for p in _path_params(e)]
        if e["body"]:
            op["requestBody"] = {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {n: {"type": types[t]} for n, t, _ in e["body"]},
                "required": [n for n, _, r in e["body"] if r]}}}}
        paths.setdefault(e["path"], {})[e["method"].lower()] = op
    return {"openapi": "3.0.3", "info": {"title": "agentlab", "version": version},
            "servers": [{"url": base}], "paths": paths}


def generate(language, base_url="http://127.0.0.1:8765"):
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {list(LANGUAGES)}")
    if not valid_base_url(base_url):
        raise ValueError("base_url must look like http://host:port")
    ops = [e for e in SPEC if e["client"]]
    return {"python": _python, "javascript": _js, "typescript": _ts, "curl": _curl}[language](ops, base_url.rstrip("/"))


def _python(ops, base):
    out = ['"""agentlab API client (generated). Python 3.9+, standard library only.',
           'Set AGENTLAB_API_KEY if you created API keys."""',
           "import json", "import os", "import urllib.error", "import urllib.request", "", "",
           "class AgentLab:",
           f'    def __init__(self, base_url="{base}", api_key=None):',
           '        self.base_url = base_url.rstrip("/")',
           '        self.api_key = api_key or os.environ.get("AGENTLAB_API_KEY")', "",
           "    def _request(self, method, path, body=None):",
           '        headers = {"Content-Type": "application/json"}',
           "        if self.api_key:",
           '            headers["Authorization"] = "Bearer " + self.api_key',
           "        data = None if body is None else json.dumps(body).encode()",
           "        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)",
           "        try:",
           "            with urllib.request.urlopen(req) as res:",
           "                return json.load(res)",
           "        except urllib.error.HTTPError as e:",
           '            raise RuntimeError(f"{e.code}: {e.read().decode()}") from None']
    for e in ops:
        req = [n for n, _, r in e["body"] if r]
        opt = [n for n, _, r in e["body"] if not r]
        args = ", ".join(["self", *_path_params(e), *req, *[f"{n}=None" for n in opt]])
        out += ["", f"    def {e['name']}({args}):", f'        """{e["summary"]}."""']
        path = f'f"{e["path"]}"' if _path_params(e) else f'"{e["path"]}"'
        if e["body"]:
            fields = ", ".join(f'"{n}": {n}' for n, _, _ in e["body"])
            out += [f"        body = {{k: v for k, v in {{{fields}}}.items() if v is not None}}",
                    f'        return self._request("{e["method"]}", {path}, body)']
        else:
            out.append(f'        return self._request("{e["method"]}", {path})')
    return "\n".join(out) + "\n"


def _js(ops, base, ts=False):
    """JavaScript client; with ts=True, the same client with type annotations."""
    ann = (lambda x: f": {x}") if ts else (lambda x: "")
    tmap = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "object": "Record<string, unknown>"}
    out = ["// agentlab API client (generated). Works in Node 18+ and browsers.",
           "// Set AGENTLAB_API_KEY (Node) or pass apiKey if you created API keys.", "",
           "export class AgentLab {"]
    if ts:
        out += ["  baseUrl: string;", "  apiKey: string | null;", ""]
    out += [f'  constructor(baseUrl{ann("string")} = "{base}", apiKey{ann("string | null")} = (globalThis.process?.env?.AGENTLAB_API_KEY ?? null)) {{',
            '    this.baseUrl = baseUrl.replace(/\\/$/, "");', "    this.apiKey = apiKey;", "  }", "",
            f'  async _request(method{ann("string")}, path{ann("string")}, body{"?: unknown" if ts else ""}){ann("Promise<any>")} {{',
            f'    const headers{ann("Record<string, string>")} = {{ "Content-Type": "application/json" }};',
            "    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;",
            "    const res = await fetch(this.baseUrl + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });",
            "    const data = await res.json();",
            "    if (!res.ok) throw new Error(`${res.status}: ${data.error}`);",
            "    return data;", "  }"]
    for e in ops:
        params = _path_params(e)
        sig = [f"{_camel(p)}{ann('string')}" for p in params]
        if e["body"]:
            if ts:
                shape = "; ".join(f"{n}{'' if r else '?'}: {tmap[tp]}" for n, tp, r in e["body"])
                sig.append(f"body: {{ {shape} }}")
            else:
                sig.append("body")
        path = f'"{e["path"]}"'
        if params:
            path = "`" + re.sub(r"\{(\w+)\}", lambda m: "${" + _camel(m.group(1)) + "}", e["path"]) + "`"
        doc = e["summary"]
        if e["body"] and not ts:
            doc += ". body: { " + ", ".join(f"{n}{'' if r else '?'}: {tp}" for n, tp, r in e["body"]) + " }"
        call = f'this._request("{e["method"]}", {path}' + (", body" if e["body"] else "") + ")"
        out += ["", f"  /** {doc} */", f"  {_camel(e['name'])}({', '.join(sig)}){ann('Promise<any>')} {{ return {call}; }}"]
    return "\n".join(out + ["}"]) + "\n"


def _ts(ops, base):
    return _js(ops, base, ts=True)


_SAMPLES = {"rules": "allow read src/**\\nnever read .env", "kind": "decision", "action": "read", "target": "src/app.py",
            "seed": "my-secret", "language": "javascript", "run": "my-run", "name": "Scratch", "content": "console.log(1)"}


def _curl(ops, base):
    out = [f'BASE="{base}"', "# If you created API keys: export AGENTLAB_API_KEY=alab_...",
           "al() {", '  if [ -n "$AGENTLAB_API_KEY" ]; then',
           '    curl -s -H "Authorization: Bearer $AGENTLAB_API_KEY" -H "Content-Type: application/json" "$@"',
           "  else", '    curl -s -H "Content-Type: application/json" "$@"', "  fi", "}"]
    for e in ops:
        url = re.sub(r"\{(\w+)\}", lambda m: m.group(1).upper(), e["path"])
        parts = {"string": '"example"', "integer": "1", "number": "0.8", "boolean": "false", "object": "{}"}
        line = f'al -X {e["method"]} "$BASE{url}"'
        req = [(n, tp) for n, tp, r in e["body"] if r]
        if req:
            body = ", ".join(f'"{n}": ' + (f'"{_SAMPLES[n]}"' if n in _SAMPLES and tp == "string" else parts[tp]) for n, tp in req)
            line += f" -d '{{{body}}}'"
        out += ["", f"# {e['summary']}", line]
    return "\n".join(out) + "\n"
