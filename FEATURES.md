# agentlab feature checklist

Every item you asked for, with an honest status. Nothing here is marked done unless it is implemented **and**
covered by a test or verified in the running app.

| Status | Means |
|---|---|
| **Done** | Implemented, tested, works in the app. |
| **Partial** | Works, but narrower than the full ask. The gap is written down. |
| **Coming soon** | Not built. Nothing fake ships: there is no stub pretending to work. |

## Agents and models

| Feature | Status | Notes |
|---|---|---|
| Compatible with all models | **Done** | Anthropic, OpenAI, OpenRouter, Groq, Mistral, Gemini, DeepSeek, xAI, Perplexity, Together, Fireworks, Hugging Face, Cloudflare Workers AI, Ollama, LM Studio, llama.cpp, vLLM, OpenClaw, any OpenAI-compatible URL, any HTTP API with a request/response mapping, any CLI agent. |
| Import a model and it becomes an agent (agentiser) | **Done** | From an API, or from your own GGUF weights run locally. |
| Model import by file, URL, GitHub, Hugging Face | **Done** | Upload, GitHub clone, Hugging Face download with checksum checks. |
| Model name + file upload when adding a model | **Done** | Streamed upload with progress, quota-checked. |
| Preset role or none (manager, dev, ...) | **Done** | 15 roles; they fill instructions, skills, plugins and safe permissions. |
| Local CPU choice and preset tokenizer | **Done** | device auto/cpu/gpu, thread count, 4 tokenizer estimates plus a real byte-level BPE from a tokenizer file. |
| Connect GitHub / Hugging Face / GPT / Anthropic | **Done** | Token verified with the service before it is stored. |
| Private models needing a password | **Done** | scrypt hash, unlock tokens, rate-limited. Honest limit: protects the app, not the files on disk. |
| Check ownership of a model | **Coming soon** | We can show the licence and author from Hugging Face metadata, but agentlab cannot verify that you own a model. |
| Embedding models | **Done** | Built-in offline embedder plus any OpenAI-compatible `/embeddings`. |
| Models dashboard | **Done** | Usage, p50/p95 latency, cost, test battery results, local weights with a hardware fit check. |

## Control, safety, insight

| Feature | Status | Notes |
|---|---|---|
| Ask before execute / don't ask / full auto | **Done** | Plus allow-ask-deny per action type. |
| Authorizations, custom instructions, preferences, custom prompt | **Done** | |
| Token limit per output, budgets, token barrier | **Done** | Per answer, per job, per day. |
| Protection from dangerous commands | **Done** | Shell-aware classifier: hard blocks survive "off", risky commands always ask. |
| Anti-hallucination barrier | **Partial** | Tripwires (canary facts planted in documents, answers audited) and an evaluation-case gate. It is a heuristic, not a guarantee. |
| Debugger as you go | **Done** | Full trace, pause before each model or tool call, edit or skip a tool call. |
| Progress bars and metrics per task | **Done** | |
| Token usage insight, analytics, more insights | **Done** | By agent, model, project and kind of work. |
| Agent health insights, diary, memory | **Done** | |
| Security | **Partial** | Local-only binding, API keys, CSP, sandboxed widget frames, path-traversal-safe file API, secret redaction in logs, no secrets on command lines. Not a container sandbox: agent commands run as your user with a scrubbed environment and (where available) no network. |
| Control center | **Done** | The Jobs page: live jobs, parallel limit, approvals, batches. |
| Notifications, AI suggestions | **Done** | |
| Deadline feature (model plans to finish in time) | **Done** | Tell a job "finish within N minutes": the agent is given its time budget, plans backwards, finishes with what is real, and is stopped cleanly at the deadline. |

## Work

| Feature | Status | Notes |
|---|---|---|
| Background and autonomous agentic tasks | **Done** | |
| Schedule tasks (cron / every N minutes) | **Done** | No overlap, no catch-up after downtime, auto-pause after repeated failures. |
| Run parallel tasks | **Done** | Resizable limit, compare batches, fan-out, isolated project copies. |
| Agent swarm: an agent fetches another for help | **Done** | `ask_teammate`: the teammate answers inside the asking job's own token budget, with no tools and no nesting, so a swarm cannot run away. |
| Multi sub-agent specialised deployment | **Partial** | Swarm (above) and rooms cover delegation and cooperation. There is no separate long-lived sub-agent process per specialist. |
| Agent chat / rooms, cooperate like humans | **Done** | @mentions, manager opens and closes, task mode ends with a result. |
| Agent playground, agent pad, code pad | **Done** | The code pad has syntax highlighting, smart indent, bracket and quote pairing, comment toggle, line move and duplicate, find and replace, a status bar, and live correction suggestions as you type with one-click fixes. |
| Model comparator (same prompt, two of your models, analytics) | **Done** | Side-by-side answers with tokens in/out, cost, cost per 1k output, median latency and spread over repeated runs, tokens per second, length, and word overlap between answers. Quality ranking is a separate opt-in step by a judge model, labelled as an opinion. |
| Project system with attributed space | **Done** | Quota per project, enforced on writes, uploads and packaging. |
| Dedicated environments per task | **Done** | Separate working copy, scrubbed environment, network off where the machine allows it. |
| Agents control the environment with permissions | **Done** | Variables, packages, network policy, all permissioned; turning network on always asks. |
| User-created commands | **Done** | Prompt and shell `/commands`. |
| Containers | **Partial** | Tasks can run in Docker when Docker is available; a Docker + API template is included. The playground itself does not run containers. |
| Connect Docker / Cloudflare / OpenClaw | **Done** | Docker task backend, Cloudflare Workers AI + Pages/Workers deploy, OpenClaw via Gateway or CLI. |
| Export as OpenClaw | **Partial** | agentlab connects to OpenClaw and ships an OpenClaw plugin; it does not export an agent as an OpenClaw package. |
| VS Code compatible / export to VS Code | **Done** | A VS Code workspace template: settings, tasks (serve, tests), launch config, recommended extensions and a devcontainer with port 8765 forwarded. |

