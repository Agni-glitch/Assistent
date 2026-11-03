"""Delegating to specialist agents, and creating new ones from chat."""

from __future__ import annotations

from pydantic import ValidationError

from fox.core.agents import AgentDef
from fox.core.events import Event
from fox.core.permissions import Risk
from fox.core.tooling import CURRENT_RUN, REGISTRY, ToolError, fox_tool


@fox_tool(risk=Risk.SAFE, describe=lambda a: f"Hand off to agent '{a['agent']}': {a['task'][:100]}", timeout=None)
async def ask_agent(agent: str, task: str) -> str:
    """Give a task to a specialist agent and get its result. Include everything the agent needs in `task`."""
    run = CURRENT_RUN.get()
    definition = run.fox.agents.get(agent)
    if definition is None:
        names = ", ".join(a.name for a in run.fox.agents.list()) or "none"
        raise ToolError(f"No agent named '{agent}'. Available agents: {names}.")
    await run.emit(Event("agent_start", {"agent": agent, "task": task}))
    result = await run.fox.run_agent(definition, task, run.emit)
    await run.emit(Event("agent_end", {"agent": agent, "text": result.text}))
    return result.text


@fox_tool(risk=Risk.SAFE)
async def list_agents() -> str:
    """List the specialist agents, their purpose and their tools."""
    agents = CURRENT_RUN.get().fox.agents.list()
    return "\n".join(f"{a.name}: {a.description} (tools: {', '.join(a.tools) or 'none'})" for a in agents) or "No agents yet."


@fox_tool(risk=Risk.SAFE)
async def list_tools() -> str:
    """List every tool name available to give to an agent."""
    return ", ".join(sorted(REGISTRY))


@fox_tool(
    scope=lambda a: a["name"],
    describe=lambda a: (
        f"Create or replace agent '{a['name']}': {a['description']}\n"
        f"Tools: {', '.join(a['tools']) or 'none'}\n\n{a['system_prompt']}"
    ),
)
async def create_agent(name: str, description: str, system_prompt: str, tools: list[str] = [], models: list[str] = []) -> str:
    """Create a specialist agent, when the user asks for one. Use a lowercase name.

    `tools` lists tool names, or ['*'] for all. `models` is an optional fallback chain such as ['groq', 'azure'].
    """
    try:
        definition = AgentDef(name=name, description=description, system_prompt=system_prompt, tools=tools, models=models)
    except ValidationError as exc:
        raise ToolError(f"Invalid agent: {exc}") from exc
    unknown = [t for t in tools if t != "*" and t not in REGISTRY]
    if unknown:
        raise ToolError(f"Unknown tools: {unknown}. Use list_tools to see valid names.")
    return f"Saved agent '{name}' to {CURRENT_RUN.get().fox.agents.save(definition)}."


@fox_tool(risk=Risk.CRITICAL, scope=lambda a: a["name"], describe=lambda a: f"Delete agent '{a['name']}'")
async def delete_agent(name: str) -> str:
    """Delete a specialist agent, when the user asks."""
    try:
        deleted = CURRENT_RUN.get().fox.agents.delete(name)
    except ValidationError as exc:
        raise ToolError(f"Invalid agent name: {name}") from exc
    return f"Deleted agent '{name}'." if deleted else f"No agent named '{name}'."
