"""Agent jobs: an agent works toward a goal inside a project workspace, in the background.

Every tool call goes through the agent's control mode / authorizations (allow, ask, deny). Asking pauses the job
until you approve or deny it. Jobs have step, token and daily budgets, a progress bar and metrics, and are
recorded as runs so they can be replayed in Activity.
"""

import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import agents, envs, llm, memory, projects, usage
from .hooks import data_dir
from .recorder import append_event, load_run

CATEGORY = {"list_files": "read", "read_file": "read", "write_file": "write", "delete_file": "write", "run_command": "run", "web_fetch": "network"}
STATUSES = ("queued", "running", "waiting_approval", "succeeded", "failed", "cancelled", "stopped")
FINAL = ("succeeded", "failed", "cancelled", "stopped")
MAX_RESULT = 8000
ALLOW_PRIVATE_FETCH = False  # tests only
_RANK = {"allow": 0, "ask": 1, "deny": 2}


class JobError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def tool_specs(agent):
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
    s = {"type": "string"}
    specs = [
        {"name": "list_files", "description": "List files in the workspace (path '.' lists everything).", "parameters": obj({"path": s})},
        {"name": "read_file", "description": "Read a text file from the workspace.", "parameters": obj({"path": s}, ["path"])},
        {"name": "write_file", "description": "Create or overwrite a text file in the workspace (or append).", "parameters": obj({"path": s, "content": s, "append": {"type": "boolean"}}, ["path", "content"])},
        {"name": "delete_file", "description": "Delete a file or folder in the workspace.", "parameters": obj({"path": s}, ["path"])},
        {"name": "run_command", "description": "Run a shell command in the workspace. Network is off unless network=true (needs permission).",
         "parameters": obj({"command": s, "network": {"type": "boolean"}, "timeout": {"type": "integer"}}, ["command"])},
        {"name": "web_fetch", "description": "Fetch a public web page as text (GET).", "parameters": obj({"url": s}, ["url"])},
        {"name": "update_progress", "description": "Report how far along you are (0-100) with a short note.", "parameters": obj({"percent": {"type": "integer"}, "note": s}, ["percent"])},
        {"name": "finish", "description": "Call when the goal is done, with a short summary of the result.", "parameters": obj({"summary": s}, ["summary"])},
    ]
    if agent.get("memory_enabled"):
        specs += [{"name": "remember", "description": "Save a short fact you want to remember in future work.", "parameters": obj({"fact": s}, ["fact"])},
                  {"name": "recall", "description": "Search what you remember.", "parameters": obj({"query": s}, ["query"])}]
    return specs