## Build and ship

| Feature | Status | Notes |
|---|---|---|
| Website / game / model / infrastructure builders | **Done** | 20 templates, all working offline, "build with AI" customises them. |
| Design templates | **Done** | Design system (tokens, type scale, components, light + dark). |
| Finance templates | **Done** | Runway and unit-economics model with projection table and chart. |
| Slides creation | **Done** | Keyboard-driven deck, content in one file, prints to PDF. |
| Draw feature | **Done** | Vector sketch pad: pen, shapes, arrows, text, exports real editable SVG. |
| AI music creator, sound integrator | **Partial** | A step sequencer and synth that exports a real WAV. It is generated from your pattern, not from a text prompt. |
| Image and video creation | **Coming soon** | Needs an image/video model. agentlab can call one once you connect it, but nothing is built yet, and we will not fake it. |
| 3D maquette design | **Coming soon** | |
| Interactive cool presentations | **Done** | The slide deck template, plus widgets in chat. |
| Web templates and tutorial | **Done** | |
| Website and game import | **Done** | GitHub import into a project. |
| Doc builder with templates | **Done** | README, API reference (OpenAPI or code), guide, changelog, contributing, with an HTML copy. |
| Packager, installer, compatibility check, optimisation check | **Done** | |
| API generator | **Done** | OpenAPI plus Python, JavaScript, TypeScript and curl clients. |
| Cloudflare and localhost deploy in one click | **Done** | Cloudflare path tested with a stand-in for wrangler, not against a real account. |
| Skills and plugins library, skill creation | **Done** | 16 skills, 10 sandboxed plugins, custom skills and plugins. |
| Dynamic builder | **Partial** | Templates plus "build with AI". No free-form drag-and-drop builder. |
| Book humaniser | **Coming soon** | |
| Engineering / science / medical pads | **Partial** | The code pad and agent pad are general-purpose. No domain-specific pads. |
| Product design planner, calendar planner, innovator | **Coming soon** | Schedules cover recurring work; there is no calendar or planning board. |
| Deep research | **Partial** | Web scrape into a knowledge base, plus a `web_fetch` tool. No multi-step research agent preset. |

## Training

| Feature | Status | Notes |
|---|---|---|
| Train specialised models, Train button, choose model + dataset | **Done** | |
| Real training process | **Done** for the built-in classifier | It really trains (logistic regression on hashed features) on your CSV/JSONL and the loss curve is real. |
| LoRA fine-tuning | **Partial** | agentlab writes the training script and runs it in your project's Python environment. Verified end to end with a stand-in process; **not** yet run against a real GPU and real torch here. |
| Loss tracking, edit a model | **Done** | Live curves; teach a correction, rename or drop labels, undo. |
| Overfit protection | **Done** | Validation split, early stopping, best epoch kept, plain-language warnings. |
| Auto-improvement gated by tests | **Done** | A change is accepted only when the score rises and nothing that passed starts failing. |
| Dream mode (thinks at night) | **Done** | |
| Web scrape integrated into a model | **Done** | Into a knowledge base the agent searches while working. |

## Interface

| Feature | Status | Notes |
|---|---|---|
| Clean Apple-style, simple, efficient UI | **Done** | |
| No emojis, SVG only | **Done** | Every emoji removed from the interface; icons are inline SVG. |
| Personalise UI and gradients | **Done** | Themes, accents, text size, 11 gradient presets plus a custom gradient builder. |
| Six languages | **Partial** | Navigation and the original tools are translated into 6 languages; the newer platform screens are English. |
| Glass feature | **Done** | Frosted panels on the sidebar and overlays. |
| User-friendly setup | **Done** | Tutorial, demo that needs no API key, model listing so you pick a model name instead of typing it. |

## What is explicitly not real yet

Image generation, video generation, 3D maquettes, book humaniser, calendar/planner, ownership verification, and
exporting an agent as an OpenClaw package. LoRA training and Cloudflare deploys are implemented but were verified
with stand-in processes, not against a real GPU or a real Cloudflare account.
