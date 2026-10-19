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

    p = sub.add_parser("install", help="install hooks into an agent (currently: claude-code)")
    p.add_argument("agent", choices=["claude-code"]); p.add_argument("--project", default=".")
    p.add_argument("--policy", help="rules file to enforce"); p.add_argument("--command")
    p.add_argument("--with-permissions", action="store_true", help="also merge generated permissions into settings.json")

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
    elif a.cmd == "install":
        from .install import install_claude_code
        text = Path(a.policy).read_text(encoding="utf-8") if a.policy else None
        print("updated", install_claude_code(a.project, a.command, text, a.with_permissions))
    elif a.cmd == "hook":
        try:
            code, msg = hooks.process(json.load(sys.stdin), ".")
        except Exception as e:  # never break the agent because logging failed
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


if __name__ == "__main__":
    sys.exit(main())
