# Local API

Fox exposes a REST and WebSocket API on your own computer. The desktop app is built on it, and you can use it to build your own front end, a browser extension or an automation.

## Running it

| How | Address |
|---|---|
| `uv run fox serve` | `http://127.0.0.1:8765` (`FOX_SERVER_PORT`) |
| Inside the desktop app | `FOX_SERVER_PORT` if it's free, otherwise a random free port. **Settings → Browser capture** shows the address. |

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
| `GET` | `/api/status` | Version, model chain, models ready to use (`available_models`), tool counts, agents, kill switch state and voice availability. |
| `GET` | `/api/tools` | Every tool with its risk level and whether it's on. |
| `GET` | `/api/audit` | The permission decision log. |

### Conversations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sessions` | Saved conversations. |
| `GET` | `/api/sessions/{id}/messages` | Messages in one conversation. A user message has `content` (what was typed) and `attachments` (`id`, `name`, `kind`, `size`). |
| `DELETE` | `/api/sessions/{id}` | Delete a conversation and its attachments. |

### Attachments

| Method | Path | Body or description |
|---|---|---|
| `POST` | `/api/uploads` | `{"name": "report.pdf", "data": "<base64>"}`. Returns `id`, `name`, `kind` (`document`, `image` or `har`) and `size`. 422 with a reason if Fox can't read it. |
| `GET` | `/api/uploads/{id}` | The attachment's details. |
| `GET` | `/api/uploads/{id}/image` | An attached image's bytes. Needs the token header like every other call. |
| `DELETE` | `/api/uploads/{id}` | Remove an attachment you haven't sent. |

Send the ids in the WebSocket `message` as `attachments`.

### Models

| Method | Path | Body or description |
|---|---|---|
| `GET` | `/api/models` | The resolved fallback order (`chain`), models ready to use (`available`), models you added (`custom`, keys never included) and provider status. |
| `PUT` | `/api/models/{name}` | `{"provider": "openai_compatible", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1", "api_key": "...", "vision": false}`. `api_key: null` keeps the saved key, `""` removes it. |
| `DELETE` | `/api/models/{name}` | |
| `POST` | `/api/models/test` | `{"model": "deepseek"}`. Asks for a one-word reply. Returns `ok`, `reply` and `seconds`, or `error`. |

Providers: `openai_compatible`, `azure`, `gemini`, `groq`, `openai`, `anthropic`, `ollama`.

### Network captures

| Method | Path | Body or description |
|---|---|---|
| `POST` | `/api/captures` | `{"source": "my-extension", "entries": [...]}`, up to 1000 entries. Secrets are removed before storing. Returns `stored`, `skipped`, `count` and `latest`. Format: [Browser capture](browser-capture.md#building-your-own-extension). |
| `GET` | `/api/captures` | `?url_contains=&limit=100`. `count`, `latest` and `entries` (without bodies). |
| `DELETE` | `/api/captures` | Delete every capture. |

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
| `POST` | `/api/reset` | `{"scopes": ["conversations", "memory", "permissions", "audit", "tools", "captures"]}`. Only listed scopes are cleared. `conversations` also deletes attachments. |

Plugin and agent names must start with a lowercase letter and contain only lowercase letters, digits, `_` and `-` (up to 40 characters).

### Memory, settings and misc

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/reminders`, `/api/facts` | Pending reminders and saved facts. |
| `DELETE` | `/api/reminders/{id}`, `/api/facts/{id}` | Cancel or forget one. |
| `GET` / `PUT` | `/api/settings` | `.env` settings. Secrets are reported only as set or not set, never returned. |
| `GET` / `PUT` | `/api/prefs` | App preferences: `theme`, `speak`, and `model` (the Model menu's choice, `""` for Auto). |
| `POST` | `/api/voice/test` | Speak a test phrase. |
| `POST` | `/api/open/env`, `/api/open/log` | Open `.env` or the log on the desktop. |

## WebSocket

Connect to `ws://127.0.0.1:<port>/ws?token=<token>`. One connection handles one turn at a time.

### Messages you send

```json
{"type": "message", "text": "Open Notepad", "session_id": null, "speak": false, "attachments": [], "model": null}
{"type": "voice_start", "wake": false, "session_id": null}
{"type": "cancel"}
{"type": "permission_response", "id": "<request id>", "decision": "once"}
```

- `session_id` `null` starts a new conversation; the `session` event tells you its id.
- `attachments` are ids from `POST /api/uploads`. `text` may be empty when there are attachments.
- `model` picks the model for this turn, by fallback-order entry (`gemini`, `groq:some-model`, or a name you added). `null` uses the fallback order.
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
| `captures` | Captured network requests changed. Includes `count` and `latest`. |
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
