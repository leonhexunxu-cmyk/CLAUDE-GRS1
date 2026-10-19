"""REST routes for the agent platform (/api/lab/...). One table drives the server, the OpenAPI file and the clients.

Every route is (method, path, name, summary, body fields, handler). Handlers get a Ctx (query string, unlock
tokens for private agents, the raw request stream for uploads) plus the body and path parameters.
"""

import re
import threading
import time

from . import (accounts, agent_stats, agentise, agents, analyze, batches, builders, commands, comparator, deploy, diary, docbuilder, dream, evals, evolution,
               hardware, importers, jobs, knowledge, library, llm, memory, model_tests, models, notifications, notify, packager, playground, privacy,
               project_env, projects, rooms, runners, scheduler, suggestions, terminal, tokenizers, training, uploads, usage, vault, widgets)
from . import embeddings

EVENT_KIND = {"approval_needed": ("approval", "warn"), "job_finished": ("job", "info"), "import_finished": ("import", "info"), "train_finished": ("train", "success"),
              "schedule_paused": ("schedule", "warn"), "dream_finished": ("system", "info"), "task_finished": ("task", "info")}


class Ctx:
    def __init__(self, query=None, unlock=(), stream=None, length=0):
        self.query, self.unlock, self.stream, self.length = query or {}, list(unlock), stream, length

    def q(self, name, default=None):
        return self.query.get(name, default)


