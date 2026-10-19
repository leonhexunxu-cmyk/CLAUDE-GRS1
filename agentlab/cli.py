"""Command line entry point: agentlab <command> ..."""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import hooks, recorder, regress, sandbox
from .tripwires import Tripwires


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentlab", description="Tools for trustworthy AI agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="start the dashboard and API on localhost")
    p.add_argument("--project", default="."); p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true")

    p = sub.add_parser("connect", help="connect agents (claude-code, kimi, openclaw) to this project; no name = every detected agent")
    p.add_argument("agents", nargs="*", choices=["claude-code", "kimi", "openclaw", []], default=[], metavar="agent")
    p.add_argument("--project", default="."); p.add_argument("--policy", help="rules file to enforce")
    p.add_argument("--no-mcp", action="store_true", help="hooks only, skip the MCP server entry")

    p = sub.add_parser("disconnect", help="remove what `connect` added")
    p.add_argument("agents", nargs="+", choices=["claude-code", "kimi", "openclaw"], metavar="agent"); p.add_argument("--project", default=".")

    p = sub.add_parser("status", help="show which agents are detected and connected")
    p.add_argument("--project", default=".")

    p = sub.add_parser("install", help="install hooks into an agent (currently: claude-code)")
    p.add_argument("agent", choices=["claude-code"]); p.add_argument("--project", default=".")
    p.add_argument("--policy", help="rules file to enforce"); p.add_argument("--command")
    p.add_argument("--with-permissions", action="store_true", help="also merge generated permissions into settings.json")
    p.add_argument("--with-mcp", action="store_true", help="also register the agentlab MCP server in .mcp.json")

    p = sub.add_parser("mcp", help="run the MCP server on stdio (for MCP-capable agents)")
    p.add_argument("--project", default="."); p.add_argument("--print-config", action="store_true",
                                                         help="print the mcpServers JSON for your client and exit")

    p = sub.add_parser("task", help="run commands in dedicated environments and review their changes")
    tsub = p.add_subparsers(dest="tcmd", required=True)
    t = tsub.add_parser("run", help="run a command in a disposable copy of the project")
    t.add_argument("command", nargs="+"); t.add_argument("--name"); t.add_argument("--timeout", type=int, default=600)
    t.add_argument("--network", action="store_true", help="allow network (default: blocked)")
    t.add_argument("--docker", action="store_true"); t.add_argument("--image", default="python:3.12-slim")
    t.add_argument("--pass-env", action="append", default=[], metavar="NAME", help="environment variable to pass through (repeatable)")
    t.add_argument("--project", default=".")
    for name in ("list", "show", "apply", "discard"):
        t = tsub.add_parser(name)
        if name != "list":
            t.add_argument("id")
        t.add_argument("--project", default=".")

    sub.add_parser("insights", help="print agent health").add_argument("--project", default=".")

    p = sub.add_parser("apigen", help="print a generated API client")
    p.add_argument("language", choices=["python", "javascript", "typescript", "curl"])
    p.add_argument("--base", default="http://127.0.0.1:8765")

    p = sub.add_parser("key", help="manage API keys")
    p.add_argument("action", choices=["create", "list", "revoke"]); p.add_argument("value", nargs="?", help="label (create) or id (revoke)")
    p.add_argument("--project", default=".")

    sub.add_parser("hook", help="hook entry point (reads a Claude Code event from stdin)")

    p = sub.add_parser("replay", help="show a recorded run as a timeline")
    p.add_argument("run"); p.add_argument("--upto", type=int)

    p = sub.add_parser("fork", help="fork a run from a step")
    p.add_argument("run"); p.add_argument("step", type=int); p.add_argument("out")

    p = sub.add_parser("regress", help="compare a baseline results file with a candidate")
    p.add_argument("baseline"); p.add_argument("candidate")
    p.add_argument("--threshold", type=float, default=0.8)

    p = sub.add_parser("sandbox", help="turn plain-language rules into a policy and attack-test it")
    p.add_argument("rules", help="text file of rules, one per line")
    p.add_argument("--json", action="store_true", help="print settings.json instead of the simulation")

    p = sub.add_parser("tripwire", help="plant canaries into a document")
    p.add_argument("doc"); p.add_argument("--seed", required=True); p.add_argument("-n", type=int, default=3)

    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import make_server
        srv = make_server(a.project, a.port)
        url = f"http://127.0.0.1:{srv.server_port}/"
        print(f"agentlab dashboard: {url}  (project: {Path(a.project).resolve()})")
        if not a.no_open:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    elif a.cmd in ("connect", "disconnect", "status"):
        from . import connect as conn
        if a.cmd == "status":
            for r in conn.status(a.project):
                state = "connected" if r["connected"] else ("detected, not connected" if r["detected"] else "not found")
                print(f"{r['name']:<12} {state}" + ("" if r["connected"] else f"   ->  {r['command']}"))
        elif a.cmd == "disconnect":
            for agent in a.agents:
                print(f"[{agent}]", *(conn.disconnect(agent, a.project) or ["nothing to remove"]), sep="\n  ")
        else:
            text = Path(a.policy).read_text(encoding="utf-8") if a.policy else None
            targets = a.agents or [r["id"] for r in conn.status(a.project) if r["detected"]]
            if not targets:
                print("No agents detected. Name one: agentlab connect claude-code | kimi | openclaw")
                return 1
            for agent in targets:
                print(f"[{agent}]", *conn.connect(agent, a.project, not a.no_mcp, policy_text=text), sep="\n  ")
            print("\nDone. Run `agentlab serve` to watch activity.")
    elif a.cmd == "install":
        from .install import install_claude_code
        text = Path(a.policy).read_text(encoding="utf-8") if a.policy else None
        print("updated", install_claude_code(a.project, a.command, text, a.with_permissions))
        if a.with_mcp:
            from .install import install_mcp_config
            print("updated", install_mcp_config(a.project))
    elif a.cmd == "insights":
        from . import insights
        data = insights.compute(a.project)
        t_ = data["totals"]
        print(f"24h: {t_['sessions']} sessions, {t_['tool_calls']} tool calls, {t_['blocked']} blocked, {t_['errors']} errors, {t_['active']} active now")
        for ag in data["agents"]:
            print(f"  {ag['name']:<12} {ag['level']:<5} " + "; ".join(r['code'] + "".join(f" {k}={v}" for k, v in r.items() if k != 'code') for r in ag["reasons"]))
    elif a.cmd == "task":
        return _task_cli(a)
    elif a.cmd == "apigen":
        from . import apigen
        print(apigen.generate(a.language, a.base), end="")
    elif a.cmd == "key":
        from . import apikeys
        if a.action == "create":
            rec, key = apikeys.create(a.project, a.value or "")
            print(f"{key}\n(id {rec['id']}; shown once, store it now)")
        elif a.action == "list":
            for k in apikeys.list_keys(a.project):
                print(f"{k['id']}  {k['prefix']}…  {k['label']}")
        else:
            print("revoked" if a.value and apikeys.revoke(a.project, a.value) else "no such key")
            return 0 if a.value else 1
    elif a.cmd == "mcp":
        from . import mcp
        if a.print_config:
            from .install import base_command
            exe, lead = base_command()
            print(json.dumps({"mcpServers": {"agentlab": {"command": exe, "args": [*lead, "mcp", "--project", str(Path(a.project).resolve())]}}}, indent=2))
        else:
            mcp.serve(a.project)
    elif a.cmd == "hook":
        try:
            code, msg = hooks.process(json.load(sys.stdin), ".")
        except Exception as e:  # never break the agent because logging failed
            try:
                hooks.log_error(f"{type(e).__name__}: {e}")
            except Exception:
                pass
            print(f"agentlab hook error: {e}", file=sys.stderr)
            return 1
        if msg:
            print(msg, file=sys.stderr)
        return code
    elif a.cmd == "replay":
        print(recorder.replay(a.run, a.upto))
    elif a.cmd == "fork":
        print("forked run", recorder.fork_run(a.run, a.step, a.out))
    elif a.cmd == "regress":
        findings = regress.compare_runs(regress.load(a.baseline), regress.load(a.candidate), a.threshold)
        for f in findings:
            sim = f" ({f['similarity']:.0%} similar)" if "similarity" in f else ""
            print(f"[{f['status'].upper():7}] {f['case']}{sim}")
            if f["detail"] and f["status"] != "ok":
                print("   " + f["detail"].replace("\n", "\n   "))
        return 1 if regress.has_regressions(findings) else 0
    elif a.cmd == "sandbox":
        policy = sandbox.parse_policy(Path(a.rules).read_text(encoding="utf-8"))
        if a.json:
            print(policy.to_json())
        else:
            for act, target, ok, why in sandbox.simulate(policy):
                print(f"{'ALLOWED' if ok else 'blocked':7}  {act:<7} {target:<32} {why}")
    elif a.cmd == "tripwire":
        t = Tripwires(a.seed); t.make(a.n)
        print(t.plant(Path(a.doc).read_text(encoding="utf-8")))
        for c, q in t.questions():
            print(f"# ask: {q}  -> expect {c['value']}", file=sys.stderr)
    return 0


