"""Library: preset roles, skills (instruction bundles), plugins (sandboxed command tools) and the connector catalog.

Everything here is optional. A role is a starting point you can edit; a skill adds instructions to an agent's prompt;
a plugin adds one tool that runs a fixed command template inside the project's sandbox (parameters are shell-quoted,
so a model cannot smuggle extra commands through them).
"""

import json
import re
import shlex
import uuid

from . import danger, envs, project_env, projects
from .hooks import data_dir

# ---------------------------------------------------------------- roles
ROLES = {
    "manager": {"name": "Manager", "avatar": "🧭", "role": "Plans the work, delegates and checks results",
                "instructions": "You lead the work. Turn the goal into a short plan with clear steps and owners, keep it in PLAN.md, hand tasks to the right teammate, check what comes back against the goal and the acceptance criteria, and report status plainly. Do not do everything yourself: delegate, then verify.",
                "skills": ["planning", "summarizer"], "plugins": ["tree", "git_status"], "mode": "ask_risky", "authorizations": {"run": "ask"}},
    "developer": {"name": "Developer", "avatar": "💻", "role": "Writes, runs and fixes code",
                  "instructions": "You are a careful senior engineer. Read the code before changing it, make small focused edits, run the code or the tests to prove it works, and explain what changed and why.",
                  "skills": ["debugging", "test-driven-dev", "commit-messages"], "plugins": ["git_status", "git_diff", "run_tests", "grep_files", "run_python_file"], "mode": "ask_risky"},
    "designer": {"name": "Designer", "avatar": "🎨", "role": "Designs clean interfaces",
                 "instructions": "You design simple, accessible interfaces: clear hierarchy, readable type, good contrast, generous spacing, works on a phone. You produce working HTML and CSS and explain the choices briefly.",
                 "skills": ["ux-writing", "accessibility"], "plugins": ["tree", "format_json"], "mode": "ask_risky"},
    "researcher": {"name": "Researcher", "avatar": "🔎", "role": "Finds and summarises information",
                   "instructions": "You research carefully. Say where each fact came from, separate what you verified from what you assume, and write concise notes into the project.",
                   "skills": ["web-research", "summarizer"], "plugins": ["grep_files", "http_headers"], "mode": "ask_risky"},
    "planner": {"name": "Planner", "avatar": "🗺️", "role": "Breaks goals into steps",
                "instructions": "You turn vague goals into a short ordered plan with checks. You keep the plan current as work progresses and flag risks early.",
                "skills": ["planning"], "plugins": ["tree"], "mode": "ask_risky", "authorizations": {"run": "deny"}},
    "reviewer": {"name": "Reviewer", "avatar": "🧐", "role": "Reviews work critically and kindly",
                 "instructions": "You review other people's work for correctness, risks and missing cases. Be specific: name the file and line, say what is wrong and how to fix it. You never edit the work yourself.",
                 "skills": ["code-review", "security-review"], "plugins": ["git_diff", "grep_files", "run_tests"], "mode": "ask_risky", "authorizations": {"write": "deny"}},
    "tester": {"name": "Tester", "avatar": "🧪", "role": "Tries to break things",
               "instructions": "You write and run tests, hunt for edge cases and failure modes, and report reproducible bugs with exact steps and expected versus actual results.",
               "skills": ["test-driven-dev", "debugging"], "plugins": ["run_tests", "run_python_file", "grep_files"], "mode": "ask_risky"},
    "writer": {"name": "Writer", "avatar": "✍️", "role": "Writes clear documents",
               "instructions": "You write clearly for the reader: lead with the point, use short sentences and concrete examples, and cut anything that does not help.",
               "skills": ["documentation", "ux-writing"], "plugins": ["tree"], "mode": "ask_risky", "authorizations": {"run": "deny"}},
    "data-analyst": {"name": "Data analyst", "avatar": "📊", "role": "Explores and summarises data",
                     "instructions": "You explore data before concluding: check sizes, types, missing values and outliers, state your assumptions, and show the numbers behind every claim.",
                     "skills": ["data-analysis", "sql-helper"], "plugins": ["csv_summary", "json_query", "run_python_file"], "mode": "ask_risky"},
    "devops": {"name": "DevOps engineer", "avatar": "🛠️", "role": "Builds and runs infrastructure",
               "instructions": "You build reliable, reproducible infrastructure: pin versions, keep secrets out of files, make every step repeatable, and explain what each command will change before it runs.",
               "skills": ["debugging", "security-review"], "plugins": ["git_status", "tree", "format_json"], "mode": "ask"},
    "security": {"name": "Security reviewer", "avatar": "🛡️", "role": "Looks for vulnerabilities",
                 "instructions": "You review for security problems: injection, unsafe deserialization, secrets in code, weak auth, missing validation. Explain impact and give a concrete fix. You never exploit anything.",
                 "skills": ["security-review", "code-review"], "plugins": ["grep_files", "git_diff"], "mode": "ask_risky", "authorizations": {"write": "deny", "network": "deny"}},
    "support": {"name": "Support agent", "avatar": "💬", "role": "Answers questions helpfully",
                "instructions": "You answer clearly and kindly, ask for missing details instead of guessing, and say when you do not know.",
                "skills": ["summarizer"], "plugins": [], "mode": "ask", "authorizations": {"run": "deny", "write": "deny"}},
    "translator": {"name": "Translator", "avatar": "🌍", "role": "Translates and localises",
                   "instructions": "You translate for meaning and tone, not word for word, keep formatting and placeholders intact, and flag phrases that need a native speaker's review.",
                   "skills": ["translation"], "plugins": [], "mode": "ask_risky", "authorizations": {"run": "deny"}},
    "product-owner": {"name": "Product owner", "avatar": "🎯", "role": "Decides what to build and why",
                      "instructions": "You clarify the user problem, write small testable requirements with acceptance criteria, say what is out of scope, and prioritise by value and risk.",
                      "skills": ["planning", "documentation"], "plugins": ["tree"], "mode": "ask_risky", "authorizations": {"run": "deny"}},
    "teacher": {"name": "Tutor", "avatar": "🎓", "role": "Explains and coaches",
                "instructions": "You explain step by step at the learner's level, check understanding with a question, give one small exercise at a time, and encourage.",
                "skills": ["summarizer"], "plugins": [], "mode": "ask_risky", "authorizations": {"run": "deny"}},
}

