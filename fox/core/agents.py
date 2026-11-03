"""User-defined agents, stored as YAML files in the agents folder."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("fox.agents")


class AgentDef(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    # Tool names, or ["*"] for every tool. Every call still goes through the permission gate.
    tools: list[str] = Field(default_factory=list)
    # Optional fallback chain for this agent, such as ["groq", "azure"]. Empty uses the main chain.
    models: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=10, ge=1, le=50)


class AgentRegistry:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.agents: dict[str, AgentDef] = {}
        self.reload()

    def reload(self) -> None:
        """Read the folder again, so new or edited files apply without a restart."""
        agents = {}
        for path in sorted(self.directory.glob("*.y*ml")):
            try:
                agent = AgentDef.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
                agents[agent.name] = agent
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                log.error("Skipping invalid agent file %s: %s", path.name, exc)
        self.agents = agents

    def get(self, name: str) -> AgentDef | None:
        self.reload()
        return self.agents.get(name)

    def list(self) -> list[AgentDef]:
        self.reload()
        return list(self.agents.values())

    def save(self, agent: AgentDef) -> Path:
        path = self.directory / f"{agent.name}.yaml"
        path.write_text(yaml.safe_dump(agent.model_dump(), sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.reload()
        return path

    def delete(self, name: str) -> bool:
        AgentDef(name=name, description="x", system_prompt="x")  # rejects names with path characters
        path = self.directory / f"{name}.yaml"
        if not path.exists():
            return False
        path.unlink()
        self.reload()
        return True