def _public_ip(host):
    if ALLOW_PRIVATE_FETCH:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def safe_fetch(url, max_bytes=100_000, hops=3, timeout=15):
    """GET a public http(s) page. Refuses private/loopback addresses, including via redirects."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(hops + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("only http(s) URLs are allowed")
        if not _public_ip(u.hostname):
            raise ValueError("that address is private or unreachable; only public web pages can be fetched")
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "agentlab-agent"}), timeout=timeout) as r:
                return r.read(max_bytes + 1)[:max_bytes].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urljoin(url, e.headers["Location"])
                continue
            raise ValueError(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError) as e:
            raise ValueError(f"could not fetch: {getattr(e, 'reason', e)}")
    raise ValueError("too many redirects")


class Runtime:
    def __init__(self, root, on_event=None, max_concurrent=4, approval_timeout=600, after_job=None):
        self.root = Path(root).resolve()
        self.on_event = on_event          # (event_name, text, data) for webhooks
        self.after_job = after_job        # (job, agent) hook: diary, evolution
        self.approval_timeout = approval_timeout
        self._lock = threading.RLock()
        self._cancel, self._wait, self._always = {}, {}, {}
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self.dir = data_dir(self.root) / "jobs"
        for j in self.list():
            if j["status"] in ("queued", "running", "waiting_approval"):
                j.update(status="failed", error="server stopped while the job was running", ended=time.time(), pending=None)
                self._save(j)

    # ---- storage ----
    def _save(self, job):
        with self._lock:
            d = self.dir / job["id"]
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "job.json.tmp"
            tmp.write_text(json.dumps(job), encoding="utf-8")
            tmp.replace(d / "job.json")

    def get(self, job_id, events=0):
        p = self.dir / job_id / "job.json"
        if not job_id.isalnum() or not p.exists():
            raise JobError("job not found", 404)
        job = json.loads(p.read_text(encoding="utf-8"))
        if events:
            run = data_dir(self.root) / "runs" / f"job-{job_id}.jsonl"
            job["events"] = load_run(run)[-events:] if run.exists() else []
        return job

    def list(self, project_id=None, agent_id=None, status=None, limit=100):
        out = []
        for p in self.dir.glob("*/job.json") if self.dir.exists() else []:
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if (project_id in (None, j["project"])) and (agent_id in (None, j["agent"])) and (status in (None, j["status"])):
                out.append(j)
        return sorted(out, key=lambda j: j["created"], reverse=True)[:limit]

    # ---- control ----
    def start(self, project_id, agent_id, prompt, *, autonomous=False, overrides=None, schedule=None):
        prompt = str(prompt or "").strip()
        if not prompt or len(prompt) > 20000:
            raise JobError("a prompt is required (max 20,000 characters)")
        project = projects.get(self.root, project_id)
        agent = agents.get(self.root, agent_id)
        job = {"id": uuid.uuid4().hex[:10], "project": project_id, "project_name": project["name"], "agent": agent_id, "agent_name": agent["name"],
               "prompt": prompt, "status": "queued", "created": time.time(), "started": None, "ended": None, "autonomous": bool(autonomous),
               "overrides": {k: v for k, v in (overrides or {}).items() if k in agents.CATEGORIES and v in ("allow", "ask", "deny")}, "schedule": schedule,
               "progress": {"percent": 0, "note": "", "step": 0, "max_steps": agent["limits"]["max_steps"]},
               "metrics": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0, "tool_calls": 0, "tool_errors": 0, "approvals_asked": 0,
                           "approvals_denied": 0, "files_written": 0, "cost": 0.0, "duration_s": 0.0},
               "summary": "", "error": None, "stop_reason": None, "pending": None}
        self._save(job)
        self._cancel[job["id"]] = threading.Event()
        threading.Thread(target=self._run, args=(job["id"],), daemon=True).start()
        return job

    def cancel(self, job_id):
        job = self.get(job_id)
        if job["status"] in FINAL:
            raise JobError("job already finished")
        self._cancel.setdefault(job_id, threading.Event()).set()
        w = self._wait.get(job_id)
        if w:
            w["event"].set()
        return job

    def decide(self, job_id, approval_id, allow, always=False):
        w = self._wait.get(job_id)
        if not w or w["id"] != approval_id:
            raise JobError("no such pending approval", 404)
        w["decision"], w["always"] = bool(allow), bool(always)
        w["event"].set()

    # ---- run loop ----
    def _event(self, job, kind, content, **fields):
        append_event(data_dir(self.root) / "runs" / f"job-{job['id']}.jsonl", kind, content, meta={"source": "agent", "agent": job["agent"], "job": job["id"], "project": job["project"]}, **fields)

    def _notify(self, name, text, data):
        if self.on_event:
            try:
                self.on_event(name, text, data)
            except Exception:
                pass

    def _finish(self, job, status, **kw):
        with self._lock:
            job.update(status=status, ended=time.time(), pending=None, **kw)
            job["metrics"]["duration_s"] = round(job["ended"] - (job["started"] or job["created"]), 1)
            if status == "succeeded":
                job["progress"]["percent"] = 100
            self._save(job)
        self._event(job, "output" if status == "succeeded" else "error", f"{status}" + (f": {job['summary'] or job['error'] or job['stop_reason'] or ''}"))
        self._notify("job_finished", f"agent {job['agent_name']} {status}: {(job['summary'] or job['error'] or job['stop_reason'] or '')[:200]}", {"job": job["id"], "status": status})

    def _run(self, job_id):
        job = self.get(job_id)
        cancel = self._cancel[job_id]
        agent = None
        with self._sem:
            if cancel.is_set():
                return self._finish(job, "cancelled")
            try:
                agent = agents.get(self.root, job["agent"])
                job.update(status="running", started=time.time())
                self._save(job)
                self._event(job, "prompt", job["prompt"])
                self._loop(job, agent, cancel)
            except llm.LLMError as e:
                self._finish(job, "failed", error=str(e)[:500])
            except Exception as e:  # never leave a job stuck
                self._finish(job, "failed", error=f"{type(e).__name__}: {e}"[:500])
            finally:
                self._cancel.pop(job_id, None)
                self._wait.pop(job_id, None)
                self._always.pop(job_id, None)
        if self.after_job and agent:
            try:
                self.after_job(self.get(job_id), agent)
            except Exception:
                pass

    def _compact(self, messages):
        if sum(len(m.get("content") or "") for m in messages) < 300_000:
            return
        for m in messages[:-8]:
            if m["role"] == "tool" and len(m["content"]) > 200:
                m["content"] = "[earlier output trimmed to save space]"

    def _loop(self, job, agent, cancel):
        lim, provider = agent["limits"], agent["provider"]
        project = projects.get(self.root, job["project"])
        mem = memory.as_prompt(self.root, agent["id"], job["prompt"]) if agent["memory_enabled"] else ""
        system = agents.system_prompt(agent, project["name"], mem)
        tools = tool_specs(agent)
        messages = [{"role": "user", "content": job["prompt"]}]
        explicit = False
        for step in range(1, lim["max_steps"] + 1):
            if cancel.is_set():
                return self._finish(job, "cancelled")
            used = job["metrics"]["input_tokens"] + job["metrics"]["output_tokens"]
            if used >= lim["token_budget"]:
                return self._finish(job, "stopped", stop_reason="token budget reached")
            if lim["daily_token_cap"] and usage.tokens_today(self.root, agent["id"]) >= lim["daily_token_cap"]:
                return self._finish(job, "stopped", stop_reason="daily token cap reached")
            job["progress"].update(step=step, max_steps=lim["max_steps"])
            if not explicit:
                job["progress"]["percent"] = min(90, int(100 * (step - 1) / lim["max_steps"]))
            self._save(job)
            self._compact(messages)
            t0 = time.time()
            try:
                reply = llm.chat(provider, system, messages, tools, max_tokens=lim["max_output_tokens"], temperature=lim["temperature"], project=self.root)
            except llm.LLMError:
                usage.record(self.root, agent=agent["id"], provider=provider["type"], model=provider["model"], input_tokens=0, output_tokens=0, ms=(time.time() - t0) * 1000, job=job["id"], project_id=job["project"], ok=False)
                raise
            cost = llm.cost(provider, reply["usage"])
            usage.record(self.root, agent=agent["id"], provider=provider["type"], model=reply.get("model") or provider["model"], input_tokens=reply["usage"]["input"], output_tokens=reply["usage"]["output"], cost=cost, ms=(time.time() - t0) * 1000, job=job["id"], project_id=job["project"])
            m = job["metrics"]
            m["input_tokens"] += reply["usage"]["input"]
            m["output_tokens"] += reply["usage"]["output"]
            m["llm_calls"] += 1
            m["cost"] = round(m["cost"] + cost, 6)
            messages.append({"role": "assistant", "content": reply["text"], "tool_calls": reply["tool_calls"]})
            if reply["text"]:
                self._event(job, "output", reply["text"], tool="assistant")
            if not reply["tool_calls"]:
                if reply["stop"] == "length":
                    messages.append({"role": "user", "content": "Your reply was cut off by the output limit. Continue, in smaller steps."})
                    continue
                return self._finish(job, "succeeded", summary=reply["text"] or "(no summary)")
            for call in reply["tool_calls"]:
                if cancel.is_set():
                    return self._finish(job, "cancelled")
                result, finished = self._tool(job, agent, call, cancel)
                if call["name"] == "update_progress":
                    explicit = True
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": result})
                if finished:
                    return self._finish(job, "succeeded", summary=finished)
        self._finish(job, "stopped", stop_reason=f"step limit reached ({lim['max_steps']} steps)")

    # ---- tools ----
    def _categories(self, call):
        cats = [CATEGORY[call["name"]]] if call["name"] in CATEGORY else []
        if call["name"] == "run_command" and call["arguments"].get("network"):
            cats.append("network")
        return cats

    def _authorize(self, job, agent, call, cancel):
        """Returns (allowed, note). May block waiting for the user."""
        verdict, cats = "allow", self._categories(call)
        for c in cats:
            if c in self._always.get(job["id"], ()):
                continue
            d = agents.effective(agent, c, job["overrides"])
            if _RANK[d] > _RANK[verdict]:
                verdict = d
        if verdict == "allow":
            return True, ""
        if verdict == "deny":
            return False, f"not permitted: {'/'.join(cats)} actions are denied for this agent"
        approval = {"id": uuid.uuid4().hex[:8], "tool": call["name"], "category": "/".join(cats), "arguments": call["arguments"], "ts": time.time()}
        w = {"id": approval["id"], "event": threading.Event(), "decision": None, "always": False}
        self._wait[job["id"]] = w
        with self._lock:
            job["status"], job["pending"] = "waiting_approval", approval
            job["metrics"]["approvals_asked"] += 1
            self._save(job)
        self._event(job, "decision", f"ASKED {call['name']}: {json.dumps(call['arguments'])[:300]}", tool=call["name"])
        self._notify("approval_needed", f"agent {job['agent_name']} is waiting for approval to use {call['name']}", {"job": job["id"], "tool": call["name"]})
        w["event"].wait(self.approval_timeout)
        self._wait.pop(job["id"], None)
        allowed = bool(w["decision"]) and not cancel.is_set()
        with self._lock:
            job["status"], job["pending"] = "running", None
            if not allowed:
                job["metrics"]["approvals_denied"] += 1
            self._save(job)
        if w["always"] and allowed:
            self._always.setdefault(job["id"], set()).update(cats)
        note = "" if allowed else ("timed out waiting for approval" if w["decision"] is None and not cancel.is_set() else "denied by the user")
        self._event(job, "decision", f"{'ALLOWED' if allowed else 'DENIED'} {call['name']}" + (f" ({note})" if note else ""), tool=call["name"])
        return allowed, note

    def _tool(self, job, agent, call, cancel):
        """Run one tool call. Returns (result_text, finish_summary_or_None)."""
        name, args = call["name"], call["arguments"]
        job["metrics"]["tool_calls"] += 1
        self._event(job, "tool_call", f"{name} {json.dumps(args, ensure_ascii=False)[:300]}", tool=name)
        try:
            if name not in tool_names(agent):
                raise ValueError(f"unknown tool: {name}")
            if "_invalid_json" in args:
                raise ValueError("your arguments were not valid JSON; try again")
            allowed, note = self._authorize(job, agent, call, cancel)
            if not allowed:
                raise PermissionError(note)
            out, finished = self._execute(job, agent, name, args, cancel)
        except (ValueError, KeyError, TypeError, projects.ProjectError, envs.TaskError, PermissionError) as e:
            job["metrics"]["tool_errors"] += 1
            out, finished = f"ERROR: {e}", None
        self._save(job)
        out = out if len(out) <= MAX_RESULT else out[:MAX_RESULT] + f"\n[truncated {len(out) - MAX_RESULT} characters]"
        self._event(job, "tool_result", out[:2000], tool=name)
        return out, finished

    def _execute(self, job, agent, name, a, cancel=None):
        root, pid = self.root, job["project"]
        lim = agent["limits"]
        if name == "list_files":
            path = str(a.get("path", ".") or ".")
            if path in (".", "./", ""):
                files = projects.tree(root, pid, 300)
                return "\n".join(files) or "(workspace is empty)", None
            return "\n".join(("📁 " if e["dir"] else "") + e["name"] + ("" if e["dir"] else f" ({e['size']} bytes)") for e in projects.list_dir(root, pid, path)) or "(empty)", None
        if name == "read_file":
            r = projects.read_file(root, pid, str(a["path"]), 20000)
            return ("(binary file)" if r["binary"] else r["content"] + ("\n[truncated]" if r["truncated"] else "")), None
        if name == "write_file":
            r = projects.write_file(root, pid, str(a["path"]), str(a["content"]), bool(a.get("append")))
            job["metrics"]["files_written"] += 1
            self._event(job, "file_edit", str(a["path"]), tool=name)
            return f"wrote {r['bytes']} bytes to {a['path']}", None
        if name == "delete_file":
            projects.delete_path(root, pid, str(a["path"]))
            return f"deleted {a['path']}", None
        if name == "run_command":
            u = projects.usage(root, pid)
            res = envs.run_command(projects.workspace(root, pid), str(a["command"]), network=bool(a.get("network")), timeout=min(int(a.get("timeout") or lim["step_timeout"]), lim["step_timeout"]),
                                   free_bytes=u["free_bytes"], home=projects.home(root, pid), cancel=cancel)
            note = "\n[killed: timed out]" if res["timed_out"] else ("\n[output truncated]" if res["truncated"] else "")
            over = "\n[warning: the project is over its space quota; delete files]" if projects.usage(root, pid)["free_bytes"] == 0 else ""
            return f"exit {res['exit_code']}\n{res['output']}{note}{over}", None
        if name == "web_fetch":
            return safe_fetch(str(a["url"])), None
        if name == "update_progress":
            pct = max(0, min(100, int(a["percent"])))
            job["progress"].update(percent=pct, note=str(a.get("note", ""))[:200])
            return "progress noted", None
        if name == "finish":
            return "finished", str(a["summary"])[:4000]
        if name == "remember":
            memory.add(root, agent["id"], a["fact"])
            return "remembered", None
        if name == "recall":
            return memory.as_prompt(root, agent["id"], a["query"], 8) or "(nothing relevant)", None
        raise ValueError(f"unknown tool: {name}")


def tool_names(agent):
    return {t["name"] for t in tool_specs(agent)}