# ---------------------------------------------------------------- skills
SKILLS = {
    "code-review": ("Code review", "Review changes for bugs, risks and clarity", "When reviewing code: read the whole diff first; look for bugs, missing edge cases, unsafe input handling, and unclear names; separate must-fix from nice-to-have; cite file and line; suggest the smallest fix."),
    "test-driven-dev": ("Test-driven development", "Write the test first, then the code", "Work test-first: write a failing test that states the behaviour, make it pass with the simplest code, then clean up. Run the whole suite before finishing and report the result."),
    "debugging": ("Debugging", "Find the cause before changing code", "When something fails: reproduce it first; read the actual error; form one hypothesis at a time and test it with a tiny experiment; fix the cause, not the symptom; add a test that would have caught it."),
    "refactoring": ("Refactoring", "Improve structure without changing behaviour", "Refactor in small steps with tests green after each one. Remove duplication, name things for what they mean, shrink long functions, and never mix a refactor with a behaviour change."),
    "documentation": ("Documentation", "Write docs people actually read", "Document for the reader's goal: a one-line purpose, a copy-pasteable quick start, then reference. Prefer examples to prose. Keep docs next to the code and update them when behaviour changes."),
    "web-research": ("Web research", "Research with sources", "For research: search broadly, open primary sources, note the date and author, quote sparingly, and list every source at the end. Label anything unverified as such."),
    "data-analysis": ("Data analysis", "Look at the data before concluding", "Start by profiling the data: row counts, columns, types, missing values, ranges. State assumptions. Show the calculation behind every number and mention sampling or bias when it matters."),
    "summarizer": ("Summarising", "Short, faithful summaries", "Summarise faithfully: lead with the conclusion, keep key numbers and names, drop filler, and never add facts that are not in the source."),
    "security-review": ("Security review", "Spot common vulnerabilities", "Check for: injection, path traversal, unsafe deserialization, hard-coded secrets, missing authentication or authorisation, insecure defaults, and unvalidated input. Explain the impact and propose a concrete fix. Do not exploit anything."),
    "planning": ("Planning", "Make a short, checkable plan", "Write a plan of 3-8 steps, each with an outcome you can check. Order by dependency, note risks and unknowns, and update the plan when reality changes."),
    "ux-writing": ("UX writing", "Clear interface text", "Write interface text that is short, specific and kind: say what happened, what to do next, and avoid jargon. Buttons are verbs. Error messages never blame the user."),
    "accessibility": ("Accessibility", "Make interfaces usable by everyone", "Check contrast, keyboard use, visible focus, labels for every control, alt text, and that nothing relies on colour alone. Prefer semantic HTML over ARIA."),
    "commit-messages": ("Commit messages", "Clear, conventional commits", "Write commit messages with a short imperative summary (under 72 characters) and, if needed, a body explaining why. One logical change per commit."),
    "api-design": ("API design", "Consistent, predictable APIs", "Design APIs around resources and clear verbs, return consistent error shapes, validate input at the edge, version breaking changes, and document each endpoint with an example request and response."),
    "sql-helper": ("SQL", "Careful SQL", "Write readable SQL: explicit columns, meaningful aliases, joins with conditions, and a WHERE clause on every update or delete. Explain the query plan when performance matters."),
    "translation": ("Translation", "Translate for meaning", "Translate for meaning and tone. Keep placeholders, markup and numbers intact. Note idioms that do not carry over and anything a native speaker should check."),
}

