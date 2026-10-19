# agentlab

Four small, zero-dependency tools for building trustworthy AI agents. Python 3.9+.

| Tool | What it does |
|---|---|
| **Flight Recorder** (`recorder`) | Log every agent step to JSONL, replay it as a timeline, fork a run from any step. |
| **Prompt Regression Lab** (`regress`) | Save baseline outputs, then diff after a prompt/model change. Fuzzy drift detection plus hard constraints. Exit code 1 for CI. |
| **Permission Sandbox Designer** (`sandbox`) | Write plain rules, get a least-privilege Claude Code `settings.json` block, and see which attack attempts would slip through. |
| **Hallucination Tripwires** (`tripwires`) | Plant seed-derived canary facts in documents, then audit answers for recall, contradiction and invention. |

## Quick start (Claude Code)

```
pip install -e .
agentlab install claude-code --policy rules.txt   # hooks + sandbox rules in .claude/settings.json
agentlab serve                                    # dashboard at http://127.0.0.1:8765
```

Use Claude Code as normal. Every prompt, tool call and edit is recorded under `.agentlab/runs/`, and tool calls that break your rules are blocked (the hook exits with code 2 and tells Claude why). Secrets are redacted before anything is written.

Add `--with-permissions` to also merge the generated `permissions` block into `settings.json`.

## Other commands

```
agentlab replay runs/a.jsonl --upto 2
agentlab fork runs/a.jsonl 2 runs/b.jsonl
agentlab regress baseline.json candidate.json --threshold 0.8
agentlab sandbox rules.txt          # attack simulation
agentlab sandbox rules.txt --json   # settings.json permissions
agentlab tripwire doc.txt --seed my-secret -n 3
```

```python
from agentlab import Recorder
with Recorder("runs/a.jsonl") as rec:
    rec.log("prompt", "Fix the bug")
    rec.log("tool_call", "pytest -q", tool="bash")
```

## HTTP API

All routes are JSON, on localhost only. POST bodies must use `Content-Type: application/json`.

| Route | Purpose |
|---|---|
| `GET /api/runs`, `GET /api/runs/<id>` | list runs, read one |
| `POST /api/runs/<id>/fork` `{step}` | fork from a step |
| `POST /api/events` `{run, kind, content, tool?}` | ingest an event from any adapter |
| `GET/POST /api/policy` | read or save the project policy |
| `POST /api/policy/simulate`, `/check`, `/settings` | `{rules}` (plus `action`, `target` for check) |
| `POST /api/regress` | `{baseline, candidate, threshold}` |
| `POST /api/tripwires/plant`, `/audit` | `{seed, n, document}` or `{seed, n, answer}` |

Sandbox rules, one per line: `allow read src/**`, `allow write src/ and tests/`, `allow run pytest, git status`, `allow network api.github.com`, `never read .env`. Anything not allowed is denied; explicit `never` rules always win.

## Limits

Rule parsing is a small fixed grammar, not an LLM. The sandbox checks what a tool call names, so an allowed command can still read files its name doesn't reveal. Redaction and tripwire audits are heuristics. See `docs/PLAN.md` for the roadmap and known limits.

## Tests

```
python -m unittest discover -s tests
```
