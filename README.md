# agentlab

Four small, zero-dependency tools for building trustworthy AI agents. Python 3.9+.

| Tool | What it does |
|---|---|
| **Flight Recorder** (`recorder`) | Log every agent step to JSONL, replay it as a timeline, fork a run from any step. |
| **Prompt Regression Lab** (`regress`) | Save baseline outputs, then diff after a prompt/model change. Fuzzy drift detection plus hard constraints. Exit code 1 for CI. |
| **Permission Sandbox Designer** (`sandbox`) | Write plain rules, get a least-privilege Claude Code `settings.json` block, and see which attack attempts would slip through. |
| **Hallucination Tripwires** (`tripwires`) | Plant seed-derived canary facts in documents, then audit answers for recall, contradiction and invention. |

## Usage

```python
from agentlab import Recorder, run_suite, compare_runs, parse_policy, Tripwires

with Recorder("runs/a.jsonl") as rec:
    rec.log("prompt", "Fix the bug")
    rec.log("tool_call", "pytest -q", tool="bash")
```

```
python -m agentlab replay runs/a.jsonl --upto 2
python -m agentlab fork runs/a.jsonl 2 runs/b.jsonl
python -m agentlab regress baseline.json candidate.json --threshold 0.8
python -m agentlab sandbox rules.txt          # attack simulation
python -m agentlab sandbox rules.txt --json   # settings.json permissions
python -m agentlab tripwire doc.txt --seed my-secret -n 3
```

Sandbox rules, one per line: `allow read src/**`, `allow write src/ and tests/`, `allow run pytest, git status`, `allow network api.github.com`, `never read .env`. Anything not allowed is denied; explicit `never` rules always win.

Rule parsing is a small fixed grammar, not an LLM. Tripwire audits are string-based heuristics, so treat them as a cheap signal rather than proof.

## Tests

```
python -m unittest discover -s tests
```
