"""Reminders, shown as Windows notifications while Fox is running."""

from __future__ import annotations

from datetime import datetime

from fox.core.permissions import Risk
from fox.core.storage import get_store
from fox.core.tooling import ToolError, fox_tool


def _parse_when(when: str) -> datetime:
    try:
        due = datetime.fromisoformat(when.strip())
    except ValueError as exc:
        raise ToolError("Use an ISO date and time such as 2026-11-01T17:30:00.") from exc
    return due.astimezone().replace(tzinfo=None) if due.tzinfo else due


@fox_tool(describe=lambda a: f"Set a reminder for {_parse_when(a['when']):%d %b %Y %H:%M}: {a['text']}")
def set_reminder(text: str, when: str) -> str:
    """Set a reminder. `when` is a local ISO date and time, such as 2026-11-01T17:30:00."""
    due = _parse_when(when)
    if due <= datetime.now():
        raise ToolError(f"{due:%d %b %Y %H:%M} is in the past.")
    return f"Reminder {get_store().add_reminder(text, due)} set for {due:%d %b %Y %H:%M}. It fires while Fox is running."


@fox_tool(risk=Risk.SAFE)
def list_reminders() -> str:
    """List reminders that haven't fired yet."""
    rows = get_store().pending_reminders()
    return "\n".join(f"[{r['id']}] {r['due']}: {r['text']}" for r in rows) or "No reminders set."


@fox_tool(describe=lambda a: f"Cancel reminder id {a['reminder_id']}")
def cancel_reminder(reminder_id: int) -> str:
    """Cancel a reminder by its id from list_reminders."""
    get_store().delete_reminder(reminder_id)
    return "Reminder cancelled."