# ---------------------------------------------------------------- plugins (command templates, run in the project sandbox)
P = lambda t="string", kind="text", desc="", default=None: {"type": t, "kind": kind, "description": desc, **({"default": default} if default is not None else {})}
PLUGINS = {
    "tree": {"name": "Project tree", "description": "List the files in the project.", "category": "read", "tool": "project_tree", "params": {},
             "command": "find . -not -path './.git/*' -not -path './node_modules/*' | sort | head -300"},
    "git_status": {"name": "Git status", "description": "Show changed files in the project's git repository.", "category": "read", "tool": "git_status", "params": {}, "command": "git status --short --branch"},
    "git_diff": {"name": "Git diff", "description": "Show uncommitted changes, optionally for one path.", "category": "read", "tool": "git_diff", "params": {"path": P("string", "path", "optional path", ".")},
                 "command": "git --no-pager diff -- {path}"},
    "grep_files": {"name": "Search files", "description": "Search file contents for a text pattern.", "category": "read", "tool": "search_files",
                   "params": {"pattern": P("string", "text", "text to find"), "path": P("string", "path", "folder or file", ".")},
                   "command": "grep -rnI --exclude-dir=.git --exclude-dir=node_modules -e {pattern} -- {path} | head -200"},
    "run_tests": {"name": "Run tests", "description": "Run the project's tests (npm test, pytest or unittest, whichever fits).", "category": "run", "tool": "run_tests", "params": {},
                  "command": "if [ -f package.json ]; then npm test --silent; elif command -v pytest >/dev/null 2>&1; then pytest -q; else python3 -m unittest discover; fi"},
    "run_python_file": {"name": "Run Python file", "description": "Run a Python file from the project.", "category": "run", "tool": "run_python", "params": {"file": P("string", "path", "a .py file")},
                        "command": "python3 {file}"},
    "json_query": {"name": "JSON query", "description": "Print a value from a JSON file using a dotted path such as items.0.name.", "category": "read", "tool": "json_query",
                   "params": {"file": P("string", "path", "a .json file"), "path": P("string", "text", "dotted path", "")},
                   "command": "python3 -c \"import json,sys; d=json.load(open(sys.argv[1])); [d:=(d[int(k)] if isinstance(d,list) else d[k]) for k in sys.argv[2].split('.') if k]; print(json.dumps(d,indent=2))\" {file} {path}"},
    "csv_summary": {"name": "CSV summary", "description": "Row count, columns and the first rows of a CSV file.", "category": "read", "tool": "csv_summary", "params": {"file": P("string", "path", "a .csv file")},
                    "command": "python3 -c \"import csv,sys; r=list(csv.reader(open(sys.argv[1], newline='')));print('rows:',len(r)-1);print('columns:',r[0] if r else []);[print(x) for x in r[1:6]]\" {file}"},
    "format_json": {"name": "Format JSON", "description": "Pretty-print a JSON file (also validates it).", "category": "read", "tool": "format_json", "params": {"file": P("string", "path", "a .json file")},
                    "command": "python3 -m json.tool {file}"},
    "http_headers": {"name": "HTTP headers", "description": "Fetch only the response headers of a URL.", "category": "network", "tool": "http_headers", "params": {"url": P("string", "text", "http(s) URL")},
                     "command": "curl -sSI --max-time 15 {url}"},
}

