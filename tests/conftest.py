from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Set before Fox is imported, so tests never touch real data or real keys.
_tmp = Path(tempfile.mkdtemp(prefix="fox-tests-"))
os.environ["FOX_DATA_DIR"] = str(_tmp / "data")
os.environ["FOX_AGENTS_DIR"] = str(_tmp / "agents")
os.environ["FOX_PLUGINS_DIR"] = str(_tmp / "plugins")
for key in ("AZURE_OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_MODEL"):
    os.environ[key] = ""

from typing import Any, Iterator  # noqa: E402

import pytest  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult  # noqa: E402
from pydantic import ConfigDict  # noqa: E402

from fox.config import get_settings  # noqa: E402
from fox.core.models import ModelHealth, ModelRouter, ModelSpec  # noqa: E402
from fox.core.permissions import Decision, PermissionRequest  # noqa: E402
from fox.runtime import Fox  # noqa: E402


class ScriptedModel(BaseChatModel):
    """Plays back scripted replies. An Exception in the script is raised instead."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: list[Any]
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages):
        self.seen.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        item: AIMessage = self._next(messages)
        calls = [
            {"name": c["name"], "args": __import__("json").dumps(c["args"]), "id": c["id"], "index": i, "type": "tool_call_chunk"}
            for i, c in enumerate(item.tool_calls)
        ]
        yield ChatGenerationChunk(message=AIMessageChunk(content=item.content, tool_call_chunks=calls))


class FakeApprover:
    def __init__(self, *answers: Decision):
        self.answers = list(answers)
        self.requests: list[PermissionRequest] = []

    async def request(self, req: PermissionRequest) -> Decision:
        self.requests.append(req)
        return self.answers.pop(0) if self.answers else Decision.DENY


def tool_call(name: str, call_id: str = "call_1", **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def router_with(settings, health, *models) -> ModelRouter:
    """A router over scripted models, keyed by position in the chain."""
    router = ModelRouter.__new__(ModelRouter)
    router.settings = settings
    router.specs = [ModelSpec("fake", str(i)) for i in range(len(models))]
    router.health = health
    table = dict(zip(router.specs, models))
    router.factory = lambda _s, spec: table[spec]
    router._built = {}
    return router


@pytest.fixture
def make_fox(tmp_path):
    def build(*scripts, approver=None):
        settings = get_settings()
        (settings.fox_data_dir / "permissions.json").unlink(missing_ok=True)
        (settings.fox_data_dir / "control.json").unlink(missing_ok=True)
        models = [ScriptedModel(script=list(s), seen=[]) for s in scripts]
        fox = Fox(settings=settings, approver=approver, router_factory=lambda s, c, h: router_with(s, h, *models))
        fox.models = models
        return fox

    return build


@pytest.fixture
def health():
    return ModelHealth(60)
