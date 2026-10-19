"""Command line entry point: python -m agentlab <tool> ..."""

import argparse
import sys

from . import recorder, regress, sandbox
from .tripwires import Tripwires


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentlab", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    if a.cmd == "replay":
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
        policy = sandbox.parse_policy(open(a.rules, encoding="utf-8").read())
        if a.json:
            print(policy.to_json())
        else:
            for act, target, ok, why in sandbox.simulate(policy):
                print(f"{'ALLOWED' if ok else 'blocked':7}  {act:<7} {target:<32} {why}")
    elif a.cmd == "tripwire":
        t = Tripwires(a.seed); t.make(a.n)
        print(t.plant(open(a.doc, encoding="utf-8").read()))
        for c, q in t.questions():
            print(f"# ask: {q}  -> expect {c['value']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