# ---------------------------------------------------------------- connector catalog
CONNECTORS = [
    {"id": "github", "name": "GitHub", "kind": "account", "does": "Import repositories into projects.", "setup": "Paste a personal access token under Connectors."},
    {"id": "huggingface", "name": "Hugging Face", "kind": "account", "does": "Import models and datasets; use Inference Providers.", "setup": "Paste a Hugging Face access token."},
    {"id": "openai", "name": "OpenAI (GPT)", "kind": "model-host", "does": "Use GPT models and embeddings as agents.", "setup": "Paste an OpenAI API key."},
    {"id": "anthropic", "name": "Anthropic (Claude)", "kind": "model-host", "does": "Use Claude models as agents.", "setup": "Paste an Anthropic API key."},
    {"id": "openclaw", "name": "OpenClaw", "kind": "agent", "does": "Use an OpenClaw agent through its Gateway or CLI.", "setup": "Enable the Gateway's chat completions endpoint and paste its token."},
    {"id": "claude-code", "name": "Claude Code", "kind": "agent", "does": "Record and govern Claude Code sessions.", "setup": "Run `agentlab connect claude-code`."},
    {"id": "kimi", "name": "Kimi Code", "kind": "agent", "does": "Record and govern Kimi Code sessions.", "setup": "Run `agentlab connect kimi`."},
    {"id": "ollama", "name": "Ollama", "kind": "model-host", "does": "Run local models on your machine.", "setup": "Install Ollama and pull a model."},
    {"id": "llama-server", "name": "llama.cpp", "kind": "model-host", "does": "Serve your own GGUF weights.", "setup": "Install llama.cpp so `llama-server` is on your PATH."},
    {"id": "cloudflare", "name": "Cloudflare", "kind": "deploy", "does": "Deploy to Pages; use Workers AI models.", "setup": "Add an API token and account id."},
    {"id": "webhooks", "name": "Webhooks", "kind": "notify", "does": "Slack, Discord or any service.", "setup": "Add a URL under Connectors."},
]


class LibraryError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def apply_role(spec, role_id):
    """Fill an agent spec from a role preset; anything you set explicitly in `spec` wins."""
    if not role_id:
        return dict(spec)
    r = ROLES.get(role_id)
    if not r:
        raise LibraryError(f"role must be one of {sorted(ROLES)} (or leave it empty)")
    merged = {k: v for k, v in r.items() if k in ("name", "avatar", "role", "instructions", "skills", "plugins", "mode", "authorizations")}
    merged.update({k: v for k, v in spec.items() if v not in (None, "")})
    return merged


def catalog():
    return {"roles": [{"id": k, **{x: v[x] for x in ("name", "avatar", "role", "skills", "plugins", "mode")}} for k, v in ROLES.items()],
            "skills": [{"id": k, "name": v[0], "description": v[1], "builtin": True} for k, v in SKILLS.items()] + custom_skills_list(),
            "plugins": [{"id": k, "name": v["name"], "description": v["description"], "category": v["category"], "tool": v["tool"], "builtin": True} for k, v in {**PLUGINS, **{k: {**v, "tool": v["tool"]} for k, v in SPECIAL_PLUGINS.items()}}.items()] + [
                {"id": p["id"], "name": p["name"], "description": p["description"], "category": p["category"], "tool": p["tool"], "builtin": False} for p in custom_plugins_list()]}


# ---- custom skills / plugins (stored per project of agentlab) ----
def _dir(root, kind):
    return data_dir(root) / kind


def custom_skills_list(root=None):
    return [] if root is None else [{"id": s["id"], "name": s["name"], "description": s["description"], "builtin": False} for s in _load_all(root, "skills")]


def custom_plugins_list(root=None):
    return [] if root is None else _load_all(root, "plugins")


