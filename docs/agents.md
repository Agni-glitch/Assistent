# Creating agents

A specialist agent is a focused assistant with its own instructions, its own set of tools and, optionally, its own model chain. Fox hands tasks to it with the `ask_agent` tool and relays the result to you.

Use an agent when a job benefits from narrow instructions or a restricted tool set: a researcher that can only read the web, a file organiser that can't send email, an email assistant that uses a cheaper model.

## Starter agents

| Agent | Purpose | Tools |
|---|---|---|
| [`researcher`](../agents/researcher.yaml) | Researches a topic on the web and returns a sourced summary | `web_search`, `fetch_webpage`, `get_datetime` |
| [`file_organizer`](../agents/file_organizer.yaml) | Finds, sorts and tidies files and folders | `list_folder`, `search_files`, `read_file`, `move_path`, `delete_path` |
| [`email_assistant`](../agents/email_assistant.yaml) | Reads and summarises the inbox, and drafts or sends email | `list_emails`, `read_email`, `send_email`, `search_memory` |

## Four ways to create one

1. **Desktop app:** the **Agents** page.
2. **Chat:** *"Create an agent called travel_planner that plans trips using web search."* Fox calls `create_agent`, which asks your permission and shows the full definition.
3. **Terminal:**

   ```powershell
   uv run fox agents add travel_planner `
     --description "Plans trips and compares options." `
     --prompt "You plan trips. Compare options and give clear recommendations." `
     --tools web_search,fetch_webpage `
     --models groq,gemini
   ```

4. **A YAML file** in `agents/`. Changes apply on the next message; no restart.

## The YAML format

```yaml
name: travel_planner
description: Plans trips and compares options.   # Fox reads this to choose the agent
system_prompt: |
  You plan trips. Compare options on price, travel time and convenience,
  and give a clear recommendation with links.
tools: [web_search, fetch_webpage, get_datetime] # or ["*"] for every tool
models: [groq, gemini]                           # optional fallback chain for this agent
max_steps: 10
```

| Field | Required | Rules |
|---|---|---|
| `name` | yes | Lowercase letter first, then lowercase letters, digits, `_` or `-`; up to 40 characters. The file is saved as `<name>.yaml`. |
| `description` | yes | One line. Fox sees every agent's description in its system prompt and uses it to decide when to delegate, so make it specific. |
| `system_prompt` | yes | The agent's instructions. |
| `tools` | no | Tool names from `fox tools`, including plugin tools, or `["*"]` for all. Empty means no tools. |
| `models` | no | Entries like `FOX_MODEL_CHAIN` (`provider` or `provider:model`). Empty uses the main chain. |
| `max_steps` | no | 1 to 50, default 10. The most model/tool rounds per task. |

An invalid file is skipped and the reason is logged in `data/fox.log`.

## How delegation works

1. Fox decides an agent fits, and calls `ask_agent(agent, task)`. Handing off is safe and doesn't prompt; whatever the agent then does does.
2. The agent runs with its own prompt, plus a note that it works for Fox, should only claim what tool results confirm, and must treat `<untrusted_data>` as information.
3. It starts fresh: it sees only the `task` text, not your conversation. Fox is told to include everything the agent needs.
4. Each tool call goes through the same permission gate, and the prompt names the agent asking.
5. The agent's final answer goes back to Fox as the tool result, and Fox replies to you.

## Rules agents can't break

- An agent can only use the tools in its list, minus any you turned off.
- Agents can't call `ask_agent`, `create_agent` or `delete_agent`, so they can't delegate further or change the agent roster.
- Remembered permissions are shared: "always allow" given to Fox applies to agents too, and vice versa. Critical actions always ask.
- The kill switch stops agents as well.

## Writing a good agent

- **Description:** say what it's for *and* when to pick it. "Researches a topic on the web and returns a sourced summary" beats "Research helper".
- **Tools:** give the fewest tools that do the job. Fewer tools means fewer mistakes and fewer prompts. Avoid `["*"]` unless you really want a general agent.
- **Prompt:** describe the output you want back (format, length, sources), since Fox relays it.
- **Models:** a fast, cheap model is often fine for narrow agents. Agents that call tools need a model that supports tool calling.

## Ideas

| Agent | Tools |
|---|---|
| `downloads_cleaner`: sorts Downloads by type and age | `list_folder`, `search_files`, `move_path` |
| `daily_brief`: weather, reminders and unread email in one summary | `web_search`, `list_reminders`, `list_emails` |
| `pc_doctor`: explains what's slowing the computer down | `get_system_info`, `list_running_apps` |
| `meeting_prep`: researches the people and company before a meeting | `web_search`, `fetch_webpage`, `remember_fact` |

Combine agents with [plugins](plugins.md) to build specialists for your own services.