class Platform:
    def __init__(self, root, background=True):
        self.root = root
        self.runtime = jobs.Runtime(root, on_event=self.event, after_job=self._after_job, after_feedback=lambda job: diary.learn_from_feedback(root, job))
        self.scheduler = scheduler.Scheduler(root, self.runtime, on_event=self.event)
        self.importer = importers.Importer(root)
        self.dreamer = dream.Dreamer(root, on_event=self.event)
        if background:
            self.scheduler.start()
            self.dreamer.start()

    # ---- events: webhooks + the in-app notification feed ----
    def event(self, name, text, data):
        try:
            notify.fire(self.root, name, text, data)
        except Exception:
            pass
        kind, level = EVENT_KIND.get(name, ("system", "info"))
        if name == "job_finished" and data.get("status") == "failed":
            level = "error"
        link = {"page": "jobs", "id": data["job"]} if data.get("job") else {"page": "schedules", "id": data["schedule"]} if data.get("schedule") else \
               {"page": "training", "id": data["run"]} if data.get("run") else {"page": "agents", "id": data["agent"]} if data.get("agent") else {}
        try:
            notifications.emit(self.root, kind, text[:140], "", level, link, dedupe_key=f"{name}:{data.get('job') or data.get('approval') or ''}" if name == "approval_needed" else None)
        except Exception:
            pass

    def _after_job(self, job, agent):
        if agent.get("diary_enabled"):
            diary.reflect(self.root, job, agent)
        evolution.maybe_auto_propose(self.root, agent)

    def stop(self):
        self.scheduler.stop()
        terminal.close_all()

    # ---- helpers ----
    def agent(self, ctx, aid):
        privacy.require(self.root, aid, ctx.unlock)
        return agents.get(self.root, aid)

    def _view(self, ctx, a):
        if privacy.is_private(self.root, a["id"]) and not privacy.unlocked(self.root, a["id"], ctx.unlock):
            return privacy.redact_agent(a)
        return {**agents.public_view(a), "private": privacy.is_private(self.root, a["id"])}

    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def routes(self):
        R, root, rt = self, getattr(self, "root", None), getattr(self, "runtime", None)
        need = lambda ctx, aid: R.agent(ctx, aid)

        def start_job(ctx, b):
            need(ctx, b.get("agent"))
            dl = b.get("deadline_minutes")
            deadline = (time.time() + max(1, min(float(dl), 7 * 24 * 60)) * 60) if dl else None
            return rt.start(b.get("project"), b.get("agent"), b.get("prompt"), autonomous=bool(b.get("autonomous")), overrides=b.get("overrides"), step_mode=bool(b.get("step_mode")), deadline=deadline)

        def playground_chat(ctx, b):
            if b.get("agent_id"):
                need(ctx, b["agent_id"])
            return playground.chat(root, b)

        def playground_compare(ctx, b):
            for t in b.get("targets") or []:
                if isinstance(t, dict) and t.get("agent_id"):
                    need(ctx, t["agent_id"])
            return playground.compare(root, b)

        def compare_run(ctx, b):
            for t in b.get("targets") or []:
                if isinstance(t, dict) and t.get("agent_id"):
                    need(ctx, t["agent_id"])
            return comparator.run(root, b)

        def compare_judge(ctx, b, cid):
            p = b.get("provider") or {}
            if p.get("preset"):
                p = {**llm.PRESETS.get(p["preset"], {}), **{k: v for k, v in p.items() if k != "preset"}}
            return comparator.judge(root, comparator.get(root, cid), p)

        def room_create(ctx, b):
            for a in b.get("agents") or []:
                need(ctx, a)
            return rooms.create(root, b)

        def room_run(ctx, b, rid):
            return rooms.run(root, rid, b.get("turns"), on_done=lambda r: R.event("job_finished", f"room “{r['name']}” finished", {"room": rid}) if r["status"] == "done" else None)

        def agent_create(ctx, b):
            spec = dict(b)
            if spec.get("role_preset"):
                spec = library.apply_role(spec, spec.pop("role_preset"))
            return agents.public_view(agents.create(root, spec))

        def agentise_api(ctx, b):
            spec = dict(b)
            if spec.get("role_preset"):
                spec = library.apply_role(spec, spec.pop("role_preset"))
            return agentise.from_api(root, spec)

        def agentise_weights(ctx, b):
            spec = dict(b)
            if spec.get("role_preset"):
                spec = library.apply_role(spec, spec.pop("role_preset"))
            return agentise.from_weights(root, spec)

        def import_start(ctx, b):
            if b.get("source") == "github":
                return R.importer.start_github(b.get("project"), b.get("repo", ""), b.get("ref"), b.get("dest"))
            if b.get("source") == "huggingface":
                return R.importer.start_hf(b.get("project"), b.get("repo_type", "model"), b.get("repo_id", ""), b.get("revision") or "main", b.get("files"), b.get("patterns"), b.get("dest"), b.get("max_bytes"))
            raise ValueError("source must be github or huggingface")

        def upload(ctx, b, pid):
            rel = ctx.q("path")
            if not rel:
                raise ValueError("add ?path=folder/file.ext")
            return uploads.receive(root, pid, rel, ctx.stream, ctx.length, ctx.q("id"))

        def project_create(ctx, b):
            return projects.create(root, b.get("name"), b.get("description", ""), b.get("quota_mb") or projects.DEFAULT_QUOTA_MB, b.get("kind", "blank"))

        def project_update(ctx, b, pid):
            return projects.update(root, pid, **{k: b[k] for k in ("name", "description", "quota_mb") if k in b})

        def project_delete(ctx, b, pid):
            projects.delete(root, pid)
            return {"deleted": True}

        def write_file(ctx, b, pid):
            return projects.write_file(root, pid, b.get("path", ""), str(b.get("content", "")), bool(b.get("append")))

        def deploy_cf(ctx, b, pid):
            return deploy.deploy_cloudflare(root, pid, b.get("folder", ""), b.get("name"))

        def kb_scrape(ctx, b, kid):
            return knowledge.scrape(root, kid, b.get("url", ""), b.get("max_pages", 1))

        def kb_add(ctx, b, kid):
            if b.get("project") and b.get("path"):
                return knowledge.add_project_file(root, kid, b["project"], b["path"])
            return knowledge.add_text(root, kid, b.get("text", ""), b.get("title", ""))

        def secret_set(ctx, b):
            vault.set_secret(root, b.get("name", ""), b.get("value", ""))
            return {"saved": True, "secrets": vault.list_secrets(root)}

        def secret_delete(ctx, b, name):
            vault.delete_secret(root, name)
            return {"deleted": True}

        def model_list(ctx, b):
            p = b.get("provider") or {}
            if p.get("preset"):
                p = {**llm.PRESETS.get(p["preset"], {}), **{k: v for k, v in p.items() if k != "preset"}}
            return {"models": accounts.list_models(root, p)}

        def privacy_set(ctx, b, aid):
            agents.get(root, aid)
            privacy.set_password(root, aid, b.get("password"), b.get("current"))
            return {"private": True}

        def privacy_remove(ctx, b, aid):
            privacy.remove_password(root, aid, b.get("password"))
            return {"private": False}

        def tokens(ctx, b):
            text = str(b.get("text", ""))
            return {"tokens": tokenizers.count(text, b.get("tokenizer"), root), "characters": len(text), "tokenizer": tokenizers.describe(root, b.get("tokenizer"))["label"]}

        def run_command_line(ctx, b, pid):
            return commands.run_shell(root, pid, b.get("line", ""))

        def bg_dream(ctx, b, aid):
            need(ctx, aid)
            e = dream.dream(root, aid, force=True)
            return {"entry": e}

        S, I, B, O, A = "string", "integer", "boolean", "object", "array"
        P = lambda *names: [(n, S, True) for n in names]
        return [
            # agents
            ("GET", "/api/lab/agents", "lab_list_agents", "List your agents", [], lambda c, b: {"agents": [R._view(c, a) for a in agents.list_agents(root)]}),
            ("POST", "/api/lab/agents", "lab_create_agent", "Create an agent (optionally from a role preset)", [("name", S, True), ("provider", O, True), ("instructions", S, False), ("mode", S, False), ("role_preset", S, False), ("teammates", A, False)], agent_create),
            ("GET", "/api/lab/agents/{agent_id}", "lab_get_agent", "Read an agent", [], lambda c, b, a: (lambda rec: {**agents.public_view(rec), "history": [{k: h[k] for k in ("version", "reason", "ts")} for h in rec.get("history", [])], "private": privacy.is_private(root, a)})(need(c, a))),
            ("POST", "/api/lab/agents/{agent_id}", "lab_update_agent", "Change an agent", [("changes", O, False)], lambda c, b, a: (need(c, a), agents.public_view(agents.update(root, a, b)))[1]),
            ("POST", "/api/lab/agents/{agent_id}/delete", "lab_delete_agent", "Delete an agent", [], lambda c, b, a: (need(c, a), agents.delete(root, a), {"deleted": True})[2]),
            ("GET", "/api/lab/agents/{agent_id}/stats", "lab_agent_stats", "Agent analytics: jobs, success, tokens, health", [], lambda c, b, a: (need(c, a), agent_stats.stats(root, a))[1]),
            ("GET", "/api/lab/agents/{agent_id}/memory", "lab_agent_memory", "What the agent remembers", [], lambda c, b, a: (need(c, a), {"facts": memory.list_facts(root, a)})[1]),
            ("POST", "/api/lab/agents/{agent_id}/memory", "lab_add_memory", "Teach the agent a fact", P("fact"), lambda c, b, a: (need(c, a), memory.add(root, a, b.get("fact", ""), source="user"))[1]),
            ("POST", "/api/lab/agents/{agent_id}/memory/{mem_id}/delete", "lab_forget", "Forget a fact", [], lambda c, b, a, m: (need(c, a), memory.forget(root, a, m), {"deleted": True})[2]),
            ("GET", "/api/lab/agents/{agent_id}/diary", "lab_agent_diary", "The agent's diary", [], lambda c, b, a: (need(c, a), {"entries": diary.entries(root, a)})[1]),
            ("POST", "/api/lab/agents/{agent_id}/diary", "lab_diary_note", "Add a note to the diary", P("text"), lambda c, b, a: (need(c, a), diary.add_note(root, a, b.get("text", ""), b.get("title", "")))[1]),
            ("GET", "/api/lab/agents/{agent_id}/proposals", "lab_proposals", "Improvement proposals", [], lambda c, b, a: (need(c, a), {"proposals": evolution.proposals(root, a)})[1]),
            ("POST", "/api/lab/agents/{agent_id}/proposals", "lab_propose", "Ask for an improvement proposal", [], lambda c, b, a: (need(c, a), evolution.propose(root, a))[1]),
            ("POST", "/api/lab/agents/{agent_id}/proposals/{prop_id}/accept", "lab_accept", "Accept a proposal", [], lambda c, b, a, p: (need(c, a), agents.public_view(evolution.accept(root, a, p)))[1]),
            ("POST", "/api/lab/agents/{agent_id}/proposals/{prop_id}/reject", "lab_reject", "Reject a proposal", [], lambda c, b, a, p: (need(c, a), evolution.reject(root, a, p))[1]),
            ("POST", "/api/lab/agents/{agent_id}/rollback", "lab_rollback", "Go back to an earlier version", [("version", I, True)], lambda c, b, a: (need(c, a), agents.public_view(evolution.rollback(root, a, int(b.get("version", 0)))))[1]),
            ("GET", "/api/lab/agents/{agent_id}/dreams", "lab_dreams", "Dream journal", [], lambda c, b, a: (need(c, a), {"dreams": dream.journal(root, a)})[1]),
            ("POST", "/api/lab/agents/{agent_id}/dream", "lab_dream_now", "Dream now (ignores the sleep window)", [], bg_dream),
            ("GET", "/api/lab/agents/{agent_id}/evals", "lab_eval_cases", "Evaluation cases and auto-improvement history", [], lambda c, b, a: (need(c, a), {"cases": evals.cases(root, a), "history": evals.history(root, a)})[1]),
            ("POST", "/api/lab/agents/{agent_id}/evals", "lab_add_eval", "Add an evaluation case", [("prompt", S, True), ("checks", A, True), ("name", S, False)], lambda c, b, a: (need(c, a), evals.add_case(root, a, b.get("prompt"), b.get("checks"), b.get("name", "")))[1]),
            ("POST", "/api/lab/agents/{agent_id}/evals/{case_id}/delete", "lab_delete_eval", "Delete an evaluation case", [], lambda c, b, a, k: (need(c, a), evals.delete_case(root, a, k), {"deleted": True})[2]),
            ("POST", "/api/lab/agents/{agent_id}/evals/run", "lab_run_evals", "Run the evaluation cases", [], lambda c, b, a: evals.run_suite(root, need(c, a))),
            ("POST", "/api/lab/agents/{agent_id}/improve", "lab_auto_improve", "Auto-improve, gated by the evaluation cases", [("allow_equal", B, False)], lambda c, b, a: (need(c, a), evals.auto_improve(root, a, allow_equal=bool(b.get("allow_equal"))))[1]),
            ("POST", "/api/lab/agents/{agent_id}/private", "lab_make_private", "Protect an agent with a password", [("password", S, True), ("current", S, False)], privacy_set),
            ("POST", "/api/lab/agents/{agent_id}/private/remove", "lab_remove_private", "Remove an agent's password", P("password"), privacy_remove),
            ("POST", "/api/lab/agents/{agent_id}/unlock", "lab_unlock", "Unlock a private agent (send the token as X-Agent-Unlock)", P("password"), lambda c, b, a: privacy.unlock(root, a, b.get("password"))),
            ("POST", "/api/lab/agents/{agent_id}/lock", "lab_lock", "Lock a private agent again", [], lambda c, b, a: (privacy.lock(a), {"locked": True})[1]),
            ("POST", "/api/lab/agentise/api", "lab_agentise_api", "Turn an API model into an agent", [("preset", S, True), ("name", S, False), ("model", S, False), ("key", S, False), ("role_preset", S, False)], agentise_api),
            ("POST", "/api/lab/agentise/weights", "lab_agentise_weights", "Turn model weights in a project into an agent", [("project_id", S, True), ("path", S, True), ("name", S, False), ("runtime", S, False), ("device", S, False), ("threads", I, False)], agentise_weights),
            # projects
            ("GET", "/api/lab/projects", "lab_list_projects", "List projects with their space use", [], lambda c, b: {"projects": projects.list_projects(root)}),
            ("POST", "/api/lab/projects", "lab_create_project", "Create a project with a space quota", [("name", S, True), ("quota_mb", I, False), ("kind", S, False), ("description", S, False)], project_create),
            ("GET", "/api/lab/projects/{project_id}", "lab_get_project", "Read a project", [], lambda c, b, p: {**projects.get(root, p), **projects.usage(root, p), "environment": project_env.get(root, p)}),
            ("POST", "/api/lab/projects/{project_id}", "lab_update_project", "Change name, description or quota", [("name", S, False), ("quota_mb", I, False)], project_update),
            ("POST", "/api/lab/projects/{project_id}/delete", "lab_delete_project", "Delete a project and its files", [], project_delete),
            ("GET", "/api/lab/projects/{project_id}/files", "lab_list_files", "List a folder (?path=)", [], lambda c, b, p: {"entries": projects.list_dir(root, p, c.q("path", ""))}),
            ("GET", "/api/lab/projects/{project_id}/file", "lab_read_file", "Read a file (?path=)", [], lambda c, b, p: projects.read_file(root, p, c.q("path", ""))),
            ("POST", "/api/lab/projects/{project_id}/file", "lab_write_file", "Write a file", [("path", S, True), ("content", S, True)], write_file),
            ("POST", "/api/lab/projects/{project_id}/file/delete", "lab_delete_file", "Delete a file or folder", P("path"), lambda c, b, p: (projects.delete_path(root, p, b.get("path", "")), {"deleted": True})[1]),
            ("POST", "/api/lab/projects/{project_id}/upload", "lab_upload", "Upload a file (raw body, ?path=)", [], upload),
            ("GET", "/api/lab/uploads/{upload_id}", "lab_upload_progress", "Upload progress", [], lambda c, b, u: uploads.progress(u)),
            ("POST", "/api/lab/projects/{project_id}/environment", "lab_save_env", "Save the project environment (vars, python, network, secrets)", [("vars", O, False), ("python", O, False), ("network", S, False), ("secrets", A, False)], lambda c, b, p: project_env.save(root, p, b)),
            ("POST", "/api/lab/projects/{project_id}/environment/setup", "lab_setup_env", "Create the venv and install packages", [], lambda c, b, p: project_env.setup_python(root, p)),
            ("GET", "/api/lab/projects/{project_id}/environment/setup", "lab_env_status", "Python setup status", [], lambda c, b, p: project_env.setup_status(root, p)),
            ("POST", "/api/lab/projects/{project_id}/clone", "lab_clone_project", "Copy a project", [("name", S, False)], lambda c, b, p: projects.clone(root, p, b.get("name"))),
            ("POST", "/api/lab/projects/{project_id}/command", "lab_run_command", "Run a /command (shell kind) in the project", P("line"), run_command_line),
            ("GET", "/api/lab/projects/{project_id}/analyze", "lab_analyze", "What kind of project this is and what it uses", [], lambda c, b, p: {k: v for k, v in analyze.project_info(root, p).items() if k != "imports_by_module"}),
            ("POST", "/api/lab/projects/{project_id}/docs", "lab_build_docs", "Build documentation from templates", [("kinds", A, True), ("description", S, False), ("openapi_path", S, False), ("include_agentlab_api", B, False)],
             lambda c, b, p: docbuilder.build(root, p, b.get("kinds"), b.get("description", ""), b.get("openapi_path"), bool(b.get("include_agentlab_api")))),
            ("GET", "/api/lab/projects/{project_id}/check", "lab_check", "Compatibility and optimisation check", [], lambda c, b, p: packager.check(root, p)),
            ("POST", "/api/lab/projects/{project_id}/package", "lab_package", "Build a .zip package with installers", [("version", S, False)], lambda c, b, p: packager.package(root, p, b.get("version") or "0.1.0")),
            ("POST", "/api/lab/projects/{project_id}/ai", "lab_project_ai", "Choose the project's model, dataset and trainer", [("dataset", S, False), ("agent", S, False), ("trainer", S, False), ("options", O, False)], lambda c, b, p: training.configure_project(root, p, b)),
            ("POST", "/api/lab/projects/{project_id}/train", "lab_train_project", "The Train button", [], lambda c, b, p: training.train_project(root, p, on_event=R.event)),
            ("GET", "/api/lab/projects/{project_id}/dataset", "lab_inspect_dataset", "Inspect a dataset (?path=)", [], lambda c, b, p: training.inspect(root, p, c.q("path", ""))),
            ("POST", "/api/lab/projects/{project_id}/serve", "lab_serve_local", "Serve the project on localhost", [("folder", S, False), ("port", I, False)], lambda c, b, p: deploy.serve_local(root, p, b.get("folder", ""), b.get("port") or 0)),
            ("POST", "/api/lab/projects/{project_id}/serve/stop", "lab_stop_local", "Stop serving on localhost", [], lambda c, b, p: (deploy.stop_local(p), {"stopped": True})[1]),
            ("POST", "/api/lab/projects/{project_id}/deploy", "lab_deploy_cloudflare", "Deploy to Cloudflare Pages or Workers", [("folder", S, False), ("name", S, False)], deploy_cf),
            ("GET", "/api/lab/deploys", "lab_deploys", "Deployments (live localhost servers and history)", [], lambda c, b: {"live": deploy.local_servers(), "history": deploy.history(root, c.q("project"))}),
            # builders and templates
            ("GET", "/api/lab/templates", "lab_templates", "Template gallery (?kind=website|game|model|infra)", [], lambda c, b: {"templates": builders.catalog(c.q("kind"))}),
            ("GET", "/api/lab/templates/{template_id}", "lab_template", "A template's files", [], lambda c, b, t: builders.preview(t)),
            ("POST", "/api/lab/build", "lab_build", "New project from a template, optionally customised by an agent", [("template", S, True), ("name", S, False), ("agent", S, False), ("prompt", S, False)],
             lambda c, b: (b.get("agent") and need(c, b["agent"]), builders.create(root, b.get("template"), b.get("name"), runtime=rt if b.get("agent") else None, agent_id=b.get("agent"), prompt=b.get("prompt")))[1]),
            # jobs
            ("GET", "/api/lab/jobs", "lab_list_jobs", "List jobs (?project=&agent=&status=)", [], lambda c, b: {"jobs": rt.list(c.q("project"), c.q("agent"), c.q("status")), "concurrency": rt.concurrency()}),
            ("POST", "/api/lab/jobs", "lab_start_job", "Give an agent a task in a project", [("project", S, True), ("agent", S, True), ("prompt", S, True), ("autonomous", B, False), ("overrides", O, False), ("step_mode", B, False), ("deadline_minutes", I, False)], start_job),
            ("GET", "/api/lab/jobs/{job_id}", "lab_get_job", "A job with progress, metrics and recent events", [], lambda c, b, j: rt.get(j, events=int(c.q("events", 80)))),
            ("POST", "/api/lab/jobs/{job_id}/cancel", "lab_cancel_job", "Stop a job", [], lambda c, b, j: rt.cancel(j)),
            ("POST", "/api/lab/jobs/{job_id}/approve", "lab_decide", "Answer an approval request", [("approval", S, True), ("allow", B, True), ("always", B, False)], lambda c, b, j: (rt.decide(j, b.get("approval"), bool(b.get("allow")), bool(b.get("always"))), {"ok": True})[1]),
            ("POST", "/api/lab/jobs/{job_id}/feedback", "lab_feedback", "Rate a finished job (+1/-1)", [("rating", I, True), ("note", S, False)], lambda c, b, j: rt.feedback(j, b.get("rating"), b.get("note", ""))),
            ("GET", "/api/lab/jobs/{job_id}/trace", "lab_trace", "Debugger: every model and tool step", [], lambda c, b, j: {"trace": rt.trace(j)}),
            ("POST", "/api/lab/jobs/{job_id}/debug", "lab_debug", "Debugger: step, continue, skip, or edit the next tool call", [("action", S, True), ("arguments", O, False)], lambda c, b, j: (rt.resume(j, b.get("action", "step"), b.get("arguments")), {"ok": True})[1]),
            ("POST", "/api/lab/jobs/{job_id}/step-mode", "lab_step_mode", "Turn step-through on or off", [("on", B, True)], lambda c, b, j: rt.set_step_mode(j, bool(b.get("on")))),
            ("POST", "/api/lab/concurrency", "lab_concurrency", "Set how many jobs run in parallel", [("limit", I, True)], lambda c, b: rt.concurrency(b.get("limit"))),
            ("GET", "/api/lab/batches", "lab_list_batches", "Parallel batches", [], lambda c, b: {"batches": batches.list_batches(root, rt)}),
            ("POST", "/api/lab/batches", "lab_start_batch", "Compare agents on one task, or fan out many tasks", [("project", S, True), ("kind", S, True), ("agents", A, False), ("agent", S, False), ("prompt", S, False), ("prompts", A, False), ("isolate", B, False)], lambda c, b: batches.start(root, rt, b)),
            ("GET", "/api/lab/batches/{batch_id}", "lab_get_batch", "A batch's progress and results", [], lambda c, b, x: batches.status(root, rt, x)),
            ("POST", "/api/lab/batches/{batch_id}/cancel", "lab_cancel_batch", "Stop a batch", [], lambda c, b, x: batches.cancel(root, rt, x)),
            # schedules
            ("GET", "/api/lab/schedules", "lab_list_schedules", "Scheduled and recurring jobs", [], lambda c, b: {"schedules": R.scheduler.list()}),
            ("POST", "/api/lab/schedules", "lab_create_schedule", "Schedule an agent (cron or every N minutes)", [("project", S, True), ("agent", S, True), ("prompt", S, True), ("cron", S, False), ("every_minutes", I, False), ("tz_minutes", I, False), ("max_runs", I, False), ("overrides", O, False)],
             lambda c, b: (need(c, b.get("agent")), R.scheduler.create(b))[1]),
            ("POST", "/api/lab/schedules/{schedule_id}", "lab_update_schedule", "Pause or resume a schedule", [("enabled", B, True)], lambda c, b, s: R.scheduler.update(s, enabled=b.get("enabled"))),
            ("POST", "/api/lab/schedules/{schedule_id}/delete", "lab_delete_schedule", "Delete a schedule", [], lambda c, b, s: (R.scheduler.delete(s), {"deleted": True})[1]),
            # rooms and playground
            ("GET", "/api/lab/rooms", "lab_list_rooms", "Agent chat rooms", [], lambda c, b: {"rooms": rooms.list_rooms(root)}),
            ("POST", "/api/lab/rooms", "lab_create_room", "Create a room where agents talk and cooperate", [("agents", A, True), ("name", S, False), ("mode", S, False), ("goal", S, False), ("manager", S, False), ("max_turns", I, False)], room_create),
            ("GET", "/api/lab/rooms/{room_id}", "lab_get_room", "A room's conversation", [], lambda c, b, r: rooms.get(root, r)),
            ("POST", "/api/lab/rooms/{room_id}/post", "lab_room_post", "Say something in a room", P("text"), lambda c, b, r: rooms.post(root, r, b.get("text"))),
            ("POST", "/api/lab/rooms/{room_id}/step", "lab_room_step", "Let one agent speak", [("speaker", S, False)], lambda c, b, r: {"message": rooms.step(root, r, speaker=b.get("speaker"))}),
            ("POST", "/api/lab/rooms/{room_id}/run", "lab_room_run", "Let the agents talk for a while", [("turns", I, False)], room_run),
            ("POST", "/api/lab/rooms/{room_id}/stop", "lab_room_stop", "Stop the conversation", [], lambda c, b, r: {"stopped": rooms.stop(root, r)}),
            ("POST", "/api/lab/rooms/{room_id}/delete", "lab_delete_room", "Delete a room", [], lambda c, b, r: (rooms.delete(root, r), {"deleted": True})[1]),
            ("POST", "/api/lab/playground/chat", "lab_playground_chat", "Chat with a model or agent (attachments, widgets, insights)", [("messages", A, True), ("agent_id", S, False), ("provider", O, False), ("attachments", A, False), ("widgets", B, False)], playground_chat),
            ("POST", "/api/lab/playground/compare", "lab_playground_compare", "Same prompt on several models side by side", [("messages", A, True), ("targets", A, True)], playground_compare),
            ("GET", "/api/lab/compare", "lab_compare_history", "Past model comparisons", [], lambda c, b: {"comparisons": comparator.history(root)}),
            ("POST", "/api/lab/compare", "lab_compare", "Ask two or more of your models the same thing and compare tokens, cost, speed and answers",
             [("targets", A, True), ("prompt", S, True), ("repeats", I, False), ("system", S, False), ("max_tokens", I, False), ("temperature", S, False)], compare_run),
            ("GET", "/api/lab/compare/{compare_id}", "lab_get_compare", "One saved comparison in full", [], lambda c, b, x: comparator.get(root, x)),
            ("POST", "/api/lab/compare/{compare_id}/judge", "lab_judge_compare", "Ask one model to rank the answers (its opinion, not a measurement)", [("provider", O, True)], compare_judge),
            ("POST", "/api/lab/compare/{compare_id}/delete", "lab_delete_compare", "Delete a saved comparison", [], lambda c, b, x: (comparator.delete(root, x), {"deleted": True})[1]),
            ("POST", "/api/lab/models/test", "lab_test_model", "Run the model test battery", [("provider", O, True)], lambda c, b: model_tests.run_battery(root, b.get("provider") or {})),
            ("GET", "/api/lab/models", "lab_models", "Models dashboard", [], lambda c, b: models.overview(root)),
            ("POST", "/api/lab/models/list", "lab_provider_models", "List the models a provider offers", [("provider", O, True)], model_list),
            ("GET", "/api/lab/widgets", "lab_list_widgets", "Saved widgets", [], lambda c, b: {"widgets": widgets.list_widgets(root), "templates": [{"id": k, "title": v[0]} for k, v in widgets.TEMPLATES.items()]}),
            ("POST", "/api/lab/widgets", "lab_save_widget", "Save a widget", [("spec", O, True)], lambda c, b: widgets.save(root, b.get("spec"), source="user")),
            ("GET", "/api/lab/widgets/{widget_id}", "lab_get_widget", "A saved widget", [], lambda c, b, w: widgets.get(root, w)),
            ("POST", "/api/lab/widgets/{widget_id}/delete", "lab_delete_widget", "Delete a widget", [], lambda c, b, w: (widgets.delete(root, w), {"deleted": True})[1]),
            ("POST", "/api/lab/widgets/{widget_id}/export", "lab_export_widget", "Save a widget as an HTML file in a project", [("project", S, True)], lambda c, b, w: widgets.export_to_project(root, w, b.get("project"))),
            # knowledge
            ("GET", "/api/lab/knowledge", "lab_list_kbs", "Knowledge bases", [], lambda c, b: {"knowledge": knowledge.list_kbs(root), "embeddings": embeddings.PRESETS}),
            ("POST", "/api/lab/knowledge", "lab_create_kb", "Create a knowledge base", [("name", S, True), ("embedding", O, False)], lambda c, b: knowledge.create(root, b.get("name"), b.get("embedding"), b.get("description", ""))),
            ("GET", "/api/lab/knowledge/{kb_id}", "lab_get_kb", "A knowledge base and its sources", [], lambda c, b, k: knowledge.get(root, k)),
            ("POST", "/api/lab/knowledge/{kb_id}/add", "lab_kb_add", "Add text or a project file", [("text", S, False), ("title", S, False), ("project", S, False), ("path", S, False)], kb_add),
            ("POST", "/api/lab/knowledge/{kb_id}/scrape", "lab_kb_scrape", "Add a web page or crawl a site (robots.txt respected)", [("url", S, True), ("max_pages", I, False)], kb_scrape),
            ("POST", "/api/lab/knowledge/{kb_id}/search", "lab_kb_search", "Search a knowledge base", P("query"), lambda c, b, k: {"results": knowledge.search(root, k, b.get("query", ""), b.get("k", 5))}),
            ("POST", "/api/lab/knowledge/{kb_id}/sources/{source_id}/delete", "lab_kb_remove", "Remove a source", [], lambda c, b, k, s: (knowledge.remove_source(root, k, s), {"deleted": True})[1]),
            ("POST", "/api/lab/knowledge/{kb_id}/delete", "lab_delete_kb", "Delete a knowledge base", [], lambda c, b, k: (knowledge.delete(root, k), {"deleted": True})[1]),
            # training
            ("GET", "/api/lab/training", "lab_list_training", "Training runs (?project=)", [], lambda c, b: {"runs": training.list_runs(root, c.q("project"))}),
            ("POST", "/api/lab/training", "lab_start_training", "Train a model on a project dataset", [("project", S, True), ("dataset", S, True), ("trainer", S, False), ("options", O, False)], lambda c, b: training.start(root, b, on_event=R.event)),
            ("GET", "/api/lab/training/{run_id}", "lab_get_training", "A run with its loss history and warnings", [], lambda c, b, r: training.get(root, r)),
            ("POST", "/api/lab/training/{run_id}/cancel", "lab_cancel_training", "Stop a training run", [], lambda c, b, r: (training.cancel(root, r), {"cancelling": True})[1]),
            ("POST", "/api/lab/training/{run_id}/predict", "lab_predict", "Use a trained classifier", P("text"), lambda c, b, r: {"predictions": training.predict(root, r, b.get("text", ""))}),
            ("POST", "/api/lab/training/{run_id}/edit", "lab_edit_model", "Edit a trained model (teach, rename, drop, undo)", [("action", S, True)], lambda c, b, r: training.edit(root, r, b.get("action"), **{k: v for k, v in b.items() if k != "action"})),
            # imports, runners, accounts, secrets
            ("GET", "/api/lab/imports", "lab_list_imports", "GitHub and Hugging Face imports", [], lambda c, b: {"imports": R.importer.list(c.q("project"))}),
            ("POST", "/api/lab/imports", "lab_start_import", "Import a GitHub repo or Hugging Face model/dataset into a project", [("source", S, True), ("project", S, True), ("repo", S, False), ("repo_id", S, False), ("repo_type", S, False), ("files", A, False), ("patterns", A, False)], import_start),
            ("GET", "/api/lab/imports/{import_id}", "lab_get_import", "Import progress", [], lambda c, b, i: R.importer.get(i)),
            ("POST", "/api/lab/imports/{import_id}/cancel", "lab_cancel_import", "Cancel an import", [], lambda c, b, i: R.importer.cancel(i)),
            ("GET", "/api/lab/runners", "lab_runners", "Local model servers", [], lambda c, b: {"runners": runners.list_runners(root), "available": runners.available(), "hardware": hardware.info()}),
            ("POST", "/api/lab/runners/{runner_id}/stop", "lab_stop_runner", "Stop a local model server", [], lambda c, b, r: runners.stop(root, r)),
            ("GET", "/api/lab/accounts", "lab_accounts", "Connected accounts (GitHub, Hugging Face, OpenAI, Anthropic)", [], lambda c, b: accounts.status(root)),
            ("POST", "/api/lab/accounts/{service}", "lab_connect_account", "Connect an account (the token is verified first)", P("token"), lambda c, b, s: accounts.connect(root, s, b.get("token"))),
            ("POST", "/api/lab/accounts/{service}/disconnect", "lab_disconnect_account", "Disconnect an account", [], lambda c, b, s: (accounts.disconnect(root, s), {"disconnected": True})[1]),
            ("GET", "/api/lab/github/repos", "lab_github_repos", "Your GitHub repositories (?q= to search)", [], lambda c, b: {"repos": accounts.github_repos(root, c.q("q"))}),
            ("GET", "/api/lab/huggingface/search", "lab_hf_search", "Search Hugging Face (?kind=models|datasets&q=)", [], lambda c, b: {"results": accounts.hf_search(root, c.q("kind", "models"), c.q("q", ""))}),
            ("GET", "/api/lab/secrets", "lab_list_secrets", "Names of saved keys (never the values)", [], lambda c, b: {"secrets": vault.list_secrets(root)}),
            ("POST", "/api/lab/secrets", "lab_save_secret", "Save a key", [("name", S, True), ("value", S, True)], secret_set),
            ("POST", "/api/lab/secrets/{name}/delete", "lab_delete_secret", "Delete a key", [], secret_delete),
            ("GET", "/api/lab/presets", "lab_presets", "Provider presets, tokenizers and role presets", [], lambda c, b: {"providers": {k: {kk: vv for kk, vv in v.items() if kk != "script"} for k, v in llm.PRESETS.items()}, "library": library.catalog(), "tokenizers": tokenizers.PRESETS, "embeddings": embeddings.PRESETS, "roles": library.catalog().get("roles")}),
            ("POST", "/api/lab/tokens", "lab_count_tokens", "Count tokens with a tokenizer", P("text"), tokens),
            # library and commands
            ("GET", "/api/lab/library", "lab_library", "Roles, skills, plugins and connectors", [], lambda c, b: {**library.catalog(), "custom_skills": library.custom_skills_list(root), "custom_plugins": library.custom_plugins_list(root), "connectors": library.connector_status(root)}),
            ("POST", "/api/lab/library/skills", "lab_save_skill", "Create a custom skill", [("name", S, True), ("instructions", S, True), ("description", S, False)], lambda c, b: library.save_skill(root, b)),
            ("POST", "/api/lab/library/plugins", "lab_save_plugin", "Create a custom plugin", [("tool", S, True), ("category", S, True), ("command", S, True), ("params", O, False), ("name", S, False)], lambda c, b: library.save_plugin(root, b)),
            ("POST", "/api/lab/library/{kind}/{item_id}/delete", "lab_delete_library_item", "Delete a custom skill or plugin", [], lambda c, b, k, i: (library.delete_custom(root, k, i), {"deleted": True})[1]),
            ("GET", "/api/lab/commands", "lab_commands", "Built-in and your own /commands", [], lambda c, b: {"commands": commands.list_commands(root)}),
            ("POST", "/api/lab/commands", "lab_save_command", "Create a /command", [("name", S, True), ("kind", S, True), ("template", S, True)], lambda c, b: commands.save(root, b)),
            ("POST", "/api/lab/commands/{name}/delete", "lab_delete_command", "Delete a /command", [], lambda c, b, n: (commands.delete(root, n), {"deleted": True})[1]),
            ("POST", "/api/lab/commands/expand", "lab_expand_command", "Expand a /command line into a prompt", P("line"), lambda c, b: commands.expand(root, b.get("line", ""))),
            # terminal
            ("GET", "/api/lab/terminals", "lab_terminals", "Open terminals", [], lambda c, b: {"terminals": terminal.list_sessions()}),
            ("POST", "/api/lab/terminals", "lab_open_terminal", "Open a terminal in a project", [("project", S, True), ("cols", I, False), ("rows", I, False)], lambda c, b: terminal.open_session(root, b.get("project"), b.get("cols", 100), b.get("rows", 30))),
            ("GET", "/api/lab/terminals/{term_id}", "lab_read_terminal", "Read terminal output (?offset=, long-polls)", [], lambda c, b, t: terminal.read(t, c.q("offset", 0), c.q("wait", 20))),
            ("POST", "/api/lab/terminals/{term_id}/input", "lab_terminal_input", "Type into a terminal", P("data"), lambda c, b, t: terminal.write(t, b.get("data", ""))),
            ("POST", "/api/lab/terminals/{term_id}/resize", "lab_resize_terminal", "Resize a terminal", [("cols", I, True), ("rows", I, True)], lambda c, b, t: (terminal.resize(t, b.get("cols", 100), b.get("rows", 30)), {"ok": True})[1]),
            ("POST", "/api/lab/terminals/{term_id}/close", "lab_close_terminal", "Close a terminal", [], lambda c, b, t: (terminal.close(t), {"closed": True})[1]),
            # insights, usage, notifications, suggestions
            ("GET", "/api/lab/usage", "lab_usage", "Token and cost usage (?days=)", [], lambda c, b: usage.summary(root, max(1, min(int(c.q("days", 14)), 365)))),
            ("GET", "/api/lab/notifications", "lab_notifications", "Notification feed", [], lambda c, b: {"items": notifications.list_notifications(root, c.q("unread") == "1"), "unread": notifications.unread_count(root)}),
            ("POST", "/api/lab/notifications/read", "lab_mark_read", "Mark notifications as read", [("ids", A, False)], lambda c, b: {"marked": notifications.mark_read(root, b.get("ids"))}),
            ("POST", "/api/lab/notifications/clear", "lab_clear_notifications", "Clear the feed", [], lambda c, b: (notifications.clear(root), {"cleared": True})[1]),
            ("GET", "/api/lab/suggestions", "lab_suggestions", "AI suggestions from your own data", [], lambda c, b: {"suggestions": suggestions.compute(root)}),
            ("POST", "/api/lab/suggestions/dismiss", "lab_dismiss_suggestion", "Snooze a suggestion for a week", P("key"), lambda c, b: (suggestions.dismiss(root, b.get("key", "")), {"dismissed": True})[1]),
            ("GET", "/api/lab/hardware", "lab_hardware", "This machine: CPU, RAM, GPUs", [], lambda c, b: hardware.info()),
        ]


def compile_routes(table):
    """-> (exact {(method, path): (handler, raw)}, patterned [(method, regex, handler, raw)])."""
    exact, patterned = {}, []
    for method, path, name, summary, body, fn in table:
        raw = name == "lab_upload"
        if "{" in path:
            rx = re.compile("^" + re.sub(r"\{[a-z_]+\}", r"([^/]+)", path) + "$")
            patterned.append((method, rx, fn, raw))
        else:
            exact[(method, path)] = (fn, raw)
    return exact, patterned


def spec_entries(table=None):
    table = table if table is not None else Platform.__new__(Platform).routes()  # only the descriptions are used
    return [{"name": name, "method": method, "path": path, "summary": summary, "body": list(body), "client": True} for method, path, name, summary, body, _ in table]