def _load_all(root, kind):
    d = _dir(root, kind)
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return out


def get_skill(root, sid):
    if sid in SKILLS:
        n, d, i = SKILLS[sid]
        return {"id": sid, "name": n, "description": d, "instructions": i}
    for s in _load_all(root, "skills"):
        if s["id"] == sid:
            return s
    raise LibraryError(f"skill not found: {sid}", 404)


def save_skill(root, spec):
    name, instr = str(spec.get("name", "")).strip(), str(spec.get("instructions", "")).strip()
    if not name or len(name) > 60 or not instr or len(instr) > 8000:
        raise LibraryError("a skill needs a name (max 60) and instructions (max 8,000 characters)")
    sid = "c-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] + "-" + uuid.uuid4().hex[:4]
    rec = {"id": sid, "name": name, "description": str(spec.get("description", ""))[:200], "instructions": instr}
    _dir(root, "skills").mkdir(parents=True, exist_ok=True)
    (_dir(root, "skills") / f"{sid}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
BUILTIN_TOOLS = {"list_files", "read_file", "write_file", "delete_file", "run_command", "web_fetch", "update_progress", "finish", "remember", "recall", "search_knowledge", "web_scrape"}


def validate_plugin(spec):
    tool = str(spec.get("tool", ""))
    if not TOOL_NAME.match(tool) or tool in BUILTIN_TOOLS or tool in {p["tool"] for p in PLUGINS.values()}:
        raise LibraryError("tool must be a new lowercase name like my_tool")
    cat = spec.get("category")
    if cat not in ("read", "run", "network"):
        raise LibraryError("category must be read, run or network")
    params = spec.get("params", {})
    if not isinstance(params, dict) or len(params) > 8 or not all(PARAM_NAME.match(k) and isinstance(v, dict) and v.get("type") in ("string", "integer", "boolean") for k, v in params.items()):
        raise LibraryError("params must map up to 8 names to {type: string|integer|boolean}")
    command = str(spec.get("command", "")).strip()
    used = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", command))
    if not command or len(command) > 2000 or used - set(params):
        raise LibraryError("command is required and may only use {placeholders} for declared params")
    if danger.classify(command.replace("{", "x").replace("}", "x"))["level"] == "block":
        raise LibraryError("that command template is blocked for safety")
    return {"name": str(spec.get("name") or tool)[:60], "description": str(spec.get("description", ""))[:300], "category": cat, "tool": tool, "command": command,
            "params": {k: {"type": v["type"], "kind": v.get("kind", "text"), "description": str(v.get("description", ""))[:120], **({"default": v["default"]} if "default" in v else {})} for k, v in params.items()}}


def save_plugin(root, spec):
    v = validate_plugin(spec)
    pid = "c-" + v["tool"]
    if any(p["id"] == pid for p in _load_all(root, "plugins")):
        raise LibraryError("a custom plugin with that tool name already exists")
    rec = {"id": pid, **v}
    _dir(root, "plugins").mkdir(parents=True, exist_ok=True)
    (_dir(root, "plugins") / f"{pid}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def delete_custom(root, kind, item_id):
    p = _dir(root, kind) / f"{item_id}.json"
    if not item_id.startswith("c-") or not p.exists():
        raise LibraryError("not found", 404)
    p.unlink()


def get_plugin(root, pid):
    if pid in PLUGINS:
        return {"id": pid, **PLUGINS[pid]}
    for p in _load_all(root, "plugins"):
        if p["id"] == pid:
            return p
    raise LibraryError(f"plugin not found: {pid}", 404)


SPECIAL_PLUGINS = {"widgets": {"name": "Widgets", "description": "Create interactive widgets (charts, tables, forms, calculators ...) and save them to the widget library.", "category": "write", "tool": "create_widget",
                               "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "spec": {"type": "string", "description": "JSON for a declarative widget, or a full HTML page for type html"},
                                                                               "type": {"type": "string", "description": "chart, table, stats, buttons, form, progress, checklist, markdown, code or html"}}, "required": ["type", "spec"]}}}


