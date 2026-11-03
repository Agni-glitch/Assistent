# Writing plugins

A plugin gives Fox a new ability. It is a Python file in the `plugins` folder with one or more functions decorated with `@fox_tool`. The model reads each function's docstring to decide when to call it, and every call goes through the same [permission gate](security-model.md) as Fox's built-in tools.

- [Quick start](#quick-start)
- [How plugins are loaded](#how-plugins-are-loaded)
- [The `@fox_tool` decorator](#the-fox_tool-decorator)
- [Arguments and return values](#arguments-and-return-values)
- [Choosing a risk level](#choosing-a-risk-level)
- [Scope: what "always allow" applies to](#scope-what-always-allow-applies-to)
- [Describe: what the user sees](#describe-what-the-user-sees)
- [Untrusted output](#untrusted-output)
- [Errors](#errors)
- [Timeouts, async and blocking code](#timeouts-async-and-blocking-code)
- [Patterns](#patterns)
- [Security rules for plugin authors](#security-rules-for-plugin-authors)
- [Testing a plugin](#testing-a-plugin)
- [Using plugins with agents](#using-plugins-with-agents)
- [Plugins in Fox.exe](#plugins-in-foxexe)
- [Limitations](#limitations)
- [Checklist](#checklist)
- [Sharing your plugin](#sharing-your-plugin)

## Quick start

1. Create `plugins/coin.py`:

   ```python
   import random

   from fox.core.permissions import Risk
   from fox.core.tooling import fox_tool


   @fox_tool(risk=Risk.SAFE)
   def flip_coin() -> str:
       """Flip a coin and return heads or tails."""
       return random.choice(["heads", "tails"])
   ```

2. Load it:
   - **Desktop app:** **Control → Add plugin**, paste the code, name it `coin`, save. It loads at once.
   - **By hand:** save the file, then restart Fox, or open **Control** and save any plugin to reload them all.

3. Check it's there: `uv run fox tools`, or **Permissions → All tools**.

4. Ask Fox: *"Flip a coin for me."*

Two ready-to-copy examples ship in the `plugins` folder. Files that start with `_` are not loaded, so copy them to a new name first:

| File | Shows |
|---|---|
| [`_example_plugin.py`](../plugins/_example_plugin.py) | The smallest possible tool. |
| [`_example_advanced_plugin.py`](../plugins/_example_advanced_plugin.py) | Async HTTP, scope, describe, a risk that depends on the arguments, untrusted output, `ToolError`, and reusing Fox's file guards. |

## How plugins are loaded

- Fox loads every `*.py` file in the plugins folder, in alphabetical order, at start-up. Files whose names start with `_` are skipped.
- Each file is executed as its own module (`fox_plugin_<filename>`). Every function decorated with `@fox_tool` in it becomes a tool **named after the function**.
- Saving, editing or deleting a plugin from the **Control** page (or the [API](api.md)) reloads **all** plugins: the built-in tools are restored first, then every plugin file runs again. No restart is needed.
- A plugin that raises an error while loading is reported on the **Control** page and in `data/fox.log`, and the other plugins still load.
- New plugin tools are **on** immediately, and still behind permission prompts. Turn any off under **Permissions → All tools**; that choice is remembered by tool name.

Because the module runs again on every reload, keep import-time code cheap and free of side effects. Don't start threads, open connections or write files at import time.

## The `@fox_tool` decorator

```python
from fox.core.tooling import fox_tool

@fox_tool(
    risk=Risk.NORMAL,      # Risk, or a function of the arguments that returns a Risk
    scope=None,            # function of the arguments -> str: the target an answer is remembered for
    describe=None,         # function of the arguments -> str: the text shown in the prompt
    untrusted=False,       # True if the output comes from outside (web, email, files)
    timeout=...,           # seconds; default FOX_TOOL_TIMEOUT; None for no limit
)
def my_tool(...) -> str:
    """What the tool does. The model reads this."""
```

| Parameter | Default | Purpose |
|---|---|---|
| `risk` | `Risk.NORMAL` | How dangerous a call is. See [Choosing a risk level](#choosing-a-risk-level). |
| `scope` | every call shares one target, `*` | What "always allow" and "never allow" apply to. |
| `describe` | `Run <name>` plus the arguments as JSON | The prompt text. First line is the summary; the rest is detail. |
| `untrusted` | `False` | Wrap the output as data the model must not obey. |
| `timeout` | `FOX_TOOL_TIMEOUT` (120 s) | Seconds before the call is abandoned. `None` disables it. |

`risk`, `scope` and `describe` functions receive **one argument: a dict of the validated arguments**, with defaults filled in. They run before the user is asked, so they must be fast and must not change anything.

The docstring is the tool description the model sees. Write it for the model:

- Say what the tool does and when to use it, in one or two sentences.
- Explain each argument and its format, with an example (`"a local ISO date and time, such as 2026-11-01T17:30:00"`).
- Mention anything it **doesn't** do that a model might assume ("Doesn't mark emails as read.").

## Arguments and return values

**Arguments** come from the function signature. Use type hints, because they become the schema the model fills in, and arguments are validated against it before anything else happens.

```python
@fox_tool(risk=Risk.SAFE)
def convert_units(value: float, from_unit: str, to_unit: str = "km") -> str:
    """Convert a distance. Units: 'km', 'mi', 'm' or 'ft'. `to_unit` defaults to km."""
```

- Supported: `str`, `int`, `float`, `bool`, `list[...]`, `dict`, `Literal[...]`, optional values with defaults, and Pydantic models.
- Prefer a few simple arguments over one complex object; models fill flat schemas more reliably.
- `Literal["play", "pause", "next"]` is a good way to give the model a fixed set of choices.
- Avoid `*args` and `**kwargs`.

**Return values** are sent back to the model as text:

- Return a `str` for prose, or a `dict`/`list` (it's serialised to JSON).
- Keep it short. Output over `FOX_MAX_TOOL_OUTPUT` (20,000 characters) is cut off, and long output costs tokens and context.
- Return what the model needs to answer the user, and confirm what actually happened ("Saved 120 characters to C:\Notes\todo.md."). Fox's system prompt tells the model to claim success only when a tool result confirms it.

## Choosing a risk level

```python
from fox.core.permissions import Risk
```

| Level | Use for | Examples |
|---|---|---|
| `Risk.SAFE` | Reading harmless, non-private information with no side effects. Runs **without asking**. | Time, a coin flip, unit conversion, CPU load |
| `Risk.NORMAL` | Anything that changes something reversible, reads private data, or reaches the network. Asks; the answer can be remembered. | Open an app, read a file, call a web API, create a note |
| `Risk.CRITICAL` | Anything hard to undo, that leaves your computer, costs money, or runs code. Asks **every time**. | Send a message, delete, overwrite, run a command, buy something, post publicly |

When in doubt, go one level higher. A tool that reads anything private (files, email, calendar, browser history) is never `SAFE`.

**Risk from the arguments.** Pass a function when only some calls are dangerous:

```python
@fox_tool(risk=lambda a: Risk.CRITICAL if a["force"] else Risk.NORMAL)
def stop_service(name: str, force: bool = False) -> str:
    """Stop a Windows service. `force` kills it without a clean shutdown."""
```

## Scope: what "always allow" applies to

When the user picks **Always allow** or **Never allow**, Fox stores the pair `(tool, scope)`. Without a `scope` function, every call shares the target `*`, so one "always allow" covers **every future call** with any arguments. That's rarely what you want for a normal tool.

Return the thing the user is really approving:

| Tool | Good scope |
|---|---|
| Open an app | The app name |
| Call a website or API | The host name |
| Read or write files | The folder |
| Send to a person | The recipient |
| Control a smart device | The device name |

```python
@fox_tool(scope=lambda a: a["device"].strip().lower(), describe=lambda a: f"Turn {a['state']} {a['device']}")
def set_light(device: str, state: Literal["on", "off"]) -> str:
    """Turn a smart light on or off by its name, such as 'desk lamp'."""
```

Scopes are compared in lower case. Normalise them (strip spaces, resolve paths) so the same target always produces the same string; otherwise a remembered answer won't match next time.

## Describe: what the user sees

The prompt is the user's only view of what will happen, so `describe` must reflect **exactly** what the tool is about to do.

- **First line:** a short summary, shown in lists and the activity feed ("Send email to anna@example.com").
- **Remaining lines:** full detail, shown in the prompt (the full message body, the full command, the full path).
- Show the **resolved** values the tool will use (an absolute path, a normalised URL), not just what the model typed.
- Never hide or shorten the part that matters for safety. Truncating a long body is fine; truncating a recipient list is not.

```python
def _describe_post(a: dict) -> str:
    return f"Post to #{a['channel']}\n\n{a['text']}"

@fox_tool(risk=Risk.CRITICAL, scope=lambda a: a["channel"], describe=_describe_post)
def post_message(channel: str, text: str) -> str:
    """Post a message to a team chat channel. Ask the user for the exact text first."""
```

## Untrusted output

Set `untrusted=True` whenever the output contains text that someone other than the user wrote: web pages, API responses, emails, documents, chat messages, file contents. Fox then wraps it:

```
<untrusted_data source="your_tool">
...output...
</untrusted_data>
```

and the model is told never to follow instructions inside it. This is Fox's main defence against [prompt injection](security-model.md#prompt-injection). If you're unsure, set it.

## Errors

Raise `ToolError` for expected failures. Its message goes back to the model as the result, so write it for the model and the user:

```python
from fox.core.tooling import ToolError

if response.status_code == 404:
    raise ToolError(f"No city called '{city}' was found. Ask the user to check the spelling.")
```

Any other exception is logged with its traceback in `data/fox.log`, and the model sees `Error: <tool> failed: <message>`. Prefer `ToolError` with a clear message to letting library exceptions escape; many have empty or cryptic messages.

**Refuse before the prompt.** If you can tell a call is invalid from its arguments, raise `ToolError` inside `risk`, `scope` or `describe`. Those run before the user is asked, so the user never sees a prompt for a call that will be refused. The advanced example does this for protected paths and wrong file types.

## Timeouts, async and blocking code

- Tools can be plain functions or `async def`. Prefer `async` for network calls (use `httpx.AsyncClient`, which Fox already depends on).
- A plain function runs in a worker thread, so it doesn't freeze Fox.
- After `timeout` seconds the model is told the call timed out **and may still have taken effect**. Python can't kill a running thread, so a blocking function keeps running in the background. Use library timeouts too (for example `httpx.AsyncClient(timeout=15)`), so work really stops.
- Set a shorter `timeout` for quick lookups (20–30 s), and `None` only for tools that wait on other tools.

## Patterns

### Call a web API with a key

Never hard-code secrets. Add the key to `.env`, then read it from there. Fox doesn't copy unknown `.env` entries into the environment, so use `python-dotenv` (installed with Fox):

```python
import os

import httpx
from dotenv import dotenv_values

from fox.config import ROOT
from fox.core.tooling import ToolError, fox_tool


def _key() -> str:
    key = os.environ.get("TODOIST_API_KEY") or dotenv_values(ROOT / ".env").get("TODOIST_API_KEY")
    if not key:
        raise ToolError("TODOIST_API_KEY isn't set in .env.")
    return key


@fox_tool(describe=lambda a: f"Add a Todoist task: {a['content']}", timeout=20)
async def add_task(content: str, due: str = "") -> str:
    """Add a task to the user's Todoist inbox. `due` is optional natural language, like 'tomorrow 9am'."""
    payload = {"content": content, **({"due_string": due} if due else {})}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.todoist.com/rest/v2/tasks",
            json=payload,
            headers={"Authorization": f"Bearer {_key()}"},
        )
    if r.status_code >= 400:
        raise ToolError(f"Todoist refused the task ({r.status_code}).")
    return f"Added task '{content}'."
```

Note that plugin code can read `.env` even though Fox's file tools can't. That's why plugins must be trusted code; see the [security rules](#security-rules-for-plugin-authors).

### Work with files safely

Reuse Fox's guards so your plugin can't be used to read Fox's keys or rewrite its permissions:

```python
from fox.tools.files import check_readable, check_writable, resolve

target = check_readable(resolve(path))   # refuses .env and the data folder
target = check_writable(resolve(path))   # also refuses Fox's code, agents and plugins
```

`resolve` expands `~` and `%VARS%` and returns an absolute path. Use it in `scope` and `describe` too, so the prompt shows the real path.

### Run a program

Prefer a dedicated tool with fixed arguments over a general "run anything" tool. Pass arguments as a list, never through a shell, and mark it critical if the arguments are free text:

```python
import subprocess

@fox_tool(risk=Risk.NORMAL, scope=lambda a: a["repo"], describe=lambda a: f"git pull in {a['repo']}", timeout=60)
def git_pull(repo: str) -> str:
    """Pull the latest changes for a git repository folder."""
    target = check_writable(resolve(repo))
    done = subprocess.run(["git", "-C", str(target), "pull", "--ff-only"], capture_output=True, text=True, timeout=50)
    return (done.stdout + done.stderr).strip() or "Done."
```

### Keep state between calls

Module-level variables are reset on every plugin reload. Store anything that must last in a file of your own, outside Fox's `data` folder, such as `%LOCALAPPDATA%\FoxPlugins\<plugin>.json`.

### Several tools in one file

Group related tools in one file (`spotify.py` with `play`, `pause`, `queue`). Give them distinct, descriptive names: tool names share one namespace across Fox.

## Security rules for plugin authors

The permission gate decides **when the model may call** your tool. It does not sandbox **what your Python does**. A plugin has the same access as Fox, which is the same access as you. So:

1. **Pick the honest risk level.** Never mark a tool `SAFE` to avoid prompts if it changes anything or reads private data.
2. **Make `describe` truthful and complete.** The user approves what they see. A tool that does more than its description is a bug at best.
3. **Scope narrowly.** One "always allow" should not unlock more than the user expects.
4. **Mark outside content `untrusted=True`.**
5. **Never touch Fox's own files**: `.env`, `data/`, `agents/`, `plugins/`, `permissions.json`, `control.json`. Use `check_readable`/`check_writable` for any path that comes from the model.
6. **Don't build a bypass.** No tool that runs arbitrary code, edits permissions, flips the kill switch, or calls other tools directly around the gate.
7. **Don't send data anywhere the description doesn't say.** No telemetry.
8. **Don't override a built-in tool.** A function with the same name as a built-in (`open_app`, `read_file`, ...) replaces it until the plugin is removed. If two plugins define the same name, the file later in alphabetical order wins.

As a user: **read a plugin before installing it.** Treat it like any program you download.

## Testing a plugin

**Try it live.** Load it, run `fox tools` to confirm it registered with the right risk, then ask Fox to use it and check the prompt text.

**Test it in isolation.** Fox's tests point every folder at a temporary directory and use fake models, so they need no keys and cost nothing. [`tests/test_plugins.py`](../tests/test_plugins.py) tests the example plugins through the real executor and permission gate; copy its pattern:

```python
from conftest import FakeApprover
from fox.core.events import noop_emit
from fox.core.permissions import Decision, Risk
from fox.core.tooling import REGISTRY


async def test_my_tool_asks_once(make_fox):
    approver = FakeApprover(Decision.ONCE)          # answers the first prompt with "Allow once"
    fox = make_fox(approver=approver)
    fox.settings.fox_plugins_dir.mkdir(parents=True, exist_ok=True)
    (fox.settings.fox_plugins_dir / "mine.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    fox.reload_tools()

    out = await fox.executor.execute("my_tool", {"city": "Berlin"}, "fox", set(REGISTRY), noop_emit)

    assert approver.requests[0].risk is Risk.NORMAL
    assert approver.requests[0].scope == "berlin"
```

Run the tests with `uv run pytest`.

See [`tests/conftest.py`](../tests/conftest.py) and [`tests/test_fox.py`](../tests/test_fox.py) for more fixtures, including a scripted model that makes tool calls.

Check in particular:

- The risk level for each kind of argument.
- That `scope` gives the same string for equivalent inputs.
- That invalid input raises `ToolError` **before** the prompt.
- That `describe` shows everything the tool will do.

## Using plugins with agents

Plugin tools are available to Fox as soon as they load. A [specialist agent](agents.md) can use one if its `tools` list names it, or if it uses `["*"]`:

```yaml
name: home_controller
description: Controls smart lights and plugs.
system_prompt: You control the user's smart home devices.
tools: [set_light, list_devices]
```

Turning a tool off under Permissions removes it from every agent too.

## Plugins in Fox.exe

The built app loads plugins from its own `plugins` folder: the project's `plugins` folder when `Fox.exe` is in the project root, otherwise `%LOCALAPPDATA%\Fox\plugins`. See [Building Fox.exe](building.md#where-the-app-keeps-its-data).

`Fox.exe` contains its own Python, so a plugin can only import libraries that are **bundled** in it (everything Fox itself uses, such as `httpx`, `psutil`, `pyautogui`, `beautifulsoup4`, `pydantic`, `pyyaml`). A plugin that needs another library has to run from source (`uv add <library>`, then `uv run fox gui`), or you add the library to the project and rebuild.

## Limitations

- **No sandbox.** Plugins run in Fox's process with full access. See above.
- **No dependency management.** Plugins can't declare requirements; install them yourself.
- **Single file.** A plugin is one `.py` file. It can import other modules installed in the environment, but not sibling files in `plugins/` (they aren't on the import path).
- **Name clashes** between plugins or with built-ins are resolved silently by load order.
- **Unstable API.** Fox is pre-1.0. `fox_tool`, `Risk` and `ToolError` are intended to stay, but internal modules such as `fox.tools.files` or `CURRENT_RUN` may change. Changes will be noted in the [changelog](../CHANGELOG.md).
- **Models vary.** Whether the model calls your tool well depends mostly on the docstring and the model. Small local models struggle with tools that have many arguments.
- **Windows only**, like the rest of Fox.

## Checklist

Before you share or rely on a plugin:

- [ ] The function name is unique, descriptive and doesn't clash with a built-in (`fox tools`).
- [ ] The docstring tells the model what it does, when to use it, and each argument's format.
- [ ] Arguments have type hints and sensible defaults.
- [ ] The risk level is honest, and dynamic where some calls are worse than others.
- [ ] Normal and critical tools have a narrow, normalised `scope`.
- [ ] `describe` shows exactly what will happen, with resolved values.
- [ ] Output from outside sources uses `untrusted=True`.
- [ ] Expected failures raise `ToolError` with a helpful message; invalid input fails before the prompt.
- [ ] Network calls have timeouts; the tool `timeout` fits.
- [ ] Paths from the model go through `check_readable`/`check_writable`.
- [ ] No secrets in the code; no import-time side effects.
- [ ] It's been tried through a real prompt.

## Sharing your plugin

Plugins that are generally useful are welcome as contributions. Open a pull request that adds your file as `plugins/_example_<name>.py` (so it isn't loaded by default) with a short note in the module docstring on what it does and what it needs. See [CONTRIBUTING.md](../CONTRIBUTING.md#contributing-a-plugin).
