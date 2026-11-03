"""Events a run emits, so the CLI, voice loop and GUI can all render the same stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Event:
    # session | text | text_reset | model_switch | tool_start | tool_end | agent_start | agent_end | done | error
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}


Emit = Callable[[Event], Awaitable[None]]


async def noop_emit(event: Event) -> None:
    return None
