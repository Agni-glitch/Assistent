"""Read the rest of a document the user attached in chat."""

from __future__ import annotations

from fox.config import get_settings
from fox.core.permissions import Risk
from fox.core.tooling import fox_tool
from fox.core.uploads import upload_text


@fox_tool(risk=Risk.SAFE, untrusted=True)
def read_attachment(attachment_id: str, start: int = 0, length: int = 20000) -> str:
    """Read part of a document the user attached in chat, from character `start`.

    Long attachments are cut short in the message; the note at the cut gives the id and where to start.
    """
    text = upload_text(get_settings(), attachment_id)
    start = max(0, start)
    part = text[start : start + max(1000, min(length, 50000))]
    end = start + len(part)
    more = f"\n[{len(text) - end} characters left. Call again with start={end}.]" if end < len(text) else "\n[End of document.]"
    return part + more
