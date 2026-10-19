# agentlab plan

Goal: make agentlab something people can plug into the coding agents they already use, with one place to see what happened.

## Status

| Milestone | State |
|---|---|
| **M0** Four core tools: recorder, regression lab, sandbox designer, tripwires | Done |
| **M1** Event schema v1 (`schema.py`) and secret redaction on every write | Done |
| **M2** Claude Code adapter: `agentlab hook` records events and enforces the sandbox policy; `agentlab install claude-code` merges hooks into `.claude/settings.json` | Done |
| **M3** Local backend (`agentlab serve`): REST API, localhost only, safe against DNS rebinding and cross-site posts | Done |
| **M4** Web dashboard: runs timeline with replay and fork, sandbox editor with live attack simulation, regression diff, tripwire audit | Done |
| **M5** MCP server exposing `check_policy`, `record_note`, `plant_canaries`, `audit_answer` to any MCP-capable agent | Next |
| **M6** Model API proxy (OpenAI-style and Anthropic-style) that records and audits any agent with a configurable base URL | Planned |
| **M7** Transcript importers for agents that write session logs to disk | Planned |
| **M8** Python and TypeScript SDK wrappers for the Claude Agent SDK and LangChain; publish to PyPI and npm | Planned |

## Architecture

```
 agents ──► adapters ──► .agentlab/runs/*.jsonl ◄── server (REST) ◄── web UI
 (hook, MCP, proxy,         event schema v1            │
  importers, SDK)           redaction on write         └── sandbox, regress, tripwires
```

- **Source of truth** is a directory of JSONL files (`.agentlab/runs/`). No database, easy to diff and commit.
- **Adapters** only translate an agent's events into schema v1. All logic lives in the core modules.
- **Policy** is a plain-text file at `.agentlab/policy.txt`, shared by the hook and the dashboard.

## Module map

| Module | Responsibility |
|---|---|
| `schema.py` | event kinds, schema version, redaction |
| `recorder.py` | append events (multi-process safe), load, replay, fork, summarize |
| `sandbox.py` | rule parsing, policy checks, tool-call mapping, attack simulation |
| `regress.py` | baseline comparison |
| `tripwires.py` | canary generation and answer audit |
| `hooks.py`, `install.py` | Claude Code adapter |
| `server.py`, `ui/index.html` | backend and dashboard |
| `cli.py` | `agentlab` command |

## Known limits (be honest in docs and UI)

- Sandbox rules check what a tool call names. A command like `cat` on a secret passes if `cat` is allowed; shell commands with `$(...)` or backticks are blocked outright unless `allow run *`.
- Redaction is pattern-based and will miss unusual secret formats.
- Tripwire audits are string heuristics, a cheap signal and not proof.
- Only Claude Code has a native adapter today. Other agents need M5 to M7; their hook and plugin support should be verified before promising integration.

## Before a public release

- Add CI (tests on 3.9 to 3.13), a `CHANGELOG`, and a license file chosen by the owner.
- Add an end-to-end test that runs a real Claude Code session with the hooks installed.
- Decide on retention: how long run logs are kept and an `agentlab prune` command.
