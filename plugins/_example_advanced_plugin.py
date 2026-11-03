"""A fuller plugin example. Copy it to a name without the leading underscore to load it.

It shows the options from docs/plugins.md: a scope so "always" answers apply to one target, a
describe that says exactly what will happen, a risk that depends on the arguments, untrusted
output, async network calls, ToolError for expected failures, and reusing Fox's file guards.
"""

import httpx

from fox.core.permissions import Risk
from fox.core.tooling import ToolError, fox_tool
from fox.tools.files import check_readable, check_writable, resolve


@fox_tool(
    scope=lambda a: a["city"].strip().lower(),
    describe=lambda a: f"Look up the weather in {a['city']}",
    untrusted=True,
    timeout=20,
)
async def get_weather(city: str) -> str:
    """Get the current weather for a city, such as 'Lahore' or 'Berlin'."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"https://wttr.in/{city.strip()}", params={"format": "3"})
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach the weather service: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ToolError(f"The weather service answered {response.status_code} for '{city}'.")
    return response.text.strip()


# Checks run in describe, which Fox calls before asking, so a refused path never reaches a prompt.
def _readable(a: dict):
    target = check_readable(resolve(a["path"]))  # keeps .env and the data folder out of reach
    if not target.is_file():
        raise ToolError(f"{target} isn't a file.")
    return target


@fox_tool(
    scope=lambda a: str(resolve(a["path"]).parent),
    describe=lambda a: f"Count the words in: {_readable(a)}",
    untrusted=True,
)
def count_words(path: str) -> dict:
    """Count the lines, words and characters in a text file. Accepts ~ and %VARS%."""
    text = _readable({"path": path}).read_text(encoding="utf-8", errors="replace")
    return {"lines": text.count("\n") + 1, "words": len(text.split()), "characters": len(text)}


def _note(a: dict):
    target = check_writable(resolve(a["path"]))  # Fox's own settings, code and plugins stay read-only
    if target.suffix.lower() not in (".txt", ".md"):
        raise ToolError("Notes must end in .txt or .md.")
    return target


def _describe_note(a: dict) -> str:
    target = _note(a)
    action = "Replace" if target.exists() else "Create"
    return f"{action} note: {target}\n\n{a['text'][:2000]}"


@fox_tool(
    risk=lambda a: Risk.CRITICAL if _note(a).exists() else Risk.NORMAL,
    scope=lambda a: str(resolve(a["path"]).parent),
    describe=_describe_note,
)
def save_note(path: str, text: str) -> str:
    """Save text to a .txt or .md note. Replacing an existing note asks every time."""
    target = _note({"path": path})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"Saved {len(text)} characters to {target}."