def _task_cli(a):
    import time
    from . import envs
    m = envs.Manager(a.project)
    try:
        if a.tcmd == "run":
            rec = m.create({"command": " ".join(a.command), "name": a.name, "timeout": a.timeout, "network": a.network,
                            "backend": "docker" if a.docker else "local", "image": a.image, "pass_env": a.pass_env})
            print(f"task {rec['id']} started" + ("" if a.network or a.docker else " (network blocked)"))
            shown = 0
            while rec["status"] in ("queued", "running"):
                time.sleep(0.3)
                rec = m.get(rec["id"])
                log = m.log_tail(rec["id"], 1_000_000)
                sys.stdout.write(log[shown:]); sys.stdout.flush(); shown = len(log)
            log = m.log_tail(rec["id"], 1_000_000)
            sys.stdout.write(log[shown:])
            ch = rec.get("changes") or {}
            print(f"\n{rec['status']} (exit {rec['exit_code']}); changes: {ch.get('added', 0)} added, {ch.get('modified', 0)} modified, {ch.get('deleted', 0)} deleted")
            if sum(ch.values()):
                print(f"review: agentlab task show {rec['id']}   apply: agentlab task apply {rec['id']}")
            return 0 if rec["status"] == "succeeded" else 1
        if a.tcmd == "list":
            for r in m.list():
                print(f"{r['id']}  {r['status']:<11} {r['name'][:60]}")
        elif a.tcmd == "show":
            for c in m.changes(a.id):
                print(f"{c['status']:<9} {c['path']}" + ("  (protected: will not be applied)" if c["protected"] else ""))
                if c.get("diff"):
                    print(c["diff"])
        elif a.tcmd == "apply":
            r = m.apply(a.id)
            print("applied:", *r["applied"] or ["nothing"])
            for sk in r["skipped"]:
                print(f"skipped {sk['path']}: {sk['reason']}")
        elif a.tcmd == "discard":
            m.discard(a.id)
            print("discarded")
    except envs.TaskError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
