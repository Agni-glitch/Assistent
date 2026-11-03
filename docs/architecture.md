# Architecture

A map of the code for contributors. Fox is a single Python package, `fox`, with three front ends (terminal, voice, desktop app) over one runtime.

```
fox/
├── cli.py              Typer commands: chat, voice, gui, serve, doctor, tools, kill, agents, permissions
├── config.py           Settings from .env (pydantic-settings) and where the data folders are
├── env_file.py         Safe editing of .env from the app
├── runtime.py          Fox: wires everything together, loads tools and plugins, runs a turn
├── server.py           FastAPI app: REST + WebSocket, token auth, the prompt hub
├── core/
│   ├── agent.py        The agent loop: call the model, run the tools it picks, repeat
│   ├── agents.py       Specialist agents: YAML definitions and the registry
│   ├── control.py      Kill switch and turned-off tools (data/control.json)
│   ├── events.py       The event stream every front end renders
│   ├── models.py       LangChain providers, models the user adds, and the fallback router with cooldowns
│   ├── permissions.py  Risk levels, the permission gate, remembered rules
│   ├── storage.py      SQLite: conversations, facts, reminders, audit log, network captures
│   ├── tooling.py      @fox_tool, the tool registry, and the executor
│   └── uploads.py      Chat attachments: saving, reading documents, images for vision models
├── tools/              Built-in tools, one module per area (network.py: capture cleaning and traffic analysis)
├── voice/              Microphone, speech recognition, text-to-speech, voice loop
└── gui/                pywebview window, launcher, and the static web page

extensions/chrome-capture/   A Manifest V3 extension that sends a tab's traffic to /api/captures
```

## A turn, step by step

1. A front end calls `Fox.chat(text, session_id, emit, attachments=..., model=...)` in [`runtime.py`](../fox/runtime.py). Attachments become part of the user message ([`uploads.py`](../fox/core/uploads.py)): documents as untrusted text, images as references that are swapped for the image data only when the model is called.
2. History is loaded from SQLite and trimmed to `FOX_MAX_CONTEXT_TOKENS`. The system prompt is built with the date, the agent roster and saved facts (as untrusted data).
3. [`Agent.run`](../fox/core/agent.py) sends the messages and the enabled tools to the [`ModelRouter`](../fox/core/models.py): the one for the chosen model, the vision order if the chat has images, or the normal fallback order. The router tries each model in the chain, streaming text as `text` events, and falls back on errors or timeouts.
4. For each tool call, [`ToolExecutor.execute`](../fox/core/tooling.py) applies the control gate, validates arguments, computes risk, scope and description, and asks [`PermissionManager.authorize`](../fox/core/permissions.py).
5. `authorize` either decides from the risk and remembered rules, or asks the **approver**: the terminal prompt, the voice loop, or the server's hub, which sends `permission_request` to the app and waits for an answer.
6. The result goes back to the model, marked untrusted if needed. The loop repeats up to `FOX_MAX_STEPS`.
7. New messages are saved, even if the turn failed or was cancelled.

`ask_agent` runs a nested `Agent` with the specialist's prompt, tools and model chain, through the same executor.

## Key ideas

- **One gate.** Nothing calls a tool except `ToolExecutor.execute`. New features that act on the computer must be tools.
- **Approvers are pluggable.** Anything with `async def request(req) -> Decision` can answer prompts. With none attached, `DenyAllApprover` refuses everything.
- **Events, not callbacks.** The runtime emits `Event` objects; the CLI, voice loop and WebSocket each render the same stream.
- **The user's controls are out of the model's reach.** Permissions, control state and keys live in `data/` and `.env`, which file tools refuse, and none are exposed as tools.
- **Plugins reload safely.** Built-in tools are snapshotted on first load; each reload restores them and re-runs every plugin file.

## The desktop app

[`gui/app.py`](../fox/gui/app.py) starts the FastAPI server inside the process, on `FOX_SERVER_PORT` when it's free and on any free port otherwise, and opens a pywebview (WebView2) window pointing at it. The page in [`gui/static/`](../fox/gui/static/) is plain HTML, CSS and JavaScript with no build step. The API token is passed in the URL fragment, so it's never sent over the network.

[`gui/launcher.py`](../fox/gui/launcher.py) is what `Fox.exe` runs first. It starts the app in a child process and retries if Windows kills it during window creation (see [Limitations](limitations.md#known-issues)).

## Tests

[`tests/`](../tests/) uses pytest with fake models ([`conftest.py`](../tests/conftest.py)), so no key or network is needed. All data folders point at a temporary directory. `ScriptedModel` plays back replies and tool calls; `FakeApprover` answers prompts.
