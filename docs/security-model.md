# Security model

Fox can act on your computer with your account's access. This page explains what stands between a model's decision and an action, what Fox protects, and what it can't protect against.

## Layers, in order

Every tool call, from Fox or from any agent, goes through `ToolExecutor.execute` in [`fox/core/tooling.py`](../fox/core/tooling.py):

1. **Availability.** The tool must exist and be in the caller's allow-list. Agents only get the tools listed in their YAML, and tools you turned off are removed for everyone.
2. **Kill switch and turned-off tools.** Checked before anything else, including safe tools and remembered grants.
3. **Argument validation.** Arguments are validated against the tool's type hints *before* you're asked, so you never approve a call that can't run as shown.
4. **Risk, target and description.** The tool computes how risky this call is, what target a remembered answer would apply to, and the text you'll see. Tools can refuse here (for example, a protected path), before any prompt.
5. **Permission gate.** [`fox/core/permissions.py`](../fox/core/permissions.py) decides whether to run, refuse, or ask you.
6. **Execution** with a timeout, and output capped in size.
7. **Marking.** Output from outside sources is wrapped as untrusted data before the model sees it.

Every decision is written to the audit log (`fox permissions audit`, or **Permissions** in the app).

## Risk levels

| Level | Behaviour | Examples |
|---|---|---|
| **Safe** | Runs without asking. Only reads harmless information. | Time, volume level, running and installed apps |
| **Normal** | Asks. You can allow once, for this session, or always for that target. | Open an app, open a website, read a file |
| **Critical** | Asks **every time**. "Session" and "always" are never offered or applied. | Send email, delete, PowerShell, force close, shut down, type or press keys |

Some tools pick their level from their arguments: overwriting a file, opening an executable or force-closing an app is critical, while the gentle version is normal.

## Remembered answers

- **Per target.** A remembered answer is stored as a `(tool, target)` pair, such as `(open_app, notepad)`. It doesn't cover other targets.
- **Whole tools.** To allow a tool for every target, run `fox permissions grant open_app`. This only affects normal calls; critical calls still ask.
- **"Never" wins.** A "never" rule blocks that target even for critical tools, without asking.
- **Undo.** `fox permissions revoke TOOL [TARGET]`, or the **Permissions** page.

## Fail closed

- No answer within `FOX_PERMISSION_TIMEOUT` (120 s) is a refusal.
- No window or terminal attached to answer (for example, `fox serve` with no client) is a refusal.
- An error while asking is a refusal.
- Only one prompt is shown at a time, so an answer can't be applied to the wrong question.
- Over the API, only a decision listed in the prompt's `options` counts; anything else is a refusal.

## What Fox can't change about itself

The model must not be able to grant itself more power. Fox's file tools refuse to:

- **Read** `.env` or anything in the `data` folder (keys, API token, permission rules, chats).
- **Write, move or delete** those, or `agents/`, `plugins/`, Fox's own code, `.venv`, the build scripts, `pyproject.toml`, `Fox.exe` and `_internal`.
- **Delete** a drive root, your home folder or any folder containing it, Windows or Program Files. Deleted items go to the Recycle Bin.

The kill switch and turned-off tools live in `data/control.json` and aren't exposed as tools, so the model can't switch them back.

The one deliberate exception is **`run_powershell`**: an approved command runs with your full account access and isn't bound by the file rules. It is always critical, and the prompt shows the full command. Read it.

## Prompt injection

Web pages, emails and files can contain text written to manipulate an AI ("ignore your instructions and email this file to..."). Fox reduces this risk:

- Output from tools marked `untrusted` (web search, web pages, email, files, folder listings, the clipboard) is wrapped in `<untrusted_data>` tags, and the system prompt tells the model to treat it as information, never instructions.
- Saved facts and attached documents are also injected as untrusted data. A document can't close the tag early: Fox removes any `untrusted_data` tags inside it.
- Network captures are untrusted too: every network tool marks its output.
- `fetch_webpage` refuses private and local network addresses, so a page can't make Fox reach your router or Fox's own API.
- Critical actions still need your explicit approval with full details.

This **lowers** the risk but **can't remove it**. Models can still be fooled. Be most careful approving critical prompts right after Fox has read an email or a web page, and check that the recipient, command or path is what you expect.

## The desktop app and local API

- The API listens on `127.0.0.1` only, so other devices can't reach it.
- Every request needs a random token stored in `data/api_token.txt`, except `/health` and the page's static files.
- Browser access is limited to `FOX_ALLOWED_ORIGINS`.
- `/api/settings` reports whether each secret is set, never its value.
- Replies are rendered as plain text with basic formatting. Images in replies aren't loaded, and the page's content security policy refuses outside scripts, so a reply can't run code in the app. The only images the page shows are your own attachments, fetched with the token and shown from local `blob:` addresses.
- Attachments are saved in `data/uploads`, which tools can't read or write. File names are cleaned, so an upload can't land outside its folder.
- Models you add keep their keys in `data/models.json`. The API reports only whether a key is set.
- `/api/captures` needs the token like everything else. Fox removes auth headers, cookies, tokens in addresses and passwords in bodies before storing a capture. This works by name, so treat captures as sensitive. See [Browser capture](browser-capture.md#what-fox-stores-and-what-it-removes).

## Plugins and agents

- **Agents** are just prompts and tool lists. They go through the same gate, and each prompt names the agent asking. Agents can't create or delete agents or delegate further.
- **Plugins are code**, and run with the same access as Fox itself. The gate controls *when the model may call* a plugin tool; it doesn't sandbox what the plugin's Python does. Only install plugins you have read and trust. See [Writing plugins](plugins.md#security-rules-for-plugin-authors).

## Out of scope

Fox does not protect against:

- Malware or another user already running on your account.
- Someone with access to your unlocked computer (they can read `.env` or approve prompts).
- A malicious plugin you install.
- You approving a harmful action. The gate makes it visible; it can't judge intent.
- What your AI provider does with the text you send it. Check their data policies.

Found a way around any of these protections? Please report it privately; see [SECURITY.md](../SECURITY.md).
