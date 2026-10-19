"""Prompt Regression Lab: baseline outputs, then diff after a prompt or model change."""

import difflib
import json
import re


def run_suite(fn, cases):
    """Run `fn(input) -> str` over cases [{'name', 'input', optional 'must_contain', 'must_not_contain', 'max_chars'}].

    Returns {name: {'output': str, **constraints}} ready to save as a baseline.
    """
    out = {}
    for c in cases:
        entry = {k: v for k, v in c.items() if k not in ("name", "input")}
        entry["output"] = fn(c["input"])
        out[c["name"]] = entry
    return out


def _similarity(a, b):
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def _constraint_failures(entry):
    out = entry.get("output", "")
    fails = []
    for s in entry.get("must_contain", []):
        if s.lower() not in out.lower():
            fails.append(f"missing required text {s!r}")
    for s in entry.get("must_not_contain", []):
        if s.lower() in out.lower():
            fails.append(f"contains forbidden text {s!r}")
    if "max_chars" in entry and len(out) > entry["max_chars"]:
        fails.append(f"too long ({len(out)} > {entry['max_chars']} chars)")
    return fails


def compare_runs(baseline, candidate, threshold=0.8):
    """Compare two {name: {'output': ...}} dicts. Returns a list of findings."""
    findings = []
    for name, base in baseline.items():
        if name not in candidate:
            findings.append({"case": name, "status": "missing", "detail": "case not in candidate run"})
            continue
        cand = candidate[name]
        fails = _constraint_failures({**base, "output": cand["output"]})
        sim = _similarity(base["output"], cand["output"])
        if fails:
            findings.append({"case": name, "status": "fail", "similarity": sim, "detail": "; ".join(fails)})
        elif sim < threshold:
            diff = "\n".join(difflib.unified_diff(
                base["output"].splitlines(), cand["output"].splitlines(),
                "baseline", "candidate", lineterm="", n=1))
            findings.append({"case": name, "status": "drift", "similarity": sim, "detail": diff})
        else:
            findings.append({"case": name, "status": "ok", "similarity": sim, "detail": ""})
    for name in candidate.keys() - baseline.keys():
        findings.append({"case": name, "status": "new", "detail": "case not in baseline"})
    return findings


def save(path, results):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def has_regressions(findings):
    return any(f["status"] in ("fail", "drift", "missing") for f in findings)
