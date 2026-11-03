"""Long-term memory that carries across conversations."""

from __future__ import annotations

from fox.core.permissions import Risk
from fox.core.storage import get_store
from fox.core.tooling import fox_tool


@fox_tool(describe=lambda a: f"Remember permanently: {a['fact']}")
def remember_fact(fact: str) -> str:
    """Save a lasting fact or preference about the user across conversations."""
    return f"Remembered (id {get_store().add_fact(fact)})."


@fox_tool(risk=Risk.SAFE)
def search_memory(query: str = "") -> str:
    """Search saved facts. An empty query lists the most recent ones."""
    facts = get_store().search_facts(query)
    return "\n".join(f"[{f['id']}] {f['text']}" for f in facts) or "Nothing saved yet."


@fox_tool(describe=lambda a: f"Forget saved fact id {a['fact_id']}")
def forget_fact(fact_id: int) -> str:
    """Delete a saved fact by its id from search_memory."""
    get_store().delete_fact(fact_id)
    return "Forgotten."