def plugin_tool_specs(root, plugin_ids):
    """Tool schemas for the plugins an agent has enabled."""
    out = []
    for pid in plugin_ids:
        if pid in SPECIAL_PLUGINS:
            sp = SPECIAL_PLUGINS[pid]
            out.append({"name": sp["tool"], "description": sp["description"], "parameters": sp["parameters"], "_plugin": pid, "_category": sp["category"], "_special": pid})
            continue
        try:
            p = get_plugin(root, pid)
        except LibraryError:
            continue
        props = {k: {"type": v["type"], "description": v.get("description", "")} for k, v in p["params"].items()}
        req = [k for k, v in p["params"].items() if "default" not in v]
        out.append({"name": p["tool"], "description": p["description"], "parameters": {"type": "object", "properties": props, "required": req}, "_plugin": p["id"], "_category": p["category"]})
    return out


def render_plugin_command(root, project_id, plugin, args):
    """Substitute shell-quoted parameters into the command template. Path parameters must stay inside the project."""
    values = {}
    for name, spec in plugin["params"].items():
        v = args.get(name, spec.get("default"))
        if v is None:
            raise LibraryError(f"missing parameter: {name}")
        if spec["type"] == "integer":
            v = int(v)
        elif spec["type"] == "boolean":
            v = "true" if v in (True, "true", 1) else "false"
        else:
            v = str(v)
            if len(v) > 2000 or "\x00" in v:
                raise LibraryError(f"parameter {name} is too long or invalid")
            if spec.get("kind") == "path" and v not in (".", ""):
                projects.resolve(root, project_id, v)  # no traversal, no symlinks
        values[name] = shlex.quote(str(v)) if v != "" else "''"
    return re.sub(r"\{([a-z][a-z0-9_]*)\}", lambda m: values[m.group(1)], plugin["command"])


def run_plugin(root, project_id, plugin, args, *, network=False, cancel=None, timeout=60):
    command = render_plugin_command(root, project_id, plugin, args)
    cfg = project_env.get(root, project_id)
    if plugin["category"] == "network" and cfg["network"] == "off":
        raise LibraryError("this project's environment is offline")
    u = projects.usage(root, project_id)
    res = envs.run_command(projects.workspace(root, project_id), command, network=plugin["category"] == "network", env=project_env.command_env(root, project_id),
                           timeout=min(timeout, cfg["timeout"]), free_bytes=u["free_bytes"], home=projects.home(root, project_id), cancel=cancel)
    return f"exit {res['exit_code']}\n{res['output']}" + ("\n[killed: timed out]" if res["timed_out"] else "")


def skills_prompt(root, skill_ids):
    parts = []
    for sid in skill_ids:
        try:
            s = get_skill(root, sid)
        except LibraryError:
            continue
        parts.append(f"## Skill: {s['name']}\n{s['instructions']}")
    return "\n\n".join(parts)


def connector_status(root):
    """The catalog with live status: connected (credentials or hooks in place) and detected (installed on this machine)."""
    import urllib.request
    from . import accounts, agents, connect, notify, runners, vault
    acc = accounts.status(root)
    hooks = {c["id"]: c for c in connect.status(root)}
    avail = runners.available()

    def ollama_up():
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=0.6):
                return True
        except Exception:
            return False
    ocl = any(str(a.get("source", {}).get("preset", "")).startswith("openclaw") for a in agents.list_agents(root))
    conn = {"github": acc["github"]["connected"], "huggingface": acc["huggingface"]["connected"], "openai": bool(vault.get_secret(root, "openai")),
            "anthropic": bool(vault.get_secret(root, "anthropic")), "openclaw": ocl or bool(vault.get_secret(root, "openclaw_token")),
            "claude-code": hooks["claude-code"]["connected"], "kimi": hooks["kimi"]["connected"], "ollama": bool(avail["ollama"]) and ollama_up(),
            "llama-server": bool(avail["llama-server"]), "cloudflare": bool(vault.get_secret(root, "cloudflare_token") and vault.get_secret(root, "cloudflare_account")),
            "webhooks": notify.has_webhooks(root)}
    det = {"claude-code": hooks["claude-code"]["detected"], "kimi": hooks["kimi"]["detected"], "ollama": bool(avail["ollama"]), "llama-server": bool(avail["llama-server"])}
    return [{**c, "connected": conn[c["id"]], "detected": det.get(c["id"], conn[c["id"]])} for c in CONNECTORS]
