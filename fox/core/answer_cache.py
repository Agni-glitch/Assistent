"""Reuse answers to questions asked before, so repeating a question doesn't spend tokens again.

Only plain answers are kept: a turn that used a tool is never cached, so asking Fox to do something
always does it again. A question that leans on the conversation ("why?", "do it again") or on the
time ("what's the weather today") is never cached either.
"""

from __future__ import annotations

import hashlib
import re

# Words that point back at the conversation, so the same text can mean something different each time.
REFERS_BACK = {
    "it", "its", "that", "this", "these", "those", "them", "they", "he", "she", "him", "her",
    "more", "again", "above", "previous", "earlier", "last", "same", "else", "continue", "also", "too",
}  # fmt: skip
# Words whose answer changes with time.
TIME_SENSITIVE = {
    "today", "tonight", "tomorrow", "yesterday", "now", "time", "date", "day", "week", "month", "year",
    "current", "currently", "latest", "recent", "news", "weather", "price", "prices", "score", "status",
}  # fmt: skip
MIN_WORDS = 3


def normalize(text: str) -> str:
    """Lowercase, single spaces, no trailing punctuation: "What is DNS?" and "what is dns" match."""
    return re.sub(r"\s+", " ", text).strip().lower().rstrip("?!. ")


def is_cacheable(text: str) -> bool:
    words = re.findall(r"[a-z0-9']+", normalize(text))
    if len(words) < MIN_WORDS:
        return False
    return not any(w in REFERS_BACK or w in TIME_SENSITIVE for w in words)


def cache_key(text: str, context: str) -> str:
    """context: everything else that shapes the answer (system prompt, tools, model), so changing any of it misses."""
    return hashlib.sha256(f"{normalize(text)}\n\x00\n{context}".encode()).hexdigest()
