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
| **M4b** API generator (Python/JS/TS/curl + OpenAPI), optional API keys, code pad with isolated JS runner, first-run language choice, settings/personalisation, Apple-style UI refresh | Done |
| **M5** MCP server (`agentlab mcp`) exposing `check_policy`, `record_note`, `plant_canaries`, `audit_answer` to any MCP-capable agent | Done |
| **M6a** Verify OpenClaw and Kimi Code support from official docs; `agentlab connect/disconnect/status`, Kimi hooks, OpenClaw plugin, shared tool-name normalisation, per-project opt-in | Done |
| **M6b** Run the Kimi and OpenClaw integrations inside the real apps and confirm the assumed fields | Next (needs the apps) |
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
| `adapters.py` | normalise tool names and parameters across agents |
| `hooks.py`, `install.py` | the hook all agents call; Claude Code settings merge |
| `connect.py`, `assets/openclaw_plugin.js` | connect, disconnect and status for Claude Code, Kimi Code, OpenClaw |
| `mcp.py` | MCP stdio server for any MCP-capable agent |
| `server.py`, `ui/` | backend and dashboard (`index.html`, `app.js`, `style.css`, `i18n.json`, `runner.html`) |
| `apigen.py` | single description of the API; OpenAPI and client generators |
| `apikeys.py`, `pads.py` | hashed API keys; code pad storage |
| `cli.py` | `agentlab` command |

## Known limits (be honest in docs and UI)

- Sandbox rules check what a tool call names. A command like `cat` on a secret passes if `cat` is allowed; shell commands with `$(...)` or backticks are blocked outright unless `allow run *`.
- Redaction is pattern-based and will miss unusual secret formats.
- Tripwire audits are string heuristics, a cheap signal and not proof.
- MCP is cooperative: the agent chooses to call `check_policy` and `record_note`, so it records and advises but cannot enforce. Enforcement needs the hook (Claude Code) or the proxy (M6).
- Claude Code, Kimi Code and OpenClaw have enforcing adapters (Kimi and OpenClaw not yet run in the real apps, see M6b). Other agents need the proxy (M6) or importers (M7); their hook and plugin support should be verified before promising integration.

- Translations were written without a native-speaker review, and plurals are simple ("1 bloqueadas"). Have speakers check them before release.
- The pad's JS runner is isolated (sandboxed frame, worker, no network, killed after 5 s) but is not a security boundary for hostile code against browser bugs.

## Before a public release

- Add CI (tests on 3.9 to 3.13), a `CHANGELOG`, and a license file chosen by the owner.
- Add an end-to-end test that runs a real Claude Code session with the hooks installed.
- Decide on retention: how long run logs are kept and an `agentlab prune` command.
