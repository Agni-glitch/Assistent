# Local API

Fox exposes a REST and WebSocket API on your own computer. The desktop app is built on it, and you can use it to build your own front end, a browser extension or an automation.

## Running it

| How | Address |
|---|---|
| `uv run fox serve` | `http://127.0.0.1:8765` (`FOX_SERVER_PORT`) |
| Inside the desktop app | A random free port, used only by the app's own window |

The server listens on `127.0.0.1` only, so other devices on your network can't reach it.

## Authentication

Every request needs the token stored in `data/api_token.txt` (created on first run), except `GET /health` and the app's static files.

- **REST:** header `Authorization: Bearer <token>`
- **WebSocket:** query string `/ws?token=<token>`

Browser pages may only call the API from origins listed in `FOX_ALLOWED_ORIGINS`.

> Anyone holding the token can ask Fox to act and can answer its permission prompts. Treat it like a password.

```powershell
$token = Get-Content data\api_token.txt
Invoke-RestMethod http://127.0.0.1:8765/api/status -Headers @{ Authorization = "Bearer $token" }
```

## REST endpoints

### Status and tools

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check. No token needed. |
| `GET` | `/api/status` | Version, model chain, tool counts, agents, kill switch state and voice availability. |
| `GET` | `/api/tools` | Every tool with its risk level and whether it's on. |
| `GET` | `/api/audit` | The permission decision log. |

### Conversations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sessions` | Saved conversations. |
| `GET` | `/api/sessions/{id}/messages` | Messages in one conversation. |
| `DELETE` | `/api/sessions/{id}` | Delete a conversation. |

### Agents

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/agents` | |
| `PUT` | `/api/agents/{name}` | `{"description": "...", "system_prompt": "...", "tools": [], "models": [], "max_steps": 10}` |
| `DELETE` | `/api/agents/{name}` | |

### Permissions

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/permissions` | Returns `allow`, `deny` and `session` rules. |
| `POST` | `/api/permissions/grant` | `{"tool": "open_app", "scope": "notepad"}`. `scope` defaults to `*`. Critical tools can't be granted. |
| `POST` | `/api/permissions/revoke` | Same body. |

### Control and plugins

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/control` | Kill switch state and turned-off tools. |
| `POST` | `/api/control/kill` | `{"on": true}` |
| `POST` | `/api/control/tools` | `{"tool": "send_email", "enabled": false}` |
| `GET` | `/api/plugins` | Load status of each plugin file. |
| `GET` | `/api/plugins/{name}` | A plugin's source code. |
| `PUT` | `/api/plugins/{name}` | `{"code": "..."}`. Saves `plugins/{name}.py` and reloads all plugins. Returns 422 if it saved but failed to load. |
| `DELETE` | `/api/plugins/{name}` | Deletes the file and reloads. |
| `POST` | `/api/reset` | `{"scopes": ["conversations", "memory", "permissions", "audit", "tools"]}`. Only listed scopes are cleared. |

Plugin and agent names must start with a lowercase letter and contain only lowercase letters, digits, `_` and `-` (up to 40 characters).

### Memory, settings and misc

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/reminders`, `/api/facts` | Pending reminders and saved facts. |
| `DELETE` | `/api/reminders/{id}`, `/api/facts/{id}` | Cancel or forget one. |
| `GET` / `PUT` | `/api/settings` | `.env` settings. Secrets are reported only as set or not set, never returned. |
| `GET` / `PUT` | `/api/prefs` | App preferences such as the theme. |
| `POST` | `/api/voice/test` | Speak a test phrase. |
| `POST` | `/api/open/env`, `/api/open/log` | Open `.env` or the log on the desktop. |

## WebSocket

Connect to `ws://127.0.0.1:<port>/ws?token=<token>`. One connection handles one turn at a time.

### Messages you send

```json
{"type": "message", "text": "Open Notepad", "session_id": null, "speak": false}
{"type": "voice_start", "wake": false, "session_id": null}
{"type": "cancel"}
{"type": "permission_response", "id": "<request id>", "decision": "once"}
```

- `session_id` `null` starts a new conversation; the `session` event tells you its id.
- `decision` must be one of the prompt's `options` (`once`, `session`, `always`, `deny`, `never`). Anything else counts as a refusal.

### Events you receive

| Event | Meaning |
|---|---|
| `session` | The conversation id for this turn. |
| `text` | A chunk of the reply, streamed, in `delta`. |
| `text_reset` | Discard streamed text (a model failed mid-reply and the next one is answering). |
| `model_switch` | Fox fell back to another model. |
| `tool_start`, `tool_end` | A tool began or finished, with a summary and (shortened) output. |
| `agent_start`, `agent_end` | A specialist agent took or returned a task. |
| `permission_request` | Fox is asking. Includes `id`, `tool`, `scope`, `summary`, `description`, `risk`, `agent`, `options` and `timeout` (seconds). |
| `permission_closed` | A prompt was answered elsewhere or timed out. |
| `reminder` | A reminder fired. |
| `control` | The kill switch or tool switches changed. |
| `voice_state`, `voice_heard` | Voice mode state and what was heard. |
| `notice` | Something to show the user. |
| `done` | The reply is complete. |
| `cancelled`, `error` | The turn was cancelled or failed. |
| `turn_end` | Everything for this turn is finished, including speech. Send the next message only after this. |

### Minimal client (Python)

```python
import asyncio, json, pathlib
import websockets

TOKEN = pathlib.Path("data/api_token.txt").read_text().strip()

async def main():
    async with websockets.connect(f"ws://127.0.0.1:8765/ws?token={TOKEN}") as ws:
        await ws.send(json.dumps({"type": "message", "text": "What time is it?"}))
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "text":
                print(event["delta"], end="", flush=True)
            elif event["type"] == "permission_request":
                print(f"\n[asks] {event['summary']} -> denying")
                await ws.send(json.dumps({"type": "permission_response", "id": event["id"], "decision": "deny"}))
            elif event["type"] == "turn_end":
                break

asyncio.run(main())
```

The API is pre-1.0 and may change between releases; changes are listed in the [changelog](../CHANGELOG.md).
